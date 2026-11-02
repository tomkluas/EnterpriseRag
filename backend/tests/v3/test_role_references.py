# 复用临时 API 夹具验证角色引用查询与删除保护；关注成员、资料库和文档引用的完整性及读取权限。
import test_api
from app.v3.models import Role,UserRole,Space,Document


class RoleReferencesTests(test_api.ApiTests):
    def test_usage_can_be_viewed_without_attempting_deletion(self):
        role=self.domain.save_role('enterprise','admin','待清理角色',permissions=['knowledge.read'])['id']
        space=self.domain.create_space('enterprise','admin','具体知识库')
        self.domain.set_resource_roles('enterprise','admin',space,[role],[],space=True)
        response=self.client.get(f'/api/v3/roles/{role}/references',headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['counts'],{'member':0,'space':1,'document':0})
        self.assertEqual(response.json()['references'][0]['name'],'具体知识库')
        with self.db.transaction() as s:self.assertIsNotNone(s.get(Role,('enterprise',role)))
        self.assertEqual(self.client.get(f'/api/v3/roles/{role}/references').status_code,401)

    def test_delete_returns_all_member_library_and_document_references(self):
        role=self.domain.save_role('enterprise','admin','历史角色',permissions=['knowledge.read'])['id']
        self.domain.add_user('enterprise','admin','employee',role_ids=[role],display_name='张三',active=False)
        space=self.domain.create_space('enterprise','admin','制度库')
        doc=self.domain.receive('enterprise','admin',space,'制度.txt',b'test')['document_id']
        self.domain.set_resource_roles('enterprise','admin',space,[role],[role],space=True)
        self.domain.set_resource_roles('enterprise','admin',doc,[],[role],inherit=False)
        self.domain.bootstrap('other','boss')
        with self.db.transaction() as s:
            s.add(Space(id='foreign',tenant_id='other',owner='boss',name='外部机密库',readers=['role:'+role]))
        response=self.client.delete(f'/api/v3/roles/{role}',headers=self.headers)
        self.assertEqual(response.status_code,409)
        details=response.json().get('details',{})
        refs=details.get('references',[])
        self.assertEqual(len(refs),3,response.text)
        self.assertEqual({item['type'] for item in refs},{'member','space','document'})
        self.assertIn('张三',response.text);self.assertIn('制度库',response.text);self.assertIn('制度.txt',response.text)
        self.assertNotIn('外部机密库',response.text)
        self.assertEqual(next(item for item in refs if item['type']=='space')['rules'],['allow','deny'])
        with self.db.transaction() as s:self.assertIsNotNone(s.get(Role,('enterprise',role)))
        with self.db.transaction() as s:
            s.delete(s.get(UserRole,('enterprise','employee',role)))
        self.domain.set_resource_roles('enterprise','admin',space,[],[],space=True)
        self.domain.set_resource_roles('enterprise','admin',doc,[],[],inherit=False)
        self.assertEqual(self.client.delete(f'/api/v3/roles/{role}',headers=self.headers).status_code,200)

    def test_reader_cannot_obtain_reference_details(self):
        self.domain.add_user('enterprise','admin','reader',password='Reader-pass-2026')
        token=self.client.post('/api/v3/auth/login',json={'id':'reader','password':'Reader-pass-2026'}).json()['access_token']
        role=self.domain.save_role('enterprise','admin','业务角色',permissions=[])['id']
        usage=self.client.get(f'/api/v3/roles/{role}/references',headers={'Authorization':'Bearer '+token})
        self.assertEqual(usage.status_code,403)
        response=self.client.delete(f'/api/v3/roles/{role}',headers={'Authorization':'Bearer '+token})
        self.assertEqual(response.status_code,403)
        self.assertNotIn('references',response.json().get('details',{}))
