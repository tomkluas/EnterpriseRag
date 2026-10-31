import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from app.v3.config import Settings
from app.v3.api import create_app
from app.v3.db import Database
from app.v3.domain import Domain


class ApiTests(unittest.TestCase):
    def test_maintenance_job_cannot_enter_general_worker_retry_queue(self):
        from app.v3.models import Job
        with self.db.transaction('enterprise') as s:
            s.add(Job(id='gc',tenant_id='enterprise',owner='admin',kind='orphan_gc',state='failed',
                idempotency_key='gc',payload={}))
        response=self.client.post('/api/v3/jobs/gc/retry',headers=self.headers)
        self.assertEqual(response.status_code,409)

    def test_failed_version_cancel_api_preserves_original_and_cannot_retry(self):
        from app.v3.models import Job,Version
        space=self.domain.create_space('enterprise','admin','test')
        draft=self.domain.receive('enterprise','admin',space,'broken.docx',b'broken')
        other=self.domain.receive('enterprise','admin',space,'other.txt',b'other')
        with self.db.transaction('enterprise') as s:
            s.get(Job,draft['job_id']).state='failed';s.get(Version,draft['version_id']).state='failed'
        route=f"/api/v3/documents/{draft['document_id']}/versions/{draft['version_id']}/cancel"
        self.assertEqual(self.client.post(route).status_code,401)
        wrong=f"/api/v3/documents/{other['document_id']}/versions/{draft['version_id']}/cancel"
        self.assertEqual(self.client.post(wrong,headers=self.headers).status_code,403)
        for _ in range(2):self.assertEqual(self.client.post(route,headers=self.headers).status_code,200)
        self.assertEqual(self.client.post(f"/api/v3/jobs/{draft['job_id']}/retry",headers=self.headers).status_code,409)
        self.assertEqual(self.domain.read_original('enterprise','admin',draft['document_id'],draft['version_id']),b'broken')

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.db=Database('sqlite:///'+str(self.root/'test.db'),testing=True); self.db.migrate()
        self.domain=Domain(self.db,self.root/'objects'); self.domain.bootstrap('enterprise','admin')
        settings=Settings(tenant_id='enterprise',mode='development',bootstrap_token='x'*40,objects_dir=self.root/'objects')
        self.client=TestClient(create_app(settings,self.domain))
        self.headers={'Authorization':'Bearer '+'x'*40}

    def tearDown(self):
        self.client.close(); self.db.engine.dispose(); self.temp.cleanup()

    def test_legacy_routes_are_gone_and_v3_requires_authentication(self):
        self.assertEqual(self.client.get('/api/v1/knowledge/list').status_code,404)
        self.assertEqual(self.client.get('/api/v3/spaces').status_code,401)
        self.assertEqual(self.client.get('/api/v3/spaces',headers=self.headers).status_code,200)

    def test_upload_original_download_and_withdrawal(self):
        response=self.client.post('/api/v3/spaces',headers=self.headers,json={'name':'制度'})
        self.assertEqual(response.status_code,201)
        space=response.json()['id']
        uploaded=self.client.post('/api/v3/documents',headers=self.headers,
            data={'space_id':space},files={'file':('policy.txt',b'private policy','text/plain')})
        self.assertEqual(uploaded.status_code,202)
        doc=uploaded.json()['document_id']
        original=self.client.get(f'/api/v3/documents/{doc}/source',headers=self.headers)
        self.assertEqual(original.content,b'private policy')
        self.assertEqual(original.headers.get('cache-control'),'private, no-store')
        self.assertEqual(self.client.delete(f'/api/v3/documents/{doc}',headers=self.headers).status_code,202)
        self.assertNotEqual(self.client.get(f'/api/v3/documents/{doc}/source',headers=self.headers).status_code,200)

    def test_api_cannot_mark_backend_verified(self):
        response=self.client.post('/api/v3/backends',headers=self.headers,json={
            'provider':'dify','base_url':'https://kb.test','key_ref':'V3_SECRET_TEST','verified':True})
        self.assertEqual(response.status_code,422)

    def test_no_evidence_answer_finishes_without_generation(self):
        space=self.client.post('/api/v3/spaces',headers=self.headers,json={'name':'制度'}).json()['id']
        conversation=self.client.post('/api/v3/conversations',headers=self.headers).json()['id']
        response=self.client.post(f'/api/v3/conversations/{conversation}/messages/stream',headers=self.headers,json={'space_id':space,'query':'不存在的制度'})
        self.assertEqual(response.status_code,200)
        self.assertIn('根据现有资料无法回答',response.text)
        self.assertIn('"event": "done"',response.text)
        history=self.client.get(f'/api/v3/conversations/{conversation}/messages',headers=self.headers).json()
        self.assertEqual(history[0]['state'],'completed')
