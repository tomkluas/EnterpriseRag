"""Configure a new development WeKnora; never qualify or activate a backend.

Run only through the development Configure service. Credentials and resumable
resource IDs remain in the ignored configuration directory, never in stdout.
"""
import json
import hashlib
import os
from pathlib import Path

import httpx
from dotenv import dotenv_values, set_key


def configure(config_path, client):
    config_path = Path(config_path)
    config = dotenv_values(config_path)
    if config.get('V3_MODE') != 'development':
        raise ValueError('Only development configuration is allowed')
    required = ('V3_MODEL_BASE_URL', 'V3_CHAT_MODEL', 'V3_SECRET_MODEL',
                'V3_EMBEDDING_BASE_URL', 'V3_EMBEDDING_MODEL',
                'V3_SECRET_EMBEDDING', 'WK_ADMIN_PASSWORD')
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise ValueError('Missing configuration fields: ' + ', '.join(missing))
    dimension = int(config.get('V3_EMBEDDING_DIMENSION') or 1024)
    if dimension <= 0:
        raise ValueError('Embedding dimension must be positive')
    state_path = config_path.with_name('.weknora-setup.json')
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    fingerprint = hashlib.sha256(json.dumps({key: config.get(key, '') for key in
        required[:-1] + ('V3_EMBEDDING_DIMENSION',)}, sort_keys=True).encode()).hexdigest()
    if state.get('fingerprint') and state['fingerprint'] != fingerprint:
        raise ValueError('Model settings changed. Existing collection was preserved; configure a new collection explicitly instead of silently reusing its index.')
    if state.get('pending'):
        raise ValueError('A previous create has an unknown outcome. Inspect platform resources and private setup state before retrying; no create was repeated.')

    def call(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        if not response.is_success:
            # Never include response text, request headers or model credentials.
            raise RuntimeError(f'{method} {path.split("?")[0]}: HTTP {response.status_code}')
        return response.json()

    def save():
        temporary = state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        temporary.replace(state_path)

    def create(label, path, payload):
        state['pending'] = label
        save()
        result = call('POST', path, json=payload)
        return result

    email = config.get('WK_ADMIN_EMAIL') or 'v3-dev@example.invalid'
    login = client.post('/api/v1/auth/login', json={'email': email, 'password': config['WK_ADMIN_PASSWORD']})
    if login.status_code == 401:
        call('POST', '/api/v1/auth/register', json={
            'username': 'v3-dev', 'email': email, 'password': config['WK_ADMIN_PASSWORD']})
        login = client.post('/api/v1/auth/login', json={'email': email, 'password': config['WK_ADMIN_PASSWORD']})
    if not login.is_success:
        raise RuntimeError(f'WeKnora login failed: HTTP {login.status_code}')
    auth = login.json()
    client.headers['Authorization'] = 'Bearer ' + auth['token']
    state['fingerprint'] = fingerprint
    save()
    for role, model, kind, base, key in [
        ('embedding', config['V3_EMBEDDING_MODEL'], 'Embedding', config['V3_EMBEDDING_BASE_URL'], config['V3_SECRET_EMBEDDING']),
        ('chat', config['V3_CHAT_MODEL'], 'KnowledgeQA', config['V3_MODEL_BASE_URL'], config['V3_SECRET_MODEL'])
    ]:
        if role not in state:
            parameters = {'base_url': base, 'api_key': key}
            if role == 'embedding':
                parameters['embedding_parameters'] = {'dimension': dimension, 'supports_dimension_override': True}
            created = create(role, '/api/v1/models', {
                'name': model, 'type': kind, 'source': 'remote', 'parameters': parameters})
            state[role] = created['data']['id']
            state.pop('pending', None)
            save()
    if 'collection' not in state:
        result = create('collection', '/api/v1/knowledge-bases', {
            'name': 'V3 development', 'type': 'document',
            'embedding_model_id': state['embedding'], 'summary_model_id': state['chat'],
            'question_generation_config': {'enabled': True, 'question_count': 1},
            'chunking_config': {'chunk_size': 800, 'chunk_overlap': 100}})
        state['collection'] = result['data']['id']
        state.pop('pending', None)
        save()
    tenant = auth['active_tenant']
    tenant = tenant['id'] if isinstance(tenant, dict) else tenant
    for purpose, capabilities in [('read', ['retrieve']), ('write', ['retrieve', 'ingest'])]:
        if purpose not in state:
            result = create(purpose, f'/api/v1/tenants/{tenant}/api-keys', {
                'name': 'V3 development ' + purpose, 'full_access': False,
                'knowledge_base_ids': [state['collection']], 'capabilities': capabilities})
            state[purpose] = result['data']
            state.pop('pending', None)
            save()
        set_key(str(config_path), 'V3_SECRET_WK_' + purpose.upper(), state[purpose]['api_key'])
    set_key(str(config_path), 'V3_WK_COLLECTION_ID', state['collection'])
    set_key(str(config_path), 'V3_WK_BASE_URL', 'http://weknora:8080')
    print('WeKnora models, collection and scoped credentials configured. Backend validation/activation is still required.')


if __name__ == '__main__':
    try:
        with httpx.Client(base_url='http://weknora:8080', timeout=45) as client:
            configure(os.environ['V3_SETUP_CONFIG'], client)
    except Exception as error:
        # Preserve fixed diagnostics while suppressing secret-bearing transport details.
        if type(error) in (ValueError, RuntimeError):
            raise SystemExit(str(error)) from None
        raise SystemExit('Configuration failed; inspect platform health and private setup state. No credentials printed.') from None
