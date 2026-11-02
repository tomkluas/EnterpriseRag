# 知识底座公共契约：为适配器和任务执行器统一授权绑定、远端回执、索引状态及证据结构。
from dataclasses import dataclass, asdict
from typing import Protocol


# 稳定错误码供上层区分可重试、结果未知和需人工核查；reason 记录更细的诊断类别。
class BackendError(Exception):
    def __init__(self, code: str, message: str = '', *, reason: str = ''):
        self.code = code
        self.reason = reason
        super().__init__(message or code)


# 连接本地文档版本与特定底座远端条目的不可变身份映射。
@dataclass(frozen=True)
class Binding:
    tenant_id: str  # 所属企业，检索批次中的所有绑定必须与 Scope 一致
    document_id: str  # 本地业务资料标识
    version_id: str  # 获准检索的本地发布版本
    generation_id: str  # 索引代际标识，返回证据必须与请求绑定相符
    instance_id: str  # 目标知识服务实例
    collection_id: str  # 远端物理知识库/集合，批次不能跨集合混发
    remote_id: str  # 远端文档标识，只允许检索这些显式列出的文档
    id: str  # 本地副本绑定标识，用于核对证据的实际来源
    batch_id: str = ''  # 远端上传批次，部分平台查询解析进度时需要


# 授权快照只允许列出的绑定；修订号用于识别授权、资料目录或路由变化。
@dataclass(frozen=True)
class Scope:
    tenant_id: str  # 本次检索的企业边界
    bindings: tuple[Binding, ...]  # 已授权发布版本的不可变绑定集合
    authz_revision: int = 0  # 形成快照时的权限修订号
    catalog_revision: int = 0  # 形成快照时的资料目录修订号
    route_revision: int = 0  # 形成快照时的知识库路由修订号


# 统一证据保留来源身份和定位精度；供应商原始分数不保证跨批次可比。
@dataclass
class Evidence:
    tenant_id: str  # 证据所属企业，必须与授权快照一致
    document_id: str  # 引用回溯到的本地资料
    version_id: str  # 证据来自哪个原件版本，不能替换成当前其他版本
    generation_id: str  # 证据声称的索引代际，用于拒绝陈旧或串用的结果
    instance_id: str  # 实际召回证据的服务实例
    collection_id: str  # 实际召回证据的远端物理集合
    chunk_id: str  # 片段标识，同一集合内用于去重和引用定位
    content: str  # 底座实际检索返回的原文片段
    score: float  # 供应商原始召回分数，不据此直接比较不同批次
    locator_level: str  # 已确认的定位精度，例如片段级或页级
    locator: dict  # 与定位精度对应的页码、段落或其他定位信息

    def to_dict(self):
        return asdict(self)


# 上传回执只表示远端已接收，不代表解析、索引或授权元数据已经完成。
@dataclass(frozen=True)
class Submission:
    remote_id: str  # 远端已接受的文档标识，供后续状态轮询和清理使用
    batch_id: str = ''  # 供应商返回的解析批次，可为空


# 状态归一化保留供应商原始状态与分块数，任务层据此决定继续轮询、失败或就绪。
@dataclass(frozen=True)
class BackendStatus:
    state: str  # 适配器归一化后的解析/索引状态
    chunks: int  # 可确认的片段数量，不以空索引当作成功发布依据
    raw_state: str  # 供应商原始状态，便于排查不同平台的状态映射


# 区分删除被接受和远端已确认不存在，避免把接收请求当成物理清除。
@dataclass(frozen=True)
class DeleteReceipt:
    accepted: bool  # 服务是否接受删除请求
    confirmed: bool = False  # 是否已确认远端 API 中不存在该文档，不等于存储介质物理擦除


# 各供应商的共同接口；实际凭据权限由工厂按 retrieve/write/manage 用途选择。
class KnowledgeBackend(Protocol):
    # 准备或确认远端集合；实际是否创建新集合由平台适配器能力决定。
    async def prepare_collection(self, name: str) -> dict: ...
    # 提交自有原件副本；binding_id 的记录方式由适配器决定，回执不代表索引就绪。
    async def submit(self, collection_id: str, filename: str, content: bytes, binding_id: str) -> Submission: ...
    # 对照提交证明认领未知上传结果；无法唯一确认时不得猜测成功。
    async def reconcile_submission(self, collection_id: str, proof: dict) -> Submission: ...
    # 查询既有绑定的解析状态，不代替业务层执行发布。
    async def status(self, binding: Binding) -> BackendStatus: ...
    # 分页读取指定文档的实际分块，外发前由调用方复查资料阅读权。
    async def list_chunks(self, binding: Binding, *, page=1, page_size=20, dispatch_gate=None) -> dict: ...
    # 检索必须受 Scope 限制，dispatch_gate 在真正发出外部请求前复查权限。
    async def retrieve(self, query: str, scope: Scope, dispatch_gate=None) -> list[Evidence]: ...
    # 发起删除与后续不存在确认分开，兼容异步清理的平台。
    async def delete(self, binding: Binding) -> DeleteReceipt: ...
    async def deletion_confirmed(self, binding: Binding) -> bool: ...
    async def capabilities(self) -> dict: ...


# 一个请求批次只属于一个实例和集合；远端条目与本地绑定均不得重复。
def validate_scope(scope: Scope) -> dict[str, Binding]:
    result = {}
    groups = set()
    binding_ids = set()
    for binding in scope.bindings:
        # 先检查每项身份，再建立 remote_id 到本地绑定的映射，不能让重复键覆盖旧授权。
        if binding.tenant_id != scope.tenant_id or not all((binding.remote_id, binding.collection_id, binding.id)):
            raise BackendError('invalid_scope')
        if binding.remote_id in result or binding.id in binding_ids:
            raise BackendError('invalid_scope')
        groups.add((binding.instance_id, binding.collection_id))
        result[binding.remote_id] = binding
        binding_ids.add(binding.id)
    if len(groups) > 1:
        # 一个远端查询只能面向一个物理集合，多集合必须由上层拆批后分别调用。
        raise BackendError('invalid_scope', '检索批次必须属于同一底座实例和物理集合')
    return result
