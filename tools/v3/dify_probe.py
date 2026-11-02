# 依赖本地 Dify 和仓库外凭据，读取范围化权限并尝试上传合成文件；报告仅记录协议诊断，不代表完整验收。
"""Protocol probe on an isolated Dify 1.18 instance. Never a G0 sign-off.

Uses synthetic economy-indexed data to diagnose file upload before model setup.
Credentials are stored outside the repository by the environment setup workflow.
"""
import argparse
import json
from datetime import datetime,timezone
from pathlib import Path
import httpx


# 先检查数据集可读与密钥不能列出全工作区，再探测文件上传；局部接口通过不会被报告为完整 G0。
def main(args):
    auth=json.loads((Path(args.runtime_dir)/'dify-test-auth.json').read_text(encoding='utf-8-sig'))
    dataset=auth['dataset']['id'];checks=[]
    with httpx.Client(base_url='http://127.0.0.1:18082/v1',headers={'Authorization':'Bearer '+auth['api_key']['token']},timeout=30) as client:
        for name,path,validate in [
            ('scoped-dataset-read',f'/datasets/{dataset}',lambda data:data.get('id')==dataset),
            ('binding-field-read',f'/datasets/{dataset}/metadata',lambda data:any(x.get('name')=='v3_binding_id' and x.get('type')=='string' for x in data.get('doc_metadata',[]))),
        ]:
            response=client.get(path)
            checks.append({'id':name,'status':'passed' if response.is_success and validate(response.json()) else 'failed','http_status':response.status_code})
        response=client.get('/datasets')
        checks.append({'id':'scoped-key-denies-workspace-list','status':'passed' if response.status_code==403 else 'failed','http_status':response.status_code})
        response=client.post(f'/datasets/{dataset}/document/create-by-file',files={'file':('diagnostic.txt',b'ALLOWED_A policy is 30 days.','text/plain')},
            data={'data':json.dumps({'indexing_technique':'economy','process_rule':{'mode':'automatic'}})})
        checks.append({'id':'file-upload','status':'passed' if response.is_success else 'failed','http_status':response.status_code})
    report={'audit_level':'runtime_integration_partial','at':datetime.now(timezone.utc).isoformat(),
        'provider':'dify','commit':'50abea97062110044f15d0e03ccecadd9ccc87ca',
        'image_digest':'sha256:0120970fb4ea59aa328d63b7dda635cccc071efb8aae470cd07115a026dbffd3',
        'checks':checks,'indexing_mode':'economy_diagnostic_only','production_g0':'not_complete',
        'model_quality':'not_run','semantic_filter':'not_run','roundtrip_switch':'not_run',
        'server_error':'File API loads dataset creator without current tenant; save_document_with_dataset_id asserts account.current_tenant_id is not None. Confirmed in isolated server logs and pinned source.'}
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'passed':sum(c['status']=='passed' for c in checks),'failed':sum(c['status']=='failed' for c in checks),'production_g0':'not_complete'}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--runtime-dir',required=True);parser.add_argument('--output',required=True)
    main(parser.parse_args())
