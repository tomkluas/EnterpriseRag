"""Read-only real-service verification; report contains no credentials or document text."""
import asyncio
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import time
import httpx
from app.core.auth import create_access_token
from app.db.database import SessionLocal
from app.db import crud


async def main():
    with SessionLocal() as db:
        user = next(u for u in crud.list_users(db) if u.role == 'admin' and u.is_active)
        headers = {'Authorization': 'Bearer ' + create_access_token(user.id, user.role)}
    report = {'time': datetime.now(timezone(timedelta(hours=8))).isoformat(), 'checks': [], 'chats': []}
    async with httpx.AsyncClient(base_url='http://localhost:5173', trust_env=False, timeout=45) as client:
        for path, auth, expected in [('/', False, 200), ('/api/health', False, 200), ('/api/v1/auth/me', True, 200),
                                     ('/api/v1/knowledge/list', True, 200), ('/api/v1/admin/users', True, 200),
                                     ('/api/v1/knowledge/list', False, 403)]:
            r = await client.get(path, headers=headers if auth else {})
            report['checks'].append({'path': path, 'authorized': auth, 'status': r.status_code, 'expected': expected, 'pass': r.status_code == expected})
        for vector in [False, True]:
            started = time.monotonic()
            result = {'vector': vector, 'events': [], 'characters': 0, 'health_latency_seconds': []}
            finished = asyncio.Event()
            async def health_probe():
                while not finished.is_set():
                    check_start = time.monotonic()
                    response = await client.get('/api/health', timeout=3)
                    assert response.status_code == 200
                    result['health_latency_seconds'].append(round(time.monotonic()-check_start, 4))
                    try:
                        await asyncio.wait_for(finished.wait(), .5)
                    except TimeoutError:
                        pass
            probe = asyncio.create_task(health_probe())
            try:
                async with client.stream('POST', '/api/v1/chat/stream', headers=headers, json={'session_id': 'live-verification', 'query': '如何转agent？请用三句话回答。', 'scope_vector': vector}) as response:
                    result['status'] = response.status_code
                    async for line in response.aiter_lines():
                        if line.startswith('data: '):
                            item = json.loads(line[6:])
                            if item['event'] not in result['events']:
                                result['events'].append(item['event'])
                            if 'first_event_seconds' not in result:
                                result['first_event_seconds'] = round(time.monotonic()-started, 3)
                            if item['event'] == 'token': result['characters'] += len(item['data'])
                            if item['event'] == 'error': result['error'] = item['data']
                result['seconds'] = round(time.monotonic()-started, 3)
            finally:
                finished.set()
                await probe
            result['completed_answer'] = 'done' in result['events'] and result['characters'] > 0
            result['backend_remained_responsive'] = bool(result['health_latency_seconds']) and max(result['health_latency_seconds']) < 1
            report['chats'].append(result)
        async with httpx.AsyncClient(base_url='http://127.0.0.1:8000', trust_env=False) as direct:
            for path in ['/login', '/knowledge', '/admin', '/setup']:
                r = await direct.get(path)
                report['checks'].append({'path': path, 'server': 'packaged-static', 'status': r.status_code, 'pass': r.status_code == 200 and 'id="app"' in r.text})
    report['validation_scope'] = {
        'cloud_calls': 'real configured DeepSeek and Embedding services; no mocks in this script',
        'automated_tests': '28 backend and 9 frontend cases; cloud I/O replaced only in isolated automated workflows',
        'limits': ['No real business database connections were exercised.', 'Knowledge query requires a working query-embedding API even when vectors are already persisted.', 'Installed Starlette/AnyIO test runs emit MemoryObject stream ResourceWarning messages; tests pass but these warnings were not classified as fixed.', 'Vite build emits CJS and bundle-size warnings.'],
    }
    target = Path(__file__).resolve().parents[2] / 'docs/testing/usability-verification-2026-10-30.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
