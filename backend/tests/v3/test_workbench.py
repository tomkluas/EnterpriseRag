# 复用临时 API 夹具验证密码会话、成员管理与诊断轨迹；后端/模型异常由测试注入，不连接真实企业服务。
import time
import test_api
from app.v3.models import Base, LoginSession


class WorkbenchTests(test_api.ApiTests):
    def member(self, name='employee', password='Member-pass-2026'):
        return self.client.post('/api/v3/users', headers=self.headers, json={
            'id': name, 'role': 'reader', 'password': password})

    def login_member(self, name='employee', password='Member-pass-2026'):
        return self.client.post('/api/v3/auth/login', json={'id': name, 'password': password})

    def test_reader_cannot_open_document_or_job_workspaces_but_can_use_chat(self):
        from app.v3.models import Document, Version, Job
        self.member()
        space = self.domain.create_space('enterprise', 'admin', 'reader-chat')
        received = self.domain.receive('enterprise', 'admin', space, 'policy.txt', b'policy')
        self.domain.grant('enterprise', 'admin', received['document_id'], ['employee'], [])
        with self.db.transaction('enterprise') as s:
            s.get(Document, received['document_id']).current_version = received['version_id']
            s.get(Version, received['version_id']).state = 'published'
            # A former editor's own task must not remain accessible after becoming a reader.
            s.get(Job, received['job_id']).owner = 'employee'
        headers = {'Authorization': 'Bearer '+self.login_member().json()['access_token']}
        for route in (f'/api/v3/spaces/{space}/documents', '/api/v3/jobs',
                      f"/api/v3/jobs/{received['job_id']}/replicas/missing/chunks"):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route, headers=headers).status_code, 403)
        self.assertIn(space, [row['id'] for row in self.client.get('/api/v3/spaces', headers=headers).json()])
        conversation = self.client.post('/api/v3/conversations', headers=headers).json()['id']
        response = self.client.post(f'/api/v3/conversations/{conversation}/messages/stream',
            headers=headers, json={'space_id': space, 'query': 'test'})
        self.assertEqual(response.status_code, 200)
        history = self.client.get(f'/api/v3/conversations/{conversation}/messages', headers=headers)
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.json()), 1)
        # Reading a cited original remains governed by the document's role grants.
        self.assertEqual(self.client.get(f"/api/v3/documents/{received['document_id']}/source",
            headers=headers).content, b'policy')

    def test_workspace_access_follows_current_custom_role_permissions(self):
        self.member()
        role = self.domain.save_role('enterprise', 'admin', '资料维护', permissions=['knowledge.read', 'knowledge.create'])
        self.domain.add_user('enterprise', 'admin', 'employee', role_ids=[role['id']])
        space = self.domain.create_space('enterprise', 'employee', 'owned')
        headers = {'Authorization': 'Bearer '+self.login_member().json()['access_token']}
        for route in (f'/api/v3/spaces/{space}/documents', '/api/v3/jobs'):
            self.assertEqual(self.client.get(route, headers=headers).status_code, 200)
        self.domain.save_role('enterprise', 'admin', '资料维护', permissions=['knowledge.read'], id=role['id'])
        for route in (f'/api/v3/spaces/{space}/documents', '/api/v3/jobs'):
            self.assertEqual(self.client.get(route, headers=headers).status_code, 403)

    def test_member_password_login_reset_disable_and_logout(self):
        self.assertEqual(self.member().status_code, 200)
        self.assertEqual(self.login_member(password='incorrect').status_code, 401)
        logged = self.login_member()
        self.assertEqual(logged.status_code, 200)
        token = logged.json()['access_token']
        headers = {'Authorization': 'Bearer '+token}
        self.assertEqual(self.client.get('/api/v3/me', headers=headers).json()['id'], 'employee')
        self.assertEqual(self.client.get('/api/v3/users', headers=headers).status_code, 403)
        self.assertEqual(self.client.post('/api/v3/auth/logout', headers=headers).status_code, 200)
        self.assertEqual(self.client.get('/api/v3/me', headers=headers).status_code, 401)
        headers['Authorization'] = 'Bearer '+self.login_member().json()['access_token']
        self.assertEqual(self.member(password='New-member-pass-2026').status_code, 200)
        self.assertEqual(self.client.get('/api/v3/me', headers=headers).status_code, 401)
        self.assertEqual(self.login_member().status_code, 401)
        self.assertEqual(self.login_member(password='New-member-pass-2026').status_code, 200)
        self.client.post('/api/v3/users', headers=self.headers, json={'id': 'employee', 'active': False})
        self.assertEqual(self.login_member(password='New-member-pass-2026').status_code, 401)

    def test_password_secrets_never_returned_and_short_password_rejected(self):
        # An invalid password must not be reflected by Pydantic's validation errors.
        invalid = self.member(password='secret123')
        self.assertEqual(invalid.status_code, 422)
        self.assertNotIn('secret123', invalid.text)
        invalid = self.login_member(password='sensitive'*20)
        self.assertEqual(invalid.status_code, 422)
        self.assertNotIn('sensitive', invalid.text)
        self.assertEqual(self.member().status_code, 200)
        data = self.client.get('/api/v3/users', headers=self.headers).text
        self.assertNotIn('Member-pass', data)
        self.assertNotIn('password_hash', data)
        self.assertEqual(self.member().status_code, 200)

    def test_login_failure_limit_and_expired_sessions(self):
        self.member()
        for _ in range(5): self.assertEqual(self.login_member(password='incorrect').status_code, 401)
        self.assertEqual(self.login_member().status_code, 429)
        from app.v3.models import LoginThrottle
        with self.db.transaction('enterprise') as s:
            for row in s.query(LoginThrottle): row.expires_at = time.time()-1
        logged = self.login_member()
        self.assertEqual(logged.status_code, 200)
        with self.db.transaction('enterprise') as s:
            for row in s.query(LoginSession): row.expires_at = time.time()-1
        headers = {'Authorization': 'Bearer '+logged.json()['access_token']}
        self.assertEqual(self.client.get('/api/v3/me', headers=headers).status_code, 401)

    def test_backend_registration_uses_service_address(self):
        result = self.client.post('/api/v3/backends', headers=self.headers, json={
            'provider': 'dify', 'base_url': 'https://kb.test', 'key_ref': 'V3_SECRET_TEST'})
        self.assertEqual(result.status_code, 201)

    def test_trace_is_persisted_and_private(self):
        space = self.domain.create_space('enterprise', 'admin', 'trace')
        conv = self.domain.create_conversation('enterprise', 'admin')
        response = self.client.post(f'/api/v3/conversations/{conv}/messages/stream',
            headers=self.headers, json={'space_id': space, 'query': 'test'})
        self.assertIn('"event": "done"', response.text)
        history = self.client.get(f'/api/v3/conversations/{conv}/messages', headers=self.headers).json()
        route = f"/api/v3/messages/{history[0]['id']}/trace"
        trace = self.client.get(route, headers=self.headers)
        self.assertEqual(trace.status_code, 200)
        stages = {item['stage'] for item in trace.json()['stages']}
        self.assertTrue({'request', 'retrieve'}.issubset(stages))
        self.assertEqual(self.member().status_code, 200)
        headers = {'Authorization': 'Bearer '+self.login_member().json()['access_token']}
        self.assertEqual(self.client.get(route, headers=headers).status_code, 403)

    def test_failed_answer_has_safe_trace(self):
        from unittest.mock import patch
        from app.v3.domain import DomainError
        async def fail(*args, **kwargs): raise DomainError('底座尚未完成当前环境的验证', 503)
        space = self.domain.create_space('enterprise', 'admin', 'trace')
        conv = self.domain.create_conversation('enterprise', 'admin')
        with patch.object(self.client.app.state.chat, 'retrieve', side_effect=fail):
            response = self.client.post(f'/api/v3/conversations/{conv}/messages/stream',
                headers=self.headers, json={'space_id': space, 'query': 'test'})
        self.assertIn('"event": "error"', response.text)
        history = self.domain.history('enterprise', 'admin', conv)
        result = self.client.get(f"/api/v3/messages/{history[0]['id']}/trace", headers=self.headers).json()
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['failure']['code'], 'request_rejected')
        self.assertIn('error', [stage['outcome'] for stage in result['stages']])

    def test_old_backup_without_password_tables_or_trace_can_restore(self):
        import hashlib
        import json
        from app.v3.models import Message
        from app.v3.recovery import backup_tenant, restore_tenant
        from app.v3.db import Database
        conv = self.domain.create_conversation('enterprise', 'admin')
        with self.db.transaction('enterprise') as s:
            s.add(Message(id='old', tenant_id='enterprise', conversation_id=conv, owner='admin', query='old'))
        bundle = self.root/'bundle'
        backup_tenant(self.db, self.domain.objects, 'enterprise', bundle)
        path = bundle/'rows.json'
        data = json.loads(path.read_text('utf8'))
        for name in ('v3_credentials', 'v3_login_sessions', 'v3_login_throttles'): data.pop(name)
        for row in data['v3_messages']: row.pop('trace')
        raw = json.dumps(data).encode()
        path.write_bytes(raw)
        manifest_path = bundle/'manifest.json'
        manifest = json.loads(manifest_path.read_text('utf8'))
        manifest['rows_sha256'] = hashlib.sha256(raw).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding='utf8')
        target = Database('sqlite:///'+str(self.root/'restored.db'), testing=True)
        try:
            target.migrate()
            restore_tenant(target, 'enterprise', bundle, self.root/'restored-objects')
            with target.transaction() as s: self.assertEqual(s.get(Message, 'old').trace, {})
        finally: target.engine.dispose()

    def test_grant_choices_available_to_space_owner_but_not_reader(self):
        self.domain.add_user('enterprise', 'admin', 'editor', 'editor', ['产品组'])
        # Editor can maintain their own knowledge base without listing account credentials.
        self.member(name='editor')
        self.domain.add_user('enterprise', 'admin', 'editor', 'editor', ['产品组'])
        space = self.domain.create_space('enterprise', 'editor', 'test')
        doc = self.domain.receive('enterprise', 'editor', space, 'test.txt', b'test')['document_id']
        route = f'/api/v3/documents/{doc}/grant-options'
        headers = {'Authorization': 'Bearer '+self.login_member(name='editor').json()['access_token']}
        result = self.client.get(route, headers=headers)
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json())
        self.assertTrue(all(item['kind']=='role' for item in result.json()))
        self.assertNotIn('group:产品组', [item['id'] for item in result.json()])
        self.assertNotIn('password', result.text)
        self.member()
        headers = {'Authorization': 'Bearer '+self.login_member().json()['access_token']}
        self.assertEqual(self.client.get(route, headers=headers).status_code, 403)

    def test_all_business_tables_and_columns_have_comments(self):
        for table in Base.metadata.tables.values():
            self.assertTrue(table.comment, table.name)
            for column in table.columns:
                self.assertTrue(column.comment, f'{table.name}.{column.name}')
