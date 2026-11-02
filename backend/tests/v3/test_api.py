# 通过进程内 TestClient 验证鉴权、原件和任务接口；夹具使用临时 SQLite 与对象目录，不依赖运行中的 API。
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from app.v3.config import Settings
from app.v3.api import create_app
from app.v3.db import Database
from app.v3.domain import Domain


class ApiTests(unittest.TestCase):
    def test_operation_records_are_latest_first_and_include_useful_resources(self):
        from app.v3.models import Audit
        self.domain.create_space('enterprise','admin','操作记录知识库')
        with self.db.transaction('enterprise') as s:
            s.add_all([Audit(id='history-'+str(i),tenant_id='enterprise',actor='admin',action='test.old',resource_id='old',created_at=i+1) for i in range(205)])
            s.add(Audit(id='newest',tenant_id='enterprise',actor='admin',action='test.latest',resource_id='new',created_at=9999999999))
            s.add(Audit(id='foreign',tenant_id='another',actor='admin',action='test.foreign',resource_id='hidden',created_at=99999999999))
        response=self.client.get('/api/v3/audit',headers=self.headers)
        self.assertEqual(response.status_code,200)
        data=response.json();items=data['items'];self.assertEqual(len(items),20)
        self.assertEqual(data['total'],207)
        self.assertEqual(data['page'],1)
        self.assertEqual(data['page_size'],20)
        self.assertEqual(items[0]['id'],'newest')
        self.assertNotIn('foreign',[a['id'] for a in items])
        self.assertTrue(any(a.get('resource_name')=='操作记录知识库' for a in items))
        self.assertEqual(self.client.get('/api/v3/audit').status_code,401)

    def test_operation_records_paginate_all_history_and_filter_before_pagination(self):
        from app.v3.models import Audit
        with self.db.transaction('enterprise') as s:
            s.add_all([Audit(id=f'page-{i:03}',tenant_id='enterprise',actor='reviewer',
                action='user.login',resource_id='member',created_at=100) for i in range(205)])
            s.add(Audit(id='foreign',tenant_id='another',actor='reviewer',action='user.login',resource_id='member',created_at=100))
        route='/api/v3/audit?category=user&query=登录&page_size=20'
        ids=[]
        for page in range(1,12):
            response=self.client.get(route+f'&page={page}',headers=self.headers)
            self.assertEqual(response.status_code,200)
            data=response.json();self.assertEqual(data['total'],205)
            ids.extend(row['id'] for row in data['items'])
        self.assertEqual(len(ids),205)
        self.assertEqual(len(set(ids)),205)
        self.assertEqual(ids,sorted(ids,reverse=True))
        self.assertEqual(self.client.get(route+'&page=12',headers=self.headers).json()['items'],[])
        self.assertEqual(self.client.get('/api/v3/audit?query=reviewer&category=user',headers=self.headers).json()['total'],205)
        self.assertEqual(self.client.get('/api/v3/audit?query=not-found',headers=self.headers).json()['total'],0)
        for query in ('page=0','page_size=0','page_size=101'):
            self.assertEqual(self.client.get('/api/v3/audit?'+query,headers=self.headers).status_code,400)

    def test_operation_record_search_matches_resource_names_and_keeps_admin_boundary(self):
        self.domain.create_space('enterprise','admin','采购审批指南')
        data=self.client.get('/api/v3/audit?query=采购审批',headers=self.headers).json()
        self.assertEqual(data['total'],1)
        self.assertEqual(data['items'][0]['resource_name'],'采购审批指南')
        self.assertEqual(self.client.get('/api/v3/audit?query=%25',headers=self.headers).json()['total'],0)
        self.domain.add_user('enterprise','admin','reader',password='Reader-password-2026')
        token=self.client.post('/api/v3/auth/login',json={'id':'reader','password':'Reader-password-2026'}).json()['access_token']
        self.assertEqual(self.client.get('/api/v3/audit?page=2',headers={'Authorization':'Bearer '+token}).status_code,403)

    def test_delete_conversation_hides_personal_access_and_preserves_audit(self):
        from app.v3.models import Message,Conversation
        conv=self.domain.create_conversation('enterprise','admin')
        with self.db.transaction('enterprise') as s:
            s.add(Message(id='kept',tenant_id='enterprise',owner='admin',conversation_id=conv,query='保留审计',state='failed'))
        route=f'/api/v3/conversations/{conv}'
        self.assertEqual(self.client.delete(route).status_code,401)
        for _ in range(2):self.assertEqual(self.client.delete(route,headers=self.headers).status_code,200)
        self.assertNotIn(conv,[c['id'] for c in self.client.get('/api/v3/conversations',headers=self.headers).json()])
        self.assertEqual(self.client.get(route+'/messages',headers=self.headers).status_code,404)
        self.assertEqual(self.client.get('/api/v3/admin/trace-conversations/'+conv,headers=self.headers).status_code,200)
        with self.db.transaction('enterprise') as s:
            self.assertTrue(s.get(Conversation,conv).archived)
            self.assertIsNotNone(s.get(Message,'kept'))
        other=self.domain.create_conversation('enterprise','admin')
        with self.db.transaction('enterprise') as s:s.get(Conversation,other).owner='someone-else'
        self.assertEqual(self.client.delete(f'/api/v3/conversations/{other}',headers=self.headers).status_code,403)

    def test_delete_conversation_rejects_active_generation(self):
        from app.v3.models import Message
        conv=self.domain.create_conversation('enterprise','admin')
        with self.db.transaction('enterprise') as s:
            s.add(Message(id='active',tenant_id='enterprise',owner='admin',conversation_id=conv,query='进行中',state='processing'))
        self.assertEqual(self.client.delete(f'/api/v3/conversations/{conv}',headers=self.headers).status_code,409)

    def test_failed_question_can_be_resent_without_overwriting_original_attempt(self):
        from unittest.mock import AsyncMock,patch
        from app.v3.domain import DomainError
        space=self.domain.create_space('enterprise','admin','retry-test')
        conv=self.domain.create_conversation('enterprise','admin')
        route=f'/api/v3/conversations/{conv}/messages/stream'
        payload={'space_id':space,'query':'重新回答这个问题'}
        with patch('app.v3.chat.service.ChatService.retrieve',new=AsyncMock(side_effect=DomainError('测试检索失败',503))):
            response=self.client.post(route,headers=self.headers,json=payload)
            self.assertIn('"event": "error"',response.text)
        response=self.client.post(route,headers=self.headers,json=payload)
        self.assertIn('"event": "done"',response.text)
        history=self.client.get(f'/api/v3/conversations/{conv}/messages',headers=self.headers).json()
        self.assertEqual([m['state'] for m in history],['failed','completed'])
        self.assertEqual([m['space_id'] for m in history],[space,space])
        self.assertNotEqual(history[0]['id'],history[1]['id'])

    def test_conversation_title_uses_first_question_and_preserves_custom_titles(self):
        from app.v3.models import Conversation,Message
        conversation=self.domain.create_conversation('enterprise','admin')
        empty=self.domain.create_conversation('enterprise','admin')
        custom=self.domain.create_conversation('enterprise','admin')
        with self.db.transaction('enterprise') as s:
            s.get(Conversation,custom).title='我的制度笔记'
            s.add_all([
                Message(id='first',tenant_id='enterprise',owner='admin',conversation_id=conversation,
                        query='  上海住宿标准是多少？\n 发票什么时候提交？  ',created_at=1),
                Message(id='later',tenant_id='enterprise',owner='admin',conversation_id=conversation,
                        query='后续追问',created_at=2),
                Message(id='custom',tenant_id='enterprise',owner='admin',conversation_id=custom,query='不覆盖标题'),
            ])
        response=self.client.get('/api/v3/conversations',headers=self.headers)
        self.assertEqual(response.status_code,200)
        titles={c['id']:c['title'] for c in response.json()}
        self.assertEqual(titles[conversation],'上海住宿标准是多少？ 发票什么时候提交？')
        self.assertEqual(titles[empty],'新会话')
        self.assertEqual(titles[custom],'我的制度笔记')
        with self.db.transaction('enterprise') as s:
            s.get(Message,'first').query='长'*40
        titles={c['id']:c['title'] for c in self.client.get('/api/v3/conversations',headers=self.headers).json()}
        self.assertEqual(titles[conversation],'长'*32+'…')

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

    # 每例独立建库和对象根目录，使上传、撤回与接口鉴权共享真实领域逻辑，同时避免测试间残留。
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
        self.assertEqual(history[0]['space_id'],space)
        actions={a['action'] for a in self.domain.list_audit('enterprise','admin')['items']}
        self.assertIn('conversation.create',actions)
        self.assertIn('message.ask',actions)
