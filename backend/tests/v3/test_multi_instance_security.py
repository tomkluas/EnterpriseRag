"""Two API instances and real PG coordination, synthetic HTTP service boundaries."""
import asyncio
import hashlib
import json
import os
import tempfile
import time
import unittest
import uuid
from contextlib import AsyncExitStack
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import httpx
from sqlalchemy import text
from sqlalchemy.engine import make_url
from app.v3.api import create_app
from app.v3.config import Settings
from app.v3.db import Database
from app.v3.domain import Domain
from app.v3.models import BackendInstance,Space,Document,Version,Replica,Message
from app.v3.knowledge.registry import BackendRegistry
from app.v3.chat.models import ModelService


@unittest.skipUnless(os.getenv('V3_TEST_DATABASE_URL'),'需要独立PostgreSQL测试库')
class MultiInstanceSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.addAsyncCleanup(self.cleanup_resources)
        self.schema_created=False
        self.schema='security_'+uuid.uuid4().hex;self.tenant='security-'+uuid.uuid4().hex
        self.base=Database(os.environ['V3_TEST_DATABASE_URL'])
        with self.base.engine.begin() as c:c.execute(text('CREATE SCHEMA "'+self.schema+'"'))
        self.schema_created=True
        url=make_url(os.environ['V3_TEST_DATABASE_URL']).update_query_dict({'options':'-csearch_path='+self.schema})
        self.db=Database(url.render_as_string(hide_password=False));self.db.migrate()
        self.other_db=Database(url.render_as_string(hide_password=False))
        self.domain=Domain(self.db,self.root/'objects');self.domain.bootstrap(self.tenant,'admin')
        self.domain.add_user(self.tenant,'admin','reader')
        self.space=self.domain.create_space(self.tenant,'admin','synthetic')
        self.instance=self.domain.register_backend(self.tenant,'admin','weknora','https://kb.test','V3_SECRET_FIXTURE')
        self.allowed=self.domain.receive(self.tenant,'admin',self.space,'allowed.txt',b'ALLOWED_EVIDENCE')
        self.forbidden=self.domain.receive(self.tenant,'admin',self.space,'forbidden.txt',b'FORBIDDEN_EVIDENCE')
        with self.db.transaction(self.tenant) as s:
            backend=s.get(BackendInstance,self.instance);backend.verified=True;backend.capabilities=['segment']
            s.get(Space,self.space).active_backend=self.instance
            for item,remote in ((self.allowed,'allowed'),(self.forbidden,'forbidden')):
                s.get(Document,item['document_id']).current_version=item['version_id']
                s.get(Version,item['version_id']).state='published'
                s.add(Replica(id='replica-'+remote,tenant_id=self.tenant,version_id=item['version_id'],
                    instance_id=self.instance,collection_id='kb',remote_id=remote,state='ready',chunks=1))
        self.domain.grant(self.tenant,'admin',self.allowed['document_id'],['reader'],[])
        self.pending=asyncio.Event();self.release=asyncio.Event();self.sent=[];self.calls=[]
        self.mode='before_first';self.rewrites=0
        outer=self
        class Body(httpx.AsyncByteStream):
            async def __aiter__(inner):
                if outer.mode=='after_first':
                    yield b'data: {"choices":[{"delta":{"content":"ALLOWED_FIRST [1]"}}]}\n\n'
                outer.pending.set();await outer.release.wait()
                yield b'data: {"choices":[{"delta":{"content":"LATE_BODY [1]"}}]}\n\ndata: [DONE]\n\n'
        class RewriteBody(httpx.AsyncByteStream):
            async def __aiter__(inner):
                outer.pending.set();await outer.release.wait()
                yield json.dumps({'choices':[{'message':{'content':json.dumps({'query':'standalone question','faithful':True})}}]}).encode()
        async def handler(request):
            body=json.loads(request.content);outer.calls.append((request.url.path,body,time.monotonic()))
            if request.url.host=='kb.test':
                return httpx.Response(200,json={'success':True,'data':[{'id':'chunk-'+rid,
                    'knowledge_id':rid,'content':('FORBIDDEN_EVIDENCE' if rid=='forbidden' else 'ALLOWED_EVIDENCE'),'score':1}
                    for rid in body['knowledge_ids']]})
            if request.url.path.endswith('/rerank'):
                return httpx.Response(200,json={'results':[{'index':i,'relevance_score':1} for i in range(len(body['documents']))]})
            if not body.get('stream'):
                outer.rewrites+=1
                return httpx.Response(200,stream=RewriteBody())
            return httpx.Response(200,stream=Body())
        self.stack=AsyncExitStack();await self.stack.__aenter__()
        self.http=await self.stack.enter_async_context(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        self.stack.enter_context(patch.dict(os.environ,{'V3_SECRET_MODEL':'fixture','V3_SECRET_FIXTURE':'fixture'}))
        settings=Settings(tenant_id=self.tenant,mode='development',bootstrap_user='reader',bootstrap_token='r'*40,
            objects_dir=self.root/'objects',model_base_url='https://model.test/v1',chat_model='test',rerank_model='test')
        app=create_app(settings,self.domain)
        app.state.chat.registry=BackendRegistry(self.http);app.state.chat.models=ModelService(settings,self.http)
        other=create_app(replace(settings,bootstrap_user='admin',bootstrap_token='a'*40),Domain(self.other_db,self.root/'objects'))
        await self.stack.enter_async_context(app.router.lifespan_context(app))
        await self.stack.enter_async_context(other.router.lifespan_context(other))
        async def observed(scope,receive,send):
            async def capture(message):
                if message['type']=='http.response.body':
                    for line in message.get('body',b'').splitlines():
                        if line.startswith(b'data: '):
                            event=json.loads(line[6:]);outer.sent.append((event,time.monotonic()))
                await send(message)
            await app(scope,receive,capture)
        self.reader=await self.stack.enter_async_context(httpx.AsyncClient(transport=httpx.ASGITransport(app=observed),base_url='http://api1.test',headers={'Authorization':'Bearer '+'r'*40}))
        self.admin=await self.stack.enter_async_context(httpx.AsyncClient(transport=httpx.ASGITransport(app=other),base_url='http://api2.test',headers={'Authorization':'Bearer '+'a'*40}))
        self.chat=app.state.chat

    async def cleanup_resources(self):
        errors=[]
        if hasattr(self,'release'):self.release.set()
        try:
            if hasattr(self,'stack'):await self.stack.aclose()
        except Exception as error:errors.append(error)
        for name in ('db','other_db'):
            if hasattr(self,name):getattr(self,name).engine.dispose()
        try:
            if self.schema_created:
                assert self.schema.startswith('security_') and len(self.schema)==41
                with self.base.engine.begin() as c:c.execute(text('DROP SCHEMA "'+self.schema+'" CASCADE'))
        except Exception as error:errors.append(error)
        finally:
            if hasattr(self,'base'):self.base.engine.dispose()
            self.temp.cleanup()
        if errors:raise errors[0]

    async def revoke(self):
        response=await self.admin.post(f"/api/v3/documents/{self.allowed['document_id']}/grants",json={'readers':[],'denied':['reader']})
        self.assertEqual(response.status_code,200)
        return time.monotonic()

    async def stream_case(self,after_first):
        self.mode='after_first' if after_first else 'before_first'
        conversation=(await self.reader.post('/api/v3/conversations')).json()['id']
        task=asyncio.create_task(self.reader.post(f'/api/v3/conversations/{conversation}/messages/stream',json={'space_id':self.space,'query':'question'}))
        try:
            await asyncio.wait_for(self.pending.wait(),5)
            committed=await asyncio.wait_for(self.revoke(),3)
            self.release.set();response=await asyncio.wait_for(task,5)
            self.assertEqual(response.status_code,200)
            tokens=[(e,t) for e,t in self.sent if e['event']=='token']
            self.assertEqual(len(tokens),1 if after_first else 0)
            self.assertTrue(all(t<committed for e,t in tokens))
            self.assertTrue(any(e['event']=='error' for e,t in self.sent))
            self.assertFalse(any(e['event']=='done' for e,t in self.sent))
            history=(await self.reader.get(f'/api/v3/conversations/{conversation}/messages')).json()
            self.assertEqual(history[-1]['state'],'restricted')
            self.assertEqual(history[-1]['answer'],'相关资料权限已变化，回答已隐藏')
            self.assertEqual(history[-1]['evidence'],[])
            self.assertNotIn('ALLOWED_FIRST',json.dumps(history))
            self.assertNotIn('ALLOWED_EVIDENCE',json.dumps(history))
            self.assertNotIn('LATE_BODY',json.dumps(history))
            self.assertTrue(all('FORBIDDEN_EVIDENCE' not in json.dumps(body) for path,body,t in self.calls))
        finally:
            self.release.set()
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)

    async def test_other_instance_revokes_before_first_body(self):await self.stream_case(False)
    async def test_other_instance_revokes_after_first_body(self):await self.stream_case(True)

    async def test_revoke_while_history_rewrite_body_pending_blocks_followup_dispatch(self):
        conversation=(await self.reader.post('/api/v3/conversations')).json()['id']
        with self.db.transaction(self.tenant) as s:
            s.add(Message(id=uuid.uuid4().hex,tenant_id=self.tenant,owner='reader',conversation_id=conversation,
                query='first',answer='ALLOWED_EVIDENCE [1]',state='completed',evidence=[{'document_id':self.allowed['document_id']}]))
        task=asyncio.create_task(self.reader.post(f'/api/v3/conversations/{conversation}/messages/stream',json={'space_id':self.space,'query':'continue'}))
        try:
            await asyncio.wait_for(self.pending.wait(),5);committed=await asyncio.wait_for(self.revoke(),3)
            self.release.set();response=await asyncio.wait_for(task,5)
            self.assertEqual(response.status_code,200);self.assertEqual(self.rewrites,1)
            self.assertTrue(all(t<committed for path,body,t in self.calls))
            self.assertFalse(any(e['event'] in ('sources','token','done') for e,t in self.sent))
            history=(await self.reader.get(f'/api/v3/conversations/{conversation}/messages')).json()
            self.assertEqual(history[0]['state'],'restricted')
            self.assertEqual(history[0]['answer'],'相关资料权限已变化，回答已隐藏')
            self.assertEqual(history[0]['evidence'],[])
            self.assertNotIn('ALLOWED_EVIDENCE',json.dumps(history))
        finally:
            self.release.set()
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)

    async def test_all_authorized_batches_include_small_tail_and_separate_collections(self):
        expected={'allowed'}
        with self.db.transaction(self.tenant) as s:
            for index in range(44):
                doc_id=uuid.uuid4().hex;version_id=uuid.uuid4().hex;remote='batch-'+str(index)
                expected.add(remote)
                s.add(Document(id=doc_id,tenant_id=self.tenant,space_id=self.space,owner='admin',name=remote,readers=['reader'],current_version=version_id))
                s.add(Version(id=version_id,tenant_id=self.tenant,document_id=doc_id,filename=remote+'.txt',
                    sha256=hashlib.sha256(b'ALLOWED_EVIDENCE').hexdigest(),object_key=hashlib.sha256(self.tenant.encode()).hexdigest()+'/'+version_id,state='published'))
                s.add(Replica(id=uuid.uuid4().hex,tenant_id=self.tenant,version_id=version_id,instance_id=self.instance,
                    collection_id='kb-0' if index<22 else 'kb-1',remote_id=remote,state='ready',chunks=1))
        scope,evidence=await self.chat.retrieve(self.tenant,'reader',self.space,'question')
        calls=[body['knowledge_ids'] for path,body,t in self.calls if path.endswith('/hybrid-search')]
        self.assertEqual(len(scope.bindings),45)
        self.assertEqual(len(evidence),45)
        self.assertEqual({rid for batch in calls for rid in batch},expected)
        self.assertEqual(sorted(map(len,calls)),[1,2,2,20,20])
        self.assertTrue(all('FORBIDDEN_EVIDENCE' not in item.content for item in evidence))
