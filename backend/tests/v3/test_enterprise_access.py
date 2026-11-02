# 复用进程内 API 夹具验证组织、角色与文档授权；迁移和撤权均发生在临时库，不修改企业身份系统。
import test_api


class EnterpriseAccessTests(test_api.ApiTests):
    def role(self,name='销售资料',permissions=None):
        result=self.client.post('/api/v3/roles',headers=self.headers,json={
            'name':name,'description':'业务角色','permissions':permissions or ['knowledge.read'],'active':True})
        self.assertEqual(result.status_code,201,result.text)
        return result.json()['id']

    def test_custom_role_document_access_and_revocation(self):
        role=self.role()
        result=self.client.post('/api/v3/users',headers=self.headers,json={
            'id':'sales','role_ids':[role],'password':'Sales-pass-2026','active':True})
        self.assertEqual(result.status_code,200,result.text)
        space=self.domain.create_space('enterprise','admin','销售资料')
        doc=self.domain.receive('enterprise','admin',space,'sales.txt',b'sales')['document_id']
        response=self.client.post(f'/api/v3/documents/{doc}/grants',headers=self.headers,
            json={'role_ids':[role],'denied_role_ids':[],'inherit':False})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(self.domain.can_read('enterprise','sales',doc))
        self.assertFalse(self.domain.can_read('enterprise','admin',doc),'所有者不能绕过显式角色规则')
        self.client.post('/api/v3/roles',headers=self.headers,json={
            'id':role,'name':'销售资料','description':'','permissions':['knowledge.read'],'active':False})
        self.assertFalse(self.domain.can_read('enterprise','sales',doc))

    def test_organization_cycle_and_cross_tenant_role_are_rejected(self):
        root=self.client.get('/api/v3/organization',headers=self.headers)
        self.assertEqual(root.status_code,200,root.text)
        root_id=root.json()[0]['id']
        child=self.client.post('/api/v3/organization',headers=self.headers,json={
            'name':'销售部','parent_id':root_id,'active':True})
        self.assertEqual(child.status_code,201,child.text)
        cycle=self.client.post('/api/v3/organization',headers=self.headers,json={
            'id':root_id,'name':'企业','parent_id':child.json()['id'],'active':True})
        self.assertEqual(cycle.status_code,409)
        self.domain.bootstrap('other','boss')
        from app.v3.models import Role
        with self.db.transaction('other') as s:
            foreign=s.query(Role).filter_by(tenant_id='other',code='reader').one().id
        response=self.client.post('/api/v3/users',headers=self.headers,json={
            'id':'evil','role_ids':[foreign],'active':True})
        self.assertEqual(response.status_code,404)

    def test_last_administrator_cannot_be_removed(self):
        roles=self.client.get('/api/v3/roles',headers=self.headers)
        self.assertEqual(roles.status_code,200,roles.text)
        reader=next(role['id'] for role in roles.json() if role['code']=='reader')
        result=self.client.post('/api/v3/users',headers=self.headers,json={
            'id':'admin','role_ids':[reader],'active':True})
        self.assertEqual(result.status_code,409)

    def test_organization_does_not_grant_document_reading(self):
        role=self.role()
        org=self.client.get('/api/v3/organization',headers=self.headers).json()[0]['id']
        for name,roles in [('alice',[role]),('bob',[])]:
            self.assertEqual(self.client.post('/api/v3/users',headers=self.headers,json={
                'id':name,'org_unit_id':org,'role_ids':roles,'active':True}).status_code,200)
        space=self.domain.create_space('enterprise','admin','test')
        doc=self.domain.receive('enterprise','admin',space,'test.txt',b'test')['document_id']
        self.client.post(f'/api/v3/documents/{doc}/grants',headers=self.headers,
            json={'role_ids':[role],'denied_role_ids':[],'inherit':False})
        self.assertTrue(self.domain.can_read('enterprise','alice',doc))
        self.assertFalse(self.domain.can_read('enterprise','bob',doc))

    def test_legacy_migration_preserves_people_scope_and_is_repeatable(self):
        from app.v3.models import Tenant,Space,Document,Role,User,OrganizationUnit
        self.domain.add_user('enterprise','admin','alice','reader',['售后部'])
        self.domain.add_user('enterprise','admin','bob','reader')
        self.domain.add_user('enterprise','admin','second','admin')
        space=self.domain.create_space('enterprise','admin','legacy')
        received=self.domain.receive('enterprise','admin',space,'legacy.txt',b'legacy')
        with self.db.transaction() as s:
            s.get(Tenant,'enterprise').access_version=0
            s.get(Space,space).readers=['alice']
            doc=s.get(Document,received['document_id']);doc.readers=[];doc.denied=[]
            s.get(User,('enterprise','alice')).org_unit_id=''
        self.db.migrate()
        self.assertTrue(self.domain.can_read('enterprise','alice',received['document_id']))
        self.assertTrue(self.domain.can_read('enterprise','admin',received['document_id']))
        self.assertFalse(self.domain.can_read('enterprise','bob',received['document_id']))
        self.assertFalse(self.domain.can_read('enterprise','second',received['document_id']))
        with self.db.transaction() as s:
            count=s.query(Role).count()
            member=s.get(User,('enterprise','alice'))
            self.assertEqual(s.get(OrganizationUnit,('enterprise',member.org_unit_id)).name,'售后部')
        self.db.migrate()
        with self.db.transaction() as s:self.assertEqual(s.query(Role).count(),count)

    def test_explicit_deny_and_knowledge_base_inheritance(self):
        from app.v3.models import Role
        with self.db.transaction() as s:reader=s.query(Role).filter_by(tenant_id='enterprise',code='reader').one().id
        self.domain.add_user('enterprise','admin','employee')
        space=self.domain.create_space('enterprise','admin','shared',role_ids=[reader])
        doc=self.domain.receive('enterprise','admin',space,'test.txt',b'test')['document_id']
        self.assertTrue(self.domain.can_read('enterprise','employee',doc))
        self.domain.set_resource_roles('enterprise','admin',doc,[],[reader],inherit=True)
        self.assertFalse(self.domain.can_read('enterprise','employee',doc))
        self.domain.set_resource_roles('enterprise','admin',doc,[],[],inherit=False)
        self.assertFalse(self.domain.can_read('enterprise','employee',doc))
        extra=self.domain.save_role('enterprise','admin','补充范围',permissions=['knowledge.read'])
        self.domain.add_user('enterprise','admin','extra',role_ids=[extra['id']])
        self.domain.set_resource_roles('enterprise','admin',doc,[extra['id']],[],inherit=True)
        self.assertTrue(self.domain.can_read('enterprise','employee',doc))
        self.assertTrue(self.domain.can_read('enterprise','extra',doc))

    def test_old_role_prefix_and_group_named_owner_do_not_expand_access(self):
        from app.v3.models import Tenant,Space,Document
        from app.v3.access import stable_id
        collision='role:'+stable_id('enterprise','builtin:reader')
        self.domain.add_user('enterprise','admin',collision)
        self.domain.add_user('enterprise','admin','bob','reader',['销售组'])
        self.domain.add_user('enterprise','admin','group:销售组','editor')
        space=self.domain.create_space('enterprise','admin','collision')
        first=self.domain.receive('enterprise','admin',space,'one.txt',b'one')['document_id']
        second=self.domain.receive('enterprise','admin',space,'two.txt',b'two')['document_id']
        with self.db.transaction() as s:
            s.get(Tenant,'enterprise').access_version=0
            s.get(Space,space).readers=[]
            s.get(Document,first).readers=[collision]
            doc=s.get(Document,second);doc.readers=[];doc.owner='group:销售组'
        self.db.migrate()
        self.assertTrue(self.domain.can_read('enterprise',collision,first))
        self.assertFalse(self.domain.can_read('enterprise','bob',first))
        self.assertTrue(self.domain.can_read('enterprise','group:销售组',second))
        self.assertFalse(self.domain.can_read('enterprise','bob',second))

    def test_company_root_name_stays_in_sync_and_org_permission_is_limited(self):
        root=self.client.get('/api/v3/organization',headers=self.headers).json()[0]
        response=self.client.post('/api/v3/organization',headers=self.headers,json={
            'id':root['id'],'name':'绕过公司管理','parent_id':'','active':True})
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.client.put('/api/v3/tenant',headers=self.headers,json={'name':'测试公司'}).status_code,200)
        self.assertEqual(self.client.get('/api/v3/organization',headers=self.headers).json()[0]['name'],'测试公司')
        role=self.role('组织维护',['organization.manage'])
        self.domain.add_user('enterprise','admin','org-manager',password='Organization-2026',role_ids=[role])
        login=self.client.post('/api/v3/auth/login',json={'id':'org-manager','password':'Organization-2026'}).json()
        headers={'Authorization':'Bearer '+login['access_token']}
        self.assertEqual(self.client.get('/api/v3/organization',headers=headers).status_code,200)
        for route in ('/api/v3/roles','/api/v3/users','/api/v3/admin/traces'):
            self.assertEqual(self.client.get(route,headers=headers).status_code,403,route)
        self.assertEqual(self.client.put('/api/v3/tenant',headers=headers,json={'name':'无权限修改'}).status_code,403)

    def test_migration_validation_can_abort_before_commit(self):
        from app.v3.models import Tenant
        with self.db.transaction() as s:s.get(Tenant,'enterprise').access_version=0
        def reject(session):raise RuntimeError('scope mismatch')
        with self.assertRaisesRegex(RuntimeError,'scope mismatch'):self.db.migrate(validate=reject)
        with self.db.transaction() as s:self.assertEqual(s.get(Tenant,'enterprise').access_version,0)
