"""Independent post-fix checks. Unique temporary databases; no runtime writes."""
import asyncio,time,unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.v3.api import create_app
from app.v3.config import Settings
from app.v3.models import Job,Replica,Version
from app.v3.domain import uid
from app.v3.knowledge.contracts import Submission
from test_worker import WorkerTests,FakeKnowledgeService


class Checks(WorkerTests):
    async def test_natural_reclaim_reaches_next_job_without_manually_changing_failed_job(self):
        first=self.domain.receive('t','admin',self.space,'first.txt',b'first')
        next_job=self.domain.receive('t','admin',self.space,'next.txt',b'next')
        sleep=asyncio.sleep;submissions=[]
        class Service(FakeKnowledgeService):
            async def submit(inner,*args):
                submissions.append(args[1])
                if args[1]=='first.txt':
                    with self.db.transaction() as s:s.get(Job,first['job_id']).lease_until=time.time()-1
                    await sleep(.05)
                return Submission(uid(),'batch')
        async def fast(seconds):await sleep(.01 if seconds==15 else seconds)
        self.worker.factory=lambda *args:Service()
        with patch('app.v3.jobs.worker.asyncio.sleep',side_effect=fast):
            for _ in range(3):await self.worker.run_once()
        with self.db.transaction() as s:
            self.assertEqual(s.get(Job,first['job_id']).state,'needs_reconciliation')
            self.assertEqual(s.get(Job,next_job['job_id']).state,'completed')
        self.assertEqual(submissions,['first.txt','next.txt'])

    async def test_api_list_cancel_publish_and_switch_form_complete_recovery_chain(self):
        from app.v3.models import BackendInstance
        target=self.domain.register_backend('t','admin','dify','https://test.invalid','V3_SECRET_TEST')
        with self.db.transaction('t') as s:
            b=s.get(BackendInstance,target);b.verified=True;b.capabilities=['segment']
        current=self.domain.receive('t','admin',self.space,'policy.txt',b'current valid policy')
        with self.db.transaction('t') as s:
            s.get(Job,current['job_id']).state='completed'
            for instance in [self.instance,target]:
                s.add(Replica(id=uid(),tenant_id='t',version_id=current['version_id'],instance_id=instance,
                    collection_id='kb',remote_id=uid(),state='ready',chunks=1))
        self.domain.publish('t','admin',current['version_id'])
        draft=self.domain.receive('t','admin',self.space,'broken.docx',b'broken',document_id=current['document_id'])
        with self.db.transaction('t') as s:
            s.get(Job,draft['job_id']).state='failed';s.get(Version,draft['version_id']).state='failed'
        settings=Settings(tenant_id='t',mode='development',bootstrap_user='admin',bootstrap_token='x'*40)
        with TestClient(create_app(settings,self.domain)) as client:
            headers={'Authorization':'Bearer '+'x'*40};base=f'/api/v3/spaces/{self.space}'
            docs=client.get(base+'/documents',headers=headers).json()
            item=next(v for v in docs[0]['versions'] if v['id']==draft['version_id'])
            self.assertTrue(item['can_cancel'])
            def prepare():return client.post(base+'/backend-switches',headers=headers,json={'target_instance_id':target}).json()['id']
            stale=prepare()
            revision=client.get(base+f'/backend-switches/{stale}',headers=headers).json()['route_revision']
            route=f"/api/v3/documents/{draft['document_id']}/versions/{draft['version_id']}"
            self.assertEqual(client.post(route+'/cancel',headers=headers).status_code,200)
            self.assertEqual(client.post(route+'/publish',headers=headers).status_code,409)
            self.assertEqual(client.post(base+f'/backend-switches/{stale}/activate',headers=headers,json={'expected_route_revision':revision}).status_code,409)
            fresh=prepare()
            status=client.get(base+f'/backend-switches/{fresh}',headers=headers).json()
            self.assertEqual((status['ready'],status['total']),(1,1))
            self.assertEqual(client.post(base+f'/backend-switches/{fresh}/activate',headers=headers,json={'expected_route_revision':revision}).status_code,200)
            self.assertEqual(client.get(f"/api/v3/documents/{current['document_id']}/source",headers=headers).content,b'current valid policy')
        scope=self.domain.scope('t','admin',self.space)
        self.assertEqual(scope.bindings[0].instance_id,target)
        self.assertEqual(scope.bindings[0].version_id,current['version_id'])


if __name__=='__main__':
    names=['test_natural_reclaim_reaches_next_job_without_manually_changing_failed_job',
           'test_api_list_cancel_publish_and_switch_form_complete_recovery_chain']
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(Checks(n) for n in names))
    raise SystemExit(0 if result.wasSuccessful() else 1)
