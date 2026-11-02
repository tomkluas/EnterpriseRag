# 任务分块查看：任务可见性、正文阅读权和副本归属分别校验，不把维护能力当成阅读授权。
from contextlib import asynccontextmanager
from sqlalchemy import select
from ..domain import DomainError, one
from ..models import Job, Replica, Version, Document, Space, BackendInstance
from ..knowledge.contracts import BackendError, Binding
from ..knowledge.chunks import validate_page


def preview_context(domain, s, tenant, user, job_id, replica_id):
    domain.require_knowledge_workspace(s, tenant, user)
    job = one(s, Job, tenant, job_id)
    if job.owner != user and not domain.has_permission(s, tenant, user, 'system.manage'):
        raise DomainError('资源不存在或无权访问', 404)
    replica = one(s, Replica, tenant, replica_id)
    version = one(s, Version, tenant, replica.version_id)
    doc = one(s, Document, tenant, version.document_id)
    space = one(s, Space, tenant, doc.space_id)
    # 只接受任务原本处理的版本与目标，不能借一个可见任务读取任意副本。
    if job.kind not in ('ingest','replicate') or job.payload.get('version_id') != version.id:
        raise DomainError('副本不属于此任务', 404)
    target = job.payload.get('instance_id')
    if target:
        if target != replica.instance_id:raise DomainError('副本不属于此任务目标', 404)
    else:
        candidates = list(s.scalars(select(Replica.id).filter_by(tenant_id=tenant, version_id=version.id)))
        if candidates != [replica.id]:raise DomainError('任务目标尚未唯一确认，不能查看分块')
    if not domain.allowed(s, tenant, user, doc):raise DomainError('没有资料阅读权限或资料已撤回', 403)
    if version.state != 'published':domain.manage_space(s, tenant, user, space)
    if replica.state != 'ready' or not replica.remote_id or not replica.collection_id:
        raise DomainError('索引尚未就绪，暂不能预览分块')
    instance = one(s, BackendInstance, tenant, replica.instance_id)
    if not domain.backend_usable(instance):raise DomainError('底座未通过当前环境验收', 503)
    binding = Binding(tenant, doc.id, version.id, replica.id, instance.id, replica.collection_id,
                      replica.remote_id, replica.id, replica.batch_id)
    return binding, instance, version.filename


async def preview_chunks(domain, registry, tenant, user, job_id, replica_id, *, page=1, page_size=20):
    try:validate_page(page, page_size)
    except BackendError as error:raise DomainError(str(error),400) from error
    async with domain.db.atransaction(tenant, shared=True) as s:
        binding, instance, filename = preview_context(domain, s, tenant, user, job_id, replica_id)
    signature = (binding, instance.base_url, instance.key_ref, instance.collection_config)

    def check(s):
        current, backend, _ = preview_context(domain, s, tenant, user, job_id, replica_id)
        if (current, backend.base_url, backend.key_ref, backend.collection_config) != signature:
            raise DomainError('任务索引目标已变化，请刷新后重试')

    @asynccontextmanager
    async def dispatch_gate():
        # 每次实际发出请求前重新核对，防止取得快照到外发之间发生撤权或绑定替换。
        async with domain.db.atransaction(tenant, shared=True) as s:
            check(s)
            yield

    result = await registry.get(instance, 'retrieve').list_chunks(binding, page=page, page_size=page_size, dispatch_gate=dispatch_gate)
    async with domain.db.atransaction(tenant, shared=True) as s:
        # 网络等待期间可能撤权，返回浏览器前必须再查一次，不能把已取回的正文继续放行。
        check(s)
        return {**result, 'filename':filename, 'replica_id':binding.id, 'version_id':binding.version_id, 'provider':instance.provider}
