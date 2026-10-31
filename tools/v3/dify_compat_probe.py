"""Diagnose the explicitly patched Dify image; economy recall is not V3 semantic G0."""
import argparse
import asyncio
import json
from datetime import datetime,timezone
from pathlib import Path
import httpx
from app.v3.knowledge.dify import DifyBackend
from app.v3.knowledge.contracts import Binding


async def main(args):
    path=Path(args.runtime_dir)/'dify-test-auth.json';auth=json.loads(path.read_text(encoding='utf-8-sig'))
    collection=auth['dataset']['id'];checks=[];bindings=[]
    async with httpx.AsyncClient() as client:
        backend=DifyBackend('http://127.0.0.1:18082',auth['api_key']['token'],client)
        metadata=await backend.request('GET',f'/v1/datasets/{collection}/metadata');metadata_id=metadata['doc_metadata'][0]['id']
        for letter,marker in [('a','ALLOWED_A'),('b','FORBIDDEN_B')]:
            key='compat_document_'+letter
            if key not in auth:
                data=await backend.request('POST',f'/v1/datasets/{collection}/document/create-by-file',files={'file':(marker+'.txt',f'{marker} returns policy is 999 days.'.encode(),'text/plain')},
                    data={'data':json.dumps({'indexing_technique':'economy','process_rule':{'mode':'automatic'}})})
                auth[key]=data;path.write_text(json.dumps(auth),encoding='utf8')
            doc=auth[key];binding=Binding('g0',marker,marker,'gen','dify-g0',collection,doc['document']['id'],marker,doc['batch']);bindings.append(binding)
            async with asyncio.timeout(90):
                while True:
                    status=await backend.status(binding)
                    if status.state=='failed':raise RuntimeError('indexing failed')
                    if status.state=='completed':break
                    await asyncio.sleep(1)
            if status.chunks<=0:raise RuntimeError('no indexed chunks')
            checks.append({'id':'file-index-'+letter,'status':'passed','chunks':status.chunks})
            await backend.set_binding_metadata(binding,metadata_id)
            checks.append({'id':'binding-write-readback-'+letter,'status':'passed'})
        for marker,expected in [('ALLOWED_A',bindings[0].remote_id),('FORBIDDEN_B',bindings[1].remote_id),('NO_MATCH',None)]:
            data=await backend.request('POST',f'/v1/datasets/{collection}/retrieve',json={'query':'policy','retrieval_model':{
                'search_method':'keyword_search','reranking_enable':False,'top_k':10,'score_threshold_enabled':False,
                'metadata_filtering_conditions':{'logical_operator':'and','conditions':[{'name':'v3_binding_id','comparison_operator':'in','value':[marker]}]}}})
            records=data.get('records',[])
            if expected and (not records or any(r['segment']['document_id']!=expected for r in records)):raise RuntimeError('positive scope failed')
            if not expected and records:raise RuntimeError('empty filter leaked evidence')
            checks.append({'id':'economy-filter-'+marker,'status':'passed','hits':len(records)})
    report={'audit_level':'runtime_integration_partial','at':datetime.now(timezone.utc).isoformat(),'provider':'dify',
        'commit':'50abea97062110044f15d0e03ccecadd9ccc87ca','image':'enterpriserag-v3-dify-api:1.18.0-tenant-compat',
        'image_id':'sha256:77c3b3963969d73eb354fde506e1e162e24cab9f52a785d0985503f8ba311f2a',
        'patch':'deploy/v3/dify-compat/patch.py','checks':checks,'indexing_mode':'economy_diagnostic_only',
        'semantic_filter':'not_run','real_model_quality':'not_run','roundtrip_switch':'not_run','production_g0':'not_complete'}
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'passed':len(checks),'production_g0':'not_complete','image':'tenant-compat'}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--runtime-dir',required=True);parser.add_argument('--output',required=True)
    asyncio.run(main(parser.parse_args()))
