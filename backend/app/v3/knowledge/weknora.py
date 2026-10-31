from urllib.parse import quote
import hashlib
import httpx
from .transport import Transport
from .contracts import BackendError, BackendStatus, DeleteReceipt, Evidence, Submission, validate_scope


def encoded(value):
    return quote(str(value), safe='')


class WeKnoraBackend(Transport):
    @property
    def headers(self):
        return {'X-API-Key': self.key}

    async def prepare_collection(self, name):
        collection = self.options.get('collection_id')
        if not collection:
            raise BackendError('invalid_configuration','请先在 WeKnora 准备知识库并配置 collection_id')
        data = (await self.request('GET',f'/api/v1/knowledge-bases/{encoded(collection)}')).get('data') or {}
        if data.get('id') != collection or not data.get('embedding_model_id'):
            raise BackendError('invalid_configuration','WeKnora 知识库必须配置 Embedding 模型并允许当前凭据访问')
        return {'id':collection}

    async def submit(self, collection_id, filename, content, binding_id):
        import json
        data = await self.request('POST', f'/api/v1/knowledge-bases/{encoded(collection_id)}/knowledge/file',
            files={'file': (filename, content)}, data={'metadata':json.dumps({'v3_binding_id':binding_id})})
        return Submission(data['data']['id'])

    async def status(self, binding):
        data = (await self.request('GET', f'/api/v1/knowledge/{encoded(binding.remote_id)}'))['data']
        if data.get('knowledge_base_id') != binding.collection_id:
            raise BackendError('invalid_scope')
        raw = data.get('parse_status', '')
        chunks = 0
        if raw == 'completed':
            detail = await self.request('GET',f'/api/v1/chunks/{encoded(binding.remote_id)}',params={'page':1,'page_size':1})
            if detail.get('success') is not True or not isinstance(detail.get('total'),int):
                raise BackendError('invalid_response')
            chunks = detail['total']
        return BackendStatus('completed' if raw == 'completed' else 'failed' if raw in ('failed','cancelled') else 'processing', int(chunks or 0), raw)

    async def reconcile_submission(self, collection_id, proof):
        # Scan the complete collection. A truncated scan cannot prove uniqueness.
        candidates=[]; seen=set()
        for page in range(1,101):
            data=await self.request('GET',f'/api/v1/knowledge-bases/{encoded(collection_id)}/knowledge',
                params={'page':page,'page_size':100})
            items=data.get('data'); total=data.get('total')
            if data.get('success') is not True or not isinstance(items,list) or type(total) is not int:
                raise BackendError('invalid_response')
            for item in items:
                if not isinstance(item,dict) or not item.get('id') or item['id'] in seen:
                    raise BackendError('reconciliation_ambiguous')
                seen.add(item['id'])
                if (item.get('metadata') or {}).get('v3_binding_id')==proof['binding_id']:
                    candidates.append(item)
            if len(seen)==total: break
            if not items or len(seen)>total: raise BackendError('invalid_response')
        else: raise BackendError('reconciliation_scan_limit')
        if len(candidates)!=1: raise BackendError('reconciliation_ambiguous')
        remote=candidates[0]['id']
        detail=(await self.request('GET',f'/api/v1/knowledge/{encoded(remote)}')).get('data') or {}
        if (detail.get('id')!=remote or detail.get('knowledge_base_id')!=collection_id
                or (detail.get('metadata') or {}).get('v3_binding_id')!=proof['binding_id']):
            raise BackendError('invalid_scope')
        digest=hashlib.sha256(); size=0
        try:
            async with self.client.stream('GET',self.base_url+f'/api/v1/knowledge/{encoded(remote)}/download',
                    headers=self.headers,timeout=httpx.Timeout(30,connect=5),follow_redirects=False) as response:
                if response.status_code!=200: raise BackendError('reconciliation_original_unavailable')
                async for chunk in response.aiter_bytes():
                    size+=len(chunk)
                    if size>proof.get('size',64*1024*1024): raise BackendError('reconciliation_hash_mismatch')
                    digest.update(chunk)
        except httpx.HTTPError as error: raise BackendError('reconciliation_original_unavailable') from error
        if digest.hexdigest()!=proof['sha256'] or ('size' in proof and size!=proof['size']):
            raise BackendError('reconciliation_hash_mismatch')
        return Submission(remote)

    async def retrieve(self, query, scope, dispatch_gate=None):
        allowed = validate_scope(scope)
        if not allowed:
            return []
        collection = next(iter(allowed.values())).collection_id
        data = await self.request('POST', f'/api/v1/knowledge-bases/{encoded(collection)}/hybrid-search', dispatch_gate=dispatch_gate,json={
            'query_text':query, 'knowledge_ids':list(allowed), 'match_count':50,
            'disable_keywords_match':False, 'disable_vector_match':False, 'skip_context_enrichment':True})
        if data.get('success') is not True or not isinstance(data.get('data'), list):
            raise BackendError('invalid_response')
        result = []
        for item in data['data']:
            binding = allowed.get(item.get('knowledge_id'))
            if not binding:
                raise BackendError('invalid_scope')
            content = item.get('content')
            if not isinstance(content,str) or not content.strip() or not item.get('id'):
                raise BackendError('invalid_response')
            locators = item.get('source_locators') or []
            raw = locators[0] if locators and isinstance(locators[0],dict) else {}
            locator = {key:raw[key] for key in ('type','page','block','sheet','row_start','row_end','start','end','partial','mapping','source_hash') if key in raw}
            level = 'page' if isinstance(locator.get('page'),int) and locator['page'] > 0 and not locator.get('partial') else 'segment'
            result.append(Evidence(scope.tenant_id,binding.document_id,binding.version_id,binding.generation_id,
                binding.instance_id,collection,str(item['id']),content,float(item.get('score') or 0),level,locator))
        return result

    async def delete(self, binding):
        await self.request('DELETE', f'/api/v1/knowledge/{encoded(binding.remote_id)}')
        return DeleteReceipt(True)

    async def capabilities(self):
        return {'provider':'weknora','document_filter':True,'runtime_verified':False}

    async def deletion_confirmed(self, binding):
        try:
            data=await self.request('GET',f'/api/v1/knowledge/{encoded(binding.remote_id)}')
        except BackendError as error:
            if error.code=='not_found':return True
            raise
        item=data.get('data') or {}
        if item.get('id')!=binding.remote_id or item.get('knowledge_base_id')!=binding.collection_id:
            raise BackendError('invalid_scope')
        return False
