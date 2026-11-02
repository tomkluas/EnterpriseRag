# 复用临时领域夹具验证后端切换和草稿取消；人工构造就绪副本，关注清单与修订号的一致性，不执行真实复制。
from test_domain import DomainFixture
from app.v3.domain import DomainError, uid
from app.v3.models import BackendInstance, Replica, Space, Tenant, User, Job, Version


class SwitchTests(DomainFixture):
    def test_cancel_failed_draft_preserves_current_and_invalidates_old_switch(self):
        wk=self.prepare_backend('weknora');dify=self.prepare_backend('dify')
        sw=self.domain.prepare_switch('t1','admin',self.space,wk);self.domain.activate_switch('t1','admin',sw,0)
        old=self.domain.receive('t1','admin',self.space,'old.txt',b'old')
        self.ready(old['version_id'],wk);self.ready(old['version_id'],dify)
        self.domain.publish('t1','admin',old['version_id'])
        draft=self.domain.receive('t1','admin',self.space,'broken.docx',b'broken',document_id=old['document_id'])
        with self.db.transaction('t1') as s:
            s.get(Job,draft['job_id']).state='failed'
            s.get(Version,draft['version_id']).state='failed'
        before=self.domain.prepare_switch('t1','admin',self.space,dify)
        self.domain.cancel_version('t1','admin',draft['document_id'],draft['version_id'])
        with self.assertRaises(DomainError):self.domain.activate_switch('t1','admin',before,1)
        after=self.domain.prepare_switch('t1','admin',self.space,dify)
        self.assertEqual(self.domain.switch_status('t1','admin',after)['total'],1)
        self.domain.activate_switch('t1','admin',after,1)
        self.assertEqual(self.domain.scope('t1','admin',self.space).bindings[0].version_id,old['version_id'])
        self.assertEqual(self.domain.read_original('t1','admin',old['document_id']),b'old')
        with self.assertRaises(DomainError):self.domain.publish('t1','admin',draft['version_id'])
        with self.db.transaction('t1') as s:
            self.assertEqual(s.get(Job,draft['job_id']).state,'cancelled')
            self.assertEqual(s.get(Version,draft['version_id']).state,'cancelled')

    def test_cancel_guards_permissions_published_and_unknown_or_running_jobs(self):
        self.domain.add_user('t1','admin','reader')
        draft=self.domain.receive('t1','admin',self.space,'draft.txt',b'draft')
        with self.db.transaction('t1') as s:s.get(Job,draft['job_id']).state='failed'
        with self.assertRaises(DomainError):self.domain.cancel_version('t1','reader',draft['document_id'],draft['version_id'])
        for state in ['running','needs_reconciliation']:
            with self.db.transaction('t1') as s:s.get(Job,draft['job_id']).state=state
            with self.assertRaises(DomainError):self.domain.cancel_version('t1','admin',draft['document_id'],draft['version_id'])
        with self.db.transaction('t1') as s:
            s.get(Job,draft['job_id']).state='failed';s.get(Version,draft['version_id']).state='published'
        with self.assertRaises(DomainError):self.domain.cancel_version('t1','admin',draft['document_id'],draft['version_id'])

    def test_target_format_limit_is_checked_before_copy_jobs_and_again_at_activation(self):
        wk=self.prepare_backend('weknora');dify=self.prepare_backend('dify')
        start=self.domain.prepare_switch('t1','admin',self.space,wk);self.domain.activate_switch('t1','admin',start,0)
        draft=self.domain.receive('t1','admin',self.space,'report.xlsx',b'synthetic spreadsheet')
        with self.db.transaction('t1') as s:s.get(BackendInstance,dify).report={'allowed_formats':['.txt']}
        with self.assertRaises(DomainError):self.domain.prepare_switch('t1','admin',self.space,dify)
        with self.db.transaction('t1') as s:s.get(BackendInstance,dify).report={'allowed_formats':['.xlsx']}
        target=self.domain.prepare_switch('t1','admin',self.space,dify);self.ready(draft['version_id'],dify)
        with self.db.transaction('t1') as s:s.get(BackendInstance,dify).report={'allowed_formats':['.txt']}
        with self.assertRaises(DomainError):self.domain.activate_switch('t1','admin',target,1)

    def test_switch_includes_ready_draft_and_new_draft_invalidates_manifest(self):
        wk=self.prepare_backend('weknora');dify=self.prepare_backend('dify')
        first=self.domain.prepare_switch('t1','admin',self.space,wk);self.domain.activate_switch('t1','admin',first,0)
        draft=self.domain.receive('t1','admin',self.space,'draft.txt',b'draft');self.ready(draft['version_id'],wk)
        target=self.domain.prepare_switch('t1','admin',self.space,dify)
        self.assertEqual(self.domain.switch_status('t1','admin',target)['total'],1)
        with self.assertRaises(DomainError):self.domain.activate_switch('t1','admin',target,1)
        self.ready(draft['version_id'],dify);self.domain.activate_switch('t1','admin',target,1)
        self.domain.publish('t1','admin',draft['version_id'])
        back=self.domain.prepare_switch('t1','admin',self.space,wk)
        self.domain.receive('t1','admin',self.space,'next.txt',b'next')
        with self.assertRaises(DomainError):self.domain.activate_switch('t1','admin',back,2)

    def prepare_backend(self,provider):
        instance=self.domain.register_backend('t1','admin',provider,'https://kb.test','V3_SECRET_TEST')
        with self.db.transaction('t1') as s:
            backend=s.get(BackendInstance,instance);backend.verified=True;backend.capabilities=['segment']
        return instance

    def ready(self,version,instance):
        with self.db.transaction('t1') as s:
            s.add(Replica(id=uid(),tenant_id='t1',version_id=version,instance_id=instance,
                collection_id='kb-'+uid(),remote_id='doc-'+uid(),state='ready',chunks=3))

    def test_roundtrip_keeps_document_and_requires_ready_target(self):
        wk=self.prepare_backend('weknora');dify=self.prepare_backend('dify')
        switch=self.domain.prepare_switch('t1','admin',self.space,wk)
        self.domain.activate_switch('t1','admin',switch,0)
        received=self.domain.receive('t1','admin',self.space,'policy.txt',b'policy')
        self.ready(received['version_id'],wk);self.domain.publish('t1','admin',received['version_id'])
        target=self.domain.prepare_switch('t1','admin',self.space,dify)
        with self.assertRaises(DomainError):self.domain.activate_switch('t1','admin',target,1)
        self.ready(received['version_id'],dify);self.domain.activate_switch('t1','admin',target,1)
        self.assertEqual(self.domain.scope('t1','admin',self.space).bindings[0].instance_id,dify)
        back=self.domain.prepare_switch('t1','admin',self.space,wk);self.domain.activate_switch('t1','admin',back,2)
        self.assertEqual(self.domain.scope('t1','admin',self.space).bindings[0].document_id,received['document_id'])

    def test_revocation_invalidates_prepared_switch(self):
        wk=self.prepare_backend('weknora');switch=self.domain.prepare_switch('t1','admin',self.space,wk)
        self.domain.add_user('t1','admin','reader')
        with self.assertRaises(DomainError):self.domain.activate_switch('t1','admin',switch,0)

    def test_required_page_capability_blocks_segment_only_backend(self):
        wk=self.prepare_backend('weknora')
        with self.db.transaction('t1') as s:s.get(Space,self.space).required=['page']
        switch=self.domain.prepare_switch('t1','admin',self.space,wk)
        with self.assertRaises(DomainError):self.domain.activate_switch('t1','admin',switch,0)

    def test_update_filename_is_version_specific(self):
        first=self.domain.receive('t1','admin',self.space,'policy.txt',b'policy')
        updated=self.domain.receive('t1','admin',self.space,'renamed.md',b'new policy',document_id=first['document_id'])
        versions=self.domain.list_documents('t1','admin',self.space)[0]['versions']
        self.assertEqual(next(v['filename'] for v in versions if v['id']==updated['version_id']),'renamed.md')
