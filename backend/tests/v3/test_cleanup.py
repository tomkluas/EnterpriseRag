# 验证删除回执与远端消失确认分离，以及清理任务重试；适配器响应和 worker 后端均为模拟。
import unittest
import httpx
import test_worker as fixtures
from app.v3.models import Replica,Job
from app.v3.knowledge.contracts import BackendError,Binding,DeleteReceipt
from app.v3.knowledge.weknora import WeKnoraBackend
from app.v3.knowledge.dify import DifyBackend


class CleanupAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_404_confirms_remote_absence(self):
        binding=Binding('t','d','v','g','i','kb','remote','r')
        for cls in (WeKnoraBackend,DifyBackend):
            for status in (200,401,403,404,500):
                with self.subTest(provider=cls.__name__,status=status):
                    payload={'id':'remote','knowledge_base_id':'kb'}
                    body={'data':payload} if cls is WeKnoraBackend else payload
                    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(status,json=body))) as client:
                        backend=cls('https://kb.test','secret',client)
                        if status in (200,404):self.assertEqual(await backend.deletion_confirmed(binding),status==404)
                        else:
                            with self.assertRaises(BackendError):await backend.deletion_confirmed(binding)


class CleanupWorkerTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.WorkerTests.asyncSetUp
    asyncTearDown=fixtures.WorkerTests.asyncTearDown

    # 先获得真实领域状态中的副本再撤回；把业务不可见与后端物理清理拆开，便于检验清理任务是否正确推进。
    async def withdrawn(self):
        received=self.domain.receive('t','admin',self.space,'policy.txt',b'policy')
        await self.worker.run_once()
        self.domain.publish('t','admin',received['version_id'])
        self.domain.withdraw('t','admin',received['document_id'])
        return received

    async def test_delete_acceptance_is_polled_until_remote_absent(self):
        received=await self.withdrawn()
        events=[];confirmed=False
        class Service(fixtures.FakeKnowledgeService):
            async def delete(inner,binding):events.append('delete');return DeleteReceipt(True)
            async def deletion_confirmed(inner,binding):events.append('confirm');return confirmed
        self.worker.factory=lambda *args:Service()
        await self.worker.run_once()
        with self.db.transaction('t') as s:
            replica=s.query(Replica).filter_by(version_id=received['version_id']).one()
            self.assertEqual(replica.state,'delete_requested')
            job=s.query(Job).filter_by(kind='delete').one()
            self.assertEqual(job.state,'queued');job.next_run=0
        confirmed=True
        await self.worker.run_once()
        with self.db.transaction('t') as s:
            replica=s.query(Replica).filter_by(version_id=received['version_id']).one()
            self.assertEqual(replica.state,'deleted')
            job=s.query(Job).filter_by(kind='delete').one()
            self.assertEqual(job.state,'completed')
            self.assertEqual(job.payload['deletion_confirmations'][replica.id]['basis'],'remote_api_not_found')
        self.assertEqual(events,['delete','confirm','confirm'])
        self.assertEqual(self.domain.scope('t','admin',self.space).bindings,())

    async def test_delete_timeout_is_retryable_not_unknown_upload(self):
        await self.withdrawn()
        class Service(fixtures.FakeKnowledgeService):
            async def delete(inner,binding):raise BackendError('timeout_unknown')
        self.worker.factory=lambda *args:Service()
        await self.worker.run_once()
        with self.db.transaction('t') as s:
            job=s.query(Job).filter_by(kind='delete').one()
            self.assertEqual(job.state,'queued')
            self.assertNotEqual(job.state,'needs_reconciliation')

    async def test_status_timeout_with_known_receipt_can_retry_without_upload(self):
        received=self.domain.receive('t','admin',self.space,'policy.txt',b'policy')
        class Service(fixtures.FakeKnowledgeService):
            async def status(inner,binding):raise BackendError('timeout_unknown')
        self.worker.factory=lambda *args:Service()
        await self.worker.run_once()
        with self.db.transaction('t') as s:
            job=s.get(Job,received['job_id']);self.assertEqual(job.state,'queued');job.next_run=0
        self.worker.factory=lambda *args:fixtures.FakeKnowledgeService()
        await self.worker.run_once()
        self.assertEqual(fixtures.FakeKnowledgeService.calls.count('submit'),1)
        with self.db.transaction('t') as s:self.assertEqual(s.get(Job,received['job_id']).state,'completed')
