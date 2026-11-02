# 依赖运行中的本地 API、既有合成试点库和真实模型接口；只读取业务状态，但会发送合成模型请求并写本地报告。
"""Read-only runtime/platform checks, synthetic cloud requests, no business writes."""
import asyncio,json,os,hashlib
from datetime import datetime,timezone
from pathlib import Path
import httpx
from dotenv import dotenv_values
from sqlalchemy import select
from sqlalchemy.engine import make_url
from app.v3.config import Settings
from app.v3.db import Database
from app.v3.models import BackendInstance,Replica,Version,Document
from app.v3.knowledge.contracts import Scope,Binding
from app.v3.knowledge.registry import BackendRegistry
from app.v3.chat.models import ModelService

# 校验已有合成原件的哈希，再实际调用向量、检索、重排与生成；成功表示这条合成链路通过，不扩大为完整业务验收。
async def main():
    cfg=dotenv_values('v3.local')
    for k,v in cfg.items():
        if v is not None:os.environ[k]=v
    settings=Settings.from_env();url=make_url(settings.database_url)
    assert (url.host,url.port,url.database)==('127.0.0.1',18433,'enterpriserag_v3')
    previous=json.loads(Path('docs/testing/v3/live-pilot/weknora-local-pilot.json').read_text())
    assert previous['test_database'].startswith('v3_live_')
    db=Database(url.set(database=previous['test_database']).render_as_string(hide_password=False))
    result={'checked_at':datetime.now(timezone.utc).isoformat(),'scope':'read-only runtime and existing synthetic test records','checks':[]}
    async with httpx.AsyncClient(timeout=60) as client:
        root='http://127.0.0.1:18000'
        response=await client.get(root+'/api/v3/spaces');assert response.status_code==401
        result['checks'].append({'name':'anonymous-rejected','status':'passed'})
        headers={'Authorization':'Bearer '+cfg['V3_BOOTSTRAP_TOKEN']}
        for path in ['/health','/api/v3/me','/api/v3/spaces','/api/v3/backends','/api/v3/jobs']:
            r=await client.get(root+path,headers=headers);r.raise_for_status()
            result['checks'].append({'name':path,'status':'passed'})
            if path.endswith('/backends'):
                result['backends']=[{k:b[k] for k in ['provider','usable','verified']} for b in r.json()]
            if path.endswith('/jobs'):
                from collections import Counter
                result['runtime_job_states']=dict(Counter(j['state'] for j in r.json()))
            if path.endswith('/spaces'):spaces=r.json()
        assert spaces
        docs=(await client.get(root+'/api/v3/spaces/'+spaces[0]['id']+'/documents',headers=headers)).json()
        sample=next(d for d in docs if d['name']=='使用示例-合成差旅制度.txt')
        r=await client.get(root+'/api/v3/documents/'+sample['id']+'/source',headers=headers);r.raise_for_status()
        v=next(v for v in sample['versions'] if v['id']==sample['current_version'])
        assert hashlib.sha256(r.content).hexdigest()==v['sha256']
        result['checks'].append({'name':'current-original-sha256','status':'passed'})
        r=await client.post(cfg['V3_EMBEDDING_BASE_URL'].rstrip('/')+'/embeddings',headers={'Authorization':'Bearer '+cfg['V3_SECRET_EMBEDDING']},json={'model':cfg['V3_EMBEDDING_MODEL'],'input':['Synthetic allowance policy 138 yuan.']})
        r.raise_for_status();dimension=len(r.json()['data'][0]['embedding'])
        assert dimension==int(cfg['V3_EMBEDDING_DIMENSION'])
        result['checks'].append({'name':'real-embedding-dimension','status':'passed','dimension':dimension})
        registry=BackendRegistry(client);models=ModelService(settings,client)
        with db.transaction('live',shared=True) as s:
            instances=s.scalars(select(BackendInstance).filter_by(tenant_id='live')).all()
            doc=s.scalars(select(Document).filter_by(tenant_id='live')).all()
            allowed=next(d for d in doc if d.name.startswith('allowed-'))
            version=s.scalar(select(Version).filter_by(document_id=allowed.id,filename=allowed.name))
            bindings=[]
            for i in instances:
                replica=s.scalar(select(Replica).filter_by(version_id=version.id,instance_id=i.id,state='ready'))
                assert replica
                bindings.append((i,Binding('live',allowed.id,version.id,replica.id,i.id,replica.collection_id,replica.remote_id,replica.id,replica.batch_id)))
        for instance,binding in bindings:
            q='本次合成制度的差旅报销标准是多少元？'
            evidence=await registry.get(instance).retrieve(q,Scope('live',(binding,)))
            assert evidence and any('138' in e.content for e in evidence) and all('9999' not in e.content for e in evidence)
            ranked=await models.rank(q,evidence);answer=''
            async for part in models.stream(q,ranked,[]):answer+=part
            assert '138' in answer and '[1]' in answer
            result['checks'].append({'name':instance.provider+'-real-filter-rerank-cited-answer','status':'passed','chunks':len(evidence),'expected_fact':138})
            print(instance.provider,'LIVE_READONLY_PASSED',flush=True)
    db.engine.dispose()
    Path(__file__).with_name('live-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print('LIVE_CHECKS',len(result['checks']),'PASSED',flush=True)

if __name__=='__main__':asyncio.run(main())
