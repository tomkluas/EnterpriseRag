import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
import httpx
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from .auth import authenticator
from .db import Database
from .domain import Domain, DomainError, one, rows, dump
from .models import User, Message, Document, BackendInstance, Job, Audit
from .knowledge.registry import BackendRegistry
from .knowledge.contracts import BackendError
from .chat.service import ChatService
from .chat.models import ModelService
from .operations import snapshot as operations_snapshot


class Payload(BaseModel): model_config=ConfigDict(extra='forbid')
class NewSpace(Payload):
    name:str=Field(min_length=1,max_length=180)
    required:list[str]=Field(default_factory=lambda:['segment'])
class BackendPayload(Payload):
    provider:str
    base_url:str
    key_ref:str
    write_key_ref:str=''
    manage_key_ref:str=''
    collection_config:dict=Field(default_factory=dict)
class Query(Payload):
    space_id:str
    query:str=Field(min_length=1,max_length=20000)
class SwitchPayload(Payload): target_instance_id:str
class Activate(Payload): expected_route_revision:int=Field(ge=0)
class Grant(Payload):
    readers:list[str]=Field(default_factory=list)
    denied:list[str]=Field(default_factory=list)
class UserPayload(Payload):
    id:str=Field(min_length=1,max_length=128)
    role:str='reader'
    groups:list[str]=Field(default_factory=list)
    active:bool=True
class Feedback(Payload): text:str=Field(max_length=2000)


def create_app(settings,domain=None):
    client=httpx.AsyncClient()
    if domain is None: domain=Domain(Database(settings.database_url),settings.objects_dir,development=settings.mode=='development')
    registry=BackendRegistry(client); chat=ChatService(domain,registry,ModelService(settings,client))
    @asynccontextmanager
    async def lifespan(app):
        yield
        await client.aclose()
    app=FastAPI(title='EnterpriseRag V3',version='3.0.0',lifespan=lifespan)
    app.state.domain=domain; app.state.chat=chat; app.state.settings=settings
    identity=authenticator(settings,domain,client); tenant=settings.tenant_id
    @app.exception_handler(DomainError)
    async def domain_error(request,error): return JSONResponse({'detail':str(error)},status_code=error.status)
    @app.exception_handler(BackendError)
    async def backend_error(request,error): return JSONResponse({'detail':'底座操作未完成','code':error.code},status_code=503)
    @app.get('/health')
    def health(): return {'version':'3.0.0','runtime_verified':False}
    @app.get('/health/ready')
    def ready():
        from sqlalchemy import select,literal
        try:
            with domain.db.transaction() as s:s.execute(select(literal(1)))
        except Exception:
            return JSONResponse({'ready':False},status_code=503)
        return {'ready':True,'scope':'api_database_only'}
    @app.get('/api/v3/operations')
    def operations(user=Depends(identity)):
        return operations_snapshot(domain,tenant,user)
    @app.get('/api/v3/me')
    def me(user=Depends(identity)):
        with domain.db.transaction(tenant,shared=True) as s: return dump(domain.user(s,tenant,user))
    @app.get('/api/v3/spaces')
    def spaces(user=Depends(identity)): return domain.list_spaces(tenant,user)
    @app.post('/api/v3/spaces',status_code=201)
    def create_space(payload:NewSpace,user=Depends(identity)):
        return {'id':domain.create_space(tenant,user,payload.name,payload.required)}
    @app.get('/api/v3/spaces/{space_id}/documents')
    def documents(space_id:str,user=Depends(identity)): return domain.list_documents(tenant,user,space_id)
    @app.post('/api/v3/documents',status_code=202)
    async def upload(space_id:str=Form(...),document_id:str=Form(''),idempotency_key:str=Form(''),file:UploadFile=File(...),user=Depends(identity)):
        content=await file.read(50*1024*1024+1)
        return await asyncio.to_thread(domain.receive,tenant,user,space_id,file.filename or '',content,idempotency_key or None,document_id or None)
    @app.post('/api/v3/documents/{document_id}/grants')
    def grant(document_id:str,payload:Grant,user=Depends(identity)):
        domain.grant(tenant,user,document_id,payload.readers,payload.denied); return {'updated':True}
    @app.post('/api/v3/documents/{document_id}/versions/{version_id}/publish')
    def publish(document_id:str,version_id:str,user=Depends(identity)):
        from .models import Version
        with domain.db.transaction(tenant,shared=True) as s:
            if one(s,Version,tenant,version_id).document_id!=document_id: raise DomainError('版本不属于该文档',403)
        domain.publish(tenant,user,version_id); return {'published':True}
    @app.post('/api/v3/documents/{document_id}/versions/{version_id}/cancel')
    def cancel_version(document_id:str,version_id:str,user=Depends(identity)):
        domain.cancel_version(tenant,user,document_id,version_id);return {'cancelled':True}
    @app.delete('/api/v3/documents/{document_id}',status_code=202)
    def withdraw(document_id:str,user=Depends(identity)):
        domain.withdraw(tenant,user,document_id); return {'withdrawn':True}
    @app.get('/api/v3/documents/{document_id}/source')
    def original(document_id:str,version_id:str|None=None,user=Depends(identity)):
        content=domain.read_original(tenant,user,document_id,version_id)
        return Response(content,media_type='application/octet-stream',headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
    @app.get('/api/v3/backends')
    def backends(user=Depends(identity)): return domain.list_backends(tenant,user)
    @app.post('/api/v3/backends',status_code=201)
    def backend(payload:BackendPayload,user=Depends(identity)):
        return {'id':domain.register_backend(tenant,user,**payload.model_dump())}
    @app.post('/api/v3/spaces/{space_id}/backend-switches',status_code=202)
    def prepare_switch(space_id:str,payload:SwitchPayload,user=Depends(identity)):
        return {'id':domain.prepare_switch(tenant,user,space_id,payload.target_instance_id)}
    @app.get('/api/v3/spaces/{space_id}/backend-switches/{switch_id}')
    def switch_status(space_id:str,switch_id:str,user=Depends(identity)):
        result=domain.switch_status(tenant,user,switch_id)
        if result['space_id']!=space_id: raise DomainError('切换任务不属于该知识库',404)
        return result
    @app.post('/api/v3/spaces/{space_id}/backend-switches/{switch_id}/activate')
    def activate(space_id:str,switch_id:str,payload:Activate,user=Depends(identity)):
        if domain.switch_status(tenant,user,switch_id)['space_id']!=space_id: raise DomainError('切换任务不属于该知识库',404)
        domain.activate_switch(tenant,user,switch_id,payload.expected_route_revision); return {'activated':True}
    @app.get('/api/v3/jobs')
    def jobs(user=Depends(identity)): return domain.list_jobs(tenant,user)
    @app.post('/api/v3/jobs/{job_id}/retry')
    def retry(job_id:str,user=Depends(identity)):
        with domain.db.transaction(tenant) as s:
            domain.admin(s,tenant,user); job=one(s,Job,tenant,job_id)
            if job.kind not in ('ingest','replicate','delete'):
                raise DomainError('维护任务必须通过对应管理命令恢复，不能进入普通任务队列')
            if job.state not in ('failed','queued'): raise DomainError('未知远端结果必须先人工对账，不能盲目重试')
            job.state='queued'; job.next_run=0; job.failures=0
        return {'queued':True}
    @app.get('/api/v3/users')
    def users(user=Depends(identity)):
        with domain.db.transaction(tenant,shared=True) as s:
            domain.admin(s,tenant,user); return [dump(u) for u in rows(s,User,tenant)]
    @app.post('/api/v3/users')
    def update_user(payload:UserPayload,user=Depends(identity)):
        domain.add_user(tenant,user,payload.id,payload.role,payload.groups,payload.active); return {'updated':True}
    @app.get('/api/v3/audit')
    def audit(user=Depends(identity)):
        with domain.db.transaction(tenant,shared=True) as s:
            domain.admin(s,tenant,user); return [dump(a) for a in rows(s,Audit,tenant)][-200:]
    @app.get('/api/v3/conversations')
    def conversations(user=Depends(identity)):
        from .models import Conversation
        with domain.db.transaction(tenant,shared=True) as s: return [dump(c) for c in rows(s,Conversation,tenant,owner=user)]
    @app.post('/api/v3/conversations',status_code=201)
    def conversation(user=Depends(identity)): return {'id':domain.create_conversation(tenant,user)}
    @app.get('/api/v3/conversations/{conversation_id}/messages')
    def history(conversation_id:str,user=Depends(identity)): return domain.history(tenant,user,conversation_id)
    @app.post('/api/v3/conversations/{conversation_id}/messages/stream')
    async def stream(conversation_id:str,payload:Query,request:Request,user=Depends(identity)):
        # Authorize before returning HTTP 200, not only inside the generator.
        await asyncio.to_thread(domain.history,tenant,user,conversation_id)
        async def events():
            source=chat.answer(tenant,user,conversation_id,payload.space_id,payload.query).__aiter__(); pending=None
            try:
                async with asyncio.timeout(180):
                    while True:
                        pending=asyncio.create_task(anext(source,None))
                        while not pending.done():
                            await asyncio.wait({pending},timeout=5)
                            if await request.is_disconnected(): return
                            if not pending.done(): yield ': heartbeat\n\n'
                        value=pending.result(); pending=None
                        if value is None: break
                        yield 'data: '+json.dumps(value,ensure_ascii=False)+'\n\n'
            except (DomainError,BackendError) as error:
                yield 'data: '+json.dumps({'event':'error','data':str(error)},ensure_ascii=False)+'\n\n'
            except Exception:
                yield 'data: '+json.dumps({'event':'error','data':'回答未完成，请检查服务配置和任务状态'},ensure_ascii=False)+'\n\n'
            finally:
                if pending is not None:
                    pending.cancel()
                    try: await pending
                    except (asyncio.CancelledError,Exception): pass
                await source.aclose()
        return StreamingResponse(events(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})
    @app.post('/api/v3/messages/{message_id}/feedback')
    def feedback(message_id:str,payload:Feedback,user=Depends(identity)):
        with domain.db.transaction(tenant) as s:
            message=one(s,Message,tenant,message_id)
            if message.owner!=user: raise DomainError('不能修改他人反馈',403)
            message.feedback=payload.text
        return {'saved':True}
    return app
