# 验证两种知识后端的协议及文档范围约束；全部 HTTP 响应由 MockTransport 提供，不验证真实平台可用性。
import unittest
import json
import httpx

from app.v3.knowledge.contracts import Binding, Scope, BackendError
from app.v3.knowledge.weknora import WeKnoraBackend
from app.v3.knowledge.dify import DifyBackend


class BackendContractTests(unittest.IsolatedAsyncioTestCase):
    def binding(self, provider='wk'):
        return Binding('t1', 'doc-a', 'version-a', 'gen-a', provider, 'collection-a', 'remote-a', 'binding-a')

    async def test_empty_scope_never_issues_request(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            for cls in (WeKnoraBackend, DifyBackend):
                backend = cls('https://kb.test', 'secret', client)
                self.assertEqual(await backend.retrieve('question', Scope('t1', ())), [])
        self.assertEqual(calls, [])

    async def test_weknora_sends_only_authorized_document_scope(self):
        observed = []
        def handler(request):
            observed.append(json.loads(request.content))
            return httpx.Response(200, json={'success': True, 'data': [{
                'id': 'chunk-a', 'knowledge_id': 'remote-a', 'content': 'Allowed evidence',
                'score': .7, 'source_locators': [{'type': 'pdf', 'page': 3}],
                'image_info': 'https://private.test/secret',
            }]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            evidence = await WeKnoraBackend('https://kb.test', 'secret', client).retrieve('question', Scope('t1', (self.binding(),)))
        self.assertEqual(observed[0]['knowledge_ids'], ['remote-a'])
        self.assertTrue(observed[0]['skip_context_enrichment'])
        self.assertNotIn('knowledge_base_ids', observed[0])
        self.assertEqual(evidence[0].document_id, 'doc-a')
        self.assertEqual(evidence[0].locator_level, 'page')
        self.assertNotIn('private.test', json.dumps(evidence[0].to_dict()))

    async def test_both_backends_fail_closed_on_unexpected_document(self):
        for cls, payload in [
            (WeKnoraBackend, {'success': True, 'data': [{'id': 'x','knowledge_id':'forbidden','content':'secret'}]}),
            (DifyBackend, {'records': [{'segment': {'id':'x','document_id':'forbidden','content':'secret'}}]}),
        ]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))) as client:
                with self.assertRaises(BackendError) as error:
                    await cls('https://kb.test', 'secret', client).retrieve('question', Scope('t1', (self.binding(),)))
                self.assertEqual(error.exception.code, 'invalid_scope')

    async def test_dify_filters_immutable_binding_and_does_not_invent_page(self):
        observed = []
        def handler(request):
            observed.append(json.loads(request.content))
            return httpx.Response(200, json={'records': [{'segment': {
                'id': 'chunk-a', 'document_id':'remote-a','position':9,'content':'Allowed evidence',
                'document':{'doc_metadata':{'v3_binding_id':'binding-a'}},
            }, 'score': .8, 'files':[{'source_url':'https://private.test/secret'}]}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            evidence = await DifyBackend('https://kb.test', 'secret', client).retrieve('question', Scope('t1', (self.binding('dify'),)))
        condition = observed[0]['retrieval_model']['metadata_filtering_conditions']['conditions'][0]
        self.assertEqual(condition, {'name':'v3_binding_id','comparison_operator':'in','value':['binding-a']})
        self.assertEqual(evidence[0].locator_level, 'segment')
        self.assertNotIn('page', evidence[0].locator)
        self.assertNotIn('private.test', json.dumps(evidence[0].to_dict()))

    async def test_dify_does_not_send_overlong_question_or_malformed_metadata(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: self.fail('must not call backend'))) as client:
            with self.assertRaises(BackendError):
                await DifyBackend('https://kb.test', 'secret', client).retrieve('x'*251, Scope('t1',(self.binding(),)))

    async def test_dify_delete_accepts_empty_204(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(204))) as client:
            receipt = await DifyBackend('https://kb.test', 'secret', client).delete(self.binding())
        self.assertTrue(receipt.accepted)
        self.assertFalse(receipt.confirmed)

    async def test_weknora_completion_requires_actual_chunk_total(self):
        def handler(request):
            if '/chunks/' in request.url.path:
                return httpx.Response(200,json={'success':True,'total':7,'data':[]})
            return httpx.Response(200,json={'data':{'id':'remote-a','knowledge_base_id':'collection-a','parse_status':'completed'}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            status=await WeKnoraBackend('https://kb.test','secret',client).status(self.binding())
        self.assertEqual(status.chunks,7)

    async def test_cross_tenant_and_duplicate_binding_rejected_before_network(self):
        binding = self.binding()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: self.fail('must not call backend'))) as client:
            backend = WeKnoraBackend('https://kb.test', 'secret', client)
            for scope in (Scope('other', (binding,)), Scope('t1',(binding,binding))):
                with self.assertRaises(BackendError):
                    await backend.retrieve('q', scope)

    async def test_weknora_collection_cannot_be_created_without_embedding_configuration(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200,json={'data':{'id':'kb'}}))) as client:
            with self.assertRaises(BackendError):
                await WeKnoraBackend('https://kb.test','secret',client).prepare_collection('test')

    async def test_prepared_collections_use_only_read_requests_and_check_configuration(self):
        for cls in (WeKnoraBackend,DifyBackend):
            calls=[]
            def handler(request):
                calls.append((request.method,request.url.path))
                if request.url.path.endswith('/metadata'):
                    return httpx.Response(200,json={'doc_metadata':[{'id':'meta','name':'v3_binding_id','type':'string'}]})
                payload={'id':'collection-a','embedding_model_id':'embedding','indexing_technique':'high_quality'}
                return httpx.Response(200,json={'data':payload} if cls is WeKnoraBackend else payload)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                result=await cls('https://kb.test','secret',client,{'collection_id':'collection-a'}).prepare_collection('ignored')
            self.assertEqual(result['id'],'collection-a')
            self.assertTrue(all(method=='GET' for method,_ in calls))
            if cls is DifyBackend:self.assertEqual(result['metadata_id'],'meta')

    async def test_dify_missing_binding_metadata_requires_administrator_preparation(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={
            'id':'collection-a','indexing_technique':'high_quality','doc_metadata':[]}))) as client:
            with self.assertRaises(BackendError):
                await DifyBackend('https://kb.test','secret',client,{'collection_id':'collection-a'}).prepare_collection('ignored')

    async def test_dify_reads_document_metadata_list_and_rejects_duplicate_names(self):
        for fields,success in [([{'name':'v3_binding_id','type':'string','value':'binding-a'}],True),
            ([{'name':'v3_binding_id','value':'binding-a'},{'name':'v3_binding_id','value':'other'}],False)]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'doc_metadata':fields}))) as client:
                backend=DifyBackend('https://kb.test','secret',client)
                if success:await backend.set_binding_metadata(self.binding(),'meta')
                else:
                    with self.assertRaises(BackendError):await backend.set_binding_metadata(self.binding(),'meta')
