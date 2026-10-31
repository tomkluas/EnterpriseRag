import asyncio
import re
from collections import defaultdict
from contextlib import asynccontextmanager
from ..domain import DomainError, one, uid
from ..models import Tenant, Space, BackendInstance, Document, Conversation, Message
from ..knowledge.contracts import Scope
from ..monitoring import capture_request, timed_call, stage_timer


class RetrievalChanged(DomainError):
    pass


class ChatService:
    def __init__(self,domain,registry,models):
        self.domain=domain; self.db=domain.db; self.registry=registry; self.models=models

    def check(self,s,tenant,user,space_id,scope,evidence,strict=True,dependencies=()):
        self.domain.user(s,tenant,user); tr=s.get(Tenant,tenant); space=one(s,Space,tenant,space_id)
        if tr.authz!=scope.authz_revision: raise DomainError('资料权限已变化，请重新提问',403)
        for item in evidence:
            if not self.domain.allowed(s,tenant,user,one(s,Document,tenant,item.document_id)):
                raise DomainError('资料已撤回或访问权限已变化',403)
        for document_id in dependencies:
            if not self.domain.allowed(s,tenant,user,one(s,Document,tenant,document_id)):
                raise DomainError('历史资料权限已变化，请开启新会话',403)
        if strict and (tr.catalog!=scope.catalog_revision or space.route_revision!=scope.route_revision):
            raise RetrievalChanged('资料版本或底座已切换，请重新提问')

    async def retrieve(self,tenant,user,space_id,query,*,authorization_scope=None,dependencies=()):
        scope=await asyncio.to_thread(self.domain.scope,tenant,user,space_id)
        async with self.db.atransaction(tenant,shared=True) as s:
            self.check(s,tenant,user,space_id,authorization_scope or scope,[],dependencies=dependencies)
        groups=defaultdict(list)
        for binding in scope.bindings: groups[(binding.instance_id,binding.collection_id)].append(binding)
        semaphore=asyncio.Semaphore(4)
        async def fetch(instance_id,bindings):
            async with semaphore:
                async with self.db.atransaction(tenant,shared=True) as s:
                    instance=one(s,BackendInstance,tenant,instance_id)
                    if not self.domain.backend_usable(instance): raise DomainError('底座尚未完成当前环境的验证',503)
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
            # TaskGroup wraps dispatch checks; preserve their domain status and
            # allow only revision changes to enter the bounded retry path.
            errors=list(exc.exceptions)
            if any(isinstance(error,DomainError) and error.status==403 for error in errors):
                raise DomainError('资料权限已变化，请重新提问',403) from exc
            if all(isinstance(error,RetrievalChanged) for error in errors):
                raise RetrievalChanged('资料版本或底座已切换，请重新提问') from exc
            raise
        # Round-robin candidate order. Never compare scores from different recall batches.
        lists=[task.result() for task in tasks]; result=[]; seen=set()
        for i in range(max((len(items) for items in lists),default=0)):
            for items in lists:
                if i>=len(items): continue
                item=items[i]; key=(item.instance_id,item.collection_id,item.chunk_id)
                if key not in seen: result.append(item); seen.add(key)
        return scope,result

    async def answer(self,tenant,user,conversation_id,space_id,query):
        async with capture_request(self.db,tenant) as samples:
            response=self._answer(tenant,user,conversation_id,space_id,query,samples)
            try:
                async for event in response:yield event
            finally:
                await response.aclose()

    async def _answer(self,tenant,user,conversation_id,space_id,query,samples):
        history=await asyncio.to_thread(self.domain.history,tenant,user,conversation_id,True)
        history=[item for item in history if item['state']=='completed'][-6:]
        dependencies={e['document_id'] for item in history for e in item['evidence']+item['dependencies']}
        message_id=uid(); answer=''; evidence=[]; completed=False; stream=None
        async with self.db.atransaction(tenant) as s:
            conv=one(s,Conversation,tenant,conversation_id)
            if conv.owner!=user: raise DomainError('不能访问他人会话',403)
            s.add(Message(id=message_id,tenant_id=tenant,owner=user,conversation_id=conversation_id,query=query))
        try:
            yield {'event':'status','data':'正在检索授权资料','message_id':message_id}
            for attempt in range(2):
                try:
                    rewrite_scope=await asyncio.to_thread(self.domain.scope,tenant,user,space_id)
                    @asynccontextmanager
                    async def rewrite_gate():
                        async with self.db.atransaction(tenant,shared=True) as s:
                            self.check(s,tenant,user,space_id,rewrite_scope,[],dependencies=dependencies)
                            yield
                    retrieval_query=query
                    if rewrite_scope.bindings and (len(query)>250 or history):
                        retrieval_query=await timed_call('rewrite',samples,self.models.rewrite,query,history,dispatch_gate=rewrite_gate)
                    scope,evidence=await timed_call('retrieve',samples,self.retrieve,tenant,user,space_id,retrieval_query,authorization_scope=rewrite_scope,dependencies=dependencies)
                    if scope.authz_revision!=rewrite_scope.authz_revision:
                        raise DomainError('资料权限已变化，请重新提问',403)
                    if not evidence:
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
                    evidence=await timed_call('rerank',samples,self.models.rank,query,evidence,dispatch_gate=dispatch_gate)
                    async with self.db.atransaction(tenant) as s:
                        self.check(s,tenant,user,space_id,scope,evidence,dependencies=dependencies)
                        message=one(s,Message,tenant,message_id)
                        message.evidence=[e.to_dict() for e in evidence]
                        message.dependencies=[{'document_id':id} for id in sorted(dependencies)]
                    async def generate():
                        async with stage_timer('generate',samples):
                            source=self.models.stream(query,evidence,history,dispatch_gate=dispatch_gate).__aiter__()
                            try:
                                async for part in source:yield part
                            finally:
                                if hasattr(source,'aclose'):await source.aclose()
                    stream=generate().__aiter__()
                    first=await anext(stream,None)
                    if not first: raise DomainError('模型未返回有效回答',502)
                    break
                except RetrievalChanged:
                    if stream is not None and hasattr(stream,'aclose'):await stream.aclose()
                    stream=None
                    if attempt:raise
            if evidence:
                async with self.db.atransaction(tenant,shared=True) as s:
                    self.check(s,tenant,user,space_id,scope,evidence,strict=False,dependencies=dependencies)
                    yield {'event':'sources','data':[e.to_dict() for e in evidence]}
                async def pieces():
                    yield first
                    async for piece in stream: yield piece
                async for piece in pieces():
                    if len(answer)+len(piece)>100000: raise DomainError('回答超过当前长度限制',502)
                    async with self.db.atransaction(tenant,shared=True) as s:
                        self.check(s,tenant,user,space_id,scope,evidence,strict=False,dependencies=dependencies)
                        answer+=piece
                        yield {'event':'token','data':piece}
                cited={int(value) for value in re.findall(r'\[(\d+)\]',answer)}
                if not cited or not cited.issubset(set(range(1,len(evidence)+1))):
                    raise DomainError('回答缺少有效引用，请重新提问',502)
            async with self.db.atransaction(tenant) as s:
                self.check(s,tenant,user,space_id,scope,evidence,strict=False,dependencies=dependencies if evidence else ())
                message=one(s,Message,tenant,message_id); message.answer=answer; message.evidence=[e.to_dict() for e in evidence]; message.state='completed'
            completed=True
            yield {'event':'done','data':{'message_id':message_id}}
        finally:
            if stream is not None and hasattr(stream,'aclose'): await stream.aclose()
            if not completed:
                async with self.db.atransaction(tenant) as s:
                    message=one(s,Message,tenant,message_id); message.state='failed'; message.answer=answer
