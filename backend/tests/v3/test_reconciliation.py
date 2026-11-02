# 验证人工对账接受回执的原子性和上下文约束；复用临时 worker 夹具，恢复不会重新上传或自动发布。
import hashlib
import unittest
from app.v3.domain import DomainError
from app.v3.models import Replica, Job, Audit
import test_worker as worker_tests
from test_worker import FakeKnowledgeService


class ReconciliationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=worker_tests.WorkerTests.asyncSetUp
    asyncTearDown=worker_tests.WorkerTests.asyncTearDown
    # 先通过 worker 生成未知结果状态及原始绑定，确保人工回执接受流程检验真实任务上下文。
    async def prepare_unknown(self):
        received=self.domain.receive('t','admin',self.space,'policy.txt',b'policy')
        with self.db.transaction('t') as s:
            s.add(Replica(id='unknown',tenant_id='t',version_id=received['version_id'],instance_id=self.instance,
                state='submitting',collection_id='remote-kb'))
        await self.worker.run_once()
        report=self.domain.reconciliation_snapshot('t','admin',received['job_id'])
        report.update(remote_id='recovered',batch_id='batch',approved_by='operator',
            evidence_sha256=hashlib.sha256(b'checked remote receipt').hexdigest(),note='Verified original and remote binding')
        return received,report

    async def test_receipt_resumes_without_upload_or_publication(self):
        received,report=await self.prepare_unknown()
        self.domain.reconcile_job('t','admin',received['job_id'],report)
        for _ in range(3):await self.worker.run_once()
        self.assertNotIn('submit',FakeKnowledgeService.calls)
        with self.db.transaction('t') as s:
            self.assertEqual(s.get(Replica,'unknown').state,'ready')
            job=s.get(Job,received['job_id'])
            self.assertEqual(job.state,'completed')
            self.assertEqual(job.payload['reconciliation']['evidence_sha256'],report['evidence_sha256'])
            self.assertEqual(s.query(Audit).filter_by(action='job.reconcile').count(),1)
        self.assertEqual(self.domain.scope('t','admin',self.space).bindings,())

    async def test_wrong_context_and_non_admin_are_rejected_atomically(self):
        received,report=await self.prepare_unknown()
        self.domain.add_user('t','admin','reader')
        with self.assertRaises(DomainError):self.domain.reconcile_job('t','reader',received['job_id'],report)
        for key in ('sha256','lease_token','replica_id','instance_id','collection_id','version_id','job_id'):
            with self.subTest(key=key),self.assertRaises(DomainError):
                self.domain.reconcile_job('t','admin',received['job_id'],dict(report,**{key:'wrong'}))
        with self.db.transaction('t') as s:
            self.assertEqual(s.get(Job,received['job_id']).state,'needs_reconciliation')
            self.assertIsNone(s.get(Replica,'unknown').remote_id)

    async def test_duplicate_remote_and_repeat_report_are_rejected(self):
        received,report=await self.prepare_unknown()
        other=self.domain.receive('t','admin',self.space,'other.txt',b'other')
        with self.db.transaction('t') as s:
            s.add(Replica(id='other',tenant_id='t',version_id=other['version_id'],instance_id=self.instance,
                collection_id='remote-kb',remote_id='occupied'))
        with self.assertRaises(DomainError):
            self.domain.reconcile_job('t','admin',received['job_id'],dict(report,remote_id='occupied'))
        self.domain.reconcile_job('t','admin',received['job_id'],report)
        with self.assertRaises(DomainError):self.domain.reconcile_job('t','admin',received['job_id'],report)

    async def test_withdrawn_receipt_only_schedules_cleanup(self):
        received,report=await self.prepare_unknown()
        self.domain.withdraw('t','admin',received['document_id'])
        self.domain.reconcile_job('t','admin',received['job_id'],report)
        with self.db.transaction('t') as s:
            self.assertEqual(s.get(Replica,'unknown').state,'delete_pending')
            self.assertEqual(s.get(Job,received['job_id']).state,'completed')
            self.assertTrue(s.query(Job).filter_by(idempotency_key='delete-after-reconcile:unknown').first())
        self.assertEqual(self.domain.scope('t','admin',self.space).bindings,())

    async def test_missing_evidence_and_corrupt_original_cannot_change_state(self):
        received,report=await self.prepare_unknown()
        for patch in ({'approved_by':''},{'note':''},{'evidence_sha256':'bad'},{'remote_id':''}):
            with self.subTest(patch=patch),self.assertRaises(DomainError):
                self.domain.reconcile_job('t','admin',received['job_id'],dict(report,**patch))
        from app.v3.models import Version
        with self.db.transaction('t') as s:
            version=s.get(Version,received['version_id'])
            (self.domain.objects.root/version.object_key).write_bytes(b'tampered')
        with self.assertRaises(DomainError):self.domain.reconcile_job('t','admin',received['job_id'],report)
        with self.db.transaction('t') as s:
            self.assertEqual(s.get(Job,received['job_id']).state,'needs_reconciliation')
            self.assertIsNone(s.get(Replica,'unknown').remote_id)

    async def test_cli_inspection_and_signoff_use_real_domain(self):
        import contextlib
        import io
        import json
        import sys
        from pathlib import Path
        from unittest.mock import patch
        from app.v3.cli import main
        from app.v3.config import Settings
        received,report=await self.prepare_unknown()
        settings=Settings(tenant_id='t',bootstrap_user='admin',objects_dir=self.domain.objects.root)
        def run(*args):
            output=io.StringIO()
            with patch.object(sys,'argv',['cli','--env-file','unused',*args]), \
                 patch('app.v3.cli.load_dotenv'),patch('app.v3.cli.Settings.from_env',return_value=settings), \
                 patch('app.v3.cli.Database',return_value=self.db),contextlib.redirect_stdout(output):
                main()
            return output.getvalue()
        snapshot=json.loads(run('inspect-reconciliation','--job',received['job_id']))
        self.assertEqual(snapshot['replica_id'],'unknown')
        path=Path(self.temp.name)/'receipt.json';path.write_text(json.dumps(report),encoding='utf8')
        self.assertIn('已记录',run('reconcile-job','--job',received['job_id'],'--report',str(path)))
        with self.db.transaction('t') as s:self.assertEqual(s.get(Job,received['job_id']).state,'queued')

    async def test_route_change_does_not_move_recovered_receipt_to_other_backend(self):
        from app.v3.models import Space
        received,report=await self.prepare_unknown()
        other=self.domain.register_backend('t','admin','dify','https://other.test','V3_SECRET_OTHER')
        with self.db.transaction('t') as s:s.get(Space,self.space).active_backend=other
        self.domain.reconcile_job('t','admin',received['job_id'],report)
        used=[]
        def factory(instance,purpose):
            used.append(instance.id)
            return FakeKnowledgeService()
        self.worker.factory=factory
        await self.worker.run_once()
        self.assertTrue(used)
        self.assertEqual(set(used),{self.instance})
        self.assertNotIn('submit',FakeKnowledgeService.calls)
