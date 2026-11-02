# 验证问答各阶段的权限复核、路由变化和流关闭；原件与数据库隔离，模型和检索结果由测试替代。
import tempfile
import unittest
from pathlib import Path
from app.v3.db import Database
from app.v3.domain import Domain,DomainError
from app.v3.models import Message
from app.v3.chat.service import ChatService
from app.v3.knowledge.contracts import Evidence


class ChatDependencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_trace_is_committed_before_done_event_reaches_client(self):
        chat=ChatService(self.domain,None,self.models())
        async def retrieve(*args,**kwargs):return self.domain.scope('t','admin',self.space),[self.evidence]
        chat.retrieve=retrieve
        source=chat.answer('t','admin',self.conv,self.space,'timed success').__aiter__()
        try:
            async for event in source:
                if event['event']=='done':
                    with self.db.transaction('t') as s:
                        message=s.get(Message,event['data']['message_id'])
                        self.assertEqual(message.state,'completed')
                        self.assertIsNone(message.trace.get('failure'))
                        stages={step['stage']:step for step in message.trace.get('stages',[])}
                        self.assertTrue({'retrieve','rerank','generate','request'}<=stages.keys())
                        self.assertEqual(stages['request']['outcome'],'ok')
                        self.assertGreaterEqual(stages['request']['milliseconds'],0)
                    break
            else:self.fail('Missing completion event')
        finally:await source.aclose()

    async def test_completed_trace_stays_successful_when_reader_closes_after_done(self):
        chat=ChatService(self.domain,None,self.models())
        async def retrieve(*args,**kwargs): return self.domain.scope('t','admin',self.space),[self.evidence]
        chat.retrieve=retrieve
        source=chat.answer('t','admin',self.conv,self.space,'continue').__aiter__()
        async for event in source:
            if event['event']=='done':
                message_id=event['data']['message_id']
                break
        await source.aclose()
        with self.db.transaction('t') as s:
            message=s.get(Message,message_id)
            self.assertEqual(message.state,'completed')
            self.assertIsNone(message.trace['failure'])
            self.assertEqual(next(step['outcome'] for step in message.trace['stages'] if step['stage']=='request'),'ok')

    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Database('sqlite:///'+str(Path(self.temp.name)/'test.db'),testing=True)
        self.db.migrate();self.domain=Domain(self.db,Path(self.temp.name)/'objects');self.domain.bootstrap('t','admin')
        self.space=self.domain.create_space('t','admin','policy');self.conv=self.domain.create_conversation('t','admin')
        self.a=self.domain.receive('t','admin',self.space,'a.txt',b'private a')
        self.b=self.domain.receive('t','admin',self.space,'b.txt',b'public b')
        self.evidence=Evidence('t',self.b['document_id'],self.b['version_id'],'g','i','c','chunk','evidence b',1,'segment',{})
        with self.db.transaction('t') as s:
            s.add(Message(id='first',tenant_id='t',owner='admin',conversation_id=self.conv,query='first',answer='private a [1]',state='completed',evidence=[{'document_id':self.a['document_id']}]))

    async def asyncTearDown(self):
        self.db.engine.dispose();self.temp.cleanup()

    def models(self):
        class Models:
            calls=0
            async def rank(self,query,evidence,dispatch_gate):
                async with dispatch_gate():self.calls+=1
                return evidence
            async def stream(self,query,evidence,history,dispatch_gate):
                async with dispatch_gate():self.calls+=1
                yield 'derived private a [1]'
        return Models()

    async def test_revoke_between_history_and_scope_stops_model_dispatch(self):
        models=self.models();chat=ChatService(self.domain,None,models)
        async def retrieve(*args,**kwargs):
            self.domain.withdraw('t','admin',self.a['document_id'])
            return self.domain.scope('t','admin',self.space),[self.evidence]
        chat.retrieve=retrieve
        with self.assertRaises(DomainError):
            async for _ in chat.answer('t','admin',self.conv,self.space,'continue'):pass
        self.assertEqual(models.calls,0)

    async def test_derived_answer_keeps_history_dependencies_after_restart(self):
        chat=ChatService(self.domain,None,self.models())
        async def retrieve(*args,**kwargs):return self.domain.scope('t','admin',self.space),[self.evidence]
        chat.retrieve=retrieve
        events=[event async for event in chat.answer('t','admin',self.conv,self.space,'continue')]
        self.assertEqual(events[-1]['event'],'done')
        from app.v3.operations import snapshot
        metrics=snapshot(self.domain,'t','admin')['durations']
        self.assertEqual({m['stage'] for m in metrics},{'request','retrieve','rerank','generate'})
        self.assertTrue(all(m['count']==1 and m['outcome']=='ok' for m in metrics))
        self.domain.withdraw('t','admin',self.a['document_id'])
        self.assertEqual(self.domain.history('t','admin',self.conv)[-1]['state'],'restricted')

    async def test_api_new_task_per_event_reaches_done_then_terminates_with_all_metrics(self):
        import asyncio
        from app.v3.operations import snapshot
        chat=ChatService(self.domain,None,self.models())
        async def retrieve(*args,**kwargs):return self.domain.scope('t','admin',self.space),[self.evidence]
        chat.retrieve=retrieve
        source=chat.answer('t','admin',self.conv,self.space,'continue').__aiter__()
        events=[]
        try:
            # Match API events(): each anext runs in a brand new Task, including
            # the step after done which performs final cleanup and returns None.
            while True:
                pending=asyncio.create_task(anext(source,None))
                value=await pending
                if value is None:break
                events.append(value)
        finally:await source.aclose()
        self.assertEqual(events[-1]['event'],'done')
        self.assertNotIn('error',{event['event'] for event in events})
        metrics=snapshot(self.domain,'t','admin')['durations']
        self.assertEqual({m['stage'] for m in metrics},{'request','retrieve','rerank','generate'})
        self.assertTrue(all(m['count']==1 and m['outcome']=='ok' for m in metrics))
        with self.db.transaction('t') as s:
            self.assertEqual(s.get(Message,events[-1]['data']['message_id']).state,'completed')

    # 复现 API 在不同任务中读取和关闭流的方式；关闭后必须释放共享范围，否则撤权或指标写入会被阻塞。
    async def test_api_cross_task_aclose_releases_shared_scope_and_records_cancellation(self):
        import asyncio
        from contextlib import asynccontextmanager
        from unittest.mock import patch
        import app.v3.monitoring as monitoring
        from app.v3.operations import snapshot
        chat=ChatService(self.domain,None,self.models())
        async def retrieve(*args,**kwargs):return self.domain.scope('t','admin',self.space),[self.evidence]
        chat.retrieve=retrieve
        original_transaction=self.db.atransaction;original_record=monitoring.record_duration;shared_scopes=0
        @asynccontextmanager
        async def transaction(*args,**kwargs):
            nonlocal shared_scopes
            async with original_transaction(*args,**kwargs) as session:
                if kwargs.get('shared'):shared_scopes+=1
                try:yield session
                finally:
                    if kwargs.get('shared'):shared_scopes-=1
        recorded=[]
        def record(*args):
            self.assertEqual(shared_scopes,0,'close business scopes before writing exclusive metrics')
            recorded.append(args[2])
            return original_record(*args)
        source=chat.answer('t','admin',self.conv,self.space,'continue').__aiter__()
        with patch.object(self.db,'atransaction',side_effect=transaction),patch.object(monitoring,'record_duration',side_effect=record):
            try:
                while True:
                    event=await asyncio.create_task(anext(source,None))
                    self.assertIsNotNone(event)
                    if event['event']=='token':break
                self.assertGreater(shared_scopes,0)
            finally:
                # API closes the stream in its parent Task, rather than the
                # per-event Task which first entered capture_request.
                await source.aclose()
        self.assertEqual(shared_scopes,0)
        self.assertEqual(set(recorded),{'request','retrieve','rerank','generate'})
        metrics={m['stage']:m for m in snapshot(self.domain,'t','admin')['durations']}
        self.assertEqual(metrics['request']['outcome'],'cancelled')
        self.assertEqual(metrics['generate']['outcome'],'cancelled')
        with self.db.transaction('t') as s:self.assertEqual(s.get(Message,self.domain.history('t','admin',self.conv)[-1]['id']).state,'failed')

    async def test_route_change_before_generation_retrieves_once(self):
        from app.v3.models import Space
        models=self.models();original=models.rank
        async def rank(*args,**kwargs):
            result=await original(*args,**kwargs)
            if models.calls==1:
                with self.db.transaction('t') as s:s.get(Space,self.space).route_revision+=1
            return result
        models.rank=rank
        chat=ChatService(self.domain,None,models);retrievals=[]
        async def retrieve(*args,**kwargs):
            retrievals.append(args[-1]);return self.domain.scope('t','admin',self.space),[self.evidence]
        chat.retrieve=retrieve
        events=[e async for e in chat.answer('t','admin',self.conv,self.space,'continue')]
        self.assertEqual(len(retrievals),2)
        self.assertEqual(events[-1]['event'],'done')

    async def test_long_query_is_rewritten_but_original_is_saved_and_generated(self):
        from dataclasses import replace
        models=self.models();queries=[];original_rank=models.rank
        async def rewrite(query,history,dispatch_gate):
            async with dispatch_gate():queries.append(('rewrite',query))
            return '退款期限'
        async def rank(query,*args,**kwargs):
            queries.append(('rank',query));return await original_rank(query,*args,**kwargs)
        models.rewrite=rewrite;models.rank=rank
        chat=ChatService(self.domain,None,models)
        original_scope=self.domain.scope
        self.domain.scope=lambda *args:replace(original_scope(*args),bindings=(object(),))
        async def retrieve(*args,**kwargs):
            queries.append(('retrieve',args[-1]));return self.domain.scope('t','admin',self.space),[self.evidence]
        chat.retrieve=retrieve
        query='订单退款条件和期限'*40
        events=[e async for e in chat.answer('t','admin',self.conv,self.space,query)]
        self.assertIn(('retrieve','退款期限'),queries)
        self.assertIn(('rank',query),queries)
        with self.db.transaction('t') as s:self.assertEqual(s.get(Message,events[-1]['data']['message_id']).query,query)

    async def test_empty_scope_does_not_dispatch_rewrite_or_models(self):
        chat=ChatService(self.domain,None,self.models())
        events=[e async for e in chat.answer('t','admin',self.conv,self.space,'长问题'*100)]
        self.assertEqual(chat.models.calls,0)
        self.assertEqual(events[-1]['event'],'done')

    async def test_second_route_change_fails_without_generation(self):
        from app.v3.models import Space
        models=self.models();original=models.rank
        async def rank(*args,**kwargs):
            result=await original(*args,**kwargs)
            with self.db.transaction('t') as s:s.get(Space,self.space).route_revision+=1
            return result
        models.rank=rank;chat=ChatService(self.domain,None,models);retrievals=[]
        async def retrieve(*args,**kwargs):
            retrievals.append(1);return self.domain.scope('t','admin',self.space),[self.evidence]
        chat.retrieve=retrieve
        with self.assertRaises(DomainError):
            async for _ in chat.answer('t','admin',self.conv,self.space,'continue'):pass
        self.assertEqual(len(retrievals),2)
        self.assertEqual(models.calls,2)

    # 已输出首段答案后切换路由必须终止旧流；继续拼接新后端证据会形成无法追溯的混合答案。
    async def test_route_change_after_token_does_not_mix_new_evidence(self):
        from app.v3.models import Space
        chat=ChatService(self.domain,None,self.models());retrievals=[]
        async def retrieve(*args,**kwargs):
            retrievals.append(1);return self.domain.scope('t','admin',self.space),[self.evidence]
        chat.retrieve=retrieve
        stream=chat.answer('t','admin',self.conv,self.space,'continue');events=[]
        async for event in stream:
            events.append(event)
            if event['event']=='token':
                with self.db.transaction('t') as s:s.get(Space,self.space).route_revision+=1
        self.assertEqual(len(retrievals),1)
        self.assertEqual(events[-1]['event'],'done')

    async def test_rewrite_rechecks_history_before_dispatch(self):
        from dataclasses import replace
        models=self.models();dispatched=[]
        async def rewrite(query,history,dispatch_gate):
            self.domain.withdraw('t','admin',self.a['document_id'])
            async with dispatch_gate():dispatched.append(True)
            return '退款期限'
        models.rewrite=rewrite;chat=ChatService(self.domain,None,models)
        original_scope=self.domain.scope
        self.domain.scope=lambda *args:replace(original_scope(*args),bindings=(object(),))
        with self.assertRaises(DomainError) as error:
            async for _ in chat.answer('t','admin',self.conv,self.space,'继续'):pass
        self.assertEqual(error.exception.status,403)
        self.assertEqual(dispatched,[])
        self.assertEqual(models.calls,0)

    async def test_retrieval_never_dispatches_rewritten_context_after_authz_change(self):
        scope=self.domain.scope('t','admin',self.space)
        self.domain.withdraw('t','admin',self.a['document_id'])
        chat=ChatService(self.domain,None,self.models())
        with self.assertRaises(DomainError) as error:
            await chat.retrieve('t','admin',self.space,'historical private content',authorization_scope=scope,dependencies={self.a['document_id']})
        self.assertEqual(error.exception.status,403)


    async def test_dify_releases_dispatch_lock_before_body_and_rechecks_second_request(self):
        import asyncio,json,httpx
        from contextlib import asynccontextmanager
        from app.v3.knowledge.dify import DifyBackend
        from app.v3.knowledge.contracts import Binding,Scope
        chat=ChatService(self.domain,None,self.models())
        snapshot=self.domain.scope('t','admin',self.space);held=False;requests=[]
        @asynccontextmanager
        async def gate():
            nonlocal held
            async with self.db.atransaction('t',shared=True) as s:
                chat.check(s,'t','admin',self.space,snapshot,[],dependencies={self.a['document_id']})
                held=True
                try:yield
                finally:held=False
        outer=self
        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                outer.assertFalse(held,'慢正文不能持有派发锁')
                async with asyncio.timeout(2):
                    await asyncio.to_thread(outer.domain.withdraw,'t','admin',outer.a['document_id'])
                yield b'{"records":[]}'
        def handler(request):
            self.assertTrue(held,'请求头派发应持有共享锁')
            requests.append(json.loads(request.content)['retrieval_model']['search_method'])
            return httpx.Response(200,stream=Body())
        binding=Binding('t',self.b['document_id'],self.b['version_id'],'g','i','c','remote','binding')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend=DifyBackend('https://dify.test','fixture',client)
            with self.assertRaises(DomainError) as error:
                await backend.retrieve('退款',Scope('t',(binding,)),dispatch_gate=gate)
        self.assertEqual(error.exception.status,403)
        self.assertEqual(requests,['semantic_search'])

    async def test_chat_weknora_slow_body_allows_revocation_without_holding_lock(self):
        import asyncio,httpx
        from dataclasses import replace
        from unittest.mock import patch
        from app.v3.knowledge.registry import BackendRegistry
        from app.v3.knowledge.contracts import Binding
        instance=self.domain.register_backend('t','admin','weknora','https://wk.test','V3_SECRET_WK')
        snapshot=self.domain.scope('t','admin',self.space)
        binding=Binding('t',self.b['document_id'],self.b['version_id'],'g',instance,'c','remote','binding')
        snapshot=replace(snapshot,bindings=(binding,))
        self.domain.scope=lambda *args:snapshot
        self.domain.backend_usable=lambda instance:True
        outer=self
        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                async with asyncio.timeout(2):
                    await asyncio.to_thread(outer.domain.withdraw,'t','admin',outer.a['document_id'])
                yield b'{"success":true,"data":[]}'
        with patch.dict('os.environ',{'V3_SECRET_WK':'fixture'}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,stream=Body()))) as client:
                chat=ChatService(self.domain,BackendRegistry(client),self.models())
                scope,evidence=await chat.retrieve('t','admin',self.space,'question',authorization_scope=snapshot,dependencies={self.a['document_id']})
        self.assertEqual(evidence,[])
        with self.db.transaction('t',shared=True) as s:
            with self.assertRaises(DomainError) as error:
                chat.check(s,'t','admin',self.space,scope,[],dependencies={self.a['document_id']})
        self.assertEqual(error.exception.status,403)
