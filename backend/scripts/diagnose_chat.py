"""Read-only local diagnosis; credentials and response text are never printed."""
import asyncio
import json
import time
import httpx
from app.core.auth import create_access_token
from app.db.database import SessionLocal
from app.db import crud


async def main():
    with SessionLocal() as db:
        user = next(u for u in crud.list_users(db) if u.role == 'admin' and u.is_active)
        token = create_access_token(user.id, user.role)
        print('configs', json.dumps([{'provider': c.provider, 'model': c.model_string, 'default': c.is_default} for c in crud.list_llm_configs(db)]))
        print('knowledge', json.dumps([{'id': k.id, 'documents': k.doc_count} for k in crud.list_kbs(db)]))
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000', trust_env=False, timeout=45) as client:
        headers = {'Authorization': f'Bearer {token}'}
        for path in ['/api/health', '/api/v1/setup/status', '/api/v1/auth/me', '/api/v1/knowledge/list', '/api/v1/admin/system-info']:
            started = time.monotonic()
            try:
                r = await client.get(path, headers=headers)
                print(path, r.status_code, round(time.monotonic()-started, 3), flush=True)
            except Exception as e:
                print(path, type(e).__name__, flush=True)
        for vector in [False, True]:
            started = time.monotonic()
            print('chat start vector=', vector, flush=True)
            try:
                async with client.stream('POST', '/api/v1/chat/stream', headers=headers, json={'session_id': 'diagnostic', 'query': '如何转agent？请简要回答。', 'scope_vector': vector}) as r:
                    print('headers', r.status_code, round(time.monotonic()-started, 3), flush=True)
                    events, chars = [], 0
                    async for line in r.aiter_lines():
                        if line.startswith('data: '):
                            item = json.loads(line[6:])
                            event = item.get('event')
                            if event not in events:
                                events.append(event)
                                print('event', event, round(time.monotonic()-started, 3), flush=True)
                            if event == 'token': chars += len(item['data'])
                            if event == 'error': print('error', item['data'], flush=True)
                    print('chat end', events, 'chars', chars, round(time.monotonic()-started, 3), flush=True)
            except Exception as e:
                print('chat exception', type(e).__name__, str(e)[:200], round(time.monotonic()-started, 3), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
