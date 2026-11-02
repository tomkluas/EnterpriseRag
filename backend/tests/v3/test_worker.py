# 验证 worker 的持久任务状态机、租约及撤回竞态；临时 SQLite 保存真实状态，FakeKnowledgeService 替代远端平台。
import tempfile
import unittest
import asyncio
import time
from unittest.mock import patch
from pathlib import Path
from app.v3.db import Database
from app.v3.domain import Domain
from app.v3.models import BackendInstance, Space, Replica, Job, Version
from app.v3.knowledge.contracts import BackendStatus, Submission
from app.v3.jobs.worker import Worker


class FakeKnowledgeService:
    """External service boundary; the real database, leases and worker execute."""
    calls=[]
    async def prepare_collection(self,name):
        self.calls.append('prepare'); return {'id':'remote-kb'}
    async def submit(self,*args):
        self.calls.append('submit'); return Submission('remote-doc','batch')
    async def status(self,binding):
        return BackendStatus('completed',3,'completed')
    async def deletion_confirmed(self,binding):return False


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_processing_poll_saves_real_count_and_check_time_for_task_details(self):
        received=self.domain.receive('t','admin',self.space,'progress.txt',b'policy')
        class Service(FakeKnowledgeService):
            async def status(inner,binding):return BackendStatus('processing',2,'indexing')
        self.worker.factory=lambda *args:Service()
        await self.worker.run_once()
        job=self.domain.list_jobs('t','admin')[0]
        self.assertEqual(job['id'],received['job_id'])
        self.assertEqual(job['state'],'queued')
        result=job['index_results'][0]
        self.assertEqual(result['chunks'],2)
        self.assertEqual(result['raw_state'],'indexing')
        self.assertGreater(result['observed_at'],0)
        self.assertFalse(result['can_preview'])

    # 只缩短心跳等待并令租约过期，保留实际续租逻辑；迟到回执不能被接受，后续任务仍应继续执行。
    async def test_expired_heartbeat_does_not_stop_next_job_or_accept_late_receipt(self):
        first=self.domain.receive('t','admin',self.space,'first.txt',b'first')
        second=self.domain.receive('t','admin',self.space,'second.txt',b'second')
        sleep=asyncio.sleep
        class Service(FakeKnowledgeService):
            async def submit(inner,*args):
                if args[1]=='first.txt':
                    with self.db.transaction() as s:s.get(Job,first['job_id']).lease_until=time.time()-1
                    await sleep(.05)
                    return Submission('late','batch')
                return Submission('next','batch')
        async def fast(seconds):await sleep(.01 if seconds==15 else seconds)
        self.worker.factory=lambda *args:Service()
        with patch('app.v3.jobs.worker.asyncio.sleep',side_effect=fast):await self.worker.run_once()
        with self.db.transaction() as s:
            self.assertIsNone(s.query(Replica).filter_by(version_id=first['version_id']).one().remote_id)
            s.get(Job,first['job_id']).state='needs_reconciliation'
        await self.worker.run_once()
        with self.db.transaction() as s:
            self.assertEqual(s.get(Job,second['job_id']).state,'completed')

    async def test_terminal_parse_failure_sets_failed_version(self):
        received=self.domain.receive('t','admin',self.space,'broken.docx',b'broken')
        class Service(FakeKnowledgeService):
            async def status(inner,binding):return BackendStatus('failed',0,'failed')
        self.worker.factory=lambda *args:Service()
        for _ in range(5):
            with self.db.transaction() as s:s.get(Job,received['job_id']).next_run=0
            await self.worker.run_once()
        with self.db.transaction() as s:
            self.assertEqual(s.get(Job,received['job_id']).state,'failed')
            self.assertEqual(s.get(Version,received['version_id']).state,'failed')

    # 使用真实领域任务和临时持久库，只替换后端工厂；断言因此能够检查任务、版本与副本的联动状态。
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.db=Database('sqlite:///'+str(Path(self.temp.name)/'test.db'),testing=True)
        self.db.migrate(); self.domain=Domain(self.db,Path(self.temp.name)/'objects'); self.domain.bootstrap('t','admin')
        self.space=self.domain.create_space('t','admin','Policies')
        self.instance=self.domain.register_backend('t','admin','weknora','https://kb.test','V3_SECRET_TEST')
        with self.db.transaction('t') as s:
            backend=s.get(BackendInstance,self.instance); backend.verified=True; backend.capabilities=['segment']
            s.get(Space,self.space).active_backend=self.instance
        FakeKnowledgeService.calls=[]
        self.worker=Worker(self.domain,lambda instance,purpose:FakeKnowledgeService())

    async def asyncTearDown(self):
        self.db.engine.dispose(); self.temp.cleanup()

    async def test_real_job_pipeline_builds_replica_without_publishing(self):
        received=self.domain.receive('t','admin',self.space,'policy.txt',b'policy')
        for _ in range(3): await self.worker.run_once()
        docs=self.domain.list_documents('t','admin',self.space)
        self.assertEqual(docs[0]['current_version'],'')
        with self.db.transaction('t') as s:
            replica=s.query(Replica).filter_by(version_id=received['version_id']).one()
            self.assertEqual(replica.state,'ready')
        self.domain.publish('t','admin',received['version_id'])
        self.assertEqual(len(self.domain.scope('t','admin',self.space).bindings),1)

    async def test_unknown_remote_submission_does_not_upload_again(self):
        received=self.domain.receive('t','admin',self.space,'policy.txt',b'policy')
        with self.db.transaction('t') as s:
            s.add(Replica(id='r',tenant_id='t',version_id=received['version_id'],instance_id=self.instance,
                state='submitting',collection_id='known-kb'))
        await self.worker.run_once()
        self.assertNotIn('submit',FakeKnowledgeService.calls)
        jobs=self.domain.list_jobs('t','admin')
        self.assertEqual(jobs[0]['state'],'needs_reconciliation')

    async def test_withdrawal_during_submit_preserves_receipt_and_schedules_cleanup(self):
        received=self.domain.receive('t','admin',self.space,'policy.txt',b'policy')
        domain=self.domain;worker=self.worker
        class Service(FakeKnowledgeService):
            async def submit(self,*args):
                domain.withdraw('t','admin',received['document_id'])
                await worker.run_once()  # Original delete job observes no remote ID yet.
                return Submission('late-remote','batch')
            async def delete(self,binding):
                self.calls.append('delete:'+binding.remote_id)
        self.worker.factory=lambda instance,purpose:Service()
        await self.worker.run_once()
        await self.worker.run_once()
        with self.db.transaction('t') as s:
            replica=s.query(Replica).filter_by(version_id=received['version_id']).one()
            self.assertEqual(replica.remote_id,'late-remote')
        self.assertIn('delete:late-remote',FakeKnowledgeService.calls)
