import unittest
import time
from unittest.mock import patch
from app.v3.models import Job,Replica
import test_api as fixtures


class OperationsTests(unittest.TestCase):
    setUp=fixtures.ApiTests.setUp
    tearDown=fixtures.ApiTests.tearDown

    def test_operations_requires_admin_and_reports_actionable_counts_without_content(self):
        self.assertEqual(self.client.get('/api/v3/operations').status_code,401)
        space=self.domain.create_space('enterprise','admin','test')
        received=self.domain.receive('enterprise','admin',space,'private.txt',b'secret body')
        with self.db.transaction('enterprise') as s:
            s.get(Job,received['job_id']).state='needs_reconciliation'
            s.add(Replica(id='pending',tenant_id='enterprise',version_id=received['version_id'],instance_id='i',
                state='delete_requested',remote_id='private-remote'))
        response=self.client.get('/api/v3/operations',headers=self.headers)
        self.assertEqual(response.status_code,200)
        result=response.json()
        self.assertEqual(result['jobs']['needs_reconciliation'],1)
        self.assertEqual(result['bindings']['delete_requested'],1)
        self.assertIn('unknown_remote_results',result['alerts'])
        self.assertIn('unconfirmed_remote_deletions',result['alerts'])
        self.assertNotIn('secret body',response.text)
        self.assertNotIn('private-remote',response.text)
        self.domain.add_user('enterprise','admin','admin',role='reader')
        self.assertEqual(self.client.get('/api/v3/operations',headers=self.headers).status_code,403)

    def test_readiness_is_database_probe_and_failure_is_sanitized(self):
        self.assertEqual(self.client.get('/health/ready').status_code,200)
        with patch.object(self.db,'transaction',side_effect=RuntimeError('password=do-not-disclose')):
            response=self.client.get('/health/ready')
        self.assertEqual(response.status_code,503)
        self.assertNotIn('do-not-disclose',response.text)

    def test_overdue_and_expired_work_are_visible(self):
        with self.db.transaction('enterprise') as s:
            s.add(Job(id='queued',tenant_id='enterprise',owner='admin',kind='ingest',idempotency_key='q',payload={},next_run=time.time()-400))
            s.add(Job(id='expired',tenant_id='enterprise',owner='admin',kind='ingest',idempotency_key='x',payload={},state='running',lease_until=time.time()-1))
        result=self.client.get('/api/v3/operations',headers=self.headers).json()
        self.assertEqual(result['expired_leases'],1)
        self.assertGreaterEqual(result['oldest_due_seconds'],399)
        self.assertIn('overdue_queue',result['alerts'])

    def test_other_tenant_and_immediate_queue_do_not_inflate_age_or_alerts(self):
        with self.db.transaction('enterprise') as s:
            s.add(Job(id='other',tenant_id='other',owner='admin',kind='ingest',idempotency_key='other',payload={},state='needs_reconciliation'))
            s.add(Job(id='immediate',tenant_id='enterprise',owner='admin',kind='ingest',idempotency_key='now',payload={},created_at=0,next_run=0))
        result=self.client.get('/api/v3/operations',headers=self.headers).json()
        self.assertEqual(result['jobs'],{'queued':1})
        self.assertEqual(result['queued_without_age'],1)
        self.assertIsNone(result['oldest_due_seconds'])
        self.assertEqual(result['alerts'],[])
