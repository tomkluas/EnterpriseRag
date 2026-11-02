# 验证任务分块预览的真实授权链及协议边界，使用临时数据库和模拟 HTTP，不读取实际资料。
import asyncio
import os
import unittest
from unittest.mock import patch

import httpx

import test_domain as fixtures
from app.v3.domain import DomainError
from app.v3.models import BackendInstance, Document, Job, Replica, Space, Version
from app.v3.knowledge.contracts import BackendError, Binding
from app.v3.knowledge.dify import DifyBackend
from app.v3.knowledge.weknora import WeKnoraBackend
from app.v3.knowledge.registry import BackendRegistry


class ChunkProtocolTests(unittest.IsolatedAsyncioTestCase):
    def binding(self):
        return Binding('t1', 'doc', 'version', 'replica', 'instance', 'collection', 'remote', 'replica')

    async def test_weknora_page_checks_document_and_collection_and_returns_only_preview_fields(self):
        requests = []
        def handler(request):
            requests.append(request)
            if '/chunks/' not in request.url.path:
                return httpx.Response(200, json={'data': {'id':'remote','knowledge_base_id':'collection'}})
            return httpx.Response(200, json={'success':True,'page':2,'page_size':1,'total':3,'data':[
                {'id':'chunk-2','knowledge_id':'remote','knowledge_base_id':'collection','content':'实际分块正文',
                 'chunk_index':1,'chunk_type':'text','is_enabled':True,'index_status':'ready',
                 'source_locators':[{'page':2,'type':'pdf','private_url':'不应返回'}], 'image_info':'不应返回'}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page = await WeKnoraBackend('https://wk.test','secret',client).list_chunks(self.binding(),page=2,page_size=1)
        self.assertEqual(page['total'],3)
        self.assertEqual(page['items'][0]['content'],'实际分块正文')
        self.assertEqual(page['items'][0]['position'],2)
        self.assertEqual(page['items'][0]['locator'],{'page':2,'type':'pdf'})
        self.assertNotIn('不应返回',str(page))
        self.assertEqual(requests[-1].url.params['page'],'2')
        self.assertTrue(all(request.method=='GET' for request in requests))

    async def test_dify_page_checks_metadata_and_does_not_invent_page_locator(self):
        requests=[]
        def handler(request):
            requests.append(request)
            if not request.url.path.endswith('/segments'):
                return httpx.Response(200,json={'id':'remote','doc_metadata':{'v3_binding_id':'replica'}})
            return httpx.Response(200,json={'total':3,'page':2,'limit':1,'has_more':True,'data':[
                {'id':'segment','document_id':'remote','content':'实际正文','position':2,'status':'completed','enabled':True,
                 'word_count':4,'tokens':5,'created_by':'不应返回','sign_content':'不应返回'}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            page=await DifyBackend('https://dify.test','secret',client).list_chunks(self.binding(),page=2,page_size=1)
        self.assertEqual(page['items'][0]['position'],2)
        self.assertEqual(page['items'][0]['locator'],{})
        self.assertNotIn('不应返回',str(page))
        self.assertEqual(requests[-1].url.params['limit'],'1')

    async def test_both_backends_reject_foreign_chunks_and_invalid_pagination(self):
        for cls in (WeKnoraBackend,DifyBackend):
            def handler(request):
                if request.url.path.endswith('/segments') or '/chunks/' in request.url.path:
                    return httpx.Response(200,json={'success':True,'total':1,'page':1,'limit':20,'page_size':20,'has_more':False,
                        'data':[{'id':'wrong','knowledge_id':'other','knowledge_base_id':'collection','document_id':'other','content':'不能泄露'}]})
                return httpx.Response(200,json={'data':{'id':'remote','knowledge_base_id':'collection'},'id':'remote','doc_metadata':{'v3_binding_id':'replica'}})
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                backend=cls('https://kb.test','secret',client)
                with self.assertRaises(BackendError):await backend.list_chunks(self.binding())
                for options in ({'page':0},{'page_size':21},{'page':True}):
                    with self.assertRaises(BackendError):await backend.list_chunks(self.binding(),**options)

    async def test_dify_rejects_wrong_binding_before_reading_chunks(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'id':'remote','doc_metadata':{'v3_binding_id':'other'}}))) as client:
            with self.assertRaises(BackendError) as error:
                await DifyBackend('https://dify.test','secret',client).list_chunks(self.binding())
        self.assertEqual(error.exception.code,'invalid_scope')

    async def test_chunk_page_rejects_bad_totals_duplicates_and_nested_metadata(self):
        from app.v3.knowledge.chunks import chunk_page
        row={'id':'a','document_id':'remote','content':'正文','status':'completed','position':1}
        for data in ({'total':True,'data':[row]}, {'total':2,'data':[row,row]},
                     {'total':1,'data':[{**row,'status':{'secret':'不得透传'}}]},
                     {'total':1,'page':3,'data':[row]}):
            with self.subTest(data=data),self.assertRaises(BackendError):
                chunk_page(data,self.binding(),'dify',1,20)
        page=chunk_page({'total':1,'data':[{**row,'content':'字'*21000}]},self.binding(),'dify',1,20)
        self.assertTrue(page['items'][0]['truncated'])
        self.assertEqual(page['items'][0]['characters'],21000)
        self.assertEqual(len(page['items'][0]['content']),20000)


class JobInspectionTests(fixtures.DomainFixture):
    def setUp(self):
        super().setUp()
        self.received=self.domain.receive('t1','admin',self.space,'差旅制度.txt',b'policy')
        self.backend=self.domain.register_backend('t1','admin','weknora','https://wk.test','V3_SECRET_PREVIEW')
        with self.db.transaction('t1') as s:
            s.get(BackendInstance,self.backend).verified=True
            s.get(Job,self.received['job_id']).payload={**s.get(Job,self.received['job_id']).payload,'instance_id':self.backend}
            s.add(Replica(id='replica',tenant_id='t1',version_id=self.received['version_id'],instance_id=self.backend,
                          collection_id='collection',remote_id='remote',state='ready',chunks=3))
        self.secrets=patch.dict(os.environ,{'V3_SECRET_PREVIEW':'secret'});self.secrets.start()

    def tearDown(self):
        self.secrets.stop();super().tearDown()

    async def preview(self, *, user='admin', replica='replica', tenant='t1', handler=None):
        from app.v3.jobs.inspection import preview_chunks
        def default(request):
            if '/chunks/' not in request.url.path:
                return httpx.Response(200,json={'data':{'id':'remote','knowledge_base_id':'collection'}})
            return httpx.Response(200,json={'success':True,'total':1,'data':[{'id':'c','knowledge_id':'remote','knowledge_base_id':'collection','content':'已授权正文','chunk_index':0}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler or default)) as client:
            return await preview_chunks(self.domain,BackendRegistry(client),tenant,user,self.received['job_id'],replica)

    def test_preview_uses_actual_job_file_and_a_separate_content_permission(self):
        result=asyncio.run(self.preview())
        self.assertEqual(result['items'][0]['content'],'已授权正文')
        self.assertEqual(result['filename'],'差旅制度.txt')
        self.domain.set_resource_roles('t1','admin',self.received['document_id'],[],[],inherit=False)
        self.assertTrue(self.domain.list_jobs('t1','admin'))
        with self.assertRaises(DomainError) as error:asyncio.run(self.preview())
        self.assertEqual(error.exception.status,403)

    def test_other_user_tenant_unrelated_replica_and_withdrawal_are_rejected_before_network(self):
        self.domain.add_user('t1','admin','reader')
        for options in ({'user':'reader'},{'tenant':'t2','user':'other'},{'replica':'missing'}):
            with self.subTest(options=options),self.assertRaises(DomainError):
                asyncio.run(self.preview(**options,handler=lambda r:self.fail('无权访问时不得外发请求')))
        self.domain.withdraw('t1','admin',self.received['document_id'])
        with self.assertRaises(DomainError):asyncio.run(self.preview(handler=lambda r:self.fail('撤回后不得外发请求')))

    def test_replica_for_another_target_cannot_be_selected_through_a_visible_job(self):
        other=self.domain.register_backend('t1','admin','weknora','https://other.test','V3_SECRET_PREVIEW')
        with self.db.transaction('t1') as s:
            s.get(BackendInstance,other).verified=True
            s.add(Replica(id='wrong-target',tenant_id='t1',version_id=self.received['version_id'],instance_id=other,
                          collection_id='other',remote_id='other',state='ready',chunks=5))
        with self.assertRaises(DomainError) as error:
            asyncio.run(self.preview(replica='wrong-target',handler=lambda r:self.fail('目标不匹配不得外发')))
        self.assertEqual(error.exception.status,404)

    def test_permission_revoked_during_upstream_request_hides_returned_body(self):
        def handler(request):
            if '/chunks/' not in request.url.path:
                return httpx.Response(200,json={'data':{'id':'remote','knowledge_base_id':'collection'}})
            self.domain.set_resource_roles('t1','admin',self.received['document_id'],[],[],inherit=False)
            return httpx.Response(200,json={'success':True,'total':1,'data':[{'id':'c','knowledge_id':'remote','knowledge_base_id':'collection','content':'撤权后不能返回','chunk_index':0}]})
        with self.assertRaises(DomainError) as error:asyncio.run(self.preview(handler=handler))
        self.assertEqual(error.exception.status,403)

    def test_job_summary_exposes_preview_option_and_observed_status_without_claiming_unknown_count(self):
        with self.db.transaction('t1') as s:
            job=s.get(Job,self.received['job_id'])
            job.payload={**job.payload,'index_status':{'replica_id':'replica','state':'completed','raw_state':'completed','observed_at':123}}
        item=self.domain.list_jobs('t1','admin')[0]['index_results'][0]
        self.assertTrue(item['can_preview'])
        self.assertEqual(item['observed_at'],123)
        with self.db.transaction('t1') as s:s.get(Replica,'replica').state='backend_processing';s.get(Replica,'replica').chunks=0
        item=self.domain.list_jobs('t1','admin')[0]['index_results'][0]
        self.assertFalse(item['can_preview'])
        self.assertIsNone(item['chunks'])

    def test_http_preview_is_authenticated_read_only_and_not_cached(self):
        from fastapi.testclient import TestClient
        from app.v3.api import create_app
        from app.v3.config import Settings
        settings=Settings(tenant_id='t1',mode='development',bootstrap_token='x'*40,objects_dir=self.domain.objects.root)
        route=f"/api/v3/jobs/{self.received['job_id']}/replicas/replica/chunks"
        async def response(backend,binding,**kwargs):
            async with kwargs['dispatch_gate']():pass
            return {'items':[],'total':0,'page':1,'page_size':20,'has_more':False}
        with patch.object(WeKnoraBackend,'list_chunks',response),TestClient(create_app(settings,self.domain)) as client:
            self.assertEqual(client.get(route).status_code,401)
            headers={'Authorization':'Bearer '+'x'*40}
            result=client.get(route,headers=headers)
            self.assertEqual(result.status_code,200)
            self.assertIn('no-store',result.headers.get('Cache-Control',''))
            self.assertEqual(result.json()['filename'],'差旅制度.txt')
            self.assertEqual(client.get(route+'?page=0',headers=headers).status_code,400)
            self.assertEqual(client.post(route,headers=headers).status_code,405)
