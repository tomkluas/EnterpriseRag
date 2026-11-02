# 任务执行器：持租约推进副本，保存远端回执，未知上传先对账再继续。
# 租约决定谁可写本地进度；远端操作不能随数据库事务回滚。
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

    # 交替优先领取普通任务和自动对账任务，避免一类持续有任务时饿死另一类。
    async def run_once(self):
        if self.prefer_reconciliation:
            job=await asyncio.to_thread(self.domain.claim_auto_reconciliation)
            if not job:job=await asyncio.to_thread(self.domain.claim_job,'v3-worker')
        else:
            job=await asyncio.to_thread(self.domain.claim_job,'v3-worker')
            if not job:job=await asyncio.to_thread(self.domain.claim_auto_reconciliation)
        if not job: return False
        self.prefer_reconciliation=not self.prefer_reconciliation
        # 续租与业务并行；续租失败取消本地操作，后续进度写入仍需验证当前令牌。
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
        # 非删除任务的超时或冲突可能留下已成功上传的文档，需对账而不能重复上传。
        except (BackendError,DomainError) as error:
            try:
                await asyncio.to_thread(self.domain.retry_job,job['id'],job['lease_token'],getattr(error,'code',str(error)),
                    unknown=job.get('_reconcile',False) or (job['kind']!='delete' and getattr(error,'code','') in ('timeout_unknown','conflict','needs_reconciliation')))
            except DomainError: pass
        except Exception:
            # 不保存上游原始异常，避免将内部地址或凭据写入任务错误记录。
            try: await asyncio.to_thread(self.domain.retry_job,job['id'],job['lease_token'],'worker_internal_error',unknown=job['kind']!='delete')
            except DomainError: pass
        finally:
            for task in (operation,heartbeat):task.cancel()
            await asyncio.gather(operation,heartbeat,return_exceptions=True)
        return True

    async def process(self,job):
        # 对账任务保留原上传证明，必须先恢复远端身份；不能走普通 ingest 的提交路径。
        if job.get('_reconcile'):await self.reconcile_upload(job)
        elif job['kind'] in ('ingest','replicate'): await self.ingest(job)
        elif job['kind']=='delete': await self.delete(job)
        else: raise DomainError('不支持的任务类型')

    # 只为当前令牌续租，旧执行器不能延长已被重新领取的任务租约。
    async def heartbeat(self,job):
        while True:
            # 心跳间隔短于租约时长；每次续期仍校验旧令牌，接管后的执行器不会被旧心跳干扰。
            await asyncio.sleep(15)
            async with self.db.atransaction() as s:
                current=self.domain.leased(s,job['id'],job['lease_token'],time.time()); current.lease_until=time.time()+60

    # 外部调用后的保存点重新核验租约、撤回和版本状态，处理提交过程中的并发变化。
    async def checkpoint(self,job,replica_id,index_status=None,**values):
        async with self.db.atransaction(job['tenant_id']) as s:
            current=self.domain.leased(s,job['id'],job['lease_token'],time.time())
            replica=one(s,Replica,job['tenant_id'],replica_id)
            doc=one(s,Document,job['tenant_id'],one(s,Version,job['tenant_id'],replica.version_id).document_id)
            if doc.withdrawn and values.get('remote_id'):
                # 即使撤回先提交，也必须保存已经获得的远端回执。
                for key,value in values.items(): setattr(replica,key,value)
                replica.state='delete_pending'
                # 即使撤回已生效也要保存远端回执，并用固定幂等键补建清理任务，避免不可追踪残留。
                cleanup_key='delete-after-submit:'+replica.id
                if not s.scalar(select(Job).filter_by(tenant_id=job['tenant_id'],idempotency_key=cleanup_key)):
                    s.add(Job(id=uid(),tenant_id=job['tenant_id'],owner=current.owner,kind='delete',
                        idempotency_key=cleanup_key,payload={'document_id':doc.id}))
                current.state='completed';current.lease_until=0
                return False
            if doc.withdrawn: raise DomainError('资料已撤回')
            # 未产生远端回执时直接拒绝撤回或取消；已产生回执的撤回由上方分支安排删除。
            if one(s,Version,job['tenant_id'],replica.version_id).state=='cancelled':raise DomainError('版本已取消')
            for key,value in values.items(): setattr(replica,key,value)
            if index_status is not None:
                # 状态时间来自真实轮询，和副本数量同事务保存，不按执行时长推算虚假百分比。
                current.payload={**current.payload,'index_status':{**index_status,'replica_id':replica.id,'observed_at':time.time()}}
            return True

    # 摄取和复制复用同一状态机；已有副本或回执时继续推进，不重复上传。
    async def ingest(self,job):
        tenant=job['tenant_id']; payload=job['payload']
        async with self.db.atransaction(tenant) as s:
            self.domain.leased(s,job['id'],job['lease_token'],time.time())
            version=one(s,Version,tenant,payload['version_id']); doc=one(s,Document,tenant,version.document_id)
            space=one(s,Space,tenant,doc.space_id)
            if doc.withdrawn: raise DomainError('资料已撤回')
            if version.state=='cancelled':raise DomainError('版本已取消')
            target=payload.get('instance_id') or space.active_backend
            # 复制任务固定目标；首次入库使用当前路由，提交前会把实际实例写入任务证明。
            if not target: raise BackendError('invalid_configuration','请先启用已验收的底座')
            instance=one(s,BackendInstance,tenant,target)
            if not self.domain.backend_usable(instance): raise BackendError('invalid_configuration','底座尚未通过当前环境的验收')
            self.domain.check_format(instance,version.filename)
            replica=s.scalar(select(Replica).filter_by(tenant_id=tenant,version_id=version.id,instance_id=target))
            if not replica:
                replica=Replica(id=uid(),tenant_id=tenant,version_id=version.id,instance_id=target); s.add(replica); s.flush()
            if replica.state=='ready':
                # 重复领取已完成副本只结束任务，不再次读取、上传或建立索引。
                self.domain.leased(s,job['id'],job['lease_token'],time.time()).state='completed'; return
            # 正在提交而无已知回执表示结果不明，必须对账，不能直接重新上传。
            if replica.state=='submitting':
                raise BackendError('needs_reconciliation')
            replica_id=replica.id; state=replica.state; collection=replica.collection_id; remote=replica.remote_id; batch=replica.batch_id; metadata_id=replica.metadata_id
            original=await asyncio.to_thread(self.domain.read_version,version)
            filename=version.filename
        writer=self.factory(instance,'write')
        # 网络调用在数据库事务外执行，每次结果落库都通过 checkpoint 重新检查并发状态。
        if not collection:
            # 集合准备只检查预先配置的资源，不创建新集合，因此允许重复执行。
            manager=self.factory(instance,'manage')
            created=await manager.prepare_collection('V3-'+replica_id)
            collection=created['id']; metadata_id=created.get('metadata_id','')
            await self.checkpoint(job,replica_id,state='received',collection_id=collection,metadata_id=metadata_id)
        if not remote:
            # Dify 上传时尚无绑定元数据，以绑定号和摘要组成文件名，供未知结果对账筛选。
            upload_filename=(f'v3-{replica_id}-{version.sha256[:16]}'+PurePath(filename).suffix) if instance.provider=='dify' else filename
            async with self.db.atransaction(tenant) as s:
                current=self.domain.leased(s,job['id'],job['lease_token'],time.time())
                current_version=one(s,Version,tenant,version.id)
                if one(s,Document,tenant,current_version.document_id).withdrawn or current_version.state=='cancelled':
                    raise DomainError('资料已撤回或版本已取消')
                # 发送前先持久化提交证据和 submitting 状态，为崩溃后的绑定、原件摘要核验保留依据。
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
            # 已有持久化回执后，查询超时只需重查索引，不能重新按未知上传处理。
            if error.code in ('timeout_unknown','conflict'):raise BackendError('index_check_retryable') from error
            raise
        if not await self.checkpoint(job,replica_id,chunks=status.chunks,
                index_status={'state':status.state,'raw_state':status.raw_state}):return
        if status.state=='failed': raise BackendError('parse_failed')
        # 索引未完成时释放租约并延后排队，不占着 running 状态长时间等待。
        if status.state!='completed':
            async with self.db.atransaction(tenant) as s:
                current=self.domain.leased(s,job['id'],job['lease_token'],time.time())
                current.state='queued'; current.next_run=time.time()+5; current.lease_until=0
            return
        if status.chunks<=0: raise BackendError('parse_failed','解析完成但没有有效分块')
        if instance.provider=='dify':
            # Dify 必须在索引完成后写入授权绑定并回读成功，才可将副本标记为 ready。
            try:await writer.set_binding_metadata(binding,metadata_id)
            except BackendError as error:
                # 重复写入同一个不可变绑定标识不会改变归属，失败后可安全重试。
                if error.code in ('timeout_unknown','conflict'):raise BackendError('metadata_check_retryable') from error
                raise
        await self.checkpoint(job,replica_id,state='ready',chunks=status.chunks,error='')
        async with self.db.atransaction(tenant) as s:
            self.domain.leased(s,job['id'],job['lease_token'],time.time()).state='completed'
            version=one(s,Version,tenant,version.id)
            if version.state!='published': version.state='ready'

    # 先验证本地提交证据未变，再以管理凭据核验远端原件；不得回退借用查询凭据。
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
            # 同时核对不可变原件和原提交目标，任何证明缺失或状态变化都不能猜测远端归属。
            if (any(proof.get(key)!=value for key,value in expected.items()) or replica.version_id!=version.id or replica.remote_id
                    or replica.state!='submitting' or version.state=='cancelled'
                    or current.payload.get('instance_id')!=replica.instance_id):
                raise BackendError('reconciliation_unprovable')
            if not self.domain.backend_usable(instance):raise BackendError('invalid_configuration')
        # 原件读取属于管理恢复操作，纯写入凭据未必具备此权限，也不能借用查询凭据。
        submitted=await self.factory(instance,'manage').reconcile_submission(replica.collection_id,proof)
        async with self.db.atransaction(tenant) as s:
            current=self.domain.leased(s,job['id'],job['lease_token'],time.time())
            current_replica=one(s,Replica,tenant,replica.id)
            # 外部核验期间可能发生接管或人工对账，保存结果前再查一次原证明和本地状态。
            if (current.payload.get('submission_proof')!=proof or current_replica.state!='submitting'
                    or current_replica.remote_id or not submitted.remote_id):raise BackendError('reconciliation_unprovable')
            # 拒绝认领已属于其他副本的远端条目，保证回执与本地副本唯一对应。
            if s.scalar(select(Replica).filter_by(instance_id=replica.instance_id,
                    collection_id=replica.collection_id,remote_id=submitted.remote_id)):
                raise BackendError('reconciliation_ambiguous')
            current.payload={**current.payload,'automatic_reconciliation':{'remote_id':submitted.remote_id,
                'sha256':proof['sha256'],'binding_id':replica.id,'verified_at':time.time(),
                'basis':'unique_upload_filename_and_original_sha256' if instance.provider=='dify'
                    else 'unique_binding_metadata_and_original_sha256'}}
            self.domain.audit(s,tenant,current.owner,'job.auto_reconcile',current.id)
        if not await self.checkpoint(job,replica.id,state='backend_processing',remote_id=submitted.remote_id,batch_id=submitted.batch_id):return
        # 恢复身份后回到普通轮询队列；找到上传结果并不等于索引已完成或版本已发布。
        async with self.db.atransaction(tenant) as s:
            current=self.domain.leased(s,job['id'],job['lease_token'],time.time())
            current.state='queued';current.next_run=0;current.lease_until=0;current.error='';current.failures=0

    # 只删除已撤回资料的已知副本；请求接受后仍独立确认不存在，未确认就继续排队。
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
            # 只处理已知回执；未知上传需先对账找回身份，不能按文档名猜测并删除。
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
            # 删除请求成功仅表示被接受，仍需单独查询不存在；未确认的副本下轮继续检查。
            pending=pending or not confirmed
            # 远端 API 不再可见，仅证明接口层删除，不代表存储及备份已物理擦除。
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
