# 为本地 WeKnora 诊断准备知识库与凭据相关状态；依赖已部署平台和运行目录，执行会写远端资源。
"""Provision isolated V3 resources using the existing local administrator session.

Secrets are read from v3.local and written only to its ignored file / private runtime directory.
"""
import argparse,json
from pathlib import Path
from dotenv import dotenv_values,set_key
import httpx


# 复用运行目录里记录的资源 ID，避免重复创建；读写密钥分别限制能力与知识库，凭据仅落在私有配置中。
def main(args):
    root=Path(args.runtime_dir);state_path=root/'weknora-live.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {}
    auth=json.loads((root/'weknora-test-auth.json').read_text(encoding='utf-8-sig'))
    config=dotenv_values(args.env_file)
    with httpx.Client(base_url='http://127.0.0.1:18080',timeout=45) as client:
        client.headers['Authorization']='Bearer '+auth['login']['token']
        # 管理员会话过期时重新登录并重放本次请求；失败信息只保留无查询串的路径与状态码。
        def call(method,path,**kw):
            response=client.request(method,path,**kw)
            if response.status_code==401:
                login=client.post('/api/v1/auth/login',json={'email':auth['email'],'password':auth['password']});login.raise_for_status()
                auth['login']=login.json();(root/'weknora-test-auth.json').write_text(json.dumps(auth))
                client.headers['Authorization']='Bearer '+auth['login']['token'];response=client.request(method,path,**kw)
            if not response.is_success:raise RuntimeError(f'{method} {path.split("?")[0]} returned {response.status_code}')
            return response.json()
        def save():state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2))
        for role,model,kind,base,key in [
            ('embedding',config['V3_EMBEDDING_MODEL'],'Embedding',config['V3_EMBEDDING_BASE_URL'],config['V3_SECRET_EMBEDDING']),
            ('chat',config['V3_CHAT_MODEL'],'KnowledgeQA',config['V3_MODEL_BASE_URL'],config['V3_SECRET_MODEL'])]:
            if role not in state:
                parameters={'base_url':base,'api_key':key}
                if role=='embedding':parameters['embedding_parameters']={'dimension':int(config.get('V3_EMBEDDING_DIMENSION') or 1024),'supports_dimension_override':True}
                result=call('POST','/api/v1/models',json={'name':model,'type':kind,'source':'remote','parameters':parameters})
                state[role]=result['data']['id'];save();print('Created real '+role+' model',flush=True)
        if 'collection' not in state:
            result=call('POST','/api/v1/knowledge-bases',json={'name':'V3 企业知识库','type':'document',
                'embedding_model_id':state['embedding'],'summary_model_id':state['chat'],
                'question_generation_config':{'enabled':True,'question_count':1},
                'chunking_config':{'chunk_size':800,'chunk_overlap':100}})
            state['collection']=result['data']['id'];save();print('Created V3 knowledge collection',flush=True)
        # PATCH explicitly: PUT does not update question generation in this pinned release.
        call('PATCH','/api/v1/knowledge-bases/'+state['collection'],json={'question_generation_config':{'enabled':True,'question_count':1}})
        tenant=auth['login']['active_tenant']['id'] if isinstance(auth['login']['active_tenant'],dict) else auth['login']['active_tenant']
        for purpose,caps in [('read',['retrieve']),('write',['retrieve','ingest'])]:
            if purpose not in state:
                result=call('POST',f'/api/v1/tenants/{tenant}/api-keys',json={'name':'V3 business '+purpose,'full_access':False,
                    'knowledge_base_ids':[state['collection']],'capabilities':caps})
                state[purpose]=result['data'];save()
            set_key(args.env_file,'V3_SECRET_WK_'+purpose.upper(),state[purpose]['api_key'])
        set_key(args.env_file,'V3_WK_COLLECTION_ID',state['collection'])
        set_key(args.env_file,'V3_WK_BASE_URL','http://127.0.0.1:18080')
        print('Scoped credential references saved to ignored V3 config; no key printed',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--runtime-dir',required=True);parser.add_argument('--env-file',required=True)
    main(parser.parse_args())
