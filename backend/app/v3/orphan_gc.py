# 孤立原件回收：先生成带对象指纹的清单，再复核引用、保留期和内容后删除。
# 仅回收无版本引用的原件，不负责远端副本、对象历史版本或备份。
"""通过显式的规划与执行两个阶段，回收超过保留期且未被引用的不可变原件。"""
import hashlib
import json
import math
import re
import stat
import time
import uuid
from sqlalchemy import select
from .domain import DomainError,one
from .models import Tenant,Version,Job


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


# 租户 ID 摘要形成命名空间；盘点仅接受该空间内固定格式的原件键。
def namespace(tenant):return hashlib.sha256(tenant.encode()).hexdigest()+'/'


def valid_key(tenant,key):
    return isinstance(key,str) and re.fullmatch(re.escape(namespace(tenant))+r'[0-9a-f]{32}',key) is not None


# 拒绝符号链接和 Windows 重解析点，并验证解析后的路径仍在原件根目录中。
def local_path(store,key):
    path=store.root/key
    for part in (path.parent,path):
        if part.exists() or part.is_symlink():
            info=part.lstat()
            if part.is_symlink() or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',1024):
                raise DomainError('原件回收拒绝链接或重解析点')
    if not path.resolve().is_relative_to(store.root.resolve()):raise DomainError('对象超出原件目录')
    return path


# 版本化桶删除当前对象仍会保留历史；未确定历史保留策略前拒绝自动回收。
def require_unversioned(store):
    if store.client and store.client.get_bucket_versioning(Bucket=store.bucket).get('Status') in ('Enabled','Suspended'):
        raise DomainError('版本化对象桶须另行确定历史版本与备份保留策略，不能自动回收')


CANARY_CONTENT = b'v3-orphan-gc-conditional-delete-probe'


# 只用新建合成对象验证 If-Match 确实生效，不能拿真实原件探测删除能力。
def require_conditional_delete(store):
    """仅使用新建的合成对象探测能力，不得使用真实原件做删除测试。"""
    if not store.client:return
    from botocore.exceptions import ClientError
    key=store.key('__orphan_gc_canary__/'+uuid.uuid4().hex)
    args={'Bucket':store.bucket,'Key':key}
    try:
        created=store.client.put_object(**args,Body=CANARY_CONTENT,IfNoneMatch='*')
        etag=created.get('ETag')
        if not isinstance(etag,str) or not etag:raise ValueError('missing probe ETag')
        wrong='"'+uuid.uuid4().hex+'"'
        # 先用错误 ETag 验证服务确实拒绝删除，再用正确 ETag 清理合成探测对象。
        while wrong==etag:wrong='"'+uuid.uuid4().hex+'"'
        try:
            store.client.delete_object(**args,IfMatch=wrong)
        except ClientError as error:
            if error.response.get('ResponseMetadata',{}).get('HTTPStatusCode')!=412:raise
        else:raise ValueError('service ignored DELETE If-Match')
        body=store.client.get_object(**args)['Body']
        try:preserved=body.read()==CANARY_CONTENT
        finally:body.close()
        if not preserved:raise ValueError('probe changed after rejected DELETE')
        store.client.delete_object(**args,IfMatch=etag)
        try:store.client.head_object(**args)
        except ClientError as error:
            if error.response.get('ResponseMetadata',{}).get('HTTPStatusCode')==404:return
            raise
        raise ValueError('probe remains after conditional cleanup')
    except Exception as error:
        # 结果未知不代表允许无条件清理；返回唯一探测键，供管理员检查可能的残留。
        raise DomainError('S3 条件删除能力未通过验证，已拒绝原件回收；请检查可能残留的合成探测对象：'+key) from error


# 只枚举合法租户原件键，记录大小、修改时间和对象身份供执行阶段复核。
def inventory(store,tenant):
    # 本地与 S3 使用同一租户键规则，但各自保留可复核的身份：纳秒修改时间或 ETag。
    prefix=namespace(tenant)
    if store.client:
        remote_prefix=store.key(prefix.rstrip('/'))+'/'
        for page in store.client.get_paginator('list_objects_v2').paginate(Bucket=store.bucket,Prefix=remote_prefix):
            for item in page.get('Contents',[]):
                key=item['Key'][len(store.prefix)+1:] if store.prefix else item['Key']
                if valid_key(tenant,key):
                    yield key,{'modified_at':item['LastModified'].timestamp(),'size':item['Size'],'etag':item['ETag']}
    else:
        folder=local_path(store,prefix+'0'*32).parent
        if folder.exists():
            for path in sorted(folder.iterdir()):
                key=prefix+path.name
                if valid_key(tenant,key):
                    path=local_path(store,key)
                    info=path.stat()
                    if stat.S_ISREG(info.st_mode):
                        yield key,{'modified_at':info.st_mtime,'size':info.st_size,'mtime_ns':info.st_mtime_ns}


def current(store,key):
    # 仅明确的文件不存在或对象 404 可视为缺失；无权限、超时等错误不能当作已删除。
    if not store.client:
        path=local_path(store,key)
        if not path.exists():return None
        info=path.stat()
        if not stat.S_ISREG(info.st_mode):raise DomainError('原件回收目标不是普通文件')
        return {'modified_at':info.st_mtime,'size':info.st_size,'mtime_ns':info.st_mtime_ns}
    from botocore.exceptions import ClientError
    try:item=store.client.head_object(Bucket=store.bucket,Key=store.key(key))
    except ClientError as error:
        if error.response.get('Error',{}).get('Code') in ('404','NoSuchKey','NotFound'):return None
        raise
    return {'modified_at':item['LastModified'].timestamp(),'size':item['ContentLength'],'etag':item['ETag']}


# 第一阶段只生成清单：排除所有业务版本引用，再记录满足保留期对象的摘要和目录修订号。
def plan_orphans(domain,tenant,user,retention_days=7,now=None):
    # 规划只读不删；记录目录修订号和存储指纹，执行时据此拒绝过期清单或错误目标。
    now=time.time() if now is None else now
    if not isinstance(retention_days,(int,float)) or not math.isfinite(retention_days) or retention_days<1:
        raise DomainError('原件回收保留期必须至少为一天',400)
    store=domain.objects
    with domain.db.transaction(tenant,shared=True) as s:
        domain.admin(s,tenant,user);revision=s.get(Tenant,tenant).catalog
        require_unversioned(store)
        referenced=set(s.scalars(select(Version.object_key)))
        # 引用检查覆盖全部租户；即使出现异常跨租户引用，也优先保护仍被业务使用的原件。
        candidates=[]
        for key,info in inventory(store,tenant):
            if key not in referenced and now-info['modified_at']>=retention_days*86400:
                candidates.append({'key':key,**info,'sha256':hashlib.sha256(store.read(key)).hexdigest()})
        plan={'format':1,'plan_id':uuid.uuid4().hex,'tenant_id':tenant,'catalog_revision':revision,
            'store_fingerprint':digest(store.location),'created_at':now,'retention_days':retention_days,'candidates':candidates}
        return {**plan,'plan_sha256':digest(plan)}


# 执行前重新验证管理员、清单摘要、存储目标、有效期、目录修订号及各对象身份和内容。
def validate(domain,s,tenant,user,plan,now):
    domain.admin(s,tenant,user)
    try:
        checksum=plan['plan_sha256'];body={k:v for k,v in plan.items() if k!='plan_sha256'}
        if checksum!=digest(body) or plan['format']!=1 or plan['tenant_id']!=tenant:
            raise ValueError()
        if plan['store_fingerprint']!=digest(domain.objects.location):raise ValueError()
        if not 0<=now-plan['created_at']<=86400 or plan['retention_days']<1:raise ValueError()
        if not re.fullmatch('[0-9a-f]{32}',plan['plan_id']):raise ValueError()
        if s.get(Tenant,tenant).catalog!=plan['catalog_revision']:raise ValueError()
        # 清单有效期内发生版本接收/发布也会改变目录修订号，需要重新盘点而非沿用旧候选。
        candidates=plan['candidates'];keys=[x['key'] for x in candidates]
        if len(keys)!=len(set(keys)) or any(not valid_key(tenant,key) for key in keys):raise ValueError()
        # 引用检查覆盖全部租户，即使出现异常跨租户引用，也不能删除仍被引用的对象。
        if keys and s.scalar(select(Version.id).where(Version.object_key.in_(keys)).limit(1)):
            raise DomainError('候选对象已经被业务版本引用，必须重新盘点')
        for item in candidates:
            info=current(domain.objects,item['key'])
            if info is None:continue
            for k,v in item.items():
                if k in ('key','sha256'):continue
                # 对象列表可能包含小数秒，而 HTTP HEAD 日期只有整数秒，比较时统一精度。
                if k=='modified_at' and domain.objects.client:
                    if math.floor(info[k])!=math.floor(v):raise ValueError()
                elif info.get(k)!=v:raise ValueError()
            # 身份、时间、大小和内容摘要一起复核，不能把同键新对象当作盘点时的旧孤件。
            # 判断保留期时取两次观测中较晚的修改时间，避免精度差异导致提前删除。
            if now-max(info['modified_at'],item['modified_at'])<plan['retention_days']*86400:raise ValueError()
            if hashlib.sha256(domain.objects.read(item['key'])).hexdigest()!=item['sha256']:raise ValueError()
    except (KeyError,TypeError,ValueError):
        raise DomainError('回收清单已过期、被修改或对象已变化，请重新盘点') from None


# 第二阶段先落持久化维护意图，再复核并删除；中断后留下可检查的维护任务。
def apply_orphan_plan(domain,tenant,user,plan,now=None):
    now=time.time() if now is None else now
    store=domain.objects
    with domain.db.transaction(tenant) as s:
        domain.admin(s,tenant,user);require_unversioned(store)
        validate(domain,s,tenant,user,plan,now)
        require_conditional_delete(store)
        key='orphan_gc:'+plan['plan_id']
        intent=s.scalar(select(Job).filter_by(tenant_id=tenant,idempotency_key=key))
        if not intent:
            intent=Job(id=uuid.uuid4().hex,tenant_id=tenant,owner=user,kind='orphan_gc',state='maintenance',idempotency_key=key,payload={'plan':plan})
            s.add(intent);s.flush()
        intent_id=intent.id
    # 先持久化维护意图再操作文件或对象，中断后仍能检查未完成的执行记录。
    result={'deleted':0,'already_absent':0,'failed':0,'intent_job_id':intent_id}
    with domain.db.transaction(tenant) as s:
        require_unversioned(store)
        # 维护意图已单独提交；真正删除前在新事务中复查，崩溃后仍可定位未完成的尝试。
        validate(domain,s,tenant,user,plan,now)
        intent=one(s,Job,tenant,intent_id);receipts=[]
        # 已不存在的候选计为幂等完成；单对象失败记录回执后继续处理其他对象。
        for item in plan['candidates']:
            key=item['key']
            if current(store,key) is None:
                result['already_absent']+=1;receipts.append({'key':key,'status':'already_absent'});continue
            try:
                if store.client:
                    # If-Match 让对象被并发替换时删除失败，绝不回退到无条件删除。
                    store.client.delete_object(Bucket=store.bucket,Key=store.key(key),IfMatch=item['etag'])
                    if current(store,key) is not None:raise DomainError('对象删除后仍可见')
                else:local_path(store,key).unlink()
            except Exception:
                result['failed']+=1;receipts.append({'key':key,'status':'delete_failed'});continue
            result['deleted']+=1;receipts.append({'key':key,'status':'deleted'})
            domain.audit(s,tenant,user,'original.orphan_delete',key.rsplit('/',1)[1])
        intent.payload={'plan':plan,'receipts':receipts,'result':result}
        intent.state='failed' if result['failed'] else 'completed'
        intent.error='orphan_delete_failed' if result['failed'] else ''
    return result
