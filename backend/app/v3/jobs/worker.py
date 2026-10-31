import asyncio
import time
from pathlib import PurePath
from sqlalchemy import select
from ..models import Job, Version, Document, Space, Replica, BackendInstance
from ..domain import DomainError, uid, one, rows
from ..knowledge.contracts import Binding, BackendError


class Worker:
    def __init__(self,domain,factory):
        self.domain=domain; self.db=domain.db; self.factory=factory
        self.prefer_reconciliation=True

    async def run_once(self):
        if self.prefer_reconciliation:
            job=await asyncio.to_thread(self.domain.claim_auto_reconciliation)
            if not job:job=await asyncio.to_thread(self.domain.claim_job,'v3-worker')
        else:
            job=await asyncio.to_thread(self.domain.claim_job,'v3-worker')
            if not job:job=await asyncio.to_thread(self.domain.claim_auto_reconciliation)
        if not job: return False
        self.prefer_reconciliation=not self.prefer_reconciliation
        heartbeat=asyncio.create_task(self.heartbeat(job))
        operation=asyncio.create_task(self.process(job))
        try:
            done,_=await asyncio.wait({heartbeat,operation},return_when=asyncio.FIRST_COMPLETED)
            if operation in done:
                await operation
            else:
                operation.cancel()
                await asyncio.gather(operation,return_exceptions=True)
                heartbeat.result()
                raise DomainError('任务心跳已停止')
        except (BackendError,DomainError) as error:
            try:
                await asyncio.to_thread(self.domain.retry_job,job['id'],job['lease_token'],getattr(error,'code',str(error)),
                    unknown=job.get('_reconcile',False) or (job['kind']!='delete' and getattr(error,'code','') in ('timeout_unknown','conflict','needs_reconciliation')))
            except DomainError: pass
        except Exception:
            # Do not persist raw upstream exceptions containing URLs/credentials.
            try: await asyncio.to_thread(self.domain.retry_job,job['id'],job['lease_token'],'worker_internal_error',unknown=job['kind']!='delete')
            except DomainError: pass
        finally:
            for task in (operation,heartbeat):task.cancel()
            await asyncio.gather(operation,heartbeat,return_exceptions=True)
        return True

    async def process(self,job):
        if job.get('_reconcile'):await self.reconcile_upload(job)
        elif job['kind'] in ('ingest','replicate'): await self.ingest(job)
        elif job['kind']=='delete': await self.delete(job)
        else: raise DomainError('不支持的任务类型')

    async def heartbeat(self,job):
        while True:
            await asyncio.sleep(15)
            async with self.db.atransaction() as s:
                current=self.domain.leased(s,job['id'],job['lease_token'],time.time()); current.lease_until=time.time()+60

    async def checkpoint(self,job,replica_id,**values):
        async with self.db.atransaction(job['tenant_id']) as s:
            current=self.domain.leased(s,job['id'],job['lease_token'],time.time())
            replica=one(s,Replica,job['tenant_id'],replica_id)
            doc=one(s,Document,job['tenant_id'],one(s,Version,job['tenant_id'],replica.version_id).document_id)
            if doc.withdrawn and values.get('remote_id'):
                # Persist a known external receipt even if withdrawal won the race.
                for key,value in values.items(): setattr(replica,key,value)
                replica.state='delete_pending'
                cleanup_key='delete-after-submit:'+replica.id
                if not s.scalar(select(Job).filter_by(tenant_id=job['tenant_id'],idempotency_key=cleanup_key)):
                    s.add(Job(id=uid(),tenant_id=job['tenant_id'],owner=current.owner,kind='delete',
                        idempotency_key=cleanup_key,payload={'document_id':doc.id}))
                current.state='completed';current.lease_until=0
                return False
            if doc.withdrawn: raise DomainError('资料已撤回')
            if one(s,Version,job['tenant_id'],replica.version_id).state=='cancelled':raise DomainError('版本已取消')
            for key,value in values.items(): setattr(replica,key,value)
            return True

    async def ingest(self,job):
        tenant=job['tenant_id']; payload=job['payload']
        async with self.db.atransaction(tenant) as s:
            self.domain.leased(s,job['id'],job['lease_token'],time.time())
            version=one(s,Version,tenant,payload['version_id']); doc=one(s,Document,tenant,version.document_id)
            space=one(s,Space,tenant,doc.space_id)
            if doc.withdrawn: raise DomainError('资料已撤回')
            if version.state=='cancelled':raise DomainError('版本已取消')
            target=payload.get('instance_id') or space.active_backend
            if not target: raise BackendError('invalid_configuration','请先启用已验收的底座')
            instance=one(s,BackendInstance,tenant,target)
            if not self.domain.backend_usable(instance): raise BackendError('invalid_configuration','底座尚未通过当前环境的验收')
            self.domain.check_format(instance,version.filename)
            replica=s.scalar(select(Replica).filter_by(tenant_id=tenant,version_id=version.id,instance_id=target))
            if not replica:
                replica=Replica(id=uid(),tenant_id=tenant,version_id=version.id,instance_id=target); s.add(replica); s.flush()
            if replica.state=='ready':
                self.domain.leased(s,job['id'],job['lease_token'],time.time()).state='completed'; return
            if replica.state=='submitting':
                raise BackendError('needs_reconciliation')
            replica_id=replica.id; state=replica.state; collection=replica.collection_id; remote=replica.remote_id; batch=replica.batch_id; metadata_id=replica.metadata_id
            original=await asyncio.to_thread(self.domain.read_version,version)
            filename=version.filename
        writer=self.factory(instance,'write')
        if not collection:
            # Collection preparation only validates pre-provisioned resources; safe to repeat.
            manager=self.factory(instance,'manage')
            created=await manager.prepare_collection('V3-'+replica_id)
            collection=created['id']; metadata_id=created.get('metadata_id','')
            await self.checkpoint(job,replica_id,state='received',collection_id=collection,metadata_id=metadata_id)
        if not remote:
            upload_filename=(f'v3-{replica_id}-{version.sha256[:16]}'+PurePath(filename).suffix) if instance.provider=='dify' else filename
            async with self.db.atransaction(tenant) as s:
                current=self.domain.leased(s,job['id'],job['lease_token'],time.time())
                current_version=one(s,Version,tenant,version.id)
                if one(s,Document,tenant,current_version.document_id).withdrawn or current_version.state=='cancelled':
                    raise DomainError('资料已撤回或版本已取消')
                proof={'schema_version':1,'binding_id':replica_id,'sha256':version.sha256,'size':len(original),
                    'instance_id':instance.id,'collection_id':collection,'version_id':version.id,
                    'upload_filename':upload_filename,'created_at':time.time()}
                current.payload={**current.payload,'instance_id':instance.id,'submission_proof':proof}
                one(s,Replica,tenant,replica_id).state='submitting'
            submitted=await writer.submit(collection,upload_filename,original,replica_id)
            remote=submitted.remote_id; batch=submitted.batch_id
            if not await self.checkpoint(job,replica_id,state='backend_processing',remote_id=remote,batch_id=batch):return
        binding=Binding(tenant,payload['document_id'] if 'document_id' in payload else version.document_id,
            version.id,replica_id,instance.id,collection,remote,replica_id,batch)
        try:
            status=await self.factory(instance,'retrieve').status(binding)
        except BackendError as error:
            # A read timeout after a persisted receipt cannot create a second upload.
            if error.code in ('timeout_unknown','conflict'):raise BackendError('index_check_retryable') from error
            raise
        if status.state=='failed': raise BackendError('parse_failed')
        if status.state!='completed':
            async with self.db.atransaction(tenant) as s:
                current=self.domain.leased(s,job['id'],job['lease_token'],time.time())
                current.state='queued'; current.next_run=time.time()+5; current.lease_until=0
            return
        if status.chunks<=0: raise BackendError('parse_failed','解析完成但没有有效分块')
        if instance.provider=='dify':
            try:await writer.set_binding_metadata(binding,metadata_id)
            except BackendError as error:
                # Assigning the same immutable binding metadata is idempotent.
                if error.code in ('timeout_unknown','conflict'):raise BackendError('metadata_check_retryable') from error
                raise
        await self.checkpoint(job,replica_id,state='ready',chunks=status.chunks,error='')
        async with self.db.atransaction(tenant) as s:
            self.domain.leased(s,job['id'],job['lease_token'],time.time()).state='completed'
            version=one(s,Version,tenant,version.id)
            if version.state!='published': version.state='ready'

    async def reconcile_upload(self,job):
        tenant=job['tenant_id']
        async with self.db.atransaction(tenant) as s:
            current=self.domain.leased(s,job['id'],job['lease_token'],time.time())
            proof=current.payload.get('submission_proof') or {}
            version=one(s,Version,tenant,current.payload['version_id'])
            replica=one(s,Replica,tenant,proof.get('binding_id',''))
            instance=one(s,BackendInstance,tenant,replica.instance_id)
            original=await asyncio.to_thread(self.domain.read_version,version)
            expected={'schema_version':1,'binding_id':replica.id,'version_id':version.id,'sha256':version.sha256,
                'instance_id':replica.instance_id,'collection_id':replica.collection_id,'size':len(original)}
            if (any(proof.get(key)!=value for key,value in expected.items()) or replica.version_id!=version.id or replica.remote_id
                    or replica.state!='submitting' or version.state=='cancelled'
                    or current.payload.get('instance_id')!=replica.instance_id):
                raise BackendError('reconciliation_unprovable')
            if not self.domain.backend_usable(instance):raise BackendError('invalid_configuration')
        # Original-file reads are an administrative recovery operation. Ingest-only
        # credentials need not have this privilege; never fall back to query keys.
        submitted=await self.factory(instance,'manage').reconcile_submission(replica.collection_id,proof)
        async with self.db.atransaction(tenant) as s:
            current=self.domain.leased(s,job['id'],job['lease_token'],time.time())
            current_replica=one(s,Replica,tenant,replica.id)
            if (current.payload.get('submission_proof')!=proof or current_replica.state!='submitting'
                    or current_replica.remote_id or not submitted.remote_id):raise BackendError('reconciliation_unprovable')
            if s.scalar(select(Replica).filter_by(instance_id=replica.instance_id,
                    collection_id=replica.collection_id,remote_id=submitted.remote_id)):
                raise BackendError('reconciliation_ambiguous')
            current.payload={**current.payload,'automatic_reconciliation':{'remote_id':submitted.remote_id,
                'sha256':proof['sha256'],'binding_id':replica.id,'verified_at':time.time(),
                'basis':'unique_upload_filename_and_original_sha256' if instance.provider=='dify'
                    else 'unique_binding_metadata_and_original_sha256'}}
            self.domain.audit(s,tenant,current.owner,'job.auto_reconcile',current.id)
        if not await self.checkpoint(job,replica.id,state='backend_processing',remote_id=submitted.remote_id,batch_id=submitted.batch_id):return
        async with self.db.atransaction(tenant) as s:
            current=self.domain.leased(s,job['id'],job['lease_token'],time.time())
            current.state='queued';current.next_run=0;current.lease_until=0;current.error='';current.failures=0

    async def delete(self,job):
        tenant=job['tenant_id']
        async with self.db.atransaction(tenant) as s:
            self.domain.leased(s,job['id'],job['lease_token'],time.time())
            doc=one(s,Document,tenant,job['payload']['document_id'])
            if not doc.withdrawn: raise DomainError('资料尚未撤回')
            versions=rows(s,Version,tenant,document_id=doc.id)
            replicas=[]
            for version in versions:
                for replica in rows(s,Replica,tenant,version_id=version.id):
                    if replica.remote_id and replica.state!='deleted':
                        replicas.append((replica,version,one(s,BackendInstance,tenant,replica.instance_id)))
        pending=False
        for replica,version,instance in replicas:
            backend=self.factory(instance,'write')
            binding=Binding(tenant,doc.id,version.id,replica.id,instance.id,replica.collection_id,replica.remote_id,replica.id,replica.batch_id)
            if replica.state!='delete_requested':
                try:await backend.delete(binding)
                except BackendError as error:
                    if error.code!='not_found':raise
                async with self.db.atransaction(tenant) as s:
                    self.domain.leased(s,job['id'],job['lease_token'],time.time())
                    one(s,Replica,tenant,replica.id).state='delete_requested'
            confirmed=await backend.deletion_confirmed(binding)
            pending=pending or not confirmed
            # Remote API absence is narrower than physical erasure of storage/backups.
            async with self.db.atransaction(tenant) as s:
                current=self.domain.leased(s,job['id'],job['lease_token'],time.time())
                one(s,Replica,tenant,replica.id).state='deleted' if confirmed else 'delete_requested'
                if confirmed:
                    confirmations={**current.payload.get('deletion_confirmations',{}),
                        replica.id:{'basis':'remote_api_not_found','confirmed_at':time.time()}}
                    current.payload={**current.payload,'deletion_confirmations':confirmations}
        async with self.db.atransaction(tenant) as s:
            current=self.domain.leased(s,job['id'],job['lease_token'],time.time())
            current.state='queued' if pending else 'completed'
            current.next_run=time.time()+30 if pending else 0;current.lease_until=0
