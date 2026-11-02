# 验证 PostgreSQL 特有的行锁、租约与并发提交；仅在提供 V3_TEST_DATABASE_URL 时运行，模型 HTTP 使用模拟响应。
import asyncio
import os
import threading
import time
import unittest
import tempfile
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from unittest.mock import patch
import httpx
from app.v3.db import Database
from app.v3.domain import Domain,DomainError
from app.v3.chat.models import ModelService
from app.v3.chat.service import ChatService
from app.v3.config import Settings
from app.v3.knowledge.contracts import Evidence


@unittest.skipUnless(os.getenv('V3_TEST_DATABASE_URL'),'需要独立 PostgreSQL 测试连接')
class PostgresLockTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_reconciliation_skip_locked_claims_unique_jobs(self):
        from sqlalchemy import text
        from sqlalchemy.engine import make_url
        from app.v3.models import Job
        base=Database(os.environ['V3_TEST_DATABASE_URL'])
        suffix=uuid.uuid4().hex;schema='autoreconcile_'+suffix;db=None
        try:
            with base.engine.begin() as connection:connection.execute(text('CREATE SCHEMA "'+schema+'"'))
            url=make_url(os.environ['V3_TEST_DATABASE_URL']).update_query_dict({'options':'-csearch_path='+schema})
            db=Database(url.render_as_string(hide_password=False));db.migrate()
            with tempfile.TemporaryDirectory() as temp:
                domain=Domain(db,Path(temp));domain.bootstrap('t','admin')
                space=domain.create_space('t','admin','test')
                received=[domain.receive('t','admin',space,'a.txt',b'test') for _ in range(2)]
                with db.transaction('t') as s:
                    for item in received:
                        job=s.get(Job,item['job_id']);job.state='needs_reconciliation'
                        job.payload={**job.payload,'submission_proof':{'format':1}}
                claimed=await asyncio.gather(asyncio.to_thread(domain.claim_auto_reconciliation),asyncio.to_thread(domain.claim_auto_reconciliation))
                self.assertEqual({j['id'] for j in claimed},{r['job_id'] for r in received})
                self.assertTrue(all(j['_reconcile'] and j['state']=='running' and j['lease_token'] for j in claimed))
        finally:
            if db:db.engine.dispose()
            assert schema=='autoreconcile_'+suffix and len(suffix)==32
            with base.engine.begin() as connection:connection.execute(text('DROP SCHEMA IF EXISTS "'+schema+'" CASCADE'))
            base.engine.dispose()

    async def test_recovery_bundle_real_postgres_retains_permissions_and_revokes_leases(self):
        from sqlalchemy import text
        from sqlalchemy.engine import make_url
        from app.v3.models import Job,BackendInstance,Tenant
        from app.v3.recovery import backup_tenant,restore_tenant
        db=Database(os.environ['V3_TEST_DATABASE_URL']);db.migrate()
        schema='recovery_'+uuid.uuid4().hex
        tenant='recovery-test-'+uuid.uuid4().hex
        target=None
        try:
            # A fresh schema isolates all restored tables from the existing test data.
            with db.engine.begin() as connection:connection.execute(text('CREATE SCHEMA "'+schema+'"'))
            url=make_url(os.environ['V3_TEST_DATABASE_URL']).update_query_dict({'options':'-csearch_path='+schema})
            target=Database(url.render_as_string(hide_password=False));target.migrate()
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp);domain=Domain(db,root/'originals');domain.bootstrap(tenant,'admin')
                space=domain.create_space(tenant,'admin','test')
                received=domain.receive(tenant,'admin',space,'policy.txt',b'original')
                domain.add_user(tenant,'admin','reader')
                domain.grant(tenant,'admin',received['document_id'],['reader'],['reader'])
                instance=domain.register_backend(tenant,'admin','weknora','https://kb.test','V3_SECRET_TEST')
                with db.transaction(tenant) as s:
                    s.get(BackendInstance,instance).verified=True
                    job=s.get(Job,received['job_id']);job.state='running';job.lease_token='old-worker';job.lease_until=time.time()+60
                backup_tenant(db,domain.objects,tenant,root/'bundle')
                restore_tenant(target,tenant,root/'bundle',root/'restored')
                restored=Domain(target,root/'restored')
                self.assertFalse(restored.can_read(tenant,'reader',received['document_id']))
                self.assertEqual(restored.read_original(tenant,'admin',received['document_id']),b'original')
                with target.transaction(tenant) as s:
                    self.assertEqual(s.query(Tenant).count(),1)
                    job=s.get(Job,received['job_id'])
                    self.assertEqual(job.state,'needs_reconciliation');self.assertEqual(job.lease_token,'')
                    self.assertFalse(s.get(BackendInstance,instance).verified)
        finally:
            if target:target.engine.dispose()
            # Remove only this invocation's freshly generated test schema.
            assert schema.startswith('recovery_') and len(schema)==41
            with db.engine.begin() as connection:connection.execute(text('DROP SCHEMA IF EXISTS "'+schema+'" CASCADE'))
            db.engine.dispose()

    # 屏障让两个线程同时提交同一回执；最终只能成功接受一次，用真实数据库锁检验原子性。
    async def test_concurrent_reconciliation_accepts_receipt_once(self):
        from app.v3.models import Job,Replica,BackendInstance,Space,Audit
        db=Database(os.environ['V3_TEST_DATABASE_URL']);db.migrate()
        tenant='reconcile-test-'+uuid.uuid4().hex
        try:
            with tempfile.TemporaryDirectory() as temp:
                domain=Domain(db,Path(temp));domain.bootstrap(tenant,'admin')
                space=domain.create_space(tenant,'admin','test')
                instance=domain.register_backend(tenant,'admin','weknora','https://kb.test','V3_SECRET_TEST')
                received=domain.receive(tenant,'admin',space,'policy.txt',b'policy')
                with db.transaction(tenant) as s:
                    job=s.get(Job,received['job_id']);job.state='needs_reconciliation';job.lease_token='expired'
                    s.add(Replica(id=uuid.uuid4().hex,tenant_id=tenant,version_id=received['version_id'],
                        instance_id=instance,state='submitting',collection_id='test-kb'))
                report=domain.reconciliation_snapshot(tenant,'admin',received['job_id'])
                report.update(remote_id='remote',batch_id='',approved_by='operator',note='verified receipt',evidence_sha256='a'*64)
                start=threading.Barrier(2)
                def accept():
                    start.wait(3)
                    try:domain.reconcile_job(tenant,'admin',received['job_id'],report);return 'accepted'
                    except DomainError:return 'rejected'
                results=await asyncio.gather(asyncio.to_thread(accept),asyncio.to_thread(accept))
                self.assertCountEqual(results,['accepted','rejected'])
                with db.transaction(tenant) as s:
                    self.assertEqual(s.query(Audit).filter_by(tenant_id=tenant,action='job.reconcile').count(),1)
        finally:db.engine.dispose()

    async def test_cancel_waits_for_claim_lock_and_rejects_claimed_job(self):
        from app.v3.models import Job,Version
        from sqlalchemy import select
        db=Database(os.environ['V3_TEST_DATABASE_URL']);db.migrate()
        tenant='cancel-test-'+uuid.uuid4().hex
        held=threading.Event();release=threading.Event()
        with tempfile.TemporaryDirectory() as temp:
            domain=Domain(db,Path(temp));domain.bootstrap(tenant,'admin')
            space=domain.create_space(tenant,'admin','test')
            draft=domain.receive(tenant,'admin',space,'broken.txt',b'broken')
            with db.transaction(tenant) as s:
                s.get(Job,draft['job_id']).state='failed';s.get(Version,draft['version_id']).state='failed'
            def claiming():
                with db.transaction() as s:
                    job=s.scalar(select(Job).where(Job.id==draft['job_id']).with_for_update())
                    job.state='running';held.set();release.wait(3)
            thread=threading.Thread(target=claiming);thread.start()
            try:
                self.assertTrue(await asyncio.to_thread(held.wait,2))
                cancel=asyncio.create_task(asyncio.to_thread(domain.cancel_version,tenant,'admin',draft['document_id'],draft['version_id']))
                await asyncio.sleep(.1);self.assertFalse(cancel.done())
                release.set()
                with self.assertRaises(DomainError):await cancel
                with db.transaction(tenant) as s:
                    self.assertEqual(s.get(Version,draft['version_id']).state,'failed')
                    s.get(Job,draft['job_id']).state='failed'
            finally:
                release.set();await asyncio.to_thread(thread.join)
        db.engine.dispose()

    # 独立线程持锁，同时运行异步计数任务；等待锁期间计数仍推进，才说明同步数据库等待未阻塞事件循环。
    async def test_waiting_advisory_lock_does_not_block_event_loop(self):
        db=Database(os.environ['V3_TEST_DATABASE_URL'])
        entered=threading.Event()
        def hold():
            with db.transaction('lock-test',shared=True):
                entered.set(); time.sleep(.5)
        thread=threading.Thread(target=hold);thread.start()
        await asyncio.to_thread(entered.wait,2)
        ticks=[]
        async def tick():
            for _ in range(4): await asyncio.sleep(.04);ticks.append(1)
        ticker=asyncio.create_task(tick())
        async with db.atransaction('lock-test'):
            self.assertEqual(len(ticks),4)
        await ticker;await asyncio.to_thread(thread.join);db.engine.dispose()

    async def test_withdrawal_can_commit_while_model_body_is_pending_and_stops_output(self):
        db=Database(os.environ['V3_TEST_DATABASE_URL']);db.migrate()
        other=Database(os.environ['V3_TEST_DATABASE_URL']);tenant='v3-test-'+uuid.uuid4().hex
        with tempfile.TemporaryDirectory() as temp:
            domain=Domain(db,Path(temp));domain.bootstrap(tenant,'admin')
            writer=Domain(other,Path(temp));space=domain.create_space(tenant,'admin','test')
            received=domain.receive(tenant,'admin',space,'policy.txt',b'test evidence')
            evidence=Evidence(tenant,received['document_id'],received['version_id'],'g','i','c','chunk','evidence',1,'segment',{})
            conversation=domain.create_conversation(tenant,'admin');pending=asyncio.Event();release=asyncio.Event()
            class Body(httpx.AsyncByteStream):
                async def __aiter__(self):
                    pending.set();await release.wait()
                    yield b'data: {"choices":[{"delta":{"content":"secret [1]"}}]}\n\ndata: [DONE]\n\n'
            def handler(request):
                if request.url.path.endswith('/rerank'):
                    return httpx.Response(200,json={'results':[{'index':0,'relevance_score':1}]})
                return httpx.Response(200,stream=Body())
            settings=Settings(model_base_url='https://model.test/v1',chat_model='test',rerank_model='test')
            with patch.dict(os.environ,{'V3_SECRET_MODEL':'fixture'}):
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    chat=ChatService(domain,None,ModelService(settings,client))
                    async def retrieve(*args,**kwargs):return await asyncio.to_thread(domain.scope,tenant,'admin',space),[evidence]
                    chat.retrieve=retrieve;events=[]
                    async def consume():
                        async for event in chat.answer(tenant,'admin',conversation,space,'question'):events.append(event)
                    task=asyncio.create_task(consume())
                    try:
                        await asyncio.wait_for(pending.wait(),3)
                        await asyncio.wait_for(asyncio.to_thread(writer.withdraw,tenant,'admin',received['document_id']),2)
                        release.set()
                        with self.assertRaises(DomainError):await task
                        self.assertFalse(any(e['event']=='token' for e in events))
                    finally:
                        release.set()
                        if not task.done():task.cancel()
                        await asyncio.gather(task,return_exceptions=True)
        db.engine.dispose();other.engine.dispose()
