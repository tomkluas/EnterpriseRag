from .transport import Transport
from urllib.parse import urlsplit, urljoin
import hashlib
import httpx
from .weknora import encoded
from .contracts import BackendError, BackendStatus, DeleteReceipt, Evidence, Submission, validate_scope


def binding_metadata(value):
    if isinstance(value,dict):return value.get('v3_binding_id')
    if isinstance(value,list):
        fields=[field for field in value if isinstance(field,dict) and field.get('name')=='v3_binding_id']
        if len(fields)==1:return fields[0].get('value')
    raise BackendError('invalid_scope','授权标识缺失或重复')


class DifyBackend(Transport):
    @property
    def headers(self):
        return {'Authorization': 'Bearer ' + self.key}

    async def prepare_collection(self, name):
        collection_id = self.options.get('collection_id')
        if not collection_id:
            raise BackendError('invalid_configuration','请先在 Dify 准备知识库并配置 collection_id')
        data = await self.request('GET',f'/v1/datasets/{encoded(collection_id)}')
        if data.get('id') != collection_id or data.get('indexing_technique') != 'high_quality':
            raise BackendError('invalid_configuration','Dify 知识库必须使用高质量索引')
        data = await self.request('GET',f'/v1/datasets/{encoded(collection_id)}/metadata')
        fields = [x for x in data.get('doc_metadata',[]) if x.get('name')=='v3_binding_id' and x.get('type')=='string' and x.get('id')]
        if len(fields) != 1:
            raise BackendError('invalid_configuration','请在 Dify 知识库创建唯一的字符串元数据字段 v3_binding_id')
        return {'id':collection_id,'metadata_id':fields[0]['id']}

    async def submit(self, collection_id, filename, content, binding_id):
        import json
        data = await self.request('POST',f'/v1/datasets/{encoded(collection_id)}/document/create-by-file',
            files={'file':(filename,content)},data={'data':json.dumps({'indexing_technique':'high_quality','process_rule':{'mode':'automatic'}})})
        # The worker must wait for completion, set metadata, and read it back before ready.
        return Submission(data['document']['id'],data['batch'])

    async def set_binding_metadata(self, binding, metadata_id):
        await self.request('POST',f'/v1/datasets/{encoded(binding.collection_id)}/documents/metadata',json={
            'operation_data':[{'document_id':binding.remote_id,'metadata_list':[
                {'id':metadata_id,'name':'v3_binding_id','value':binding.id}]}]})
        data = await self.request('GET',f'/v1/datasets/{encoded(binding.collection_id)}/documents/{encoded(binding.remote_id)}',params={'metadata':'all'})
        metadata = data.get('doc_metadata') or {}
        if binding_metadata(metadata) != binding.id:
            raise BackendError('invalid_scope','授权标识未通过回读校验')

    async def status(self, binding):
        if not binding.batch_id:
            # Fixed 1.18 detail API exposes real document state and segment_count.
            row=await self.request('GET',f'/v1/datasets/{encoded(binding.collection_id)}/documents/{encoded(binding.remote_id)}')
            if row.get('id')!=binding.remote_id:raise BackendError('invalid_scope')
            raw=row.get('indexing_status','')
            count=row.get('segment_count')
            if not isinstance(raw,str) or not raw or type(count) is not int or count<0:
                raise BackendError('invalid_response')
            return BackendStatus('completed' if raw=='completed' else 'failed' if raw in ('error','paused') else 'processing',count,raw)
        data = await self.request('GET',f'/v1/datasets/{encoded(binding.collection_id)}/documents/{encoded(binding.batch_id)}/indexing-status')
        rows = [row for row in data.get('data',[]) if row.get('id') == binding.remote_id]
        if len(rows) != 1:
            raise BackendError('invalid_response')
        row = rows[0]; raw = row.get('indexing_status','')
        return BackendStatus('completed' if raw == 'completed' else 'failed' if raw in ('error','paused') else 'processing',int(row.get('completed_segments') or 0),raw)

    async def reconciliation_candidates(self, collection_id, proof):
        # Metadata assigned after upload is never used to establish ownership.
        candidates=[]; seen=set()
        for page in range(1,101):
            data=await self.request('GET',f'/v1/datasets/{encoded(collection_id)}/documents',
                params={'page':page,'limit':100})
            if not isinstance(data.get('data'),list) or type(data.get('has_more')) is not bool:
                raise BackendError('invalid_response')
            for row in data['data']:
                if not isinstance(row,dict) or not row.get('id') or row['id'] in seen:
                    raise BackendError('invalid_response')
                seen.add(row['id'])
                if proof.get('upload_filename') and row.get('name')==proof['upload_filename']:
                    candidates.append({'remote_id':row['id'],'basis':'upload_filename_only','verified':False})
            if not data['has_more']: return candidates
            if not data['data']:raise BackendError('invalid_response')
        raise BackendError('reconciliation_scan_limit')

    async def reconcile_submission(self, collection_id, proof):
        if not proof.get('upload_filename'):
            raise BackendError('reconciliation_unprovable')
        candidates=await self.reconciliation_candidates(collection_id,proof)
        if len(candidates)!=1:raise BackendError('reconciliation_ambiguous')
        remote=candidates[0]['remote_id']
        path=f'/v1/datasets/{encoded(collection_id)}/documents/{encoded(remote)}'
        detail=await self.request('GET',path)
        if (detail.get('id')!=remote or detail.get('name')!=proof['upload_filename']
                or detail.get('data_source_type')!='upload_file'):raise BackendError('invalid_scope')
        signed=await self.request('GET',path+'/download')
        url=signed.get('url')
        if not isinstance(url,str) or not url or '#' in url or '\\' in url or any(ord(c)<=32 for c in url):
            raise BackendError('reconciliation_original_unavailable',reason='invalid_download_url')
        try:
            # FILES_URL may be empty in Dify 1.18; its signed link is then root-relative.
            url=urljoin(self.base_url+'/',url)
            target=urlsplit(url);origin=urlsplit(self.base_url)
            if target.username or target.password:
                raise BackendError('reconciliation_original_unavailable',reason='invalid_download_url')
            if (target.scheme,target.hostname,target.port)!=(origin.scheme,origin.hostname,origin.port):
                raise BackendError('reconciliation_original_unavailable',reason='untrusted_download_origin')
        except ValueError as error:
            raise BackendError('reconciliation_original_unavailable',reason='invalid_download_url') from error
        digest=hashlib.sha256();size=0
        try:
            # Signed links carry their own authorization; never forward API credentials.
            request=self.client.build_request('GET',url,timeout=httpx.Timeout(30,connect=5))
            for header in ('authorization','cookie','proxy-authorization'):
                request.headers.pop(header,None)
            response=await self.client.send(request,auth=None,stream=True,follow_redirects=False)
            try:
                if response.status_code!=200:
                    raise BackendError('reconciliation_original_unavailable',reason=(
                        'download_redirect_rejected' if 300<=response.status_code<400 else 'download_http_'+str(response.status_code)))
                async for chunk in response.aiter_bytes():
                    size+=len(chunk)
                    if size>proof.get('size',64*1024*1024):raise BackendError('reconciliation_hash_mismatch')
                    digest.update(chunk)
            finally:await response.aclose()
        except httpx.TimeoutException as error:
            raise BackendError('reconciliation_original_unavailable',reason='download_timeout') from error
        except httpx.HTTPError as error:
            raise BackendError('reconciliation_original_unavailable',reason='download_transport_error') from error
        if digest.hexdigest()!=proof['sha256'] or ('size' in proof and size!=proof['size']):
            raise BackendError('reconciliation_hash_mismatch')
        return Submission(remote)

    async def retrieve(self, query, scope, dispatch_gate=None):
        allowed = validate_scope(scope)
        if not allowed:
            return []
        if not query.strip() or len(query) > 250:
            raise BackendError('invalid_query','Dify 检索问题需要在 1—250 字符内，不能静默截断')
        collection = next(iter(allowed.values())).collection_id
        result = []
        # Separate semantic and full-text recall avoids relying on implicit hybrid weights.
        for method in ('semantic_search','full_text_search'):
            data = await self.request('POST',f'/v1/datasets/{encoded(collection)}/retrieve',dispatch_gate=dispatch_gate,json={
                'query':query,'retrieval_model':{'search_method':method,'reranking_enable':False,
                'top_k':50,'score_threshold_enabled':False,'metadata_filtering_conditions':{
                    'logical_operator':'and','conditions':[{'name':'v3_binding_id','comparison_operator':'in',
                    'value':[b.id for b in allowed.values()]}]}}})
            if not isinstance(data.get('records'),list):
                raise BackendError('invalid_response')
            for record in data['records']:
                segment = record.get('segment') or {}
                binding = allowed.get(segment.get('document_id'))
                if not binding:
                    raise BackendError('invalid_scope')
                metadata = (segment.get('document') or {}).get('doc_metadata') or {}
                if binding_metadata(metadata) != binding.id:
                    raise BackendError('invalid_scope')
                if not segment.get('id') or not isinstance(segment.get('content'),str) or not segment['content'].strip():
                    raise BackendError('invalid_response')
                if not any(e.chunk_id == str(segment['id']) for e in result):
                    result.append(Evidence(scope.tenant_id,binding.document_id,binding.version_id,binding.generation_id,
                        binding.instance_id,collection,str(segment['id']),segment['content'],float(record.get('score') or 0),
                        'segment',{'position':segment.get('position')}))
        return result

    async def delete(self, binding):
        await self.request('DELETE',f'/v1/datasets/{encoded(binding.collection_id)}/documents/{encoded(binding.remote_id)}')
        return DeleteReceipt(True)

    async def capabilities(self):
        return {'provider':'dify','document_filter':True,'runtime_verified':False,'max_query_chars':250}

    async def deletion_confirmed(self, binding):
        try:
            data=await self.request('GET',f'/v1/datasets/{encoded(binding.collection_id)}/documents/{encoded(binding.remote_id)}')
        except BackendError as error:
            if error.code=='not_found':return True
            raise
        if data.get('id')!=binding.remote_id:raise BackendError('invalid_scope')
        return False
