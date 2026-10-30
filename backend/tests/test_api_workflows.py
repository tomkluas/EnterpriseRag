import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.core.deps import get_db
from app.db.models import Base
from app.db import crud
from app.core.security import hash_password


class APIWorkflows(unittest.TestCase):
    """Exercise real API/ORM/Chroma/pipeline with isolated data; replace cloud I/O only."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        config = {'initialized': True, 'secret_key': '01'*32, 'encryption_key': '02'*32}
        (self.root / 'config.json').write_text(json.dumps(config), encoding='utf-8')
        self.engine = create_engine(f'sqlite:///{self.root / "test.db"}', connect_args={'check_same_thread': False})
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        def db_override():
            with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = db_override
        self.addCleanup(app.dependency_overrides.clear)
        patches = [
            patch('app.core.config.get_config_path', return_value=self.root / 'config.json'),
            patch('app.main.create_tables', return_value=None),
            patch('app.api.knowledge.get_data_dir', return_value=self.root),
            patch('app.vectorstore.chroma_store.get_data_dir', return_value=self.root),
            patch('app.vectorstore.chroma_store._client', None),
            patch('app.db.database.SessionLocal', self.sessions),
        ]
        for guard in patches:
            guard.start()
            self.addCleanup(guard.stop)
        with self.sessions() as db:
            crud.create_user(db, 'admin', 'admin', hash_password('test-password'), 'admin')
            crud.create_user(db, 'user', 'member', hash_password('test-password'), 'user')
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = self.login('admin')

    def login(self, username):
        response = self.client.post('/api/v1/auth/login', json={'username': username, 'password': 'test-password'})
        self.assertEqual(response.status_code, 200)
        return {'Authorization': 'Bearer ' + response.json()['access_token']}

    def test_auth_and_administration(self):
        self.assertEqual(self.client.get('/api/health').json(), {'status': 'ok'})
        self.assertEqual(self.client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'wrong'}).status_code, 401)
        self.assertIn(self.client.get('/api/v1/knowledge/list').status_code, [401, 403])
        self.assertEqual(self.client.get('/api/v1/admin/users', headers=self.login('member')).status_code, 403)
        self.assertEqual(self.client.get('/api/v1/auth/me', headers=self.headers).json()['role'], 'admin')
        response = self.client.post('/api/v1/admin/llm-configs', headers=self.headers, json={'name': 'test', 'provider': 'deepseek', 'api_key': 'test-key'})
        self.assertEqual(response.status_code, 200)
        config_id = response.json()['id']
        self.assertEqual(self.client.put(f'/api/v1/admin/llm-configs/{config_id}/set-default', headers=self.headers).status_code, 200)
        configs = self.client.get('/api/v1/admin/llm-configs', headers=self.headers).json()
        self.assertTrue(configs[0]['is_default'])
        self.assertNotIn('api_key_enc', configs[0])
        self.assertEqual(self.client.delete(f'/api/v1/admin/llm-configs/{config_id}', headers=self.headers).status_code, 200)

    def test_packaged_frontend_routes_survive_page_refresh(self):
        for route in ['/login', '/setup', '/knowledge', '/admin']:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertIn('id="app"', response.text)
        self.assertEqual(self.client.get('/api/nonexistent').status_code, 404)
        self.assertEqual(self.client.get('/assets/nonexistent.js').status_code, 404)

    def test_document_delete_must_match_knowledge_base(self):
        kb = self.client.post('/api/v1/knowledge/create', headers=self.headers, json={'name': 'owner'}).json()
        with self.sessions() as db:
            crud.create_doc(db, id='doc', kb_id=kb['id'], filename='test.txt', file_path='unused', file_hash='hash', status='pending')
        response = self.client.delete('/api/v1/knowledge/wrong-library/documents/doc', headers=self.headers)
        self.assertEqual(response.status_code, 404)
        docs = self.client.get(f'/api/v1/knowledge/{kb["id"]}/documents', headers=self.headers).json()
        self.assertEqual(len(docs), 1)

    def test_initialized_setup_cannot_overwrite_model_or_keys(self):
        for endpoint, payload in [('llm', {'provider': 'deepseek', 'api_key': 'attacker'}),
                                  ('embedding', {'provider': 'qwen', 'api_key': 'attacker'}),
                                  ('search', {'provider': 'bocha', 'api_key': 'attacker'}),
                                  ('complete', {})]:
            with self.subTest(endpoint=endpoint):
                response = self.client.post('/api/v1/setup/' + endpoint, json=payload)
                self.assertIn(response.status_code, [400, 403], response.text)

    def test_knowledge_upload_search_delete_workflow(self):
        saved = self.client.put('/api/v1/admin/embedding-config', headers=self.headers, json={'provider': 'qwen', 'model': '', 'api_key': 'test-key'})
        self.assertEqual(saved.status_code, 200)
        kb = self.client.post('/api/v1/knowledge/create', headers=self.headers, json={'name': 'temporary workflow'}).json()
        def embeddings(url, *, headers, json, **kwargs):
            return httpx.Response(200, json={'data': [{'index': i, 'embedding': [1., 0., 0.]} for i, _ in enumerate(json['input'])]})
        async def async_embeddings(*args, **kwargs):
            return embeddings(*args, **kwargs)
        with patch('app.models.embedding_client.post_with_retry', side_effect=embeddings), \
             patch('app.core.http_retry.async_post_with_retry', side_effect=async_embeddings):
            response = self.client.post(f'/api/v1/knowledge/{kb["id"]}/upload', headers=self.headers, files={'file': ('workflow.txt', 'Agent 转型：学习 Python 和工具调用。'.encode(), 'text/plain')})
            self.assertEqual(response.status_code, 200)
            doc_id = response.json()['doc_id']
            docs = self.client.get(f'/api/v1/knowledge/{kb["id"]}/documents', headers=self.headers).json()
            self.assertEqual(docs[0]['status'], 'done', docs)
            self.assertGreater(docs[0]['chunk_count'], 0)
            class Model:
                async def astream(self, messages):
                    assert '工具调用' in messages[-1].content
                    yield SimpleNamespace(content='学习 Python 和工具调用。')
            with patch('app.api.chat.get_llm', return_value=Model()):
                response = self.client.post('/api/v1/chat/stream', headers=self.headers, json={'session_id': 'test', 'query': '如何转Agent？', 'knowledge_ids': [kb['id']]})
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            self.assertIn('source', [e['event'] for e in events], events)
            self.assertEqual(events[-1]['event'], 'done', events)
            self.assertEqual(self.client.delete(f'/api/v1/knowledge/{kb["id"]}/documents/{doc_id}', headers=self.headers).status_code, 200)
            self.assertEqual(self.client.get(f'/api/v1/knowledge/{kb["id"]}/documents', headers=self.headers).json(), [])
            self.assertEqual(self.client.delete(f'/api/v1/knowledge/{kb["id"]}', headers=self.headers).status_code, 200)


if __name__ == '__main__':
    unittest.main()
