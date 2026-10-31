import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from sqlalchemy import select
from .models import Tenant, User, Space, Document, Version, BackendInstance, Replica, Job, BackendSwitch, Audit, Conversation, Message
from .knowledge.contracts import Binding, Scope


class DomainError(Exception):
    def __init__(self, message, status=409):
        self.status = status
        super().__init__(message)


def uid(): return uuid.uuid4().hex
def rows(session, model, tenant, **filters):
    return session.scalars(select(model).filter_by(tenant_id=tenant,**filters)).all()
def one(session,model,tenant,id):
    row = session.scalar(select(model).filter_by(tenant_id=tenant,id=id))
    if row is None: raise DomainError('资源不存在或无权访问',404)
    return row
def dump(row):
    return {column.name:getattr(row,column.name) for column in row.__table__.columns}


class Domain:
    def __init__(self, db, objects, development=False):
        self.db = db
        self.development=development
        from .storage import OriginalStore
        self.objects = OriginalStore(objects)

    def backend_usable(self,instance):
        return instance.verified or (self.development and instance.report.get('profile')=='local_text_pilot'
            and instance.report.get('audit_level')=='runtime_local_pilot')

    def check_format(self,instance,filename):
        formats=instance.report.get('allowed_formats')
        if formats and Path(filename).suffix.lower() not in formats:
            raise DomainError('该底座尚未验收此格式，当前支持：'+', '.join(formats),400)

    def audit(self,s,tenant,user,action,resource):
        s.add(Audit(id=uid(),tenant_id=tenant,actor=user,action=action,resource_id=resource))

    def bootstrap(self,tenant,user):
        with self.db.transaction(tenant) as s:
            if s.get(Tenant,tenant) is None: s.add(Tenant(id=tenant))
            if s.get(User,(tenant,user)) is None: s.add(User(tenant_id=tenant,id=user,role='admin'))

    def user(self,s,tenant,user):
        row = one(s,User,tenant,user)
        if not row.active: raise DomainError('账号已停用',403)
        return row

    def admin(self,s,tenant,user):
        if self.user(s,tenant,user).role != 'admin': raise DomainError('需要系统管理员权限',403)

    def manage_space(self,s,tenant,user,space):
        account=self.user(s,tenant,user)
        if space.owner != user and account.role != 'admin': raise DomainError('没有资料管理权限',403)

    def allowed(self,s,tenant,user,doc):
        account=self.user(s,tenant,user)
        if doc.withdrawn: return False
        space=one(s,Space,tenant,doc.space_id)
        identities={user,*('group:'+g for g in account.groups)}
        if identities.intersection(doc.denied) or identities.intersection(space.denied): return False
        return doc.owner == user or bool(identities.intersection(doc.readers or space.readers))

    def revisions(self,tenant):
        with self.db.transaction(tenant,shared=True) as s:
            row=s.get(Tenant,tenant)
            if not row: raise DomainError('企业不存在',404)
            return {'authz':row.authz,'catalog':row.catalog}

    def create_space(self,tenant,user,name,required=None):
        if not name.strip(): raise DomainError('知识库名称不能为空',400)
        with self.db.transaction(tenant) as s:
            if self.user(s,tenant,user).role not in ('admin','editor'): raise DomainError('没有创建权限',403)
            row=Space(id=uid(),tenant_id=tenant,owner=user,name=name.strip(),readers=[user],required=required or ['segment'])
            s.add(row); self.audit(s,tenant,user,'space.create',row.id)
            return row.id

    def list_spaces(self,tenant,user):
        with self.db.transaction(tenant,shared=True) as s:
            account=self.user(s,tenant,user); identities={user,*('group:'+g for g in account.groups)}
            result=[dump(row) for row in rows(s,Space,tenant)
                if not identities.intersection(row.denied) and (row.owner == user or bool(identities.intersection(row.readers))
                    or any(doc.current_version and self.allowed(s,tenant,user,doc) for doc in rows(s,Document,tenant,space_id=row.id)))]
            for item in result:
                backend=one(s,BackendInstance,tenant,item['active_backend']) if item['active_backend'] else None
                item['validation_profile']=backend.report.get('profile','production' if backend.verified else 'unverified') if backend else 'unconfigured'
                item['allowed_formats']=backend.report.get('allowed_formats',[]) if backend else []
            return result

    def add_user(self,tenant,actor,user,role='reader',groups=None,active=True):
        if role not in ('reader','editor','admin') or not user: raise DomainError('账号或角色无效',400)
        with self.db.transaction(tenant) as s:
            self.admin(s,tenant,actor)
            row=s.get(User,(tenant,user))
            if not row: row=User(tenant_id=tenant,id=user); s.add(row)
            row.role=role; row.groups=groups or []; row.active=active
            s.get(Tenant,tenant).authz+=1; self.audit(s,tenant,actor,'user.update',user)

    def grant(self,tenant,user,document_id,readers,denied):
        with self.db.transaction(tenant) as s:
            doc=one(s,Document,tenant,document_id); self.manage_space(s,tenant,user,one(s,Space,tenant,doc.space_id))
            doc.readers=list(dict.fromkeys(readers)); doc.denied=list(dict.fromkeys(denied))
            s.get(Tenant,tenant).authz+=1; self.audit(s,tenant,user,'document.grant',document_id)

    def can_read(self,tenant,user,document_id):
        with self.db.transaction(tenant,shared=True) as s:
            return self.allowed(s,tenant,user,one(s,Document,tenant,document_id))

    def receive(self,tenant,user,space_id,filename,content,idempotency_key=None,document_id=None):
        name=Path(filename.replace('\\','/')).name
        if Path(name).suffix.lower() not in ('.pdf','.docx','.xlsx','.txt','.md') or not content or len(content)>50*1024*1024:
            raise DomainError('仅支持非空 PDF、DOCX、XLSX、TXT、Markdown，最大 50 MiB',400)
        digest=hashlib.sha256(content).hexdigest(); key=idempotency_key or uid()
        with self.db.transaction(tenant) as s:
            space=one(s,Space,tenant,space_id); self.manage_space(s,tenant,user,space)
            if space.active_backend:self.check_format(one(s,BackendInstance,tenant,space.active_backend),name)
            existing=s.scalar(select(Job).filter_by(tenant_id=tenant,idempotency_key='receive:'+key))
            if existing:
                if existing.payload['sha256'] != digest or existing.payload['space_id'] != space_id or existing.payload.get('request_document_id','')!=(document_id or '') or existing.payload.get('filename',name)!=name:
                    raise DomainError('幂等键已用于不同内容')
                return {k:existing.payload[k] for k in ('document_id','version_id','job_id')}
            if document_id:
                doc=one(s,Document,tenant,document_id)
                if doc.space_id != space_id or doc.withdrawn: raise DomainError('文档不可更新')
            else:
                doc=Document(id=uid(),tenant_id=tenant,space_id=space_id,owner=user,name=name,readers=[user]); s.add(doc)
            version_id=uid(); object_key=f'{hashlib.sha256(tenant.encode()).hexdigest()}/{version_id}'
            self.objects.put(object_key,content)
            version=Version(id=version_id,tenant_id=tenant,document_id=doc.id,filename=name,sha256=digest,object_key=object_key)
            s.add(version)
            job_id=uid(); payload={'document_id':doc.id,'request_document_id':document_id or '','filename':name,'version_id':version_id,'space_id':space_id,'sha256':digest,'job_id':job_id}
            s.add(Job(id=job_id,tenant_id=tenant,owner=user,kind='ingest',idempotency_key='receive:'+key,payload=payload))
            s.get(Tenant,tenant).catalog+=1
            self.audit(s,tenant,user,'document.receive',doc.id)
            return {'document_id':doc.id,'version_id':version_id,'job_id':job_id}

    def read_original(self,tenant,user,document_id,version_id=None):
        with self.db.transaction(tenant,shared=True) as s:
            doc=one(s,Document,tenant,document_id)
            if not self.allowed(s,tenant,user,doc): raise DomainError('没有资料访问权限',403)
            version=one(s,Version,tenant,version_id or doc.current_version) if (version_id or doc.current_version) else rows(s,Version,tenant,document_id=doc.id)[0]
            if version.document_id!=doc.id: raise DomainError('版本不属于该文档',403)
            space=one(s,Space,tenant,doc.space_id)
            if version.state!='published' and space.owner!=user and self.user(s,tenant,user).role!='admin':
                raise DomainError('未发布版本仅允许资料维护人员访问',403)
            return self.read_version(version)

    def read_version(self,version):
        content=self.objects.read(version.object_key)
        if hashlib.sha256(content).hexdigest()!=version.sha256:
            raise DomainError('原件完整性校验失败，请检查对象存储或恢复记录',503)
        return content

    def list_documents(self,tenant,user,space_id):
        with self.db.transaction(tenant,shared=True) as s:
            one(s,Space,tenant,space_id)
            result=[]
            for doc in rows(s,Document,tenant,space_id=space_id):
                if not self.allowed(s,tenant,user,doc): continue
                manager=self.user(s,tenant,user).role=='admin' or one(s,Space,tenant,doc.space_id).owner==user
                if not manager and not doc.current_version: continue
                item=dump(doc); item['versions']=[dump(v) for v in rows(s,Version,tenant,document_id=doc.id)]
                if not manager:item['versions']=[v for v in item['versions'] if v['state']=='published']
                for version in item['versions']: version.pop('object_key',None)
                for version in item['versions']:
                    version['can_cancel']=manager and version['state'] not in ('published','cancelled') and any(
                        j.state=='failed' and j.payload.get('version_id')==version['id'] for j in rows(s,Job,tenant))
                result.append(item)
            return result

    def publish(self,tenant,user,version_id):
        with self.db.transaction(tenant) as s:
            version=one(s,Version,tenant,version_id); doc=one(s,Document,tenant,version.document_id)
            space=one(s,Space,tenant,doc.space_id); self.manage_space(s,tenant,user,space)
            if version.state=='cancelled':raise DomainError('版本已取消，不能发布')
            if not space.active_backend or not self.backend_usable(one(s,BackendInstance,tenant,space.active_backend)):
                raise DomainError('当前底座未通过本环境的验收，不能发布')
            replica=s.scalar(select(Replica).filter_by(tenant_id=tenant,version_id=version_id,instance_id=space.active_backend,state='ready'))
            if not replica or doc.withdrawn: raise DomainError('当前底座索引尚未就绪，不能发布')
            if doc.current_version==version_id: return
            doc.current_version=version_id; version.state='published'; s.get(Tenant,tenant).catalog+=1
            self.audit(s,tenant,user,'document.publish',doc.id)

    def cancel_version(self,tenant,user,document_id,version_id):
        with self.db.transaction(tenant) as s:
            doc=one(s,Document,tenant,document_id);version=one(s,Version,tenant,version_id)
            self.manage_space(s,tenant,user,one(s,Space,tenant,doc.space_id))
            if version.document_id!=doc.id:raise DomainError('版本不属于该文档',403)
            if version.state=='cancelled':return
            if doc.withdrawn or version.state=='published' or doc.current_version==version.id:
                raise DomainError('已发布或已撤回的资料不能取消草稿')
            # Claiming does not acquire the tenant gate: lock job rows as well so
            # a concurrent claimant cannot start a draft while it is cancelled.
            jobs=s.scalars(select(Job).where(Job.tenant_id==tenant,Job.kind.in_(('ingest','replicate')),
                Job.payload['version_id'].as_string()==version.id).with_for_update()).all()
            replicas=rows(s,Replica,tenant,version_id=version.id)
            if not any(j.state=='failed' for j in jobs):raise DomainError('仅允许取消处理失败的草稿')
            if any(j.state in ('running','needs_reconciliation') for j in jobs) or any(r.state=='submitting' for r in replicas):
                raise DomainError('版本正在处理或远端结果未知，请先停止处理并完成对账')
            version.state='cancelled'
            for job in jobs:
                if job.state in ('failed','queued'):
                    job.state='cancelled';job.lease_token='';job.lease_until=0
            s.get(Tenant,tenant).catalog+=1
            self.audit(s,tenant,user,'version.cancel',version.id)

    def withdraw(self,tenant,user,document_id):
        with self.db.transaction(tenant) as s:
            doc=one(s,Document,tenant,document_id); self.manage_space(s,tenant,user,one(s,Space,tenant,doc.space_id))
            if doc.withdrawn: return
            doc.withdrawn=True; tenant_row=s.get(Tenant,tenant); tenant_row.authz+=1; tenant_row.catalog+=1
            s.add(Job(id=uid(),tenant_id=tenant,owner=user,kind='delete',idempotency_key='delete:'+doc.id,payload={'document_id':doc.id}))
            self.audit(s,tenant,user,'document.withdraw',doc.id)

    def register_backend(self,tenant,user,provider,url,key_ref,write_key_ref='',manage_key_ref='',collection_config=None):
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

    def list_backends(self,tenant,user):
        with self.db.transaction(tenant,shared=True) as s:
            self.admin(s,tenant,user); return [dict(dump(x),usable=self.backend_usable(x)) for x in rows(s,BackendInstance,tenant)]

    def accept_validation(self,tenant,user,instance_id,report):
        with self.db.transaction(tenant) as s:
            self.admin(s,tenant,user); instance=one(s,BackendInstance,tenant,instance_id)
            pilot=report.get('profile')=='local_text_pilot'
            if pilot:
                formats=set(report.get('allowed_formats') or [])
                proven={row.get('extension') for row in report.get('format_checks',[]) if row.get('provider')==instance.provider
                    and row.get('status')=='passed' and row.get('fact_preserved') is True}
                if not self.development or not formats or not formats.issubset({'.txt','.md','.pdf','.docx','.xlsx'}) or not (formats-{'.txt','.md'}).issubset(proven):
                    raise DomainError('本机试运行只允许开发环境及已有真实解析证据的文档格式',400)
                if set(report.get('capabilities',[]))!={'segment'}:
                    raise DomainError('本机试运行仅验收证据片段，不能声明精确页码能力',400)
            expected={f'LOCAL-{n:02}' for n in range(1,9)} if pilot else {f'G0-{n:02}' for n in range(1,15)}
            passed={c.get('id') for c in report.get('checks',[]) if c.get('status')=='passed'}
            sha={'weknora':'f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7','dify':'50abea97062110044f15d0e03ccecadd9ccc87ca'}
            level='runtime_local_pilot' if pilot else 'runtime_integration'
            if not expected.issubset(passed) or report.get('audit_level')!=level or report.get('instance_id')!=instance_id or report.get('commit')!=sha[instance.provider] or not report.get('image_digest') or not report.get('approved_by'):
                raise DomainError('需要固定实例、版本和镜像的完整 G0 运行时报告及负责人签收',400)
            instance.verified=not pilot; instance.report=report; instance.capabilities=report.get('capabilities',[])
            self.audit(s,tenant,user,'backend.accept_validation',instance_id)

    def prepare_switch(self,tenant,user,space_id,target):
        with self.db.transaction(tenant) as s:
            self.admin(s,tenant,user); space=one(s,Space,tenant,space_id); backend=one(s,BackendInstance,tenant,target)
            versions=self.switch_manifest(s,tenant,space_id)
            for version_id in versions:self.check_format(backend,one(s,Version,tenant,version_id).filename)
            tenant_row=s.get(Tenant,tenant)
            switch=BackendSwitch(id=uid(),tenant_id=tenant,space_id=space_id,target=target,manifest=versions,
                authz_revision=tenant_row.authz,catalog_revision=tenant_row.catalog,route_revision=space.route_revision)
            s.add(switch)
            for version_id in versions: self.enqueue_replica(s,tenant,user,version_id,target)
            return switch.id

    def switch_manifest(self,s,tenant,space_id):
        versions=set()
        for doc in rows(s,Document,tenant,space_id=space_id):
            if doc.withdrawn:continue
            if doc.current_version:versions.add(doc.current_version)
            versions.update(v.id for v in rows(s,Version,tenant,document_id=doc.id) if v.state not in ('published','cancelled'))
        return sorted(versions)

    def enqueue_replica(self,s,tenant,user,version_id,instance_id):
        key=f'replicate:{version_id}:{instance_id}'
        if not s.scalar(select(Job).filter_by(tenant_id=tenant,idempotency_key=key)):
            s.add(Job(id=uid(),tenant_id=tenant,owner=user,kind='replicate',idempotency_key=key,payload={'version_id':version_id,'instance_id':instance_id}))

    def switch_status(self,tenant,user,switch_id):
        with self.db.transaction(tenant,shared=True) as s:
            self.admin(s,tenant,user); switch=one(s,BackendSwitch,tenant,switch_id); instance=one(s,BackendInstance,tenant,switch.target)
            ready=[v for v in switch.manifest if s.scalar(select(Replica).filter_by(tenant_id=tenant,version_id=v,instance_id=switch.target,state='ready'))]
            result=dump(switch); result.update(ready=len(ready),total=len(switch.manifest),verified=self.backend_usable(instance),production_verified=instance.verified)
            return result

    def activate_switch(self,tenant,user,switch_id,expected_revision):
        with self.db.transaction(tenant) as s:
            self.admin(s,tenant,user); switch=one(s,BackendSwitch,tenant,switch_id); space=one(s,Space,tenant,switch.space_id)
            backend=one(s,BackendInstance,tenant,switch.target); tr=s.get(Tenant,tenant)
            current=self.switch_manifest(s,tenant,space.id)
            if switch.state!='preparing' or space.route_revision!=expected_revision or switch.route_revision!=expected_revision or tr.authz!=switch.authz_revision or tr.catalog!=switch.catalog_revision or current!=switch.manifest:
                raise DomainError('资料、权限或路由已变化，请重新准备切换')
            if not self.backend_usable(backend) or not set(space.required).issubset(backend.capabilities): raise DomainError('目标底座尚未通过所需能力验证')
            for version_id in current:
                self.check_format(backend,one(s,Version,tenant,version_id).filename)
                if not s.scalar(select(Replica).filter_by(tenant_id=tenant,version_id=version_id,instance_id=backend.id,state='ready')):
                    raise DomainError('目标索引未全部就绪')
            space.active_backend=backend.id; space.route_revision+=1; switch.state='activated'
            self.audit(s,tenant,user,'backend.activate',space.id)

    def scope(self,tenant,user,space_id):
        with self.db.transaction(tenant,shared=True) as s:
            self.user(s,tenant,user); space=one(s,Space,tenant,space_id); tr=s.get(Tenant,tenant)
            if space.active_backend and not self.backend_usable(one(s,BackendInstance,tenant,space.active_backend)):
                raise DomainError('当前环境不允许使用尚未完成生产验收的底座',503)
            bindings=[]
            for doc in rows(s,Document,tenant,space_id=space_id):
                if not doc.current_version or not self.allowed(s,tenant,user,doc): continue
                replica=s.scalar(select(Replica).filter_by(tenant_id=tenant,version_id=doc.current_version,instance_id=space.active_backend,state='ready'))
                if not replica or not replica.remote_id or not replica.collection_id: raise DomainError('资料索引未就绪')
                bindings.append(Binding(tenant,doc.id,doc.current_version,replica.id,replica.instance_id,replica.collection_id,replica.remote_id,replica.id,replica.batch_id))
            return Scope(tenant,tuple(bindings),tr.authz,tr.catalog,space.route_revision)

    def list_jobs(self,tenant,user):
        with self.db.transaction(tenant,shared=True) as s:
            account=self.user(s,tenant,user)
            return [dump(j) for j in rows(s,Job,tenant) if j.owner==user or account.role=='admin']

    def claim_job(self,worker_id,now=None,lease_seconds=60):
        now=time.time() if now is None else now
        with self.db.transaction() as s:
            query=select(Job).where(((Job.state=='queued') & (Job.next_run<=now)) | ((Job.state=='running') & (Job.lease_until<now))).order_by(Job.next_run).with_for_update(skip_locked=True).limit(1)
            job=s.scalar(query)
            if not job: return None
            job.state='running'; job.lease_token=uid(); job.lease_until=now+lease_seconds; job.attempts+=1
            s.flush(); return dump(job)

    def reconciliation_context(self,s,tenant,job):
        if job.state!='needs_reconciliation' or job.kind not in ('ingest','replicate'):
            raise DomainError('仅允许对账远端结果未知的入库任务')
        version=one(s,Version,tenant,job.payload['version_id'])
        replicas=rows(s,Replica,tenant,version_id=version.id)
        # Do not guess from the current active route: it may have changed.
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

    def claim_auto_reconciliation(self,now=None):
        now=time.time() if now is None else now
        with self.db.transaction() as s:
            job=s.scalar(select(Job).where(Job.state=='needs_reconciliation',Job.next_run<=now,
                Job.kind.in_(('ingest','replicate')),Job.payload['submission_proof'].as_string().is_not(None))
                .order_by(Job.next_run).with_for_update(skip_locked=True).limit(1))
            if job:
                job.state='running';job.lease_token=uid();job.lease_until=now+60;job.attempts+=1
                s.flush();result=dump(job);result['_reconcile']=True;return result
        return None

    def reconciliation_snapshot(self,tenant,user,job_id):
        with self.db.transaction(tenant,shared=True) as s:
            self.admin(s,tenant,user)
            context,_,_=self.reconciliation_context(s,tenant,one(s,Job,tenant,job_id))
            return context

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
            if any(report.get(key)!=value for key,value in context.items()):
                raise DomainError('对账快照已过期或不属于此任务')
            if s.scalar(select(Replica).filter_by(instance_id=replica.instance_id,
                    collection_id=replica.collection_id,remote_id=report['remote_id'])):
                raise DomainError('远端文档已绑定其他版本，禁止重复认领')
            # Verify the immutable local original before recording an operator attestation.
            self.read_version(version)
            receipt={**context,**{key:report[key] for key in fields},'actor':user,'accepted_at':time.time()}
            job.payload={**job.payload,'instance_id':replica.instance_id,'reconciliation':receipt}
            replica.remote_id=report['remote_id'];replica.batch_id=report['batch_id']
            replica.state='backend_processing';replica.error=''
            job.state='queued';job.next_run=0;job.failures=0;job.error='';job.lease_token='';job.lease_until=0
            doc=one(s,Document,tenant,version.document_id)
            if doc.withdrawn:
                replica.state='delete_pending';job.state='completed'
                s.add(Job(id=uid(),tenant_id=tenant,owner=user,kind='delete',
                    idempotency_key='delete-after-reconcile:'+replica.id,payload={'document_id':doc.id}))
            self.audit(s,tenant,user,'job.reconcile',job.id)

    def leased(self,s,job_id,token,now):
        job=s.get(Job,job_id)
        if not job or job.state!='running' or job.lease_token!=token or job.lease_until<now: raise DomainError('任务租约已失效')
        return job

    def complete_job(self,job_id,token,now=None):
        with self.db.transaction() as s:
            job=self.leased(s,job_id,token,time.time() if now is None else now); job.state='completed'; job.lease_until=0

    def retry_job(self,job_id,token,error,unknown=False):
        with self.db.transaction() as s:
            job=self.leased(s,job_id,token,time.time()); job.error=error[:200]; job.failures+=1
            job.state='needs_reconciliation' if unknown else 'failed' if job.failures>=5 else 'queued'
            if job.state=='failed' and job.kind in ('ingest','replicate'):
                version=one(s,Version,job.tenant_id,job.payload['version_id'])
                if version.state not in ('published','ready','cancelled'):version.state='failed'
            job.next_run=time.time()+min(300,2**job.failures); job.lease_until=0

    def create_conversation(self,tenant,user):
        with self.db.transaction(tenant) as s:
            self.user(s,tenant,user); row=Conversation(id=uid(),tenant_id=tenant,owner=user); s.add(row); return row.id

    def history(self,tenant,user,conversation_id,internal=False):
        with self.db.transaction(tenant,shared=True) as s:
            self.user(s,tenant,user); conv=one(s,Conversation,tenant,conversation_id)
            if conv.owner!=user: raise DomainError('不能访问他人会话',403)
            result=[]
            for message in sorted(rows(s,Message,tenant,conversation_id=conversation_id),key=lambda m:m.created_at):
                item=dump(message)
                if any(not self.allowed(s,tenant,user,one(s,Document,tenant,e['document_id'])) for e in message.evidence+message.dependencies):
                    item['answer']='相关资料权限已变化，回答已隐藏'; item['evidence']=[]; item['state']='restricted'
                if not internal:item.pop('dependencies',None)
                result.append(item)
            return result
