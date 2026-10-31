import tempfile
import unittest
from pathlib import Path
from app.v3.db import Database
from app.v3.domain import Domain, DomainError


class DomainFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database('sqlite:///' + str(Path(self.temp.name) / 'test.db'), testing=True)
        self.db.migrate()
        self.domain = Domain(self.db, Path(self.temp.name)/'objects')
        self.domain.bootstrap('t1','admin')
        self.domain.bootstrap('t2','other')
        self.space = self.domain.create_space('t1','admin','人事制度')

    def tearDown(self):
        self.db.engine.dispose()
        self.temp.cleanup()


class DomainTests(DomainFixture):
    def test_office_formats_require_actual_provider_probe_and_pilot_cannot_claim_pages(self):
        self.domain.development=True
        backend=self.domain.register_backend('t1','admin','weknora','https://kb.test','V3_SECRET_TEST')
        report={'profile':'local_text_pilot','audit_level':'runtime_local_pilot','instance_id':backend,
            'commit':'f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7','image_digest':'sha256:test',
            'approved_by':'local-test','allowed_formats':['.txt','.pdf'],'capabilities':['segment'],
            'checks':[{'id':f'LOCAL-{i:02}','status':'passed'} for i in range(1,9)]}
        with self.assertRaises(DomainError):self.domain.accept_validation('t1','admin',backend,report)
        report['format_checks']=[{'provider':'weknora','extension':'.pdf','status':'passed','fact_preserved':True}]
        self.domain.accept_validation('t1','admin',backend,report)
        report['capabilities']=['page']
        with self.assertRaises(DomainError):self.domain.accept_validation('t1','admin',backend,report)

    def test_local_pilot_qualification_never_enables_production(self):
        from app.v3.models import BackendInstance
        backend=self.domain.register_backend('t1','admin','weknora','https://kb.test','V3_SECRET_TEST')
        report={'profile':'local_text_pilot','audit_level':'runtime_local_pilot','instance_id':backend,
            'commit':'f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7','image_digest':'sha256:test',
            'approved_by':'local-test','allowed_formats':['.txt','.md'],'capabilities':['segment'],
            'checks':[{'id':f'LOCAL-{i:02}','status':'passed'} for i in range(1,9)]}
        with self.assertRaises(DomainError):self.domain.accept_validation('t1','admin',backend,report)
        self.domain.development=True;self.domain.accept_validation('t1','admin',backend,report)
        with self.db.transaction('t1') as s:
            instance=s.get(BackendInstance,backend)
            self.assertFalse(instance.verified);self.assertTrue(self.domain.backend_usable(instance))
        task=self.domain.prepare_switch('t1','admin',self.space,backend);self.domain.activate_switch('t1','admin',task,0)
        with self.assertRaises(DomainError):self.domain.receive('t1','admin',self.space,'unverified.pdf',b'%PDF-test')
        self.domain.development=False
        with self.assertRaises(DomainError):self.domain.scope('t1','admin',self.space)

    def test_idempotency_key_rejects_another_target_document(self):
        a=self.domain.receive('t1','admin',self.space,'a.txt',b'a')
        b=self.domain.receive('t1','admin',self.space,'b.txt',b'b')
        self.domain.receive('t1','admin',self.space,'update.txt',b'same','same-key',a['document_id'])
        with self.assertRaises(DomainError):
            self.domain.receive('t1','admin',self.space,'update.txt',b'same','same-key',b['document_id'])

    def test_migration_upgrades_v3_schema_without_dropping_records(self):
        from sqlalchemy import text
        instance=self.domain.register_backend('t1','admin','dify','https://example.test','V3_SECRET_A')
        with self.db.engine.begin() as connection:
            connection.execute(text('ALTER TABLE v3_backends DROP COLUMN collection_config'))
        self.db.migrate()
        backends=self.domain.list_backends('t1','admin')
        self.assertEqual(backends[0]['id'],instance)
        self.assertEqual(backends[0]['collection_config'],{})


    def test_tenant_boundary_and_default_deny(self):
        self.assertEqual(self.domain.list_spaces('t2','other'), [])
        with self.assertRaises(DomainError):
            self.domain.receive('t2','other',self.space,'secret.txt',b'private')

    def test_upload_is_durable_and_duplicate_request_is_idempotent(self):
        first = self.domain.receive('t1','admin',self.space,'../policy.txt',b'policy','request-1')
        second = self.domain.receive('t1','admin',self.space,'../policy.txt',b'policy','request-1')
        self.assertEqual(first,second)
        self.assertEqual(self.domain.read_original('t1','admin',first['document_id']),b'policy')
        self.assertEqual(len(self.domain.list_jobs('t1','admin')),1)

    def test_unready_version_cannot_be_published(self):
        received = self.domain.receive('t1','admin',self.space,'a.txt',b'policy')
        with self.assertRaises(DomainError):
            self.domain.publish('t1','admin',received['version_id'])

    def test_lease_token_prevents_stale_worker_completion(self):
        self.domain.receive('t1','admin',self.space,'a.txt',b'policy')
        first = self.domain.claim_job('worker1',now=100,lease_seconds=10)
        second = self.domain.claim_job('worker2',now=111,lease_seconds=10)
        self.assertNotEqual(first['lease_token'],second['lease_token'])
        with self.assertRaises(DomainError):
            self.domain.complete_job(first['id'], first['lease_token'],now=112)
        self.domain.complete_job(second['id'], second['lease_token'],now=112)

    def test_unknown_backend_cannot_be_selected(self):
        with self.assertRaises(DomainError):
            self.domain.register_backend('t1','admin','unknown','https://example.test','V3_SECRET_A')

    def test_backend_unverified_cannot_activate(self):
        instance = self.domain.register_backend('t1','admin','dify','https://example.test','V3_SECRET_A')
        switch = self.domain.prepare_switch('t1','admin',self.space,instance)
        with self.assertRaises(DomainError):
            self.domain.activate_switch('t1','admin',switch,0)

    def test_withdrawal_hides_original_and_changes_revision(self):
        received = self.domain.receive('t1','admin',self.space,'a.txt',b'policy')
        before = self.domain.revisions('t1')
        self.domain.withdraw('t1','admin',received['document_id'])
        after = self.domain.revisions('t1')
        self.assertGreater(after['authz'],before['authz'])
        with self.assertRaises(DomainError):
            self.domain.read_original('t1','admin',received['document_id'])

    def test_admin_role_alone_does_not_grant_document_read(self):
        received = self.domain.receive('t1','admin',self.space,'a.txt',b'policy')
        self.domain.add_user('t1','admin','second','admin')
        self.assertFalse(self.domain.can_read('t1','second',received['document_id']))

    def test_unverified_import_cannot_forge_runtime_validation(self):
        instance = self.domain.register_backend('t1','admin','dify','https://example.test','V3_SECRET_A')
        with self.assertRaises(DomainError):
            self.domain.accept_validation('t1','admin',instance,{'checks':[]})

    def test_unpublished_original_is_visible_only_to_maintainer(self):
        received=self.domain.receive('t1','admin',self.space,'draft.txt',b'unpublished salary')
        self.domain.add_user('t1','admin','reader')
        self.domain.grant('t1','admin',received['document_id'],['reader'],[])
        with self.assertRaises(DomainError):
            self.domain.read_original('t1','reader',received['document_id'],received['version_id'])
        self.assertEqual(self.domain.list_documents('t1','reader',self.space),[])

    def test_document_grantee_can_discover_space_with_published_documents(self):
        from app.v3.models import Document, Version
        received=self.domain.receive('t1','admin',self.space,'a.txt',b'policy')
        self.domain.add_user('t1','admin','reader')
        self.domain.grant('t1','admin',received['document_id'],['reader'],[])
        with self.db.transaction('t1') as s:
            s.get(Document,received['document_id']).current_version=received['version_id']
            s.get(Version,received['version_id']).state='published'
        self.assertEqual([s['id'] for s in self.domain.list_spaces('t1','reader')],[self.space])

    def test_history_hides_answers_with_revoked_transitive_dependencies(self):
        from app.v3.models import Message
        received=self.domain.receive('t1','admin',self.space,'a.txt',b'secret')
        conversation=self.domain.create_conversation('t1','admin')
        with self.db.transaction('t1') as s:
            message=Message(id='derived',tenant_id='t1',owner='admin',conversation_id=conversation,query='explain',answer='secret',state='completed',evidence=[])
            message.dependencies=[{'document_id':received['document_id']}]
            s.add(message)
        self.domain.withdraw('t1','admin',received['document_id'])
        self.assertEqual(self.domain.history('t1','admin',conversation)[0]['state'],'restricted')

    def test_corrupted_original_is_not_served(self):
        from app.v3.models import Version
        received=self.domain.receive('t1','admin',self.space,'a.txt',b'original')
        with self.db.transaction('t1') as s:key=s.get(Version,received['version_id']).object_key
        (self.domain.objects.root/key).write_bytes(b'corrupted')
        with self.assertRaises(DomainError):self.domain.read_original('t1','admin',received['document_id'])
