# 验证管理员诊断轨迹的租户范围、字段筛选及访问审计；数据全部写入临时库，不读取实际用户对话。
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select

from app.v3.db import Database
from app.v3.domain import Domain, DomainError
from app.v3.models import Audit, Conversation, Message
from app.v3.trace_admin import list_traces, trace_detail
from app.v3 import trace_admin


class TraceAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database('sqlite:///' + str(Path(self.temp.name) / 'audit.db'), testing=True)
        self.db.migrate()
        self.domain = Domain(self.db, Path(self.temp.name) / 'objects')
        self.domain.bootstrap('t1', 'admin')
        self.domain.bootstrap('t2', 'other')
        with self.db.transaction('t1') as s:
            s.add_all([
                Conversation(id='c1', tenant_id='t1', owner='alice'),
                Conversation(id='c2', tenant_id='t1', owner='bob', title='制度咨询'),
                Conversation(id='cx', tenant_id='t2', owner='other'),
                Conversation(id='empty', tenant_id='t1', owner='alice'),
                Message(id='old', tenant_id='t1', owner='alice', conversation_id='c1', query='旧问题', state='completed', created_at=1),
                Message(id='completed', tenant_id='t1', owner='bob', conversation_id='c2', query='制度 100%', answer='完整回答', feedback='有帮助', state='completed', created_at=2,
                        trace={'secret': 'hidden', 'stages': [{'stage': 'request', 'milliseconds': 15, 'outcome': 'ok', 'api_key': 'hidden'}]},
                        evidence=[{'tenant_id': 't1', 'document_id': 'doc1', 'version_id': 'v1', 'content': '来源正文', 'api_key': 'hidden', 'locator': {'page': 2, 'key_ref': 'hidden'}},
                                  {'tenant_id': 't2', 'document_id': 'foreign', 'content': '外租户证据'}]),
                Message(id='failed', tenant_id='t1', owner='alice', conversation_id='c1', query='制度 1000', state='failed', created_at=3,
                        trace={'stages': [{'stage': 'request', 'milliseconds': 12, 'outcome': 'error'}], 'failure': {'code': 'service_error', 'detail': '调用失败', 'token': 'hidden'}}),
                Message(id='foreign', tenant_id='t2', owner='other', conversation_id='cx', query='制度 100%', state='completed', created_at=4),
            ])

    def tearDown(self):
        self.db.engine.dispose()
        self.temp.cleanup()

    def test_admin_lists_other_members_with_tenant_scope_and_filtered_summary(self):
        result = list_traces(self.domain, 't1', 'admin', page_size=1)
        self.assertEqual(result['total'], 3)
        self.assertEqual([m['id'] for m in result['items']], ['failed'])
        self.assertEqual(result['summary'], {'total': 3, 'completed': 2, 'failed': 1, 'processing': 0, 'with_feedback': 1})
        second = list_traces(self.domain, 't1', 'admin', page=2, page_size=1)
        self.assertEqual(second['items'][0]['id'], 'completed')
        self.assertEqual(second['items'][0]['total_milliseconds'], 15)
        filtered = list_traces(self.domain, 't1', 'admin', owner='alice', query='制度', start=2, end=3)
        self.assertEqual(filtered['total'], 1)
        self.assertEqual(filtered['summary']['completed'], 0)
        self.assertEqual(filtered['summary']['failed'], 1)
        literal = list_traces(self.domain, 't1', 'admin', query='100%')
        self.assertEqual([item['id'] for item in literal['items']], ['completed'])
        self.assertEqual(list_traces(self.domain, 't1', 'admin', state='completed')['total'], 2)

    def test_detail_audits_and_returns_only_known_fields(self):
        result = trace_detail(self.domain, 't1', 'admin', 'completed')
        self.assertEqual(result['owner'], 'bob')
        self.assertEqual(result['answer'], '完整回答')
        self.assertEqual(result['feedback'], '有帮助')
        self.assertEqual(len(result['evidence']), 1)
        self.assertEqual(result['evidence'][0]['locator'], {'page': 2})
        self.assertNotIn('hidden', str(result))
        self.assertNotIn('dependencies', result)
        self.assertEqual(trace_detail(self.domain, 't1', 'admin', 'failed')['failure'], {'code': 'service_error', 'detail': '调用失败'})
        with self.db.transaction('t1') as s:
            audits = s.scalars(select(Audit).where(Audit.tenant_id == 't1', Audit.action == 'trace.view')).all()
            self.assertEqual({a.resource_id for a in audits}, {'completed', 'failed'})
            self.assertTrue(all(a.actor == 'admin' for a in audits))

    def test_empty_historic_trace_is_safe(self):
        result = trace_detail(self.domain, 't1', 'admin', 'old')
        self.assertEqual(result['trace'], {'stages': [], 'failure': None})
        self.assertIsNone(result['total_milliseconds'])

    def test_permission_hook_and_cross_tenant_detail(self):
        self.domain.add_user('t1', 'admin', 'reader')
        for call in (lambda: list_traces(self.domain, 't1', 'reader'),
                     lambda: trace_detail(self.domain, 't1', 'reader', 'old')):
            with self.assertRaises(DomainError) as error:
                call()
            self.assertEqual(error.exception.status, 403)
        with self.assertRaises(DomainError) as error:
            trace_detail(self.domain, 't1', 'admin', 'foreign')
        self.assertEqual(error.exception.status, 404)
        with self.db.transaction('t1') as s:
            self.assertEqual(len(s.scalars(select(Audit).where(Audit.action == 'trace.view')).all()), 0)

    def test_custom_permission_grants_audit_access_and_disabled_role_revokes_it(self):
        role = self.domain.save_role('t1', 'admin', '问答审计员', permissions=['traces.view'])
        self.domain.add_user('t1', 'admin', 'auditor', role_ids=[role['id']])
        self.assertEqual(list_traces(self.domain, 't1', 'auditor')['total'], 3)
        self.assertEqual(trace_detail(self.domain, 't1', 'auditor', 'completed')['owner'], 'bob')
        self.domain.save_role('t1', 'admin', '问答审计员', permissions=['traces.view'], active=False, id=role['id'])
        with self.assertRaises(DomainError) as error:
            list_traces(self.domain, 't1', 'auditor')
        self.assertEqual(error.exception.status, 403)

    def test_validation_and_empty_pages(self):
        for options in ({'page': 0}, {'page_size': 0}, {'page_size': 101}, {'page': 1.5}, {'page': True},
                        {'start': 3, 'end': 2}, {'start': 'bad'}, {'end': float('inf')}, {'state': 'unknown'}):
            with self.subTest(options=options), self.assertRaises(DomainError) as error:
                list_traces(self.domain, 't1', 'admin', **options)
            self.assertEqual(error.exception.status, 400)
        result = list_traces(self.domain, 't1', 'admin', page=5)
        self.assertEqual(result['items'], [])
        self.assertEqual(result['total'], 3)

    def test_conversation_list_groups_before_paging_and_keeps_full_counts_when_filtered(self):
        result = trace_admin.list_conversations(self.domain, 't1', 'admin', page_size=1)
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['items'][0]['id'], 'c1')
        self.assertEqual(result['items'][0]['message_count'], 2)
        self.assertEqual(result['items'][0]['failed'], 1)
        self.assertEqual(result['items'][0]['title'], '旧问题')
        self.assertEqual(result['summary'], {'conversations': 2, 'messages': 3, 'matching_messages': 3,
                         'completed': 2, 'failed': 1, 'processing': 0, 'with_feedback': 1})
        second = trace_admin.list_conversations(self.domain, 't1', 'admin', page=2, page_size=1)
        self.assertEqual(second['items'][0]['title'], '制度咨询')
        filtered = trace_admin.list_conversations(self.domain, 't1', 'admin', owner='alice', state='failed', start=2, end=3)
        self.assertEqual(filtered['total'], 1)
        self.assertEqual(filtered['items'][0]['message_count'], 2)
        self.assertEqual(filtered['summary']['matching_messages'], 1)
        self.assertEqual(filtered['summary']['completed'], 1)
        literal = trace_admin.list_conversations(self.domain, 't1', 'admin', query='100%')
        self.assertEqual([item['id'] for item in literal['items']], ['c2'])
        self.assertEqual(trace_admin.list_conversations(self.domain, 't1', 'admin', query='不存在')['total'], 0)

    def test_conversation_detail_orders_all_rounds_and_audits_only_returned_page(self):
        result = trace_admin.conversation_detail(self.domain, 't1', 'admin', 'c1', page_size=1)
        self.assertEqual([item['id'] for item in result['items']], ['old'])
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['title'], '旧问题')
        second = trace_admin.conversation_detail(self.domain, 't1', 'admin', 'c1', page=2, page_size=1)
        self.assertEqual([item['id'] for item in second['items']], ['failed'])
        self.assertEqual(second['items'][0]['trace']['failure']['detail'], '调用失败')
        self.assertNotIn('hidden', str(second))
        with self.db.transaction('t1') as s:
            audits = s.scalars(select(Audit).where(Audit.tenant_id == 't1', Audit.action == 'trace.view')).all()
            self.assertEqual({a.resource_id for a in audits}, {'old', 'failed'})
        result = trace_admin.conversation_detail(self.domain, 't1', 'admin', 'c2')
        self.assertEqual(len(result['items'][0]['evidence']), 1)
        self.assertNotIn('hidden', str(result))

    def test_conversation_boundaries_permissions_and_stable_ties(self):
        self.domain.add_user('t1', 'admin', 'reader')
        for call in (lambda: trace_admin.list_conversations(self.domain, 't1', 'reader'),
                     lambda: trace_admin.conversation_detail(self.domain, 't1', 'reader', 'c1')):
            with self.assertRaises(DomainError) as error: call()
            self.assertEqual(error.exception.status, 403)
        with self.assertRaises(DomainError) as error:
            trace_admin.conversation_detail(self.domain, 't1', 'admin', 'cx')
        self.assertEqual(error.exception.status, 404)
        with self.db.transaction('t1') as s:
            s.add_all([
                Message(id='a-tie', tenant_id='t1', owner='alice', conversation_id='c1', query='同刻前一条', created_at=3),
                Message(id='foreign-owner', tenant_id='t1', owner='bob', conversation_id='c1', query='不能混入', created_at=9),
                Message(id='foreign-tenant', tenant_id='t2', owner='alice', conversation_id='c1', query='不能跨租户', created_at=10),
            ])
        result = trace_admin.conversation_detail(self.domain, 't1', 'admin', 'c1')
        self.assertEqual([item['id'] for item in result['items']], ['old', 'a-tie', 'failed'])
        self.assertEqual(trace_admin.list_conversations(self.domain, 't1', 'admin', query='不能')['total'], 0)
        self.assertEqual(trace_admin.list_conversations(self.domain, 't1', 'admin')['items'][0]['message_count'], 3)

    def test_conversation_validation_empty_pages_and_role_revocation(self):
        for options in ({'page': 0}, {'page_size': 101}, {'page': True}, {'start': 4, 'end': 1}, {'state': 'unknown'}):
            with self.subTest(options=options), self.assertRaises(DomainError):
                trace_admin.list_conversations(self.domain, 't1', 'admin', **options)
        for options in ({'page_size': 0}, {'page': 1.5}):
            with self.subTest(options=options), self.assertRaises(DomainError):
                trace_admin.conversation_detail(self.domain, 't1', 'admin', 'c1', **options)
        self.assertEqual(trace_admin.list_conversations(self.domain, 't1', 'admin', page=9)['items'], [])
        self.assertEqual(trace_admin.conversation_detail(self.domain, 't1', 'admin', 'c1', page=9)['items'], [])
        self.assertEqual(trace_admin.conversation_detail(self.domain, 't1', 'admin', 'empty')['total'], 0)
        role = self.domain.save_role('t1', 'admin', '会话审计员', permissions=['traces.view'])
        self.domain.add_user('t1', 'admin', 'auditor', role_ids=[role['id']])
        self.assertEqual(trace_admin.list_conversations(self.domain, 't1', 'auditor')['total'], 2)
        self.domain.save_role('t1', 'admin', '会话审计员', permissions=['traces.view'], active=False, id=role['id'])
        for call in (lambda: trace_admin.list_conversations(self.domain, 't1', 'auditor'),
                     lambda: trace_admin.conversation_detail(self.domain, 't1', 'auditor', 'c1')):
            with self.assertRaises(DomainError): call()

    def test_conversation_http_routes_use_same_audit_permissions(self):
        from fastapi.testclient import TestClient
        from app.v3.api import create_app
        from app.v3.config import Settings
        settings = Settings(tenant_id='t1', mode='development', bootstrap_token='x'*40, objects_dir=Path(self.temp.name)/'objects')
        with TestClient(create_app(settings, self.domain)) as client:
            route = '/api/v3/admin/trace-conversations'
            self.assertEqual(client.get(route).status_code, 401)
            headers = {'Authorization': 'Bearer '+'x'*40}
            result = client.get(route, headers=headers)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()['total'], 2)
            result = client.get(route+'/c1?page_size=1', headers=headers)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()['items'][0]['id'], 'old')
            self.assertEqual(client.get(route+'/cx', headers=headers).status_code, 404)

    def test_conversation_list_stable_ties_and_empty_summary(self):
        with self.db.transaction('t1') as s:
            s.add(Conversation(id='c3', tenant_id='t1', owner='bob'))
            s.add(Message(id='tied', tenant_id='t1', owner='bob', conversation_id='c3', query='同一时刻', created_at=3))
        first = trace_admin.list_conversations(self.domain, 't1', 'admin', page_size=1)
        second = trace_admin.list_conversations(self.domain, 't1', 'admin', page=2, page_size=1)
        self.assertEqual([first['items'][0]['id'], second['items'][0]['id']], ['c3', 'c1'])
        empty = trace_admin.list_conversations(self.domain, 't1', 'admin', query='没有命中的关键词')
        self.assertEqual(empty['items'], [])
        self.assertEqual(empty['total'], 0)
        self.assertTrue(all(value == 0 for value in empty['summary'].values()))


if __name__ == '__main__':
    unittest.main()
