"""Real WeKnora HTTP/storage probe with synthetic documents and fixture embeddings.

Not a production G0 sign-off. It deliberately leaves real-model quality not_run.
"""
import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
import httpx
from app.v3.knowledge.weknora import WeKnoraBackend
from app.v3.knowledge.contracts import Binding, Scope


async def main(args):
    root=Path(args.runtime_dir);key=json.loads((root/'weknora-scoped-key.json').read_text(encoding='utf-8-sig'))['data']['api_key']
    collection=json.loads((root/'weknora-fixture-kb.json').read_text(encoding='utf-8-sig'))['data']['id']
    checks=[];bindings=[]
    async with httpx.AsyncClient() as client:
        backend=WeKnoraBackend('http://127.0.0.1:18080',key,client)
        listed=await client.get(f'http://127.0.0.1:18080/api/v1/knowledge-bases/{collection}/knowledge',headers={'X-API-Key':key},params={'page':1,'page_size':100})
        listed.raise_for_status()
        for marker in ('ALLOWED_A','FORBIDDEN_B'):
            matches=[item for item in listed.json()['data'] if (item.get('metadata') or {}).get('v3_binding_id')==marker]
            if len(matches)>1:raise RuntimeError('Duplicate fixture binding')
            remote_id=matches[0]['id'] if matches else (await backend.submit(collection,marker+'.txt',f'{marker} policy standard is {100 if marker=="ALLOWED_A" else 999}. policy policy policy.'.encode(),marker)).remote_id
            binding=Binding('g0',marker,marker,'gen-'+marker,'weknora-g0',collection,remote_id,marker)
            bindings.append(binding)
            async with asyncio.timeout(120):
                while True:
                    state=await backend.status(binding)
                    if state.state=='failed':raise RuntimeError('Synthetic document parsing failed')
                    if state.state=='completed':
                        if not state.chunks:raise RuntimeError('Completed document has no chunks')
                        break
                    await asyncio.sleep(1)
            checks.append({'id':'ingest-'+marker,'status':'passed','chunks':state.chunks})
        for method,vector_off,keyword_off in [('vector',False,True),('keyword',True,False),('hybrid',False,False)]:
            # First show forbidden document is searchable; otherwise zero leakage could be accidental.
            for binding in bindings:
                response=await client.post(f'http://127.0.0.1:18080/api/v1/knowledge-bases/{collection}/hybrid-search',headers={'X-API-Key':key},json={
                    'query_text':'policy standard','knowledge_ids':[binding.remote_id],'match_count':10,
                    'disable_vector_match':vector_off,'disable_keywords_match':keyword_off,
                    'skip_context_enrichment':True})
                response.raise_for_status();records=response.json()['data']
                if not records or any(r.get('knowledge_id')!=binding.remote_id for r in records):
                    raise RuntimeError(method+' did not enforce the document scope or returned no evidence')
                checks.append({'id':method+'-'+binding.document_id,'status':'passed','hits':len(records)})
        evidence=await backend.retrieve('policy standard',Scope('g0',(bindings[0],)))
        if not evidence or any('FORBIDDEN_B' in e.content for e in evidence):raise RuntimeError('Adapter leaked forbidden marker')
        checks.append({'id':'adapter-scope','status':'passed'})
    report={'audit_level':'runtime_integration_partial','at':datetime.now(timezone.utc).isoformat(),
        'provider':'weknora','commit':'f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7','model_mode':'deterministic_http_fixture',
        'checks':checks,'real_model_quality':'not_run','production_g0':'not_complete',
        'diagnostic_configuration':{'question_generation_enabled':True,'note':'Default disabled setting failed keyword/hybrid in ParadeDB; this contrast does not approve enabling it in production.'},
        'bindings':[{'marker':b.document_id,'remote_id':b.remote_id,'collection_id':b.collection_id} for b in bindings]}
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'passed':len(checks),'model_mode':report['model_mode'],'production_g0':'not_complete'}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--runtime-dir',required=True);parser.add_argument('--output',required=True)
    asyncio.run(main(parser.parse_args()))
