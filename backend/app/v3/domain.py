# 业务规则总入口：连接 API、角色授权、原件存储、知识索引、持久化任务与会话。
# 业务状态以自有数据库为准；远端服务成功、索引就绪与业务发布是不同阶段。

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from sqlalchemy import select
from .models import Tenant, User, Space, Document, Version, BackendInstance, Replica, Job, BackendSwitch, Audit, Conversation, Message
from .knowledge.contracts import Binding, Scope
from .access import AccessManagement, migrate_tenant_access, stable_id, legacy_role


# 可向调用方解释的业务错误；status 对应 HTTP 状态，details 承载占用明细等结构化信息。
class DomainError(Exception):
    # 保存 HTTP 状态与可选结构化明细，异常正文供接口解释业务失败。
    def __init__(self, message, status=409,details=None):
        self.status = status
        self.details = details
        super().__init__(message)


# 生成本地资源标识，不复用知识服务的远端文档标识。
def uid(): return uuid.uuid4().hex
# 所有集合查询固定带 tenant_id，调用方再补充资源与所有者条件。
def rows(session, model, tenant, **filters):
    return session.scalars(select(model).filter_by(tenant_id=tenant,**filters)).all()
# 以租户和标识共同查找资源，不区分不存在与越租户命中，避免泄露其他租户资源。
def one(session,model,tenant,id):
    row = session.scalar(select(model).filter_by(tenant_id=tenant,id=id))
    if row is None: raise DomainError('资源不存在或无权访问',404)
    return row
# 按映射字段生成普通字典；是否适合对外返回仍由上层白名单和敏感字段过滤决定。
def dump(row):
    return {column.name:getattr(row,column.name) for column in row.__table__.columns}


# 在事务内执行资料生命周期与权限规则，供 API、Worker 和维护命令共同复用。
class Domain(AccessManagement):
    # 接入数据库与自有原件存储；development 仅影响本机试运行验收的可用范围。
    def __init__(self, db, objects, development=False):
        self.db = db
        self.development=development
        from .storage import OriginalStore
        self.objects = OriginalStore(objects)

    # 生产验收实例可用；只有开发模式允许带指定运行证据的本机文档试运行实例。
    def backend_usable(self,instance):
        return instance.verified or (self.development and instance.report.get('profile')=='local_text_pilot'
            and instance.report.get('audit_level')=='runtime_local_pilot')

    # 以该实例真实验收报告限制格式，不能仅凭平台宣称支持就放行。
    def check_format(self,instance,filename):
        formats=instance.report.get('allowed_formats')
        if formats and Path(filename).suffix.lower() not in formats:
            raise DomainError('该底座尚未验收此格式，当前支持：'+', '.join(formats),400)

    # 把操作者、动作和资源写入同一业务事务，成功提交后形成操作记录。
    def audit(self,s,tenant,user,action,resource):
        s.add(Audit(id=uid(),tenant_id=tenant,actor=user,action=action,resource_id=resource))

    def list_audit(self,tenant,user,*,page=1,page_size=20,query='',category=''):
        from .audit import list_audit
        return list_audit(self,tenant,user,page=page,page_size=page_size,query=query,category=category)

    # 显式初始化租户与首次管理员，补齐角色数据；已有账号不会被覆盖或重新启用。
    def bootstrap(self,tenant,user):
        with self.db.transaction(tenant) as s:
            # 先补齐旧授权结构，再为首次创建的账号绑定管理员角色；重复初始化不覆盖现有账号。
            if s.get(Tenant,tenant) is None: s.add(Tenant(id=tenant))
            creating=s.get(User,(tenant,user)) is None
            if creating:s.add(User(tenant_id=tenant,id=user,role='admin'))
            s.flush();migrate_tenant_access(s,tenant)
            if creating:
                from .models import UserRole
                id=stable_id(tenant,'builtin:admin')
                if not s.get(UserRole,(tenant,user,id)):s.add(UserRole(tenant_id=tenant,user_id=user,role_id=id))
                s.get(User,(tenant,user)).org_unit_id=stable_id(tenant,'root')

    # 核对企业与成员均启用；有效令牌不能绕过数据库中的停用状态。
    def user(self,s,tenant,user):
        company=s.get(Tenant,tenant)
        if company is None or not company.active:raise DomainError('企业不存在或已停用',403)
        row = one(s,User,tenant,user)
        if not row.active: raise DomainError('账号已停用',403)
        return row

    # 使用 system.manage 能力判断系统管理权，兼容自定义角色而非硬编码角色名称。
    def admin(self,s,tenant,user):
        self.require_permission(s,tenant,user,'system.manage')

    # 全局资料管理者或具备创建能力的知识库负责人可维护；维护权不等于资料阅读权。
    def manage_space(self,s,tenant,user,space):
        account=self.user(s,tenant,user)
        if not self.has_permission(s,tenant,user,'knowledge.manage') and not (space.owner==user and self.has_permission(s,tenant,user,'knowledge.create')): raise DomainError('没有资料管理权限',403)

    # 统一资料阅读判断：撤回/停用先拒绝，有效角色的明确禁止优先，再计算继承与追加允许。
    def allowed(self,s,tenant,user,doc):
        account=self.user(s,tenant,user)
        if doc.withdrawn: return False
        space=one(s,Space,tenant,doc.space_id)
        if not self.has_permission(s,tenant,user,'knowledge.read'):return False
        identities={'role:'+role.id for role in self.effective_roles(s,tenant,user)}
        # 任意有效角色命中禁止规则就拒绝；即使资料不继承允许列表，知识库的禁止仍然生效。
        if identities.intersection(doc.denied) or identities.intersection(space.denied): return False
        # 继承表示合并知识库与资料的允许角色；未继承时只检查资料本身。
        return bool(identities.intersection(space.readers+doc.readers if doc.inherit_permissions else doc.readers))

    # 取得权限和资料目录修订号，供问答快照以及底座切换的一致性校验使用。
    def revisions(self,tenant):
        with self.db.transaction(tenant,shared=True) as s:
            row=s.get(Tenant,tenant)
            if not row: raise DomainError('企业不存在',404)
            return {'authz':row.authz,'catalog':row.catalog}

    # 新建业务知识库，默认采用创建者当前有效角色作为阅读范围，也允许显式指定角色。
    def create_space(self,tenant,user,name,required=None,role_ids=None,denied_role_ids=None):
        if not name.strip(): raise DomainError('知识库名称不能为空',400)
        with self.db.transaction(tenant) as s:
            self.require_permission(s,tenant,user,'knowledge.create')
            roles=self.effective_roles(s,tenant,user) if role_ids is None else self.validate_roles(s,tenant,role_ids)
            self.validate_roles(s,tenant,denied_role_ids or [])
            row=Space(id=uid(),tenant_id=tenant,owner=user,name=name.strip(),readers=['role:'+r.id for r in roles],
                denied=['role:'+id for id in (denied_role_ids or [])],required=required or ['segment'])
            s.add(row); self.audit(s,tenant,user,'space.create',row.id)
            return row.id

    # 维护者可发现需要管理的知识库，普通成员仅发现角色规则或已发布资料授予的知识库。
    def list_spaces(self,tenant,user):
        with self.db.transaction(tenant,shared=True) as s:
            account=self.user(s,tenant,user); identities={'role:'+r.id for r in self.effective_roles(s,tenant,user)}
            # 管理入口按维护权展示；普通成员也可因单份已发布资料的授权而发现所属知识库。
            result=[dump(row) for row in rows(s,Space,tenant)
                if self.has_permission(s,tenant,user,'knowledge.manage') or (row.owner==user and self.has_permission(s,tenant,user,'knowledge.create'))
                or (not identities.intersection(row.denied) and (bool(identities.intersection(row.readers))
                    or any(doc.current_version and self.allowed(s,tenant,user,doc) for doc in rows(s,Document,tenant,space_id=row.id))))]
            for item in result:
                # 将当前实例的验收范围一起返回，前端据此提示可上传格式和试运行限制。
                backend=one(s,BackendInstance,tenant,item['active_backend']) if item['active_backend'] else None
                item['validation_profile']=backend.report.get('profile','production' if backend.verified else 'unverified') if backend else 'unconfigured'
                item['allowed_formats']=backend.report.get('allowed_formats',[]) if backend else []
                item['can_manage']=self.has_permission(s,tenant,user,'knowledge.manage') or (item['owner']==user and self.has_permission(s,tenant,user,'knowledge.create'))
            return result

    # 维护成员、部门、多角色与可选密码；保护最后一个管理员，并在密码重置/停用时撤销会话。
    def add_user(self,tenant,actor,user,role='reader',groups=None,active=True,password=None,role_ids=None,org_unit_id=None,display_name=''):
        if role not in ('reader','editor','admin') or not user: raise DomainError('账号或角色无效',400)
        if password is not None and not 12<=len(password)<=128: raise DomainError('密码长度应为 12 至 128 位',400)
        with self.db.transaction(tenant) as s:
            self.require_permission(s,tenant,actor,'members.manage')
            row=s.get(User,(tenant,user))
            creating=row is None
            if not row: row=User(tenant_id=tenant,id=user); s.add(row);s.flush()
            from .models import Role,UserRole,OrganizationUnit
            from sqlalchemy import delete
            ids=role_ids if role_ids is not None else [stable_id(tenant,'builtin:'+role)]
            role_rows=self.validate_roles(s,tenant,ids,allow_inactive=not creating)
            existing_roles=self.effective_roles(s,tenant,user)
            admin_id=stable_id(tenant,'builtin:admin')
            # 停用或降权前先查其他启用管理员，避免公司失去全部内置管理员账号。
            if row.active and any(r.id==admin_id for r in existing_roles) and (not active or admin_id not in ids):
                others=s.scalars(select(User).join(UserRole,(UserRole.tenant_id==User.tenant_id)&(UserRole.user_id==User.id))
                    .where(User.tenant_id==tenant,User.id!=user,User.active.is_(True),UserRole.role_id==admin_id)).all()
                if not others:raise DomainError('不能停用或移除最后一个系统管理员')
            department=org_unit_id if org_unit_id is not None else (row.org_unit_id or stable_id(tenant,'root'))
            # 允许保留原停用部门，但不能把新成员或其他部门成员转入停用部门。
            unit=one(s,OrganizationUnit,tenant,department)
            if not unit.active and (creating or department!=row.org_unit_id):raise DomainError('不能将成员分配到停用部门')
            row.role=next((r.code for r in role_rows if r.code=='admin'),next((r.code for r in role_rows if r.code=='editor'),'reader'))
            row.groups=groups if groups is not None else row.groups;row.active=active;row.org_unit_id=department;row.display_name=display_name
            # 普通成员编辑保留迁移角色；新管理界面可通过 role_ids 显式移除这些角色。
            if role_ids is None:ids=list(dict.fromkeys(ids+[r.id for r in existing_roles if r.legacy_subject]))
            s.execute(delete(UserRole).where(UserRole.tenant_id==tenant,UserRole.user_id==user))
            for id in dict.fromkeys(ids):s.add(UserRole(tenant_id=tenant,user_id=user,role_id=id))
            s.flush()
            if groups is not None:
                # 旧工具仍可更新用户组；同步历史专用角色，保持迁移前的组匹配含义。
                for legacy in s.scalars(select(Role).where(Role.tenant_id==tenant,Role.legacy_subject!='')):
                    subject=legacy.legacy_subject
                    owner=subject.startswith('user:')
                    raw=subject[5:] if owner else subject.removeprefix('principal:')
                    match=raw==user or (not owner and raw.startswith('group:') and raw[6:] in groups)
                    membership=s.get(UserRole,(tenant,user,legacy.id))
                    if match and not membership:s.add(UserRole(tenant_id=tenant,user_id=user,role_id=legacy.id))
                    elif not match and membership:s.delete(membership)
            from .passwords import password_hash,revoke_sessions
            from .models import Credential
            if password is not None:
                # 密码变化与旧会话撤销同事务提交，旧令牌不能继续使用。
                credential=s.get(Credential,(tenant,user))
                if credential is None:
                    credential=Credential(tenant_id=tenant,id=user); s.add(credential)
                credential.password_hash=password_hash(password)
                revoke_sessions(s,tenant,user)
            if not active: revoke_sessions(s,tenant,user)
            s.get(Tenant,tenant).authz+=1; self.audit(s,tenant,actor,'user.update',user)

    # 只有有资料维护权的人才能读取该资料的角色授权选项。
    def grant_options(self,tenant,user,document_id):
        with self.db.transaction(tenant,shared=True) as s:
            doc=one(s,Document,tenant,document_id)
            self.manage_space(s,tenant,user,one(s,Space,tenant,doc.space_id))
            return self.role_options(s,tenant)

    # 旧管理工具的直接身份授权适配入口；新 HTTP 接口统一使用 set_resource_roles。
    def grant(self,tenant,user,document_id,readers,denied):
        with self.db.transaction(tenant) as s:
            doc=one(s,Document,tenant,document_id); self.manage_space(s,tenant,user,one(s,Space,tenant,doc.space_id))
            # 兼容旧管理工具及备份；新 HTTP 接口统一调用 set_resource_roles，不直接向成员授权。
            doc.readers=list(dict.fromkeys([legacy_role(s,tenant,r) for r in readers]+[legacy_role(s,tenant,doc.owner,owner=True)]))
            doc.denied=[legacy_role(s,tenant,r) for r in denied]
            doc.inherit_permissions=False
            s.get(Tenant,tenant).authz+=1; self.audit(s,tenant,user,'document.grant',document_id)

    # 为原件读取、历史复查等调用方提供独立事务中的资料阅读判断。
    def can_read(self,tenant,user,document_id):
        with self.db.transaction(tenant,shared=True) as s:
            return self.allowed(s,tenant,user,one(s,Document,tenant,document_id))

    # 校验原件并以幂等键登记版本与入库任务；此时仅接收文件，不把资料直接标记为已发布。
    def receive(self,tenant,user,space_id,filename,content,idempotency_key=None,document_id=None):
        # 同时处理 Windows 与 Unix 路径，只把末尾文件名存入业务记录。
        name=Path(filename.replace('\\','/')).name
        if Path(name).suffix.lower() not in ('.pdf','.docx','.xlsx','.txt','.md') or not content or len(content)>50*1024*1024:
            raise DomainError('仅支持非空 PDF、DOCX、XLSX、TXT、Markdown，最大 50 MiB',400)
        digest=hashlib.sha256(content).hexdigest(); key=idempotency_key or uid()
        with self.db.transaction(tenant) as s:
            space=one(s,Space,tenant,space_id); self.manage_space(s,tenant,user,space)
            if space.active_backend:self.check_format(one(s,BackendInstance,tenant,space.active_backend),name)
            existing=s.scalar(select(Job).filter_by(tenant_id=tenant,idempotency_key='receive:'+key))
            if existing:
                # 幂等键只允许重放同一请求，内容、知识库、目标文档或文件名变化都视为冲突。
                if existing.payload['sha256'] != digest or existing.payload['space_id'] != space_id or existing.payload.get('request_document_id','')!=(document_id or '') or existing.payload.get('filename',name)!=name:
                    raise DomainError('幂等键已用于不同内容')
                return {k:existing.payload[k] for k in ('document_id','version_id','job_id')}
            if document_id:
                doc=one(s,Document,tenant,document_id)
                if doc.space_id != space_id or doc.withdrawn: raise DomainError('文档不可更新')
            else:
                doc=Document(id=uid(),tenant_id=tenant,space_id=space_id,owner=user,name=name,readers=[],inherit_permissions=True); s.add(doc)
            version_id=uid(); object_key=f'{hashlib.sha256(tenant.encode()).hexdigest()}/{version_id}'
            # 原件先独占写入，再登记版本和异步任务；数据库失败可能留下孤件，由回收流程处理。
            self.objects.put(object_key,content)
            version=Version(id=version_id,tenant_id=tenant,document_id=doc.id,filename=name,sha256=digest,object_key=object_key)
            s.add(version)
            job_id=uid(); payload={'document_id':doc.id,'request_document_id':document_id or '','filename':name,'version_id':version_id,'space_id':space_id,'sha256':digest,'job_id':job_id}
            s.add(Job(id=job_id,tenant_id=tenant,owner=user,kind='ingest',idempotency_key='receive:'+key,payload=payload))
            s.get(Tenant,tenant).catalog+=1
            self.audit(s,tenant,user,'document.receive',doc.id)
            return {'document_id':doc.id,'version_id':version_id,'job_id':job_id}

    # 下载时重新校验当前阅读权；未发布原件还需维护权，不能仅凭版本标识下载草稿。
    def read_original(self,tenant,user,document_id,version_id=None):
        with self.db.transaction(tenant,shared=True) as s:
            doc=one(s,Document,tenant,document_id)
            if not self.allowed(s,tenant,user,doc): raise DomainError('没有资料访问权限',403)
            version=one(s,Version,tenant,version_id or doc.current_version) if (version_id or doc.current_version) else rows(s,Version,tenant,document_id=doc.id)[0]
            if version.document_id!=doc.id: raise DomainError('版本不属于该文档',403)
            # 有文档阅读权也不能任意下载草稿；未发布版本还需要该知识库的维护权。
            space=one(s,Space,tenant,doc.space_id)
            if version.state!='published' and not self.has_permission(s,tenant,user,'knowledge.manage') and not (space.owner==user and self.has_permission(s,tenant,user,'knowledge.create')):
                raise DomainError('未发布版本仅允许资料维护人员访问',403)
            content=self.read_version(version)
            self.audit(s,tenant,user,'document.download',doc.id)
            return content

    # 从自有存储读取不可变原件并校验内容摘要，供下载、入库和对账复用。
    def read_version(self,version):
        content=self.objects.read(version.object_key)
        if hashlib.sha256(content).hexdigest()!=version.sha256:
            raise DomainError('原件完整性校验失败，请检查对象存储或恢复记录',503)
        return content

    # 资料工作区仅向维护人员开放；再按各资料的管理权与阅读权筛选，对外去除对象存储键。
    def list_documents(self,tenant,user,space_id):
        with self.db.transaction(tenant,shared=True) as s:
            self.require_knowledge_workspace(s,tenant,user)
            one(s,Space,tenant,space_id)
            result=[]
            for doc in rows(s,Document,tenant,space_id=space_id):
                space=one(s,Space,tenant,doc.space_id)
                manager=self.has_permission(s,tenant,user,'knowledge.manage') or (space.owner==user and self.has_permission(s,tenant,user,'knowledge.create'))
                can_read=self.allowed(s,tenant,user,doc)
                if not can_read and not manager:continue
                if not manager and not doc.current_version: continue
                item=dump(doc); item['versions']=[dump(v) for v in rows(s,Version,tenant,document_id=doc.id)]
                # 维护者可排查草稿，阅读者只见已发布版本；两类用户都不暴露内部存储键。
                item.update(can_manage=manager,can_read=can_read)
                if not manager:item['versions']=[v for v in item['versions'] if v['state']=='published']
                for version in item['versions']: version.pop('object_key',None)
                for version in item['versions']:
                    version['can_cancel']=manager and version['state'] not in ('published','cancelled') and any(
                        j.state=='failed' and j.payload.get('version_id')==version['id'] for j in rows(s,Job,tenant))
                result.append(item)
            return result

    # 只有当前底座的索引副本就绪且实例可用才能发布；发布指针变化时递增资料目录修订号。
    def publish(self,tenant,user,version_id):
        with self.db.transaction(tenant) as s:
            version=one(s,Version,tenant,version_id); doc=one(s,Document,tenant,version.document_id)
            space=one(s,Space,tenant,doc.space_id); self.manage_space(s,tenant,user,space)
            if version.state=='cancelled':raise DomainError('版本已取消，不能发布')
            if not space.active_backend or not self.backend_usable(one(s,BackendInstance,tenant,space.active_backend)):
                raise DomainError('当前底座未通过本环境的验收，不能发布')
            replica=s.scalar(select(Replica).filter_by(tenant_id=tenant,version_id=version_id,instance_id=space.active_backend,state='ready'))
            # 其他底座上的 ready 副本不能替代当前路由的索引；这里只切换业务发布指针。
            if not replica or doc.withdrawn: raise DomainError('当前底座索引尚未就绪，不能发布')
            if doc.current_version==version_id: return
            doc.current_version=version_id; version.state='published'; s.get(Tenant,tenant).catalog+=1
            self.audit(s,tenant,user,'document.publish',doc.id)

    # 仅取消失败且未发布的草稿，锁定相关任务以避免取消与 Worker 领取并发交错。
    def cancel_version(self,tenant,user,document_id,version_id):
        with self.db.transaction(tenant) as s:
            doc=one(s,Document,tenant,document_id);version=one(s,Version,tenant,version_id)
            self.manage_space(s,tenant,user,one(s,Space,tenant,doc.space_id))
            if version.document_id!=doc.id:raise DomainError('版本不属于该文档',403)
            if version.state=='cancelled':return
            if doc.withdrawn or version.state=='published' or doc.current_version==version.id:
                raise DomainError('已发布或已撤回的资料不能取消草稿')
            # 任务领取不持租户协调锁，因此还需锁定任务行，避免取消草稿时被执行器并发领取。
            jobs=s.scalars(select(Job).where(Job.tenant_id==tenant,Job.kind.in_(('ingest','replicate')),
                Job.payload['version_id'].as_string()==version.id).with_for_update()).all()
            replicas=rows(s,Replica,tenant,version_id=version.id)
            # 有执行中任务或未知上传时不能取消，否则会丢失对远端结果继续核查的入口。
            if not any(j.state=='failed' for j in jobs):raise DomainError('仅允许取消处理失败的草稿')
            if any(j.state in ('running','needs_reconciliation') for j in jobs) or any(r.state=='submitting' for r in replicas):
                raise DomainError('版本正在处理或远端结果未知，请先停止处理并完成对账')
            version.state='cancelled'
            for job in jobs:
                if job.state in ('failed','queued'):
                    job.state='cancelled';job.lease_token='';job.lease_until=0
            s.get(Tenant,tenant).catalog+=1
            self.audit(s,tenant,user,'version.cancel',version.id)

    # 先撤回本地业务可见性并使权限快照失效，远端清理另交给持久化删除任务。
    def withdraw(self,tenant,user,document_id):
        with self.db.transaction(tenant) as s:
            doc=one(s,Document,tenant,document_id); self.manage_space(s,tenant,user,one(s,Space,tenant,doc.space_id))
            if doc.withdrawn: return
            # 先让问答/下载的权限快照失效，再排队清理远端；远端故障不延迟业务撤回。
            doc.withdrawn=True; tenant_row=s.get(Tenant,tenant); tenant_row.authz+=1; tenant_row.catalog+=1
            s.add(Job(id=uid(),tenant_id=tenant,owner=user,kind='delete',idempotency_key='delete:'+doc.id,payload={'document_id':doc.id}))
            self.audit(s,tenant,user,'document.withdraw',doc.id)

    # 只登记经过格式校验的连接与环境变量引用；登记不代表已完成运行验收。
    def register_backend(self,tenant,user,provider,url,key_ref,write_key_ref='',manage_key_ref='',collection_config=None):
        # 此处只登记连接形状和秘密引用，不发网络请求；实例是否可启用由后续验收报告决定。
        from urllib.parse import urlsplit
        parsed=urlsplit(url)
        if provider not in ('weknora','dify') or not key_ref.startswith('V3_SECRET_') or parsed.scheme not in ('https','http') or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise DomainError('底座配置无效，密钥必须使用 V3_SECRET_ 环境变量引用',400)
        if any(ref and not ref.startswith('V3_SECRET_') for ref in (write_key_ref,manage_key_ref)):
            raise DomainError('密钥引用必须使用 V3_SECRET_ 前缀',400)
        with self.db.transaction(tenant) as s:
            self.admin(s,tenant,user)
            row=BackendInstance(id=uid(),tenant_id=tenant,provider=provider,base_url=url.rstrip('/'),key_ref=key_ref,write_key_ref=write_key_ref,manage_key_ref=manage_key_ref,collection_config=collection_config or {})
            s.add(row); self.audit(s,tenant,user,'backend.register',row.id)
            return row.id

    # 仅系统管理者可读服务配置，并附上当前环境可用性，而非只返回生产验收布尔值。
    def list_backends(self,tenant,user):
        with self.db.transaction(tenant,shared=True) as s:
            self.admin(s,tenant,user); return [dict(dump(x),usable=self.backend_usable(x)) for x in rows(s,BackendInstance,tenant)]

    # 核验实例、固定版本、镜像及签收人；开发试运行与完整生产验收使用不同的证据集合。
    def accept_validation(self,tenant,user,instance_id,report):
        with self.db.transaction(tenant) as s:
            self.admin(s,tenant,user); instance=one(s,BackendInstance,tenant,instance_id)
            pilot=report.get('profile')=='local_text_pilot'
            if pilot:
                # TXT/Markdown 之外的格式必须有对应平台的真实解析通过记录，不能只声明扩展名。
                formats=set(report.get('allowed_formats') or [])
                proven={row.get('extension') for row in report.get('format_checks',[]) if row.get('provider')==instance.provider
                    and row.get('status')=='passed' and row.get('fact_preserved') is True}
                if not self.development or not formats or not formats.issubset({'.txt','.md','.pdf','.docx','.xlsx'}) or not (formats-{'.txt','.md'}).issubset(proven):
                    raise DomainError('本机试运行只允许开发环境及已有真实解析证据的文档格式',400)
                if set(report.get('capabilities',[]))!={'segment'}:
                    raise DomainError('本机试运行仅验收证据片段，不能声明精确页码能力',400)
            expected={f'LOCAL-{n:02}' for n in range(1,9)} if pilot else {f'G0-{n:02}' for n in range(1,15)}
            # 报告绑定具体实例、固定源码版本、镜像和签收人，防止复用其他环境的通过结论。
            passed={c.get('id') for c in report.get('checks',[]) if c.get('status')=='passed'}
            sha={'weknora':'f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7','dify':'50abea97062110044f15d0e03ccecadd9ccc87ca'}
            level='runtime_local_pilot' if pilot else 'runtime_integration'
            if not expected.issubset(passed) or report.get('audit_level')!=level or report.get('instance_id')!=instance_id or report.get('commit')!=sha[instance.provider] or not report.get('image_digest') or not report.get('approved_by'):
                raise DomainError('需要固定实例、版本和镜像的完整 G0 运行时报告及负责人签收',400)
            instance.verified=not pilot; instance.report=report; instance.capabilities=report.get('capabilities',[])
            self.audit(s,tenant,user,'backend.accept_validation',instance_id)

    # 为切换冻结版本清单和修订号，安排目标副本准备；准备阶段不改当前服务路由。
    def prepare_switch(self,tenant,user,space_id,target):
        with self.db.transaction(tenant) as s:
            self.admin(s,tenant,user); space=one(s,Space,tenant,space_id); backend=one(s,BackendInstance,tenant,target)
            versions=self.switch_manifest(s,tenant,space_id)
            # 准备清单和修订号在同一事务冻结；后续只复制目标索引，不提前改活动路由。
            for version_id in versions:self.check_format(backend,one(s,Version,tenant,version_id).filename)
            tenant_row=s.get(Tenant,tenant)
            switch=BackendSwitch(id=uid(),tenant_id=tenant,space_id=space_id,target=target,manifest=versions,
                authz_revision=tenant_row.authz,catalog_revision=tenant_row.catalog,route_revision=space.route_revision)
            s.add(switch)
            for version_id in versions: self.enqueue_replica(s,tenant,user,version_id,target)
            self.audit(s,tenant,user,'backend.prepare',space.id)
            return switch.id

    # 收集当前发布版与仍需处理的草稿，排除撤回资料和已取消草稿。
    def switch_manifest(self,s,tenant,space_id):
        versions=set()
        for doc in rows(s,Document,tenant,space_id=space_id):
            if doc.withdrawn:continue
            if doc.current_version:versions.add(doc.current_version)
            # 历史发布版无需迁移，但尚未完成的草稿要纳入准备，避免切换后无法继续处理。
            versions.update(v.id for v in rows(s,Version,tenant,document_id=doc.id) if v.state not in ('published','cancelled'))
        return sorted(versions)

    # 同一版本/实例只登记一个复制任务，通过幂等键避免重复排队。
    def enqueue_replica(self,s,tenant,user,version_id,instance_id):
        key=f'replicate:{version_id}:{instance_id}'
        if not s.scalar(select(Job).filter_by(tenant_id=tenant,idempotency_key=key)):
            s.add(Job(id=uid(),tenant_id=tenant,owner=user,kind='replicate',idempotency_key=key,payload={'version_id':version_id,'instance_id':instance_id}))

    # 报告目标版本清单的索引就绪进度，同时区分环境可用与生产验收通过。
    def switch_status(self,tenant,user,switch_id):
        with self.db.transaction(tenant,shared=True) as s:
            self.admin(s,tenant,user); switch=one(s,BackendSwitch,tenant,switch_id); instance=one(s,BackendInstance,tenant,switch.target)
            ready=[v for v in switch.manifest if s.scalar(select(Replica).filter_by(tenant_id=tenant,version_id=v,instance_id=switch.target,state='ready'))]
            result=dump(switch); result.update(ready=len(ready),total=len(switch.manifest),verified=self.backend_usable(instance),production_verified=instance.verified)
            return result

    # 切换前重验清单、能力和修订号；受跟踪的权限、目录、路由或版本清单变化会要求重新准备。
    def activate_switch(self,tenant,user,switch_id,expected_revision):
        with self.db.transaction(tenant) as s:
            self.admin(s,tenant,user); switch=one(s,BackendSwitch,tenant,switch_id); space=one(s,Space,tenant,switch.space_id)
            backend=one(s,BackendInstance,tenant,switch.target); tr=s.get(Tenant,tenant)
            current=self.switch_manifest(s,tenant,space.id)
            # 不只比较路由：准备期间权限、目录或版本集合任一变化，都必须重新准备。
            if switch.state!='preparing' or space.route_revision!=expected_revision or switch.route_revision!=expected_revision or tr.authz!=switch.authz_revision or tr.catalog!=switch.catalog_revision or current!=switch.manifest:
                raise DomainError('资料、权限或路由已变化，请重新准备切换')
            if not self.backend_usable(backend) or not set(space.required).issubset(backend.capabilities): raise DomainError('目标底座尚未通过所需能力验证')
            for version_id in current:
                # 每份目标副本均就绪后才能原子切路由，不能让部分资料落在未准备好的底座上。
                self.check_format(backend,one(s,Version,tenant,version_id).filename)
                if not s.scalar(select(Replica).filter_by(tenant_id=tenant,version_id=version_id,instance_id=backend.id,state='ready')):
                    raise DomainError('目标索引未全部就绪')
            space.active_backend=backend.id; space.route_revision+=1; switch.state='activated'
            self.audit(s,tenant,user,'backend.activate',space.id)

    # 只把当前角色有权阅读的发布版本绑定交给检索，并携带权限、目录和路由修订号。
    def scope(self,tenant,user,space_id):
        with self.db.transaction(tenant,shared=True) as s:
            self.user(s,tenant,user); space=one(s,Space,tenant,space_id); tr=s.get(Tenant,tenant)
            if space.active_backend and not self.backend_usable(one(s,BackendInstance,tenant,space.active_backend)):
                raise DomainError('当前环境不允许使用尚未完成生产验收的底座',503)
            bindings=[]
            # 授权绑定在共享事务内与修订号一起读取，后续外发可据此识别快照是否过期。
            for doc in rows(s,Document,tenant,space_id=space_id):
                if not doc.current_version or not self.allowed(s,tenant,user,doc): continue
                replica=s.scalar(select(Replica).filter_by(tenant_id=tenant,version_id=doc.current_version,instance_id=space.active_backend,state='ready'))
                if not replica or not replica.remote_id or not replica.collection_id: raise DomainError('资料索引未就绪')
                bindings.append(Binding(tenant,doc.id,doc.current_version,replica.id,replica.instance_id,replica.collection_id,replica.remote_id,replica.id,replica.batch_id))
            return Scope(tenant,tuple(bindings),tr.authz,tr.catalog,space.route_revision)

    # 维护人员只能看自己的任务，系统管理者可看本企业全部任务，普通读者不得进入。
    def list_jobs(self,tenant,user):
        with self.db.transaction(tenant,shared=True) as s:
            self.require_knowledge_workspace(s,tenant,user)
            manager=self.has_permission(s,tenant,user,'system.manage')
            query=select(Job).where(Job.tenant_id==tenant)
            if not manager:query=query.where(Job.owner==user)
            jobs=list(s.scalars(query.order_by(Job.created_at.desc(),Job.id)))
            version_ids={j.payload.get('version_id') for j in jobs}-{None,''}
            document_ids={j.payload.get('document_id') for j in jobs}-{None,''}
            versions={v.id:v for v in s.scalars(select(Version).where(Version.tenant_id==tenant,
                (Version.id.in_(version_ids)) | (Version.document_id.in_(document_ids))))}
            document_ids.update(v.document_id for v in versions.values())
            documents={d.id:d for d in s.scalars(select(Document).where(Document.tenant_id==tenant,Document.id.in_(document_ids)))}
            spaces={sp.id:sp for sp in s.scalars(select(Space).where(Space.tenant_id==tenant,Space.id.in_({d.space_id for d in documents.values()})))}
            replicas=list(s.scalars(select(Replica).where(Replica.tenant_id==tenant,Replica.version_id.in_(versions))))
            backends={b.id:b for b in s.scalars(select(BackendInstance).where(BackendInstance.tenant_id==tenant,BackendInstance.id.in_({r.instance_id for r in replicas})))}
            result=[]
            for job in jobs:
                item=dump(job);version=versions.get(job.payload.get('version_id'))
                doc=documents.get(version.document_id if version else job.payload.get('document_id'))
                space=spaces.get(doc.space_id) if doc else None
                item['file']=None if not doc else {'document_id':doc.id,'filename':version.filename if version else doc.name,
                    'version_id':version.id if version else None,'version_state':version.state if version else None,
                    'space_name':space.name if space else '', 'withdrawn':doc.withdrawn}
                target=job.payload.get('instance_id')
                # 任务结果只关联实际目标，不能把另一个底座的分块数算到本任务上。
                related=[r for r in replicas if (r.version_id==version.id if version else doc and versions[r.version_id].document_id==doc.id)
                    and (not target or r.instance_id==target)]
                # 任务元数据可见不等于正文可读；草稿预览还要有资料维护权，且任务目标必须明确。
                readable=bool(doc and self.allowed(s,tenant,user,doc))
                can_manage=bool(space and (self.has_permission(s,tenant,user,'knowledge.manage') or
                    (space.owner==user and self.has_permission(s,tenant,user,'knowledge.create'))))
                observed=job.payload.get('index_status') or {}
                item['index_results']=[{'id':r.id,'version_id':r.version_id,'instance_id':r.instance_id,
                    'provider':backends[r.instance_id].provider if r.instance_id in backends else 'unknown',
                    'state':r.state,'chunks':r.chunks if r.state=='ready' or r.chunks>0 else None,
                    'collection_id':r.collection_id,'remote_id':r.remote_id,'error':r.error,
                    'raw_state':observed.get('raw_state','') if observed.get('replica_id')==r.id else '',
                    'observed_at':observed.get('observed_at') if observed.get('replica_id')==r.id else None,
                    'can_preview':bool(readable and job.kind in ('ingest','replicate') and version and
                        (target or len(related)==1) and (version.state=='published' or can_manage) and r.state=='ready'
                        and r.collection_id and r.remote_id and r.instance_id in backends and self.backend_usable(backends[r.instance_id]))}
                    for r in related]
                item['can_retry']=manager and job.kind in ('ingest','replicate','delete') and job.state=='failed'
                result.append(item)
            return result

    # 用跳过已锁行的领取方式支持多 Worker；重新领取过期任务时更换租约令牌。
    def claim_job(self,worker_id,now=None,lease_seconds=60):
        now=time.time() if now is None else now
        with self.db.transaction() as s:
            # 跳过其他执行器已锁住的行；过期 running 允许重新领取，但必须生成新令牌。
            query=select(Job).where(((Job.state=='queued') & (Job.next_run<=now)) | ((Job.state=='running') & (Job.lease_until<now))).order_by(Job.next_run).with_for_update(skip_locked=True).limit(1)
            job=s.scalar(query)
            if not job: return None
            job.state='running'; job.lease_token=uid(); job.lease_until=now+lease_seconds; job.attempts+=1
            # 先提交领取结果再执行远端调用，进度保存时仍需核对令牌和有效期。
            s.flush(); return dump(job)

    # 从原提交绑定确定未知结果的唯一上下文，不能用已变化的当前路由猜测上传目标。
    def reconciliation_context(self,s,tenant,job):
        if job.state!='needs_reconciliation' or job.kind not in ('ingest','replicate'):
            raise DomainError('仅允许对账远端结果未知的入库任务')
        version=one(s,Version,tenant,job.payload['version_id'])
        replicas=rows(s,Replica,tenant,version_id=version.id)
        # 当前活动路由可能已经变化，必须从原提交副本确定目标，不能按现有路由猜测。
        candidates=[r for r in replicas if r.state=='submitting' and (
            not job.payload.get('instance_id') or r.instance_id==job.payload['instance_id'])]
        if len(candidates)!=1:raise DomainError('无法唯一确定未知上传绑定，请隔离后人工调查')
        replica=candidates[0]
        if not replica.collection_id or replica.remote_id or version.state=='cancelled':
            raise DomainError('绑定状态不允许签收入库回执')
        context={'job_id':job.id,'lease_token':job.lease_token,'version_id':version.id,
            'sha256':version.sha256,'replica_id':replica.id,'instance_id':replica.instance_id,
            'collection_id':replica.collection_id}
        return context,version,replica

    # 仅领取带提交证明的未知结果任务；自动对账与普通入库队列分别调度。
    def claim_auto_reconciliation(self,now=None):
        now=time.time() if now is None else now
        with self.db.transaction() as s:
            job=s.scalar(select(Job).where(Job.state=='needs_reconciliation',Job.next_run<=now,
                Job.kind.in_(('ingest','replicate')),Job.payload['submission_proof'].as_string().is_not(None))
                .order_by(Job.next_run).with_for_update(skip_locked=True).limit(1))
            if job:
                # 沿用普通租约保护本地写入，额外标记让 Worker 进入对账而不是重新上传。
                job.state='running';job.lease_token=uid();job.lease_until=now+60;job.attempts+=1
                s.flush();result=dump(job);result['_reconcile']=True;return result
        return None

    # 输出管理员签收所需的冻结上下文，让回执能绑定到具体任务与租约。
    def reconciliation_snapshot(self,tenant,user,job_id):
        with self.db.transaction(tenant,shared=True) as s:
            self.admin(s,tenant,user)
            context,_,_=self.reconciliation_context(s,tenant,one(s,Job,tenant,job_id))
            return context

    # 核验冻结快照、签收字段格式与本地原件后记录管理员签收；证据摘要仅检查格式，后续仍需轮询。
    def reconcile_job(self,tenant,user,job_id,report):
        if not isinstance(report,dict):raise DomainError('对账报告必须是 JSON 对象',400)
        fields={'remote_id':128,'batch_id':180,'approved_by':128,'note':2000,'evidence_sha256':64}
        for key,limit in fields.items():
            value=report.get(key)
            if not isinstance(value,str) or len(value)>limit or (key!='batch_id' and not value.strip()):
                raise DomainError('对账签收字段无效：'+key,400)
        digest=report['evidence_sha256']
        if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
            raise DomainError('证据必须提供 SHA256 校验值',400)
        with self.db.transaction(tenant) as s:
            self.admin(s,tenant,user)
            job=s.scalar(select(Job).filter_by(tenant_id=tenant,id=job_id).with_for_update())
            if not job:raise DomainError('资源不存在或无权访问',404)
            context,version,replica=self.reconciliation_context(s,tenant,job)
            # 操作者必须回传原冻结上下文；任务已被处理或租约已变化时拒绝旧报告。
            if any(report.get(key)!=value for key,value in context.items()):
                raise DomainError('对账快照已过期或不属于此任务')
            if s.scalar(select(Replica).filter_by(instance_id=replica.instance_id,
                    collection_id=replica.collection_id,remote_id=report['remote_id'])):
                raise DomainError('远端文档已绑定其他版本，禁止重复认领')
            # 记录管理员签收前，再次核验不可变的本地原件。
            self.read_version(version)
            receipt={**context,**{key:report[key] for key in fields},'actor':user,'accepted_at':time.time()}
            job.payload={**job.payload,'instance_id':replica.instance_id,'reconciliation':receipt}
            replica.remote_id=report['remote_id'];replica.batch_id=report['batch_id']
            replica.state='backend_processing';replica.error=''
            job.state='queued';job.next_run=0;job.failures=0;job.error='';job.lease_token='';job.lease_until=0
            doc=one(s,Document,tenant,version.document_id)
            if doc.withdrawn:
                # 撤回后才找回的远端回执只能进入清理，不能恢复资料的业务可见性。
                replica.state='delete_pending';job.state='completed'
                s.add(Job(id=uid(),tenant_id=tenant,owner=user,kind='delete',
                    idempotency_key='delete-after-reconcile:'+replica.id,payload={'document_id':doc.id}))
            self.audit(s,tenant,user,'job.reconcile',job.id)

    # 租约令牌、状态和有效期必须同时匹配，防止过期执行者覆盖新执行者的结果。
    def leased(self,s,job_id,token,now):
        job=s.get(Job,job_id)
        if not job or job.state!='running' or job.lease_token!=token or job.lease_until<now: raise DomainError('任务租约已失效')
        return job

    # 仅当前有效租约持有者可以提交任务完成状态。
    def complete_job(self,job_id,token,now=None):
        with self.db.transaction() as s:
            job=self.leased(s,job_id,token,time.time() if now is None else now); job.state='completed'; job.lease_until=0

    # 可确定失败按退避重试；远端结果未知则进入对账，不能盲目重复上传。
    def retry_job(self,job_id,token,error,unknown=False):
        with self.db.transaction() as s:
            job=self.leased(s,job_id,token,time.time()); job.error=error[:200]; job.failures+=1
            # 未知结果始终优先进入对账；可确定失败最多尝试五次，间隔按指数增加且封顶。
            job.state='needs_reconciliation' if unknown else 'failed' if job.failures>=5 else 'queued'
            if job.state=='failed' and job.kind in ('ingest','replicate'):
                version=one(s,Version,job.tenant_id,job.payload['version_id'])
                if version.state not in ('published','ready','cancelled'):version.state='failed'
            job.next_run=time.time()+min(300,2**job.failures); job.lease_until=0

    # 按租户与成员建立独立会话，后续消息只能归属于该会话所有者。
    def create_conversation(self,tenant,user):
        with self.db.transaction(tenant) as s:
            self.user(s,tenant,user); row=Conversation(id=uid(),tenant_id=tenant,owner=user); s.add(row)
            self.audit(s,tenant,user,'conversation.create',row.id)
            return row.id

    def delete_conversation(self,tenant,user,conversation_id):
        with self.db.transaction(tenant) as s:
            self.user(s,tenant,user);conv=one(s,Conversation,tenant,conversation_id)
            if conv.owner!=user:raise DomainError('不能删除他人会话',403)
            if conv.archived:return
            if s.scalar(select(Message.id).where(Message.tenant_id==tenant,
                    Message.conversation_id==conversation_id,Message.state=='processing').limit(1)):
                raise DomainError('会话正在生成回答，请先停止回答再删除')
            conv.archived=True
            self.audit(s,tenant,user,'conversation.delete',conversation_id)

    def conversations(self,tenant,user):
        # 会话归当前成员所有，列表摘要也必须在租户和所有者范围内计算，不能混入他人问题。
        with self.db.transaction(tenant,shared=True) as s:
            self.user(s,tenant,user)
            first_question=(select(Message.query).where(Message.tenant_id==tenant,
                Message.owner==user,Message.conversation_id==Conversation.id)
                .order_by(Message.created_at,Message.id).limit(1).correlate(Conversation).scalar_subquery())
            result=[]
            for conversation,query in s.execute(select(Conversation,first_question)
                    .where(Conversation.tenant_id==tenant,Conversation.owner==user,Conversation.archived.is_(False))):
                item=dump(conversation)
                if item['title'] in ('新会话','') and query:
                    title=' '.join(query.split())
                    if title:item['title']=title[:32]+('…' if len(title)>32 else '')
                result.append(item)
            return result

    # 查看历史时重新检查来源和历史依赖的当前权限；权限失效则隐藏回答与证据。
    def history(self,tenant,user,conversation_id,internal=False):
        with self.db.transaction(tenant,shared=True) as s:
            self.user(s,tenant,user); conv=one(s,Conversation,tenant,conversation_id)
            if conv.owner!=user: raise DomainError('不能访问他人会话',403)
            if conv.archived:raise DomainError('会话已删除',404)
            result=[]
            for message in sorted(rows(s,Message,tenant,conversation_id=conversation_id),key=lambda m:m.created_at):
                item=dump(message)
                # 不仅复查本轮证据，也检查生成时使用的历史依赖，防止通过旧回答绕过撤权。
                if any(not self.allowed(s,tenant,user,one(s,Document,tenant,e['document_id'])) for e in message.evidence+message.dependencies):
                    item['answer']='相关资料权限已变化，回答已隐藏'; item['evidence']=[]; item['state']='restricted'
                if not internal:
                    item.pop('dependencies',None)
                    item.pop('trace',None)
                result.append(item)
            return result
