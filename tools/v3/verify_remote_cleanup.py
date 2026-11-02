# 依赖既有本地合成试点，上传后删除本轮新文件；远端 API 确认不存在只证明接口不可见，不能证明文件、向量或备份物理擦除。
"""Exercise upload/delete with fresh synthetic files in the recorded isolated pilot.

Never uses existing remote documents or writes V3 business records.
"""
import argparse
import asyncio
import json
import os
import time
import uuid
from datetime import datetime,timezone
from pathlib import Path
import httpx
from dotenv import dotenv_values
from sqlalchemy import select
from sqlalchemy.engine import make_url
from app.v3.db import Database
from app.v3.models import BackendInstance
from app.v3.knowledge.registry import BackendRegistry
from app.v3.knowledge.contracts import Binding,BackendError


# 先证明文件存在并解析完成，再接受删除并轮询消失；仅收到删除回执不足以说明清理完成。
async def verify(env_file,output):
    cfg=dotenv_values(env_file)
    for key,value in cfg.items():
        if value is not None:os.environ[key]=value
    url=make_url(cfg['V3_DATABASE_URL'])
    if (url.host,url.port,url.database)!=('127.0.0.1',18433,'enterpriserag_v3'):
        raise ValueError('Only the documented local pilot environment is allowed')
    report=json.loads(Path('docs/testing/v3/live-pilot/weknora-local-pilot.json').read_text(encoding='utf8'))
    name=report['test_database']
    if not name.startswith('v3_live_'):raise ValueError('Isolated test database required')
    db=Database(url.set(database=name).render_as_string(hide_password=False))
    result={'run_id':uuid.uuid4().hex,'started_at':datetime.now(timezone.utc).isoformat(),
        'scope':'fresh synthetic documents in existing isolated pilot collections',
        'checks':[],'limitations':['Remote API absence is not physical erasure of files, vectors or backups.']}
    try:
        with db.transaction('live',shared=True) as s:
            instances=s.scalars(select(BackendInstance).filter_by(tenant_id='live')).all()
        if {i.provider for i in instances}!={'weknora','dify'}:raise ValueError('Both recorded pilot providers required')
        async with httpx.AsyncClient() as client:
            registry=BackendRegistry(client)
            for instance in instances:
                started=time.perf_counter();binding=None
                check={'provider':instance.provider,'status':'failed','cleanup_confirmed':False}
                writer=registry.get(instance,'write')
                try:
                    async with asyncio.timeout(150):
                        prepared=await registry.get(instance,'manage').prepare_collection('ignored')
                        marker='cleanup-probe-'+uuid.uuid4().hex
                        submitted=await writer.submit(prepared['id'],marker+'.txt',
                            ('Synthetic cleanup test '+marker+' allowance 138 yuan.').encode(),marker)
                        binding=Binding('live',marker,marker,marker,instance.id,prepared['id'],submitted.remote_id,marker,submitted.batch_id)
                        for _ in range(24):
                            status=await registry.get(instance,'retrieve').status(binding)
                            if status.state=='completed':break
                            if status.state=='failed':raise BackendError('probe_parse_failed')
                            await asyncio.sleep(2)
                        else:raise BackendError('probe_parse_timeout')
                        if await writer.deletion_confirmed(binding):raise BackendError('probe_missing_before_delete')
                        receipt=await writer.delete(binding)
                        if not receipt.accepted:raise BackendError('probe_delete_not_accepted')
                        for _ in range(24):
                            if await writer.deletion_confirmed(binding):
                                check['cleanup_confirmed']=True;break
                            await asyncio.sleep(2)
                        if not check['cleanup_confirmed']:raise BackendError('probe_cleanup_timeout')
                        check.update(status='passed',parsed_chunks=status.chunks,confirmation_basis='remote_api_not_found')
                except Exception as error:
                    check['error_code']=getattr(error,'code',type(error).__name__)
                finally:
                    if binding and not check['cleanup_confirmed']:
                        # Only touch the fresh receipt returned in this invocation.
                        try:
                            await writer.delete(binding)
                            check['cleanup_confirmed']=await writer.deletion_confirmed(binding)
                        except Exception:pass
                        if not check['cleanup_confirmed']:
                            check['unresolved_remote_id']=binding.remote_id
                    check['elapsed_seconds']=time.perf_counter()-started
                    result['checks'].append(check)
                    print(instance.provider,check['status'],'cleanup_confirmed='+str(check['cleanup_confirmed']),flush=True)
    finally:
        db.engine.dispose()
        result['finished_at']=datetime.now(timezone.utc).isoformat()
        Path(output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    return len(result['checks'])==2 and all(c['status']=='passed' for c in result['checks'])


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--env-file',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--run-isolated-probe',action='store_true',required=True,
        help='Create and delete fresh synthetic files in the recorded local pilot only')
    args=parser.parse_args()
    raise SystemExit(0 if asyncio.run(verify(args.env_file,args.output)) else 1)
