"""Explicit two-phase collection of aged, unreferenced immutable originals."""
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


def namespace(tenant):return hashlib.sha256(tenant.encode()).hexdigest()+'/'


def valid_key(tenant,key):
    return isinstance(key,str) and re.fullmatch(re.escape(namespace(tenant))+r'[0-9a-f]{32}',key) is not None


def local_path(store,key):
    path=store.root/key
    for part in (path.parent,path):
        if part.exists() or part.is_symlink():
            info=part.lstat()
            if part.is_symlink() or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',1024):
                raise DomainError('原件回收拒绝链接或重解析点')
    if not path.resolve().is_relative_to(store.root.resolve()):raise DomainError('对象超出原件目录')
    return path


def require_unversioned(store):
    if store.client and store.client.get_bucket_versioning(Bucket=store.bucket).get('Status') in ('Enabled','Suspended'):
        raise DomainError('版本化对象桶须另行确定历史版本与备份保留策略，不能自动回收')


CANARY_CONTENT = b'v3-orphan-gc-conditional-delete-probe'


def require_conditional_delete(store):
    """Probe only a newly created synthetic key; never test capability on originals."""
    if not store.client:return
    from botocore.exceptions import ClientError
    key=store.key('__orphan_gc_canary__/'+uuid.uuid4().hex)
    args={'Bucket':store.bucket,'Key':key}
    try:
        created=store.client.put_object(**args,Body=CANARY_CONTENT,IfNoneMatch='*')
        etag=created.get('ETag')
        if not isinstance(etag,str) or not etag:raise ValueError('missing probe ETag')
        wrong='"'+uuid.uuid4().hex+'"'
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
        # An unknown result gives no permission for an unconditional cleanup.
        # Include the unique probe key so an operator can inspect possible residue.
        raise DomainError('S3 条件删除能力未通过验证，已拒绝原件回收；请检查可能残留的合成探测对象：'+key) from error


def inventory(store,tenant):
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


def plan_orphans(domain,tenant,user,retention_days=7,now=None):
    now=time.time() if now is None else now
    if not isinstance(retention_days,(int,float)) or not math.isfinite(retention_days) or retention_days<1:
        raise DomainError('原件回收保留期必须至少为一天',400)
    store=domain.objects
    with domain.db.transaction(tenant,shared=True) as s:
        domain.admin(s,tenant,user);revision=s.get(Tenant,tenant).catalog
        require_unversioned(store)
        referenced=set(s.scalars(select(Version.object_key)))
        candidates=[]
        for key,info in inventory(store,tenant):
            if key not in referenced and now-info['modified_at']>=retention_days*86400:
                candidates.append({'key':key,**info,'sha256':hashlib.sha256(store.read(key)).hexdigest()})
        plan={'format':1,'plan_id':uuid.uuid4().hex,'tenant_id':tenant,'catalog_revision':revision,
            'store_fingerprint':digest(store.location),'created_at':now,'retention_days':retention_days,'candidates':candidates}
        return {**plan,'plan_sha256':digest(plan)}


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
        candidates=plan['candidates'];keys=[x['key'] for x in candidates]
        if len(keys)!=len(set(keys)) or any(not valid_key(tenant,key) for key in keys):raise ValueError()
        # Check all tenants: an unexpected cross-tenant reference still protects the object.
        if keys and s.scalar(select(Version.id).where(Version.object_key.in_(keys)).limit(1)):
            raise DomainError('候选对象已经被业务版本引用，必须重新盘点')
        for item in candidates:
            info=current(domain.objects,item['key'])
            if info is None:continue
            for k,v in item.items():
                if k in ('key','sha256'):continue
                # LIST may include fractional seconds; HTTP HEAD dates cannot.
                if k=='modified_at' and domain.objects.client:
                    if math.floor(info[k])!=math.floor(v):raise ValueError()
                elif info.get(k)!=v:raise ValueError()
            # Preserve the more precise/later observation at the retention boundary.
            if now-max(info['modified_at'],item['modified_at'])<plan['retention_days']*86400:raise ValueError()
            if hashlib.sha256(domain.objects.read(item['key'])).hexdigest()!=item['sha256']:raise ValueError()
    except (KeyError,TypeError,ValueError):
        raise DomainError('回收清单已过期、被修改或对象已变化，请重新盘点') from None


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
    # Durable intent precedes filesystem effects. Interrupted runs remain inspectable.
    result={'deleted':0,'already_absent':0,'failed':0,'intent_job_id':intent_id}
    with domain.db.transaction(tenant) as s:
        require_unversioned(store)
        validate(domain,s,tenant,user,plan,now)
        intent=one(s,Job,tenant,intent_id);receipts=[]
        for item in plan['candidates']:
            key=item['key']
            if current(store,key) is None:
                result['already_absent']+=1;receipts.append({'key':key,'status':'already_absent'});continue
            try:
                if store.client:
                    # Conditional delete refuses concurrent replacement; no fallback without it.
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
