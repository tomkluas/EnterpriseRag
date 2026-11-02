# 依赖既有合成试点库、回环服务及真实模型的容量演练；只在独立命名的测试资源中写入，结束需核对恢复与清理结果。
"""Explicitly opted-in, reversible capacity exercise against the existing synthetic live-pilot DB."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dotenv import dotenv_values
import httpx
from sqlalchemy import select
from sqlalchemy.engine import make_url
from app.v3.config import Settings
from app.v3.db import Database
from app.v3.domain import Domain
from app.v3.models import Document, Version, Replica, Space, BackendInstance
from app.v3.recovery import _private_directory
import acceptance


# 只包装本轮临时 API 的异常观测，记录类型和应用栈位置；正文和凭据不能进入诊断日志。
def create_diagnostic_app():
    # Only this disposable API instance gets exception-type diagnostics. Never
    # log exception text, questions, answer tokens, request URLs or credentials.
    from app.main import app
    chat=app.state.chat
    retrieve=chat.retrieve
    async def observed_retrieve(*args,**kwargs):
        try:
            return await retrieve(*args,**kwargs)
        except BaseException as error:
            def describe(exc):
                result={'type':type(exc).__name__, 'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(exc.__traceback__) if '/app/' in f.filename.replace('\\','/')]}
                if isinstance(exc,BaseExceptionGroup):
                    result['children']=[describe(child) for child in exc.exceptions]
                return result
            print('CAPACITY_RETRIEVE_ERROR '+json.dumps(describe(error)),flush=True)
            raise
    chat.retrieve=observed_retrieve
    answer=chat._answer
    async def observed_answer(*args,**kwargs):
        source=answer(*args,**kwargs)
        try:
            async for event in source:
                yield event
        except BaseException as error:
            detail={'type':type(error).__name__, 'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(error.__traceback__) if '/app/' in f.filename.replace('\\','/')]}
            print('CAPACITY_CHAT_ERROR '+json.dumps(detail),flush=True)
            raise
        finally:
            await source.aclose()
    chat._answer=observed_answer
    return app


# 严格核对源端点和合成测试库命名，再改写数据库名，避免压测误连日常业务库。
def isolated_url(source, test_name):
    url = make_url(source)
    if (url.host, url.port, url.database) != ('127.0.0.1', 18433, 'enterpriserag_v3'):
        raise ValueError('Unexpected source database; refusing writes')
    if not re.fullmatch(r'v3_live_[0-9a-f]{32}', test_name):
        raise ValueError('Invalid pilot test database')
    return url.set(database=test_name).render_as_string(hide_password=False)


# 从已知合成文件名重建预期内容，用哈希核对资料身份，拒绝拿未知原件参与在线演练。
def expected_original(name):
    match = re.fullmatch(r'allowed-([0-9a-f]{32})\.txt', name)
    if not match:
        raise ValueError('Not the synthetic allowed fixture')
    run = match[1]
    body = f'本次合成制度新版 {run}：差旅报销标准为每天246元。policy standard 246。'.encode()
    return 'updated-'+run+'.md', body


# 记录应用源文件摘要，便于把测量结果对应到本次代码版本，避免不同实现的结果混用。
def application_fingerprint():
    entries={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted((ROOT/'backend/app').rglob('*.py'))}
    return {'sha256':acceptance.fingerprint(entries),'file_count':len(entries)}


# 演练会临时调整合成资料授权并启动专用 API；退出时先停止在途请求，再恢复授权，清理失败必须反映在报告中。
async def exercise(args):
    stop_file = args.output.with_suffix('.stop')
    if stop_file.exists():
        raise ValueError('Remove stale stop file before explicitly starting a new run')
    config = {key:value for key,value in dotenv_values(ROOT/'v3.local').items() if value is not None}
    pilot = json.loads((ROOT/'docs/testing/v3/live-pilot/weknora-local-pilot.json').read_text(encoding='utf-8-sig'))
    test_name = pilot['test_database']
    config['V3_DATABASE_URL'] = isolated_url(config['V3_DATABASE_URL'], test_name)
    reader = 'capacity-'+uuid.uuid4().hex
    token = secrets.token_urlsafe(48)
    private = Path(tempfile.gettempdir()).resolve()/('v3-capacity-'+uuid.uuid4().hex)
    _private_directory(private)
    config.update(V3_MODE='development', V3_TENANT_ID='live', V3_BOOTSTRAP_USER=reader,
                  V3_BOOTSTRAP_TOKEN=token, V3_OBJECTS_DIR=str(private/'objects'), V3_OBJECTS_URI='')
    (private/'objects').mkdir()
    env_path = private/'capacity.env'
    env_path.write_text('\n'.join(key+'='+json.dumps(value,ensure_ascii=False) for key,value in config.items())+'\n',encoding='utf-8')
    child_env = {key:value for key,value in os.environ.items() if not key.startswith('V3_')}
    child_env.update(PYTHONPATH=os.pathsep.join([str(ROOT/'backend'),str(ROOT/'tools/v3')]), V3_ENV_FILE=str(env_path))
    for key,value in config.items():
        os.environ[key] = value
    settings = Settings.from_env()
    db = Database(settings.database_url)
    domain = Domain(db, settings.objects_dir, development=True)
    server = None
    granted = []
    created_reader = False
    log = None
    report = None
    source_started=application_fingerprint()
    try:
        # This URL has already been constrained to the separate, preexisting pilot database.
        db.migrate()
        with db.transaction('live',shared=True) as session:
            docs = list(session.scalars(select(Document).where(Document.tenant_id=='live', Document.name.like('allowed-%'), Document.withdrawn.is_(False))))
            if len(docs) != 1:
                raise ValueError('Expected exactly one synthetic allowed document')
            document = docs[0]
            version = session.get(Version, document.current_version)
            filename, original = expected_original(document.name)
            if not version or version.filename != filename or version.sha256 != hashlib.sha256(original).hexdigest():
                raise ValueError('Published synthetic version does not match known 246 fixture')
            space = session.get(Space, document.space_id)
            backend = session.get(BackendInstance, space.active_backend)
            if backend.base_url != 'http://127.0.0.1:18080' or backend.provider != 'weknora':
                raise ValueError('Expected unchanged local WeKnora route')
            replica = session.scalar(select(Replica).where(Replica.tenant_id=='live',Replica.version_id==version.id,Replica.instance_id==backend.id))
            if not replica or replica.state != 'ready' or replica.chunks < 1:
                raise ValueError('Published fixture has no ready active chunks')
            saved = (document.id, list(document.readers), list(document.denied))
            space_id = space.id
            metadata = dict(document_id=document.id, version_id=version.id, source_sha256=version.sha256,
                            active_chunks=replica.chunks, route_revision=space.route_revision,
                            instance_id=backend.id, bytes=len(original),active_backend_provider=backend.provider)
        image=subprocess.check_output(['docker','inspect','WeKnora-app','--format','{{.Image}}'],text=True).strip()
        if not re.fullmatch(r'sha256:[0-9a-f]{64}',image):
            raise ValueError('Unable to fingerprint active backend image')
        metadata['active_backend_image']=image
        domain.add_user('live','admin',reader,'reader')
        created_reader = True
        granted.append(saved)
        domain.grant('live','admin',saved[0],saved[1]+[reader],saved[2])
        scope = domain.scope('live',reader,space_id)
        if len(scope.bindings) != 1 or scope.bindings[0].document_id != saved[0]:
            raise ValueError('Reader scope must contain only the synthetic allowed document')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0))
            port = listener.getsockname()[1]
        log = (private/'server.log').open('wb')
        server = subprocess.Popen([sys.executable,'-m','uvicorn','run_isolated_capacity:create_diagnostic_app','--factory','--host','127.0.0.1','--port',str(port),'--no-access-log'],
                                  cwd=ROOT,env=child_env,stdout=log,stderr=log,
                                  creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        target = f'http://127.0.0.1:{port}'
        async with httpx.AsyncClient(base_url=target,headers={'Authorization':'Bearer '+token}) as client:
            for attempt in range(60):
                if server.poll() is not None:
                    raise RuntimeError('Isolated server exited; private log retained until cleanup')
                try:
                    me = await client.get('/api/v3/me')
                    if me.status_code == 200:
                        identity=me.json()
                        if identity['id'] != reader or identity['role'] != 'reader' or identity['tenant_id'] != 'live':
                            raise RuntimeError('Unexpected API identity')
                        break
                except httpx.ConnectError:
                    pass
                await asyncio.sleep(.5)
            else:
                raise RuntimeError('Isolated API did not become ready')
        os.environ['V3_CAPACITY_EPHEMERAL_TOKEN'] = token
        capacity_args = argparse.Namespace(target=target,space_id=space_id,question='本次合成制度的差旅报销标准是多少元？简短回答并引用资料。',
            token_env='V3_CAPACITY_EPHEMERAL_TOKEN',duration=args.duration,concurrency=args.concurrency,timeout=180,expect_substring=['246'])
        context = dict(host=dict(machine=platform.node(),platform=platform.platform(),logical_cpus=os.cpu_count(),python=platform.python_version()),
            model=dict(chat=settings.chat_model,rerank=settings.rerank_model,chat_endpoint=make_url(settings.model_base_url).host),
            network='loopback API/PostgreSQL/WeKnora; configured remote cloud model endpoints',
            original_size=dict(bytes=metadata['bytes'],sha256=metadata['source_sha256']),document_count=1,
            active_chunks=metadata['active_chunks'],load_phase='steady continuous closed-loop; includes initial cold requests')
        completed = []
        original_request = acceptance.request_once
        async def observe(*values,**kwargs):
            row = await original_request(*values,**kwargs)
            completed.append(row)
            return row
        acceptance.request_once = observe
        async def progress():
            while True:
                await asyncio.sleep(30)
                print(json.dumps(dict(event='progress',completed=len(completed),successes=sum(r['success'] for r in completed),
                                      failures={reason:sum(r.get('failure')==reason for r in completed) for reason in sorted({r.get('failure') for r in completed if r.get('failure')})})),flush=True)
        ticker=asyncio.create_task(progress())
        print(json.dumps(dict(event='started',server_pid=server.pid,runner_pid=os.getpid(),target=target,test_database=test_name,
                              duration_s=args.duration,concurrency=args.concurrency,authorized_document_count=1,active_chunks=metadata['active_chunks'])),flush=True)
        run_started=time.monotonic()
        capacity_task=asyncio.create_task(acceptance.run_capacity(capacity_args,context))
        async def stop_requested():
            while not stop_file.exists():
                await asyncio.sleep(1)
            capacity_task.cancel()
        stop_task=asyncio.create_task(stop_requested())
        try:
            report = await capacity_task
        except asyncio.CancelledError:
            elapsed=time.monotonic()-run_started
            report=acceptance.capacity_report(completed,elapsed_s=elapsed,load_s=min(elapsed,args.duration),concurrency=args.concurrency,context=context)
            report.update(passed=False,interrupted=True,stop_reason='operator stop file')
        finally:
            ticker.cancel()
            stop_task.cancel()
            acceptance.request_once = original_request
        report.update(scenario='isolated_single_synthetic_published_document',test_database=test_name,fixture=metadata,
                      production_acceptance='not_complete',large_authorized_scope='not_tested')
        source_finished=application_fingerprint()
        source_unchanged=source_started==source_finished
        report['application_source']={'started':source_started,'finished':source_finished,'unchanged':source_unchanged,
                                      'note':'Uncommitted workspace; Git HEAD does not identify the tested source. Hash covers backend/app/**/*.py only.'}
        report['gates']['source_unchanged']=source_unchanged
        report['passed']=report['passed'] and source_unchanged
        report['limitations'].extend(['Only one synthetic authorized document; not representative enterprise workload.',
                                     'No Worker started; no backend route changed; cloud model calls were real.'])
    finally:
        cleanup_errors=[]
        # Close only our API first, releasing any unfinished request transactions
        # before restoring authorization under the tenant's exclusive lock.
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
        for document_id,readers,denied in granted:
            try:
                domain.grant('live','admin',document_id,readers,denied)
            except Exception:
                cleanup_errors.append('grant_restore_failed')
        if created_reader:
            try:
                domain.add_user('live','admin',reader,'reader',active=False)
            except Exception:
                cleanup_errors.append('reader_disable_failed')
        if log:
            log.close()
        if report is not None and (private/'server.log').exists():
            server_log=(private/'server.log').read_text(encoding='utf-8',errors='replace')
            report['server_error_counts']={key:server_log.count(key) for key in ('LockNotAvailable','lock timeout','different Context','Exception in ASGI application')}
            report['retrieval_error_diagnostics']=[json.loads(line.split(' ',1)[1]) for line in server_log.splitlines() if line.startswith('CAPACITY_RETRIEVE_ERROR ')]
            report['chat_error_diagnostics']=[json.loads(line.split(' ',1)[1]) for line in server_log.splitlines() if line.startswith('CAPACITY_CHAT_ERROR ')]
        db.engine.dispose()
        os.environ.pop('V3_CAPACITY_EPHEMERAL_TOKEN',None)
        if private.parent == Path(tempfile.gettempdir()).resolve() and private.name.startswith('v3-capacity-'):
            shutil.rmtree(private)
        if report is not None:
            report['cleanup'] = dict(original_grants_restored=not cleanup_errors,temporary_reader_disabled=not cleanup_errors,
                                     own_server_stopped=server is None or server.poll() is not None,private_directory_removed=not private.exists(),errors=cleanup_errors)
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        if cleanup_errors:
            raise RuntimeError('Isolated test cleanup failed: '+','.join(cleanup_errors))
    print(json.dumps(dict(event='finished',passed=report['passed'],requests=report['request_count'],elapsed_s=report['elapsed_s'],output=str(args.output))),flush=True)


# 仅验证隔离守卫的接受与拒绝条件，不加载本地配置、不连接服务，也不发送压测请求。
def self_test():
    # Pure guards only: never read local configuration or connect to services.
    source='postgresql://u:p@127.0.0.1:18433/enterpriserag_v3'
    name='v3_live_'+'a'*32
    assert make_url(isolated_url(source,name)).database == name
    for bad_source,bad_name in [(source,'enterpriserag_v3'),(source.replace('127.0.0.1','remote'),name),
                                (source.replace('18433','5432'),name),(source.replace('enterpriserag_v3','another'),name)]:
        try:
            isolated_url(bad_source,bad_name)
        except ValueError:
            pass
        else:
            raise AssertionError('Guard accepted unsafe target')
    filename,body=expected_original('allowed-'+'a'*32+'.txt')
    assert filename.endswith('.md') and b'246' in body
    print('PASS: isolated target guards and fixed published fixture identity')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true',help='Explicitly authorize real model calls and isolated test DB writes')
    parser.add_argument('--duration',type=float,default=1800)
    parser.add_argument('--concurrency',type=int,default=10)
    parser.add_argument('--preflight',action='store_true',help='Allow an explicitly nonpassing short diagnostic run')
    parser.add_argument('--output',type=Path,default=ROOT/'docs/testing/v3/capacity-2026-10-31.json')
    args=parser.parse_args()
    if not args.execute:
        self_test()
    elif args.duration < 1800 and not args.preflight:
        parser.error('Real exercise must sustain at least 1800 seconds')
    else:
        try:
            asyncio.run(exercise(args))
        except Exception as error:
            print('FAILED: '+type(error).__name__+' (details suppressed to protect credentials)',flush=True)
            raise SystemExit(1)
