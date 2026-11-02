# 业务持久化模型：资料、原件版本、角色授权与远端索引副本各自独立保存。
# 以下源码注释解释业务含义；数据库 COMMENT 由 schema_comments.py 统一写入元数据。
# 跨表归属与租户范围由业务层校验，不能仅凭某条记录的 id 判断访问权。

import time
from sqlalchemy import String, Text, Boolean, Integer, Float, JSON, UniqueConstraint, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# 所有业务模型共用的 SQLAlchemy 映射基类；元数据供显式迁移与备份表清单复用。
class Base(DeclarativeBase): pass


# 企业边界及一致性修订号；权限、资料目录变化使进行中的问答或切换重新校验。
class Tenant(Base):
    __tablename__ = 'v3_tenants'
    id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 企业租户标识，所有业务查询的隔离边界
    authz: Mapped[int] = mapped_column(Integer,default=0)  # 权限修订号；角色、成员或资料授权变化时递增
    catalog: Mapped[int] = mapped_column(Integer,default=0)  # 资料目录修订号；版本、撤回等变化使旧检索快照失效
    name: Mapped[str] = mapped_column(String(180),default='企业')  # 业务显示名称
    active: Mapped[bool] = mapped_column(Boolean,default=True)  # 是否启用
    access_version: Mapped[int] = mapped_column(Integer,default=0)  # 授权数据迁移版本：0 为旧身份规则，1 为已转换角色，防止重复迁移扩权


# 成员账号与组织归属；实际多角色关系保存在 UserRole，旧 role/groups 仅兼容历史工具。
class User(Base):
    __tablename__ = 'v3_users'
    tenant_id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 所属企业标识
    id: Mapped[str] = mapped_column(String(128),primary_key=True)  # 成员登录账号；仅在所属 tenant_id 内唯一
    role: Mapped[str] = mapped_column(String(24),default='reader')  # 旧单角色兼容值；新授权以 UserRole 和启用角色的 permissions 为准
    active: Mapped[bool] = mapped_column(Boolean,default=True)  # 是否启用
    groups: Mapped[list] = mapped_column(JSON,default=list)  # 旧用户组兼容数据；迁移后新成员通过 org_unit_id 归属公司部门
    display_name: Mapped[str] = mapped_column(String(128),default='')  # 成员显示姓名
    org_unit_id: Mapped[str] = mapped_column(String(64),default='')  # 成员所属组织部门标识


# 公司部门树；上下级关系用于组织管理，不直接参与资料阅读授权。
class OrganizationUnit(Base):
    __tablename__ = 'v3_organization_units'
    tenant_id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 所属企业标识
    id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 记录唯一标识
    name: Mapped[str] = mapped_column(String(128))  # 业务显示名称
    parent_id: Mapped[str] = mapped_column(String(64),default='')  # 上级部门标识，根部门为空
    active: Mapped[bool] = mapped_column(Boolean,default=True)  # 是否启用


# 系统操作权限与业务角色定义；角色是否获得某份资料阅读范围由资料规则决定。
class Role(Base):
    __tablename__ = 'v3_roles'
    tenant_id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 所属企业标识
    id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 记录唯一标识
    name: Mapped[str] = mapped_column(String(128))  # 业务显示名称
    code: Mapped[str] = mapped_column(String(24),default='')  # 内置角色代码 admin/editor/reader；自定义角色为空，不以显示名称判断能力
    description: Mapped[str] = mapped_column(Text,default='')  # 业务说明
    permissions: Mapped[list] = mapped_column(JSON,default=list)  # 角色可执行的系统操作权限
    active: Mapped[bool] = mapped_column(Boolean,default=True)  # 是否启用
    builtin: Mapped[bool] = mapped_column(Boolean,default=False)  # 是否内置角色
    legacy_subject: Mapped[str] = mapped_column(String(180),default='')  # 旧授权迁移对象：user: 区分负责人，principal: 保留旧身份规则


# 成员与角色的多对多关联，联合主键防止同一租户内重复分配。
class UserRole(Base):
    __tablename__ = 'v3_user_roles'
    tenant_id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 所属企业标识
    user_id: Mapped[str] = mapped_column(String(128),primary_key=True)  # 成员账号
    role_id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 关联角色标识


# 本机登录密码摘要独立存储，避免普通成员目录的字段序列化带出敏感凭据。
class Credential(Base):
    __tablename__ = 'v3_credentials'
    tenant_id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 所属企业标识
    id: Mapped[str] = mapped_column(String(128),primary_key=True)  # 密码所属成员账号，与 User 的租户/账号组合对应
    password_hash: Mapped[str] = mapped_column(String(300))  # 带随机盐的 scrypt 密码摘要


# 本机密码登录会话；令牌明文不落库，密码重置、停用和退出会使会话失效。
class LoginSession(Base):
    __tablename__ = 'v3_login_sessions'
    tenant_id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 所属企业标识
    id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 本机会话令牌的 SHA-256 摘要，不是令牌明文
    owner: Mapped[str] = mapped_column(String(128),index=True)  # 所属成员账号
    expires_at: Mapped[float] = mapped_column(Float)  # 失效时间，Unix 秒


# 账号与来源地址的短期限速窗口，失败计数必须提交才能限制后续尝试。
class LoginThrottle(Base):
    __tablename__ = 'v3_login_throttles'
    tenant_id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 所属企业标识
    id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 账号或来源地址限速键的 SHA-256 摘要
    attempts: Mapped[int] = mapped_column(Integer,default=0)  # 当前分钟窗口的尝试次数；账号成功登录后清零账号计数，来源计数保留
    expires_at: Mapped[float] = mapped_column(Float)  # 失效时间，Unix 秒


# 业务知识库：保存默认角色阅读规则、所需定位能力和当前知识服务路由。
class Space(Base):
    __tablename__ = 'v3_spaces'
    id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 记录唯一标识
    tenant_id: Mapped[str] = mapped_column(String(64),index=True)  # 所属企业标识
    owner: Mapped[str] = mapped_column(String(128))  # 所属成员账号
    name: Mapped[str] = mapped_column(String(180))  # 业务显示名称
    readers: Mapped[list] = mapped_column(JSON,default=list)  # 知识库默认允许阅读的 role:角色标识列表
    denied: Mapped[list] = mapped_column(JSON,default=list)  # 知识库级明确禁止角色；资料关闭默认允许继承后仍受此限制
    required: Mapped[list] = mapped_column(JSON,default=lambda:['segment'])  # 知识库要求的证据定位能力
    active_backend: Mapped[str] = mapped_column(String(64),default='')  # 当前启用的知识服务实例标识
    route_revision: Mapped[int] = mapped_column(Integer,default=0)  # 知识库服务路由修订号


# 业务资料与阅读规则；发布指针和原件版本分离，授权判断不依赖远端服务的角色系统。
class Document(Base):
    __tablename__ = 'v3_documents'
    id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 记录唯一标识
    tenant_id: Mapped[str] = mapped_column(String(64),index=True)  # 所属企业标识
    space_id: Mapped[str] = mapped_column(String(64),index=True)  # 所属业务知识库标识
    owner: Mapped[str] = mapped_column(String(128))  # 所属成员账号
    name: Mapped[str] = mapped_column(String(240))  # 业务显示名称
    readers: Mapped[list] = mapped_column(JSON,default=list)  # 本资料允许角色；启用继承时追加到知识库默认允许集合
    denied: Mapped[list] = mapped_column(JSON,default=list)  # 明确禁止阅读的 role:角色标识列表，禁止优先
    inherit_permissions: Mapped[bool] = mapped_column(Boolean,default=True)  # 是否继承默认允许角色；知识库与资料的明确禁止始终优先
    withdrawn: Mapped[bool] = mapped_column(Boolean,default=False)  # 资料是否已撤回
    current_version: Mapped[str] = mapped_column(String(64),default='')  # 当前生效的已发布版本标识；上传草稿不会自动替换此指针


# 不可变原件版本；文件接收、索引可用与业务发布是不同状态，不能相互替代。
class Version(Base):
    __tablename__ = 'v3_document_versions'
    id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 记录唯一标识
    tenant_id: Mapped[str] = mapped_column(String(64),index=True)  # 所属企业标识
    document_id: Mapped[str] = mapped_column(String(64),index=True)  # 所属知识资料标识
    sha256: Mapped[str] = mapped_column(String(64))  # 原件内容 SHA-256 摘要
    object_key: Mapped[str] = mapped_column(String(240))  # 自有存储中的不可变原件位置，不使用底座远端 ID 代替
    filename: Mapped[str] = mapped_column(String(240))  # 上传原件文件名
    state: Mapped[str] = mapped_column(String(32),default='received')  # 当前处理状态
    created_at: Mapped[float] = mapped_column(Float,default=time.time)  # 创建时间，Unix 秒


# 知识服务实例及验收证据；服务凭据只保存环境变量名称，不保存密钥本身。
class BackendInstance(Base):
    __tablename__ = 'v3_backends'
    id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 记录唯一标识
    tenant_id: Mapped[str] = mapped_column(String(64),index=True)  # 所属企业标识
    provider: Mapped[str] = mapped_column(String(24))  # 服务类型：weknora 或 dify
    base_url: Mapped[str] = mapped_column(String(600))  # 知识服务基础地址
    key_ref: Mapped[str] = mapped_column(String(128))  # 检索凭据环境变量名称
    write_key_ref: Mapped[str] = mapped_column(String(128),default='')  # 写入凭据环境变量名称
    manage_key_ref: Mapped[str] = mapped_column(String(128),default='')  # 管理凭据环境变量名称
    collection_config: Mapped[dict] = mapped_column(JSON,default=dict)  # 远端知识库配置
    verified: Mapped[bool] = mapped_column(Boolean,default=False)  # 是否通过生产验收
    capabilities: Mapped[list] = mapped_column(JSON,default=list)  # 已验证的服务能力列表
    report: Mapped[dict] = mapped_column(JSON,default=dict)  # 验收报告或本机试运行证据


# 资料版本在某底座中的索引副本；同一版本可同时在不同底座准备索引。
class Replica(Base):
    __tablename__ = 'v3_backend_bindings'
    __table_args__ = (UniqueConstraint('tenant_id','version_id','instance_id'),
        UniqueConstraint('instance_id','collection_id','remote_id'))
    id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 记录唯一标识
    tenant_id: Mapped[str] = mapped_column(String(64),index=True)  # 所属企业标识
    version_id: Mapped[str] = mapped_column(String(64),index=True)  # 资料版本标识
    instance_id: Mapped[str] = mapped_column(String(64),index=True)  # 知识服务实例标识
    collection_id: Mapped[str | None] = mapped_column(String(128),nullable=True)  # 远端知识库标识
    remote_id: Mapped[str | None] = mapped_column(String(128),nullable=True)  # 知识服务中对应文档的标识；未知提交结果时不能凭猜测填写
    batch_id: Mapped[str] = mapped_column(String(180),default='')  # 远端导入批次标识
    metadata_id: Mapped[str] = mapped_column(String(128),default='')  # 远端权限元数据字段标识
    state: Mapped[str] = mapped_column(String(32),default='received')  # 该底座副本的索引/清理状态，不等同于业务资料是否已发布
    chunks: Mapped[int] = mapped_column(Integer,default=0)  # 已建立索引的片段数量
    error: Mapped[str] = mapped_column(Text,default='')  # 最近失败原因或错误代码


# 持久化任务队列；租约令牌阻止过期执行者提交，未知远端结果须对账后恢复。
class Job(Base):
    __tablename__ = 'v3_jobs'
    __table_args__ = (UniqueConstraint('tenant_id','idempotency_key'), Index('ix_v3_jobs_due','state','next_run'))
    id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 记录唯一标识
    tenant_id: Mapped[str] = mapped_column(String(64),index=True)  # 所属企业标识
    owner: Mapped[str] = mapped_column(String(128))  # 所属成员账号
    kind: Mapped[str] = mapped_column(String(24))  # 后台任务类型
    idempotency_key: Mapped[str] = mapped_column(String(240))  # 防止重复创建任务的幂等键
    payload: Mapped[dict] = mapped_column(JSON)  # 任务参数与提交/对账证据；内容随任务类型变化
    state: Mapped[str] = mapped_column(String(32),default='queued')  # 当前处理状态
    created_at: Mapped[float] = mapped_column(Float,default=time.time)  # 创建时间，Unix 秒
    next_run: Mapped[float] = mapped_column(Float,default=0)  # 下一次可执行时间，Unix 秒
    lease_token: Mapped[str] = mapped_column(String(64),default='')  # 本次领取生成的所有权令牌；重领任务后旧令牌不得提交结果
    lease_until: Mapped[float] = mapped_column(Float,default=0)  # 执行租约到期时间，Unix 秒
    attempts: Mapped[int] = mapped_column(Integer,default=0)  # 领取和执行的累计次数，与连续失败次数分开统计
    failures: Mapped[int] = mapped_column(Integer,default=0)  # 连续失败计数，用于退避与达到上限后的失败判定
    error: Mapped[str] = mapped_column(Text,default='')  # 最近失败原因或错误代码


# 底座切换准备快照；权限、目录、路由及版本清单一致后才允许启用目标。
class BackendSwitch(Base):
    __tablename__ = 'v3_backend_switches'
    id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 记录唯一标识
    tenant_id: Mapped[str] = mapped_column(String(64),index=True)  # 所属企业标识
    space_id: Mapped[str] = mapped_column(String(64))  # 所属业务知识库标识
    target: Mapped[str] = mapped_column(String(64))  # 目标知识服务实例标识
    authz_revision: Mapped[int] = mapped_column(Integer)  # 准备切换时的权限修订号
    catalog_revision: Mapped[int] = mapped_column(Integer)  # 准备切换时的资料目录修订号
    route_revision: Mapped[int] = mapped_column(Integer)  # 知识库服务路由修订号
    manifest: Mapped[list] = mapped_column(JSON)  # 切换需验证的资料版本清单
    state: Mapped[str] = mapped_column(String(24),default='preparing')  # 当前处理状态


# 属于单个成员的会话容器；个人历史接口不会因同租户或管理员身份自动共享。
class Conversation(Base):
    __tablename__ = 'v3_conversations'
    id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 记录唯一标识
    tenant_id: Mapped[str] = mapped_column(String(64),index=True)  # 所属企业标识
    owner: Mapped[str] = mapped_column(String(128))  # 所属成员账号
    title: Mapped[str] = mapped_column(String(180),default='新会话')  # 会话标题
    archived: Mapped[bool] = mapped_column(Boolean,default=False)  # 个人删除后隐藏，保留独立审计记录


# 问题、回答、来源与历史依赖快照；查看和继续对话时仍需按当前权限复查。
class Message(Base):
    __tablename__ = 'v3_messages'
    __table_args__ = (Index('ix_v3_messages_tenant_created','tenant_id','created_at','id'),)
    id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 记录唯一标识
    tenant_id: Mapped[str] = mapped_column(String(64),index=True)  # 所属企业标识
    conversation_id: Mapped[str] = mapped_column(String(64),index=True)  # 所属问答会话标识
    space_id: Mapped[str] = mapped_column(String(64),default='')  # 首次提问选择的知识库，供失败重试复用；旧记录为空
    owner: Mapped[str] = mapped_column(String(128))  # 所属成员账号
    query: Mapped[str] = mapped_column(Text)  # 成员提问原文
    answer: Mapped[str] = mapped_column(Text,default='')  # 生成的回答正文
    state: Mapped[str] = mapped_column(String(24),default='processing')  # 当前处理状态
    evidence: Mapped[list] = mapped_column(JSON,default=list)  # 回答引用的资料片段与定位信息
    dependencies: Mapped[list] = mapped_column(JSON,default=list)  # 本回答依赖的历史资料标识；引用较少也不能遗漏历史内容的权限检查
    feedback: Mapped[str] = mapped_column(Text,default='')  # 成员对回答的反馈
    trace: Mapped[dict] = mapped_column(JSON,default=dict)  # 各处理阶段耗时、结果和安全失败说明，不保存完整模型请求或服务密钥
    created_at: Mapped[float] = mapped_column(Float,default=time.time)  # 创建时间，Unix 秒


# 业务操作审计；仅保存操作者、动作和资源定位，不用它存储模型或服务密钥。
class Audit(Base):
    __tablename__ = 'v3_audit'
    id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 记录唯一标识
    tenant_id: Mapped[str] = mapped_column(String(64),index=True)  # 所属企业标识
    actor: Mapped[str] = mapped_column(String(128))  # 执行操作的成员账号
    action: Mapped[str] = mapped_column(String(64))  # 审计操作名称
    resource_id: Mapped[str] = mapped_column(String(128))  # 被操作资源标识
    created_at: Mapped[float] = mapped_column(Float,default=time.time)  # 创建时间，Unix 秒


# 按租户保存后台进程心跳；空闲进程也要更新，避免用任务活动代替进程存活。
class WorkerPulse(Base):
    __tablename__ = 'v3_worker_pulses'
    tenant_id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 所属企业标识
    worker_id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 后台进程标识
    last_seen: Mapped[float] = mapped_column(Float)  # 最后心跳时间，Unix 秒
    started_at: Mapped[float] = mapped_column(Float)  # 进程启动时间，Unix 秒


# 按分钟聚合阶段耗时，保存计数和汇总值，不记录问题或资料原文。
class MetricBucket(Base):
    __tablename__ = 'v3_metric_buckets'
    tenant_id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 所属企业标识
    stage: Mapped[str] = mapped_column(String(32),primary_key=True)  # 运行阶段名称
    outcome: Mapped[str] = mapped_column(String(16),primary_key=True)  # 阶段结果：ok、error 或 cancelled
    bucket: Mapped[int] = mapped_column(Integer,primary_key=True)  # 分钟起点时间戳（Unix 秒），不是调用序号
    count: Mapped[int] = mapped_column(Integer,default=0)  # 调用次数
    total_seconds: Mapped[float] = mapped_column(Float,default=0)  # 累计耗时秒数
    max_seconds: Mapped[float] = mapped_column(Float,default=0)  # 最大单次耗时秒数


# 告警状态与通知投递租约；重复投递通过 event_id 由接收方去重。
class AlertState(Base):
    __tablename__ = 'v3_alert_states'
    tenant_id: Mapped[str] = mapped_column(String(64),primary_key=True)  # 所属企业标识
    code: Mapped[str] = mapped_column(String(64),primary_key=True)  # 告警规则标识，用于区分同租户内不同运行问题
    active: Mapped[bool] = mapped_column(Boolean,default=True)  # 当前告警条件是否成立，不是成员或角色的启用状态
    event_id: Mapped[str] = mapped_column(String(64))  # 通知去重事件标识
    changed_at: Mapped[float] = mapped_column(Float)  # 告警状态变化时间，Unix 秒
    delivery: Mapped[str] = mapped_column(String(16),default='disabled')  # 通知发送状态，与告警条件 active 分开记录
    attempts: Mapped[int] = mapped_column(Integer,default=0)  # 当前告警事件的通知投递尝试次数
    next_attempt: Mapped[float] = mapped_column(Float,default=0)  # 下次通知尝试时间，Unix 秒
    lease_until: Mapped[float] = mapped_column(Float,default=0)  # 执行租约到期时间，Unix 秒
    delivered_at: Mapped[float] = mapped_column(Float,default=0)  # 最近成功发送时间，Unix 秒
    error: Mapped[str] = mapped_column(String(32),default='')  # 最近失败原因或错误代码


from .schema_comments import apply_comments
apply_comments(Base.metadata)
