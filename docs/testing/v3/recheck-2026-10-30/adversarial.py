"""Independent acceptance repros. Expected behavior assertions expose current defects.
Only unique temporary SQLite databases; no credentials, runtime writes or external calls.
Run with PYTHONPATH=backend.
"""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from app.v3.db import Database
from app.v3.domain import Domain, DomainError, uid
from app.v3.models import BackendInstance, Replica, Job, Version
from app.v3.jobs.worker import Worker
from app.v3.knowledge.contracts import Submission, BackendStatus


class AcceptanceRepros(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='v3-independent-')
        self.db=Database('sqlite:///'+str(Path(self.tmp.name)/'test.db'),testing=True)
        self.db.migrate();self.d=Domain(self.db,Path(self.tmp.name)/'objects')
        self.d.bootstrap('t','admin');self.space=self.d.create_space('t','admin','test')
        self.instances=[]
        for provider in ['weknora','dify']:
            i=self.d.register_backend('t','admin',provider,'https://test.invalid','V3_SECRET_TEST')
            with self.db.transaction('t') as s:
                b=s.get(BackendInstance,i);b.verified=True;b.capabilities=['segment']
            self.instances.append(i)
        sw=self.d.prepare_switch('t','admin',self.space,self.instances[0])
        self.d.activate_switch('t','admin',sw,0)

    async def asyncTearDown(self):
        self.db.engine.dispose();self.tmp.cleanup()

    async def test_failed_update_must_not_permanently_block_switch_of_valid_current_version(self):
        old=self.d.receive('t','admin',self.space,'policy.txt',b'valid current policy')
        with self.db.transaction('t') as s:
            for i in self.instances:
                s.add(Replica(id=uid(),tenant_id='t',version_id=old['version_id'],instance_id=i,
                    collection_id='kb',remote_id=uid(),state='ready',chunks=1))
            s.get(Job,old['job_id']).state='completed'
        self.d.publish('t','admin',old['version_id'])
        draft=self.d.receive('t','admin',self.space,'broken.docx',b'not a valid docx',document_id=old['document_id'])
        class ParseFailure:
            async def prepare_collection(self,name):return {'id':'kb'}
            async def submit(self,*args):return Submission(uid(),'batch')
            async def status(self,binding):return BackendStatus('failed',0,'failed')
        worker=Worker(self.d,lambda *args:ParseFailure())
        async def exhaust():
            for _ in range(5):
                with self.db.transaction('t') as s:
                    for j in s.query(Job).filter_by(state='queued'):j.next_run=0
                await worker.run_once()
        await exhaust()
        sw=self.d.prepare_switch('t','admin',self.space,self.instances[1])
        await exhaust()
        status=self.d.switch_status('t','admin',sw)
        with self.db.transaction('t') as s:
            draft_state=s.get(Version,draft['version_id']).state
        self.assertEqual(len(self.d.scope('t','admin',self.space).bindings),1)
        print('FAILED_DRAFT',{'target_ready':status['ready'],'target_total':status['total'],'draft_state':draft_state},flush=True)
        # Keep the original failure assertion, then exercise the new recovery path.
        with self.assertRaises(DomainError):self.d.activate_switch('t','admin',sw,1)
        self.d.cancel_version('t','admin',draft['document_id'],draft['version_id'])
        with self.assertRaises(DomainError):self.d.activate_switch('t','admin',sw,1)
        fresh=self.d.prepare_switch('t','admin',self.space,self.instances[1])
        self.d.activate_switch('t','admin',fresh,1)
        self.assertEqual(self.d.scope('t','admin',self.space).bindings[0].version_id,old['version_id'])

    async def test_expired_lease_must_not_escape_and_terminate_worker_loop(self):
        self.d.receive('t','admin',self.space,'policy.txt',b'policy')
        original_sleep=asyncio.sleep
        class ExpireDuringUpload:
            async def prepare_collection(self,name):return {'id':'kb'}
            async def submit(inner,*args):
                with self.db.transaction() as s:
                    for j in s.query(Job).filter_by(state='running'):j.lease_until=time.time()-1
                await original_sleep(0.05)
                return Submission('remote','batch')
        async def accelerated_heartbeat(seconds):await original_sleep(0.01 if seconds==15 else seconds)
        worker=Worker(self.d,lambda *args:ExpireDuringUpload())
        with patch('app.v3.jobs.worker.asyncio.sleep',side_effect=accelerated_heartbeat):
            # The real heartbeat executes against an expired real DB lease.
            # run_once must contain this failure so CLI's loop can continue.
            await worker.run_once()


if __name__=='__main__':unittest.main(verbosity=2)
