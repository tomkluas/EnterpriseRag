import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import knowledge
from app.agents.vector_node import vector_node
from app.core.auth import get_current_user
from app.core.deps import get_db
from app.db import crud
from app.db.models import Base
from app.models.bge_model import delete_doc_chunks as real_delete_doc_chunks


class KnowledgeApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.engine.dispose)
        self.user = SimpleNamespace(role='admin')
        app = FastAPI()
        app.include_router(knowledge.router)
        def db_override():
            with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = db_override
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        for target, kwargs in (
            ('app.api.knowledge.settings.upload_dir', {'new': self.tmp.name}),
            ('app.api.knowledge.ensure_collection', {}),
            ('app.api.knowledge.process_document', {'new_callable': AsyncMock}),
            ('app.models.bge_model.delete_doc_chunks', {}),
            ('app.models.bge_model.get_qdrant', {}),
            ('app.pipeline.embedder.SessionLocal', {'new': self.sessions}),
        ):
            mock = patch(target, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)
        self.kb = self.create_kb('first')

    def create_kb(self, name):
        response = self.client.post('/api/v1/knowledge/create', params={'name': name})
        self.assertEqual(response.status_code, 200)
        return response.json()['id']

    def upload(self, kb=None, name='note.txt', content=b'hello'):
        return self.client.post(f'/api/v1/knowledge/{kb or self.kb}/upload', files={'file': (name, content)})

    def test_list_reports_actual_completed_documents_despite_legacy_counter(self):
        with self.sessions() as db:
            crud.get_kb(db, self.kb).doc_count = 99
            db.commit()
        response = self.client.get('/api/v1/knowledge/list')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]['doc_count'], 0)

    def test_list_create_upload_list_delete_document(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200)
        doc_id = response.json()['doc_id']
        docs = self.client.get(f'/api/v1/knowledge/{self.kb}/documents').json()
        self.assertEqual([d['id'] for d in docs], [doc_id])
        with self.sessions() as db:
            path = crud.get_doc(db, doc_id).file_path
        response = self.client.delete(f'/api/v1/knowledge/{self.kb}/documents/{doc_id}')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Path(path).exists())
        self.assertEqual(self.client.get(f'/api/v1/knowledge/{self.kb}/documents').json(), [])
        self.assertEqual(self.client.delete(f'/api/v1/knowledge/{self.kb}/documents/{doc_id}').status_code, 404)

    def test_wrong_kb_cannot_delete_document(self):
        doc_id = self.upload().json()['doc_id']
        other = self.create_kb('other')
        self.assertEqual(self.client.delete(f'/api/v1/knowledge/{other}/documents/{doc_id}').status_code, 404)
        with self.sessions() as db:
            self.assertIsNotNone(crud.get_doc(db, doc_id))

    def test_upload_names_are_unique_and_paths_are_safe(self):
        first = self.upload(content=b'first').json()['doc_id']
        second = self.upload(content=b'second').json()['doc_id']
        with self.sessions() as db:
            a, b = crud.get_doc(db, first), crud.get_doc(db, second)
            self.assertNotEqual(a.file_path, b.file_path)
            self.assertEqual(Path(a.file_path).read_bytes(), b'first')
        self.assertEqual(self.upload(name='../bad.txt').status_code, 400)
        self.assertEqual(self.upload(name='..\\bad.txt').status_code, 400)
        self.assertEqual(self.upload(name='bad.exe').status_code, 400)
        self.assertEqual(self.upload(name='old.doc').status_code, 400)
        self.assertEqual(self.upload(content=b'').status_code, 400)

    def test_missing_kb_has_consistent_404(self):
        self.assertEqual(self.upload(kb='missing').status_code, 404)
        self.assertEqual(self.client.get('/api/v1/knowledge/missing/documents').status_code, 404)
        self.assertEqual(self.client.delete('/api/v1/knowledge/missing').status_code, 404)

    def test_delete_kb_cleans_documents_files_and_requires_admin(self):
        doc_id = self.upload().json()['doc_id']
        with self.sessions() as db:
            path = crud.get_doc(db, doc_id).file_path
        self.user.role = 'user'
        self.assertEqual(self.client.delete(f'/api/v1/knowledge/{self.kb}').status_code, 403)
        self.user.role = 'admin'
        self.assertEqual(self.client.delete(f'/api/v1/knowledge/{self.kb}').status_code, 200)
        self.assertFalse(Path(path).exists())
        with self.sessions() as db:
            self.assertIsNone(crud.get_kb(db, self.kb))
            self.assertIsNone(crud.get_doc(db, doc_id))

    def test_empty_scope_searches_all_kbs(self):
        with patch('app.agents.vector_node.SessionLocal', self.sessions), patch('app.agents.vector_node.search_chunks', return_value=[{'text': 'hit'}]) as search:
            result = asyncio.run(vector_node({'query': 'q', 'scope_vector': True, 'knowledge_ids': []}))
        self.assertEqual(len(result['retrieval_results']), 1)
        search.assert_called_once_with(self.kb, 'q', top_k=10)

    def test_vector_retrieval_failure_is_reported_without_secrets(self):
        with patch('app.agents.vector_node.search_chunks', side_effect=RuntimeError('secret-key')):
            result = asyncio.run(vector_node({'query': 'q', 'scope_vector': True, 'knowledge_ids': [self.kb]}))
        self.assertEqual(result['retrieval_results'], [])
        self.assertIn('知识库检索失败', result['error'])
        self.assertNotIn('secret-key', result['error'])

    def test_pipeline_preserves_source_name_and_counts_only_done_docs(self):
        from app.pipeline.embedder import process_document
        doc_id = self.upload().json()['doc_id']
        with self.sessions() as db:
            path = crud.get_doc(db, doc_id).file_path
        with patch('app.models.bge_model.bge_singleton.load', new_callable=AsyncMock), patch('app.models.bge_model.upsert_chunks') as upsert:
            asyncio.run(process_document(doc_id, self.kb, path))
        chunk = upsert.call_args.args[1][0]
        self.assertEqual(chunk['metadata']['filename'], 'note.txt')
        with self.sessions() as db:
            self.assertEqual(crud.get_doc(db, doc_id).status, 'done')
            self.assertEqual(crud.get_kb(db, self.kb).doc_count, 1)
        pending_id = self.upload(name='pending.txt').json()['doc_id']
        self.assertEqual(self.client.delete(f'/api/v1/knowledge/{self.kb}/documents/{pending_id}').status_code, 200)
        with self.sessions() as db:
            self.assertEqual(crud.get_kb(db, self.kb).doc_count, 1)
        self.assertEqual(self.client.delete(f'/api/v1/knowledge/{self.kb}/documents/{doc_id}').status_code, 200)
        with self.sessions() as db:
            self.assertEqual(crud.get_kb(db, self.kb).doc_count, 0)

    def test_processing_document_cannot_be_deleted_with_its_kb(self):
        doc_id = self.upload().json()['doc_id']
        with self.sessions() as db:
            crud.update_doc_status(db, doc_id, 'processing')
        self.assertEqual(self.client.delete(f'/api/v1/knowledge/{self.kb}/documents/{doc_id}').status_code, 409)
        self.assertEqual(self.client.delete(f'/api/v1/knowledge/{self.kb}').status_code, 409)

    def test_vector_failure_keeps_document_for_retry(self):
        doc_id = self.upload().json()['doc_id']
        with patch('app.models.bge_model.delete_doc_chunks', side_effect=RuntimeError('offline')):
            self.assertEqual(self.client.delete(f'/api/v1/knowledge/{self.kb}/documents/{doc_id}').status_code, 503)
        with self.sessions() as db:
            doc = crud.get_doc(db, doc_id)
            self.assertIsNotNone(doc)
            self.assertTrue(Path(doc.file_path).exists())

    def test_create_vector_failure_does_not_leave_kb(self):
        with patch('app.api.knowledge.ensure_collection', side_effect=RuntimeError('offline')):
            response = self.client.post('/api/v1/knowledge/create', params={'name': 'failed'})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(self.client.get('/api/v1/knowledge/list').json()), 1)

    def test_upload_limit_rejects_without_leaving_files_or_records(self):
        with patch('app.api.knowledge.settings.max_file_size_mb', 0):
            self.assertEqual(self.upload().status_code, 413)
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])
        self.assertEqual(self.client.get(f'/api/v1/knowledge/{self.kb}/documents').json(), [])

    def test_deleted_document_background_task_does_not_embed(self):
        from app.pipeline.embedder import process_document
        with patch('app.models.bge_model.upsert_chunks') as upsert:
            asyncio.run(process_document('deleted', self.kb, '/missing.txt'))
        upsert.assert_not_called()

    def test_delete_vectors_uses_filter_without_pagination_and_propagates_failure(self):
        with patch('app.models.bge_model.get_qdrant') as qdrant:
            real_delete_doc_chunks('kb', 'doc')
            call = qdrant.return_value.delete.call_args.kwargs
            self.assertEqual(call['points_selector'].filter.must[0].match.value, 'doc')
            self.assertTrue(call['wait'])
            qdrant.return_value.scroll.assert_not_called()
            qdrant.return_value.delete.side_effect = RuntimeError('offline')
            with self.assertRaises(RuntimeError):
                real_delete_doc_chunks('kb', 'doc')


if __name__ == '__main__':
    unittest.main()
