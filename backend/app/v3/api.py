# HTTP 接口装配层：只做身份依赖、请求结构校验、业务调用与响应转换。
# 租户取自服务配置和已验证身份上下文，具体授权与状态变更仍交给 Domain 在事务中执行。

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
import httpx
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
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


# 拒绝未声明字段，避免客户端通过额外属性伪造验收状态或其他内部配置。
class Payload(BaseModel): model_config=ConfigDict(extra='forbid')
# 新建知识库的名称、能力要求及可选角色阅读范围。
class NewSpace(Payload):
    name:str=Field(min_length=1,max_length=180)
    required:list[str]=Field(default_factory=lambda:['segment'])
    role_ids:list[str]|None=None
    denied_role_ids:list[str]=Field(default_factory=list)
# 登记知识服务的连接参数；这里只接受密钥环境变量引用。
class BackendPayload(Payload):
    provider:str
    base_url:str
    key_ref:str
    write_key_ref:str=''
    manage_key_ref:str=''
    collection_config:dict=Field(default_factory=dict)
# 一次问答的知识库范围与用户原问题，限制长度避免无界输入。
class Query(Payload):
    space_id:str
    query:str=Field(min_length=1,max_length=20000)
# 准备底座切换时选择的目标实例。
class SwitchPayload(Payload): target_instance_id:str
# 切换提交携带期望路由版本，避免覆盖其他操作者已完成的切换。
class Activate(Payload): expected_route_revision:int=Field(ge=0)
# 知识库/资料的角色授权规则；inherit 仅在资料层用于追加默认允许角色。
class Grant(Payload):
    role_ids:list[str]=Field(default_factory=list)
    denied_role_ids:list[str]=Field(default_factory=list)
    inherit:bool=False
# 成员身份、组织和角色分配；密码仅供可选创建/重置，不返回给成员列表。
class UserPayload(Payload):
    id:str=Field(min_length=1,max_length=128)
    role:str='reader'
    role_ids:list[str]|None=None
    org_unit_id:str|None=None
    display_name:str=Field(default='',max_length=128)
    active:bool=True
    password:str|None=Field(default=None,min_length=12,max_length=128)
# 本机账号密码登录输入，不允许附带角色或租户权限声明。
class LoginPayload(Payload):
    id:str=Field(min_length=1,max_length=128)
    password:str=Field(min_length=1,max_length=128)
# 当前消息所有者提交的回答反馈。
class Feedback(Payload): text:str=Field(max_length=2000)
# 角色定义与系统能力集合，内置角色限制由业务层校验。
class RolePayload(Payload):
    id:str|None=None
    name:str=Field(min_length=1,max_length=128)
    description:str=Field(default='',max_length=2000)
    permissions:list[str]=Field(default_factory=list)
    active:bool=True
# 部门父子关系与状态，由业务层检查循环和同级重名。
class OrganizationPayload(Payload):
    id:str|None=None
    name:str=Field(min_length=1,max_length=128)
    parent_id:str=''
    active:bool=True
# 当前公司可编辑的信息；租户标识不通过此接口变更。
class CompanyPayload(Payload):name:str=Field(min_length=1,max_length=128)


# 构造共享 HTTP 客户端、知识服务与问答服务，再注册统一身份依赖和异常处理。
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
    @app.exception_handler(RequestValidationError)
    # 校验错误只返回位置、类型与说明，去掉可能回显明文密码的原始输入。
    async def validation_error(request,error):
        # 默认校验错误中的 input 字段可能回显提交的密码，因此只返回经过筛选的错误信息。
        return JSONResponse({'detail':[{key:item[key] for key in ('loc','msg','type')}
            for item in error.errors()]},status_code=422)
    @app.post('/api/v3/auth/login')
    # 把本机密码验证放到登录服务中，返回的会话令牌响应禁止缓存。
    def login(payload:LoginPayload,request:Request):
        from .passwords import issue_session
        result=issue_session(domain,tenant,payload.id,payload.password,request.client.host if request.client else 'unknown')
        return JSONResponse(result,headers={'Cache-Control':'no-store'})
    @app.post('/api/v3/auth/logout')
    # 只撤销本次请求使用的本机会话摘要；企业身份令牌仍由上游身份系统管理。
    def logout(request:Request,user=Depends(identity)):
        import hashlib
        from .models import LoginSession
        token=request.headers.get('authorization','')[7:]
        with domain.db.transaction(tenant) as s:
            row=s.get(LoginSession,(tenant,hashlib.sha256(token.encode()).hexdigest()))
            if row is not None: s.delete(row)
            domain.audit(s,tenant,user,'user.logout',user)
        return {'logged_out':True}
    @app.exception_handler(DomainError)
    # 把可解释的业务失败及结构化占用明细交给前端展示。
    async def domain_error(request,error):
        body={'detail':str(error)}
        if error.details is not None:body['details']=error.details
        return JSONResponse(body,status_code=error.status)
    @app.exception_handler(BackendError)
    # 对远端故障返回固定业务说明与错误码，不透传底座敏感响应。
    async def backend_error(request,error): return JSONResponse({'detail':'底座操作未完成','code':error.code},status_code=503)
    @app.get('/health')
    # 进程存活探针不代表知识底座已完成运行验收。
    def health(): return {'version':'3.0.0','runtime_verified':False}
    @app.get('/health/ready')
    # 就绪检查只验证 API 到业务数据库的连接，不伪造模型或底座健康。
    def ready():
        from sqlalchemy import select,literal
        try:
            with domain.db.transaction() as s:s.execute(select(literal(1)))
        except Exception:
            return JSONResponse({'ready':False},status_code=503)
        return {'ready':True,'scope':'api_database_only'}
    @app.get('/api/v3/operations')
    # 读取管理员运行概览，监控结果与内容数据分开返回。
    def operations(user=Depends(identity)):
        return operations_snapshot(domain,tenant,user)
    @app.get('/api/v3/me')
    # 从当前数据库重新组装角色与操作能力，不信任令牌中的角色声明。
    def me(user=Depends(identity)):
        with domain.db.transaction(tenant,shared=True) as s: return domain.identity(s,tenant,user)
    @app.get('/api/v3/tenant')
    # 返回当前公司信息，租户边界固定，不提供平台级跨企业管理。
    def company(user=Depends(identity)):
        from .models import Tenant
        with domain.db.transaction(tenant,shared=True) as s:
            domain.user(s,tenant,user);row=s.get(Tenant,tenant)
            return {'id':row.id,'name':row.name,'active':row.active}
    @app.put('/api/v3/tenant')
    # 公司名称与组织根名称在同一事务更新，避免两个页面显示不同名称。
    def update_company(payload:CompanyPayload,user=Depends(identity)):
        from .models import Tenant,OrganizationUnit
        from .access import stable_id
        with domain.db.transaction(tenant) as s:
            domain.require_permission(s,tenant,user,'system.manage')
            if not payload.name.strip():raise DomainError('企业名称不能为空',400)
            s.get(Tenant,tenant).name=payload.name.strip()
            s.get(OrganizationUnit,(tenant,stable_id(tenant,'root'))).name=payload.name.strip()
            domain.audit(s,tenant,user,'tenant.update',tenant)
        return {'updated':True}
    @app.get('/api/v3/permission-catalog')
    # 给角色管理页面提供可分配系统能力的说明，而非开放任意权限字符串。
    def permission_catalog(user=Depends(identity)):
        from .access import PERMISSIONS
        with domain.db.transaction(tenant,shared=True) as s:domain.require_permission(s,tenant,user,'roles.manage')
        return [{'id':id,'label':value[0],'description':value[1]} for id,value in PERMISSIONS.items()]
    @app.get('/api/v3/roles')
    # 按目录访问权限返回本企业角色定义。
    def roles(user=Depends(identity)):return domain.list_roles(tenant,user)
    @app.get('/api/v3/roles/{role_id}/references')
    # 删除前的只读占用查询，列出成员、知识库与资料的全部直接引用。
    def role_references(role_id:str,user=Depends(identity)):
        from .models import Role
        with domain.db.transaction(tenant,shared=True) as s:
            domain.require_permission(s,tenant,user,'roles.manage')
            role=one(s,Role,tenant,role_id);references=domain.role_references(s,tenant,role_id)
            return {'id':role.id,'name':role.name,'references':references,
                'counts':{kind:sum(item['type']==kind for item in references) for kind in ('member','space','document')}}
    @app.post('/api/v3/roles',status_code=201)
    # 将角色新增/编辑请求交给统一的内置角色与权限集合校验。
    def save_role(payload:RolePayload,user=Depends(identity)):return domain.save_role(tenant,user,**payload.model_dump())
    @app.delete('/api/v3/roles/{role_id}')
    # 保留仍被引用的角色，返回可定位占用，不代替管理员自动取消授权。
    def delete_role(role_id:str,user=Depends(identity)):
        domain.delete_role(tenant,user,role_id);return {'deleted':True}
    @app.get('/api/v3/organization')
    # 返回部门树原始记录，成员目录可以复用以选择所属组织。
    def organization(user=Depends(identity)):return domain.list_organization(tenant,user)
    @app.post('/api/v3/organization',status_code=201)
    # 新增或编辑部门时由业务层检查父链与启用规则。
    def save_organization(payload:OrganizationPayload,user=Depends(identity)):return domain.save_organization(tenant,user,**payload.model_dump())
    @app.delete('/api/v3/organization/{organization_id}')
    # 只移除满足无成员、无下级约束的部门。
    def delete_organization(organization_id:str,user=Depends(identity)):
        domain.delete_organization(tenant,user,organization_id);return {'deleted':True}
    @app.get('/api/v3/spaces')
    # 读取当前成员可发现或可维护的知识库。
    def spaces(user=Depends(identity)): return domain.list_spaces(tenant,user)
    @app.post('/api/v3/spaces',status_code=201)
    # 创建知识库并登记默认允许/禁止角色，不同时创建远端索引。
    def create_space(payload:NewSpace,user=Depends(identity)):
        return {'id':domain.create_space(tenant,user,payload.name,payload.required,payload.role_ids,payload.denied_role_ids)}
    @app.get('/api/v3/spaces/{space_id}/grant-options')
    # 资料维护者读取知识库角色选项，不需要取得全体成员目录。
    def space_options(space_id:str,user=Depends(identity)):
        from .models import Space
        with domain.db.transaction(tenant,shared=True) as s:
            domain.manage_space(s,tenant,user,one(s,Space,tenant,space_id));return domain.role_options(s,tenant)
    @app.post('/api/v3/spaces/{space_id}/grants')
    # 知识库默认规则只接收角色标识，拒绝直接给人员或部门授阅读权。
    def space_grants(space_id:str,payload:Grant,user=Depends(identity)):
        domain.set_resource_roles(tenant,user,space_id,payload.role_ids,payload.denied_role_ids,space=True)
        return {'updated':True}
    @app.get('/api/v3/spaces/{space_id}/documents')
    # 由业务层检查工作区访问权，再决定资料可维护性、可阅读性及可见版本。
    def documents(space_id:str,user=Depends(identity)): return domain.list_documents(tenant,user,space_id)
    @app.post('/api/v3/documents',status_code=202)
    # 有界读取原件后转到线程执行登记；返回已接收任务，不等待远端解析完成。
    async def upload(space_id:str=Form(...),document_id:str=Form(''),idempotency_key:str=Form(''),file:UploadFile=File(...),user=Depends(identity)):
        # 多读一个字节即可识别超限文件，避免为判断大小把整个超大文件载入内存。
        content=await file.read(50*1024*1024+1)
        return await asyncio.to_thread(domain.receive,tenant,user,space_id,file.filename or '',content,idempotency_key or None,document_id or None)
    @app.post('/api/v3/documents/{document_id}/grants')
    # 资料规则统一转换为角色授权，inherit 控制是否追加知识库允许范围。
    def grant(document_id:str,payload:Grant,user=Depends(identity)):
        domain.set_resource_roles(tenant,user,document_id,payload.role_ids,payload.denied_role_ids,payload.inherit); return {'updated':True}
    @app.get('/api/v3/documents/{document_id}/grant-options')
    # 资料管理者仅取得可授权角色，不混用成员或旧用户组输入。
    def grant_options(document_id:str,user=Depends(identity)):
        return domain.grant_options(tenant,user,document_id)
    @app.post('/api/v3/documents/{document_id}/versions/{version_id}/publish')
    # 先确认 URL 中资料与版本对应，再由业务层检查索引和发布条件。
    def publish(document_id:str,version_id:str,user=Depends(identity)):
        from .models import Version
        with domain.db.transaction(tenant,shared=True) as s:
            if one(s,Version,tenant,version_id).document_id!=document_id: raise DomainError('版本不属于该文档',403)
        domain.publish(tenant,user,version_id); return {'published':True}
    @app.post('/api/v3/documents/{document_id}/versions/{version_id}/cancel')
    # 取消失败草稿的业务入口，不执行远端不确定清理。
    def cancel_version(document_id:str,version_id:str,user=Depends(identity)):
        domain.cancel_version(tenant,user,document_id,version_id);return {'cancelled':True}
    @app.delete('/api/v3/documents/{document_id}',status_code=202)
    # 立即撤回业务可见性，实际索引删除异步进行。
    def withdraw(document_id:str,user=Depends(identity)):
        domain.withdraw(tenant,user,document_id); return {'withdrawn':True}
    @app.get('/api/v3/documents/{document_id}/source')
    # 下载重新检查当前阅读权，私有原件禁止缓存并防止类型嗅探。
    def original(document_id:str,version_id:str|None=None,user=Depends(identity)):
        content=domain.read_original(tenant,user,document_id,version_id)
        return Response(content,media_type='application/octet-stream',headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
    @app.get('/api/v3/backends')
    # 只向系统管理者返回服务实例配置与环境可用状态。
    def backends(user=Depends(identity)): return domain.list_backends(tenant,user)
    @app.post('/api/v3/backends',status_code=201)
    # 将接口地址字段转换为业务参数；新登记服务仍需真实运行验收。
    def backend(payload:BackendPayload,user=Depends(identity)):
        values=payload.model_dump(); values['url']=values.pop('base_url')
        return {'id':domain.register_backend(tenant,user,**values)}
    @app.post('/api/v3/spaces/{space_id}/backend-switches',status_code=202)
    # 创建目标索引准备任务；此时不改变当前知识库活动底座。
    def prepare_switch(space_id:str,payload:SwitchPayload,user=Depends(identity)):
        return {'id':domain.prepare_switch(tenant,user,space_id,payload.target_instance_id)}
    @app.get('/api/v3/spaces/{space_id}/backend-switches/{switch_id}')
    # 检查切换任务属于 URL 指定知识库后返回进度。
    def switch_status(space_id:str,switch_id:str,user=Depends(identity)):
        result=domain.switch_status(tenant,user,switch_id)
        if result['space_id']!=space_id: raise DomainError('切换任务不属于该知识库',404)
        return result
    @app.post('/api/v3/spaces/{space_id}/backend-switches/{switch_id}/activate')
    # 携带预期路由修订号提交切换，业务层会重新校验全部准备快照。
    def activate(space_id:str,switch_id:str,payload:Activate,user=Depends(identity)):
        if domain.switch_status(tenant,user,switch_id)['space_id']!=space_id: raise DomainError('切换任务不属于该知识库',404)
        domain.activate_switch(tenant,user,switch_id,payload.expected_route_revision); return {'activated':True}
    @app.get('/api/v3/jobs')
    # 资料维护人员只看自己的任务，系统管理者可排查企业任务；普通读者不可访问。
    def jobs(user=Depends(identity)): return domain.list_jobs(tenant,user)
    @app.get('/api/v3/jobs/{job_id}/replicas/{replica_id}/chunks')
    async def job_chunks(job_id:str,replica_id:str,page:int=1,page_size:int=20,user=Depends(identity)):
        # 浏览器只提供本地任务及副本标识，真实连接与凭据由服务端从已验证绑定取得。
        from .jobs.inspection import preview_chunks
        result=await preview_chunks(domain,registry,tenant,user,job_id,replica_id,page=page,page_size=page_size)
        return JSONResponse(result,headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
    @app.post('/api/v3/jobs/{job_id}/retry')
    # 仅允许普通业务任务进入重试队列；维护任务和未知结果须走各自恢复流程。
    def retry(job_id:str,user=Depends(identity)):
        with domain.db.transaction(tenant) as s:
            domain.admin(s,tenant,user); job=one(s,Job,tenant,job_id)
            if job.kind not in ('ingest','replicate','delete'):
                raise DomainError('维护任务必须通过对应管理命令恢复，不能进入普通任务队列')
            if job.state not in ('failed','queued'): raise DomainError('未知远端结果必须先人工对账，不能盲目重试')
            # 此入口只恢复已知可重试任务；未知上传须经过独立对账流程，不能直接清掉不确定状态。
            job.state='queued'; job.next_run=0; job.failures=0
            domain.audit(s,tenant,user,'job.retry',job.id)
        return {'queued':True}
    @app.get('/api/v3/users')
    # 成员目录只返回密码是否启用，不返回摘要；停用成员保留已分配角色以便调整。
    def users(user=Depends(identity)):
        from .models import Credential
        with domain.db.transaction(tenant,shared=True) as s:
            domain.require_permission(s,tenant,user,'members.manage')
            return [{**domain.identity(s,tenant,u.id,include_inactive=True),
                'password_enabled':s.get(Credential,(tenant,u.id)) is not None} for u in rows(s,User,tenant)]
    @app.post('/api/v3/users')
    # 维护成员角色、组织和密码；不再从 HTTP 接口接收旧用户组授权。
    def update_user(payload:UserPayload,user=Depends(identity)):
        domain.add_user(tenant,user,payload.id,payload.role,None,payload.active,payload.password,payload.role_ids,payload.org_unit_id,payload.display_name); return {'updated':True}
    @app.get('/api/v3/admin/trace-conversations')
    def admin_trace_conversations(page:int=1,page_size:int=20,query:str='',owner:str='',state:str='',start:float|None=None,end:float|None=None,user=Depends(identity)):
        # 先筛选命中的会话再分页，摘要统计包含这些会话的完整上下文。
        from .trace_admin import list_conversations
        return list_conversations(domain,tenant,user,page=page,page_size=page_size,query=query,owner=owner,state=state,start=start,end=end)
    @app.get('/api/v3/admin/trace-conversations/{conversation_id}')
    def admin_trace_conversation(conversation_id:str,page:int=1,page_size:int=20,user=Depends(identity)):
        # 管理审计使用独立权限，不复用只能读取本人会话的普通历史入口。
        from .trace_admin import conversation_detail
        return conversation_detail(domain,tenant,user,conversation_id,page=page,page_size=page_size)
    @app.get('/api/v3/admin/traces')
    # 具有独立审计权限的人员按条件分页查看企业问答。
    def admin_traces(page:int=1,page_size:int=20,query:str='',owner:str='',state:str='',start:float|None=None,end:float|None=None,user=Depends(identity)):
        from .trace_admin import list_traces
        return list_traces(domain,tenant,user,page=page,page_size=page_size,query=query,owner=owner,state=state,start=start,end=end)
    @app.get('/api/v3/admin/traces/{message_id}')
    # 审计详情入口会记录查看行为，普通个人会话读取不能借此越权。
    def admin_trace(message_id:str,user=Depends(identity)):
        from .trace_admin import trace_detail
        return trace_detail(domain,tenant,user,message_id)
    @app.get('/api/v3/audit')
    # 全量历史先筛选再分页，固定校验系统管理能力与租户范围。
    def audit(page:int=1,page_size:int=20,query:str='',category:str='',user=Depends(identity)):
        return domain.list_audit(tenant,user,page=page,page_size=page_size,query=query,category=category)
    @app.get('/api/v3/conversations')
    # 个人会话列表固定按 owner 过滤。
    def conversations(user=Depends(identity)):
        return domain.conversations(tenant,user)
    @app.post('/api/v3/conversations',status_code=201)
    # 新建属于当前成员的会话，不接受客户端指定所有者。
    def conversation(user=Depends(identity)): return {'id':domain.create_conversation(tenant,user)}
    @app.delete('/api/v3/conversations/{conversation_id}')
    def delete_conversation(conversation_id:str,user=Depends(identity)):
        domain.delete_conversation(tenant,user,conversation_id);return {'deleted':True}
    @app.get('/api/v3/conversations/{conversation_id}/messages')
    # 历史读取经过所有权与来源权限复查，不能仅凭会话 id 读取他人记录。
    def history(conversation_id:str,user=Depends(identity)): return domain.history(tenant,user,conversation_id)
    @app.post('/api/v3/conversations/{conversation_id}/messages/stream')
    # 在发送成功响应前检查会话归属，再把业务事件编码为 SSE 流。
    async def stream(conversation_id:str,payload:Query,request:Request,user=Depends(identity)):
        # 在返回 HTTP 200 前先检查会话归属，不能等到生成器开始输出时才验证。
        await asyncio.to_thread(domain.history,tenant,user,conversation_id)
        # 每次拉取事件都检查断连；心跳不表示回答完成，关闭时还必须释放内部生成器。
        async def events():
            source=chat.answer(tenant,user,conversation_id,payload.space_id,payload.query).__aiter__(); pending=None
            try:
                async with asyncio.timeout(180):
                    while True:
                        pending=asyncio.create_task(anext(source,None))
                        # 同一个拉取任务可跨多个心跳周期等待，不能每次心跳都重新推进生成器。
                        while not pending.done():
                            await asyncio.wait({pending},timeout=5)
                            if await request.is_disconnected(): return
                            # SSE 注释只保活，不参与事件 JSON，也不代表答案已经完成。
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
                    # 先取消并等待正在执行的 anext，再关闭生成器，避免同时推进与关闭导致资源泄漏。
                    pending.cancel()
                    try: await pending
                    except (asyncio.CancelledError,Exception): pass
                await source.aclose()
        return StreamingResponse(events(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})
    @app.post('/api/v3/messages/{message_id}/feedback')
    # 只允许消息所有者修改本次回答反馈。
    def feedback(message_id:str,payload:Feedback,user=Depends(identity)):
        with domain.db.transaction(tenant) as s:
            message=one(s,Message,tenant,message_id)
            if message.owner!=user: raise DomainError('不能修改他人反馈',403)
            message.feedback=payload.text
            domain.audit(s,tenant,user,'message.feedback',message.id)
        return {'saved':True}
    @app.get('/api/v3/messages/{message_id}/trace')
    # 个人处理明细只返回自己消息；全企业查看应走独立审计接口。
    def trace(message_id:str,user=Depends(identity)):
        with domain.db.transaction(tenant,shared=True) as s:
            message=one(s,Message,tenant,message_id)
            if message.owner!=user: raise DomainError('不能访问他人问答明细',403)
            return {'message_id':message.id,'state':message.state,**message.trace}
    return app
