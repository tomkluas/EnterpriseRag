# WeKnora 协议适配：映射本地绑定与远端知识条目，统一摄取、授权检索、对账及删除确认。
from urllib.parse import quote
import hashlib
import httpx
from .transport import Transport
from .contracts import BackendError, BackendStatus, DeleteReceipt, Evidence, Submission, validate_scope


# 将标识编码成单一路径段，避免远端 ID 中的斜杠改变 API 路径。
def encoded(value):
    return quote(str(value), safe='')


class WeKnoraBackend(Transport):
    @property
    def headers(self):
        return {'X-API-Key': self.key}

    # 校验预建知识库及嵌入模型，不按传入名称创建新知识库。
    async def prepare_collection(self, name):
        collection = self.options.get('collection_id')
        if not collection:
            raise BackendError('invalid_configuration','请先在 WeKnora 准备知识库并配置 collection_id')
        data = (await self.request('GET',f'/api/v1/knowledge-bases/{encoded(collection)}')).get('data') or {}
        if data.get('id') != collection or not data.get('embedding_model_id'):
            raise BackendError('invalid_configuration','WeKnora 知识库必须配置 Embedding 模型并允许当前凭据访问')
        return {'id':collection}

    # 上传时写入本地绑定号，未知结果对账据此筛选候选条目。
    async def submit(self, collection_id, filename, content, binding_id):
        import json
        data = await self.request('POST', f'/api/v1/knowledge-bases/{encoded(collection_id)}/knowledge/file',
            files={'file': (filename, content)}, data={'metadata':json.dumps({'v3_binding_id':binding_id})})
        return Submission(data['data']['id'])

    # 解析完成后读取实际分块总数，空分块不能成为可发布资料。
    async def status(self, binding):
        data = (await self.request('GET', f'/api/v1/knowledge/{encoded(binding.remote_id)}'))['data']
        if data.get('knowledge_base_id') != binding.collection_id:
            raise BackendError('invalid_scope')
        raw = data.get('parse_status', '')
        chunks = 0
        if raw == 'completed':
            # 解析状态成功仍需确认确有分块，不能把空索引作为可发布的资料。
            detail = await self.request('GET',f'/api/v1/chunks/{encoded(binding.remote_id)}',params={'page':1,'page_size':1})
            if detail.get('success') is not True or not isinstance(detail.get('total'),int):
                raise BackendError('invalid_response')
            chunks = detail['total']
        return BackendStatus('completed' if raw == 'completed' else 'failed' if raw in ('failed','cancelled') else 'processing', int(chunks or 0), raw)

    # 完整扫描确认绑定候选唯一，再用详情身份及原件大小、SHA-256 核验归属。
    async def reconcile_submission(self, collection_id, proof):
        # 必须完整扫描集合；扫描被截断时无法证明候选唯一。
        candidates=[]; seen=set()
        for page in range(1,101):
            # 完整盘点后才判断唯一性；页内或跨页重复身份说明扫描不可信，必须拒绝恢复。
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
        # 列表元数据只用于筛选，再从详情确认知识库和绑定，防止错误或陈旧列表被当作证明。
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
                    # 在有限大小内核对原件内容，摘要和字节数都一致才恢复上传回执。
                    size+=len(chunk)
                    if size>proof.get('size',64*1024*1024): raise BackendError('reconciliation_hash_mismatch')
                    digest.update(chunk)
        except httpx.HTTPError as error: raise BackendError('reconciliation_original_unavailable') from error
        if digest.hexdigest()!=proof['sha256'] or ('size' in proof and size!=proof['size']):
            raise BackendError('reconciliation_hash_mismatch')
        return Submission(remote)

    # 请求明确列出授权知识 ID 并禁用上下文扩展；返回结果仍须逐条检查来源。
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
            # 即便请求已显式过滤，远端返回任一未授权条目也拒绝整批，而非悄悄过滤后继续。
            if not binding:
                raise BackendError('invalid_scope')
            content = item.get('content')
            if not isinstance(content,str) or not content.strip() or not item.get('id'):
                raise BackendError('invalid_response')
            locators = item.get('source_locators') or []
            raw = locators[0] if locators and isinstance(locators[0],dict) else {}
            locator = {key:raw[key] for key in ('type','page','block','sheet','row_start','row_end','start','end','partial','mapping','source_hash') if key in raw}
            # 只有正数页码且不是局部映射才声明页级定位，其余保守标为分段定位。
            level = 'page' if isinstance(locator.get('page'),int) and locator['page'] > 0 and not locator.get('partial') else 'segment'
            result.append(Evidence(scope.tenant_id,binding.document_id,binding.version_id,binding.generation_id,
                binding.instance_id,collection,str(item['id']),content,float(item.get('score') or 0),level,locator))
        return result

    async def list_chunks(self, binding, *, page=1, page_size=20, dispatch_gate=None):
        from .chunks import validate_page, chunk_page
        validate_page(page, page_size)
        # 先核对远端文档所属集合，再分页读取；每条返回分块仍需核对文档及集合身份。
        detail = await self.request('GET', f'/api/v1/knowledge/{encoded(binding.remote_id)}', dispatch_gate=dispatch_gate)
        row = detail.get('data') or {}
        if row.get('id') != binding.remote_id or row.get('knowledge_base_id') != binding.collection_id:
            raise BackendError('invalid_scope')
        data = await self.request('GET', f'/api/v1/chunks/{encoded(binding.remote_id)}', dispatch_gate=dispatch_gate,
                                  params={'page':page, 'page_size':page_size})
        if data.get('success') is not True:raise BackendError('invalid_response')
        return chunk_page(data, binding, 'weknora', page, page_size)

    async def delete(self, binding):
        await self.request('DELETE', f'/api/v1/knowledge/{encoded(binding.remote_id)}')
        return DeleteReceipt(True)

    # 静态声明协议能力，runtime_verified 为假；实际环境仍须完成独立验收。
    async def capabilities(self):
        return {'provider':'weknora','document_filter':True,'runtime_verified':False}

    # 404 只证明远端 API 不存在；返回条目若属于其他集合，不能当成删除成功。
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
