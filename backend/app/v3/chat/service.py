# 问答编排层：从领域层取得授权快照，调用知识底座与模型，并将证据和回答写入会话。
# 权限检查覆盖发往外部服务的数据，也覆盖返回给浏览器的每一段内容。
import asyncio
import re
from collections import defaultdict
from contextlib import asynccontextmanager
from ..domain import DomainError, one, uid
from ..models import Tenant, Space, BackendInstance, Document, Conversation, Message
from ..knowledge.contracts import Scope
from ..monitoring import capture_request, timed_call, stage_timer


# 只有目录或路由变化允许有限重试；权限变化必须直接拒绝。
class RetrievalChanged(DomainError):
    pass


# 串联领域授权、底座召回与模型服务，负责消息状态和流关闭后的收尾。
class ChatService:
    def __init__(self,domain,registry,models):
        self.domain=domain; self.db=domain.db; self.registry=registry; self.models=models

    # 统一检查用户、授权修订号、当前证据及历史依赖；strict 额外要求目录与路由快照未变。
    def check(self,s,tenant,user,space_id,scope,evidence,strict=True,dependencies=()):
        self.domain.user(s,tenant,user); tr=s.get(Tenant,tenant); space=one(s,Space,tenant,space_id)
        if tr.authz!=scope.authz_revision: raise DomainError('资料权限已变化，请重新提问',403)
        # 修订号拦截全局授权变化，逐份资料复查再覆盖撤回及历史依赖的当前访问状态。
        for item in evidence:
            if not self.domain.allowed(s,tenant,user,one(s,Document,tenant,item.document_id)):
                raise DomainError('资料已撤回或访问权限已变化',403)
        for document_id in dependencies:
            if not self.domain.allowed(s,tenant,user,one(s,Document,tenant,document_id)):
                raise DomainError('历史资料权限已变化，请开启新会话',403)
        # 生成前要求资料与路由保持一致；输出中可放宽版本变化，但始终检查权限。
        if strict and (tr.catalog!=scope.catalog_revision or space.route_revision!=scope.route_revision):
            raise RetrievalChanged('资料版本或底座已切换，请重新提问')

    # 按底座实例和物理集合分组，并限制并发和单批绑定数，避免混用不同集合的授权范围。
    async def retrieve(self,tenant,user,space_id,query,*,authorization_scope=None,dependencies=()):
        scope=await asyncio.to_thread(self.domain.scope,tenant,user,space_id)
        async with self.db.atransaction(tenant,shared=True) as s:
            self.check(s,tenant,user,space_id,authorization_scope or scope,[],dependencies=dependencies)
        groups=defaultdict(list)
        for binding in scope.bindings: groups[(binding.instance_id,binding.collection_id)].append(binding)
        semaphore=asyncio.Semaphore(4)
        # 同一实例也可能存在多个物理集合，必须按实例和集合一起拆批，不能混发授权 ID。
        async def fetch(instance_id,bindings):
            async with semaphore:
                async with self.db.atransaction(tenant,shared=True) as s:
                    instance=one(s,BackendInstance,tenant,instance_id)
                    if not self.domain.backend_usable(instance): raise DomainError('底座尚未完成当前环境的验证',503)
                # 实际发送前再次持共享事务复查，封住取得快照到外发之间的权限撤回窗口。
                @asynccontextmanager
                async def retrieval_gate():
                    async with self.db.atransaction(tenant,shared=True) as s:
                        self.check(s,tenant,user,space_id,authorization_scope or scope,[],dependencies=dependencies)
                        if not self.domain.backend_usable(one(s,BackendInstance,tenant,instance_id)):
                            raise DomainError('底座尚未完成当前环境的验证',503)
                        yield
                return await self.registry.get(instance,'retrieve').retrieve(query,Scope(tenant,tuple(bindings),scope.authz_revision,scope.catalog_revision,scope.route_revision),dispatch_gate=retrieval_gate)
        tasks=[]
        try:
            async with asyncio.timeout(15):
                async with asyncio.TaskGroup() as group:
                    for (instance_id,_),bindings in groups.items():
                        for start in range(0,len(bindings),20): tasks.append(group.create_task(fetch(instance_id,bindings[start:start+20])))
        except ExceptionGroup as exc:
            # 并发子任务任一撤权都优先拒绝；只有全部错误均为目录/路由变化才允许重试。
            errors=list(exc.exceptions)
            if any(isinstance(error,DomainError) and error.status==403 for error in errors):
                raise DomainError('资料权限已变化，请重新提问',403) from exc
            if all(isinstance(error,RetrievalChanged) for error in errors):
                raise RetrievalChanged('资料版本或底座已切换，请重新提问') from exc
            raise
        # 不同召回批次的分数不直接比较；轮转合并并按实例、集合与块身份去重。
        lists=[task.result() for task in tasks]; result=[]; seen=set()
        for i in range(max((len(items) for items in lists),default=0)):
            for items in lists:
                if i>=len(items): continue
                item=items[i]; key=(item.instance_id,item.collection_id,item.chunk_id)
                if key not in seen: result.append(item); seen.add(key)
        return scope,result

    # 外层收集整个流的耗时与失败原因；关闭内层生成器后才落指标，避免持业务锁写监控。
    async def answer(self,tenant,user,conversation_id,space_id,query):
        message_id=None; samples=[]; failure=None; completion=None
        try:
            async with capture_request(self.db,tenant) as samples:
                response=self._answer(tenant,user,conversation_id,space_id,query,samples)
                try:
                    async for event in response:
                        if event.get('message_id'): message_id=event['message_id']
                        # 完成通知必须晚于耗时提交；浏览器收到 done 后会立即关闭流。
                        if event.get('event')=='done':
                            completion=event
                            break
                        yield event
                finally:
                    await response.aclose()
        except DomainError as error:
            failure={'code':'request_rejected','detail':str(error)}
            raise
        except (asyncio.CancelledError,GeneratorExit):
            failure={'code':'cancelled','detail':'回答已中止或连接已关闭'}
            raise
        except Exception:
            failure={'code':'service_error','detail':'服务调用失败，请联系管理员检查服务连接与模型配置'}
            raise
        finally:
            if message_id:
                # 业务生成器已关闭、共享事务已退出后再记录明细，避免持读锁尝试写入。
                async with self.db.atransaction(tenant) as s:
                    message=one(s,Message,tenant,message_id)
                    # 浏览器收到完成事件后会关闭读取器；连接关闭不能把已保存的完整回答改成失败。
                    complete=message.state=='completed'
                    message.trace={'stages':[{'stage':stage,'milliseconds':round(seconds*1000,2),
                        'outcome':'ok' if complete and stage=='request' and outcome=='cancelled' else outcome}
                        for stage,seconds,outcome in samples], 'failure':None if complete else failure}
        if completion is not None:
            yield completion

    async def _answer(self,tenant,user,conversation_id,space_id,query,samples):
        # 失败或未完成回答不作为模型历史，最多携带最近六条已完成记录。
        history=await asyncio.to_thread(self.domain.history,tenant,user,conversation_id,True)
        history=[item for item in history if item['state']=='completed'][-6:]
        # 历史回答也会发送给模型，因此历史证据及其依赖都必须通过本轮权限检查。
        dependencies={e['document_id'] for item in history for e in item['evidence']+item['dependencies']}
        message_id=uid(); answer=''; evidence=[]; completed=False; stream=None
        async with self.db.atransaction(tenant) as s:
            conv=one(s,Conversation,tenant,conversation_id)
            # 先保存 processing 消息，即使后续模型失败，也能追踪这次提问的状态。
            if conv.owner!=user: raise DomainError('不能访问他人会话',403)
            if conv.archived:raise DomainError('会话已删除',404)
            s.add(Message(id=message_id,tenant_id=tenant,owner=user,conversation_id=conversation_id,space_id=space_id,query=query))
            self.domain.audit(s,tenant,user,'message.ask',message_id)
        try:
            yield {'event':'status','data':'正在检索授权资料','message_id':message_id}
            # 尚未输出证据与正文时，遇到目录或路由切换最多重建一次检索流程。
            for attempt in range(2):
                try:
                    rewrite_scope=await asyncio.to_thread(self.domain.scope,tenant,user,space_id)
                    @asynccontextmanager
                    async def rewrite_gate():
                        async with self.db.atransaction(tenant,shared=True) as s:
                            self.check(s,tenant,user,space_id,rewrite_scope,[],dependencies=dependencies)
                            yield
                    retrieval_query=query
                    # 长问题或带历史时改写为独立问题；没有授权资料时不调用改写模型。
                    if rewrite_scope.bindings and (len(query)>250 or history):
                        retrieval_query=await timed_call('rewrite',samples,self.models.rewrite,query,history,dispatch_gate=rewrite_gate)
                    scope,evidence=await timed_call('retrieve',samples,self.retrieve,tenant,user,space_id,retrieval_query,authorization_scope=rewrite_scope,dependencies=dependencies)
                    if scope.authz_revision!=rewrite_scope.authz_revision:
                        raise DomainError('资料权限已变化，请重新提问',403)
                    if not evidence:
                        # 无证据直接输出固定答复，不把无关历史继续发送给生成模型。
                        async with self.db.atransaction(tenant,shared=True) as s:
                            self.check(s,tenant,user,space_id,scope,[],dependencies=dependencies)
                            answer='根据现有资料无法回答此问题。'
                            yield {'event':'token','data':answer}
                        break
                    yield {'event':'status','data':'正在核对并整理证据'}
                    @asynccontextmanager
                    async def dispatch_gate():
                        async with self.db.atransaction(tenant,shared=True) as s:
                            self.check(s,tenant,user,space_id,scope,evidence,dependencies=dependencies)
                            yield
                    # 精排会把证据发送给外部服务，也必须通过与生成相同的授权门。
                    evidence=await timed_call('rerank',samples,self.models.rank,query,evidence,dispatch_gate=dispatch_gate)
                    async with self.db.atransaction(tenant) as s:
                        self.check(s,tenant,user,space_id,scope,evidence,dependencies=dependencies)
                        message=one(s,Message,tenant,message_id)
                        message.evidence=[e.to_dict() for e in evidence]
                        message.dependencies=[{'document_id':id} for id in sorted(dependencies)]
                    # 单独包装模型生成器，保证提前中止和重试时也关闭底层 HTTP 流并结算耗时。
                    async def generate():
                        async with stage_timer('generate',samples):
                            source=self.models.stream(query,evidence,history,dispatch_gate=dispatch_gate).__aiter__()
                            try:
                                async for part in source:yield part
                            finally:
                                if hasattr(source,'aclose'):await source.aclose()
                    stream=generate().__aiter__()
                    # 先取得首段确认模型可用，此时尚未向浏览器发送证据和生成正文。
                    first=await anext(stream,None)
                    if not first: raise DomainError('模型未返回有效回答',502)
                    break
                except RetrievalChanged:
                    # 只有尚未对外输出正文时才能重建快照；关闭旧流，避免同时保留两次生成请求。
                    if stream is not None and hasattr(stream,'aclose'):await stream.aclose()
                    stream=None
                    if attempt:raise
            # 开始输出后不再因目录切换重跑模型；每次来源和正文输出仍复查资料及历史权限。
            if evidence:
                async with self.db.atransaction(tenant,shared=True) as s:
                    self.check(s,tenant,user,space_id,scope,evidence,strict=False,dependencies=dependencies)
                    yield {'event':'sources','data':[e.to_dict() for e in evidence]}
                async def pieces():
                    yield first
                    async for piece in stream: yield piece
                async for piece in pieces():
                    # 输出端也持共享事务复查；不能只在模型请求发出前检查一次权限。
                    if len(answer)+len(piece)>100000: raise DomainError('回答超过当前长度限制',502)
                    async with self.db.atransaction(tenant,shared=True) as s:
                        self.check(s,tenant,user,space_id,scope,evidence,strict=False,dependencies=dependencies)
                        answer+=piece
                        yield {'event':'token','data':piece}
                # 只验证引用编号存在且属于本轮证据，不等于已证明每条结论的事实正确性。
                cited={int(value) for value in re.findall(r'\[(\d+)\]',answer)}
                if not cited or not cited.issubset(set(range(1,len(evidence)+1))):
                    raise DomainError('回答缺少有效引用，请重新提问',502)
            async with self.db.atransaction(tenant) as s:
                self.check(s,tenant,user,space_id,scope,evidence,strict=False,dependencies=dependencies if evidence else ())
                # 最终保存与最后一次权限复查处于同一事务，完成通知必须等这个事务成功提交。
                message=one(s,Message,tenant,message_id); message.answer=answer; message.evidence=[e.to_dict() for e in evidence]; message.state='completed'
            # 先提交完整消息再通知 done；客户端随后关闭流不应覆盖完成状态。
            completed=True
            yield {'event':'done','data':{'message_id':message_id}}
        finally:
            if stream is not None and hasattr(stream,'aclose'): await stream.aclose()
            # 异常或中断保留已输出正文，但失败消息不会进入后续已完成历史。
            if not completed:
                async with self.db.atransaction(tenant) as s:
                    message=one(s,Message,tenant,message_id); message.state='failed'; message.answer=answer
