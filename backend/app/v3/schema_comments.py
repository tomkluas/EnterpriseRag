# 维护数据库表与字段的中文备注字典；字典文本是元数据，不是授权规则。
# 新增模型字段时需同时维护此字典，显式 migrate 会为已有 PostgreSQL 表更新备注。

"""中文数据库字典，同时用于建表与已有 PostgreSQL 表的备注迁移。"""

TABLES = {
    'tenants': '企业租户信息及权限、资料目录修订号', 'users': '企业成员与组织归属，旧角色及组字段仅用于兼容',
    'credentials': '成员本机密码摘要，不保存明文密码', 'login_sessions': '账号密码登录会话，仅保存令牌摘要',
    'login_throttles': '登录尝试限速窗口', 'spaces': '业务知识库与当前知识服务路由',
    'documents': '知识资料及访问规则', 'document_versions': '资料版本与原件存储位置',
    'backends': '知识服务实例及验收结果', 'backend_bindings': '资料版本在知识服务中的索引副本',
    'jobs': '持久化后台任务与执行租约', 'backend_switches': '知识库底座切换与一致性快照',
    'conversations': '成员问答会话', 'messages': '问答消息、证据与处理明细',
    'audit': '业务操作审计记录', 'worker_pulses': '后台进程心跳',
    'metric_buckets': '按分钟聚合的运行阶段耗时', 'alert_states': '运行告警状态与通知重试记录',
    'organization_units': '公司组织架构部门树', 'roles': '租户内系统与业务角色',
    'user_roles': '成员与角色的多对多关联',
}
COLUMNS = {
    'archived': '会话是否已从个人列表删除；审计记录仍保留',
    'id': '记录唯一标识', 'tenant_id': '所属企业标识', 'owner': '所属成员账号',
    'authz': '权限修订号，权限变化时递增', 'catalog': '资料目录修订号，资料变化时递增',
    'role': '系统角色：reader 知识使用者、editor 资料管理员、admin 系统管理员',
    'active': '是否启用', 'groups': '旧用户组名称，仅用于授权迁移兼容', 'password_hash': '带随机盐的 scrypt 密码摘要',
    'expires_at': '失效时间，Unix 秒', 'name': '业务显示名称',
    'readers': '允许阅读的 role:角色标识列表',
    'denied': '明确禁止阅读的 role:角色标识列表，禁止优先',
    'required': '知识库要求的证据定位能力', 'active_backend': '当前启用的知识服务实例标识',
    'route_revision': '知识库服务路由修订号', 'space_id': '所属业务知识库标识',
    'withdrawn': '资料是否已撤回', 'current_version': '当前已发布版本标识',
    'document_id': '所属知识资料标识', 'sha256': '原件内容 SHA-256 摘要',
    'object_key': '原件在自有存储中的对象键', 'filename': '上传原件文件名',
    'state': '当前处理状态', 'created_at': '创建时间，Unix 秒',
    'provider': '服务类型：weknora 或 dify', 'base_url': '知识服务基础地址',
    'key_ref': '检索凭据环境变量名称', 'write_key_ref': '写入凭据环境变量名称',
    'manage_key_ref': '管理凭据环境变量名称', 'collection_config': '远端知识库配置',
    'verified': '是否通过生产验收', 'capabilities': '已验证的服务能力列表', 'report': '验收报告或本机试运行证据',
    'version_id': '资料版本标识', 'instance_id': '知识服务实例标识', 'collection_id': '远端知识库标识',
    'remote_id': '远端文档标识', 'batch_id': '远端导入批次标识', 'metadata_id': '远端权限元数据字段标识',
    'chunks': '已建立索引的片段数量', 'error': '最近失败原因或错误代码',
    'kind': '后台任务类型', 'idempotency_key': '防止重复创建任务的幂等键', 'payload': '任务参数及恢复所需凭据',
    'next_run': '下一次可执行时间，Unix 秒', 'lease_token': '当前执行者租约令牌',
    'lease_until': '执行租约到期时间，Unix 秒', 'attempts': '累计尝试次数', 'failures': '连续失败次数',
    'target': '目标知识服务实例标识', 'authz_revision': '准备切换时的权限修订号',
    'catalog_revision': '准备切换时的资料目录修订号', 'manifest': '切换需验证的资料版本清单',
    'title': '会话标题', 'conversation_id': '所属问答会话标识', 'query': '成员提问原文',
    'answer': '生成的回答正文', 'evidence': '回答引用的资料片段与定位信息',
    'dependencies': '回答依赖的历史资料标识', 'feedback': '成员对回答的反馈',
    'trace': '问答各阶段耗时与执行结果，不含密钥',
    'actor': '执行操作的成员账号', 'action': '审计操作名称', 'resource_id': '被操作资源标识',
    'worker_id': '后台进程标识', 'last_seen': '最后心跳时间，Unix 秒', 'started_at': '进程启动时间，Unix 秒',
    'stage': '运行阶段名称', 'outcome': '阶段结果：ok、error 或 cancelled',
    'bucket': '聚合分钟起点，Unix 秒', 'count': '调用次数', 'total_seconds': '累计耗时秒数',
    'max_seconds': '最大单次耗时秒数', 'code': '告警规则代码', 'event_id': '通知去重事件标识',
    'changed_at': '告警状态变化时间，Unix 秒', 'delivery': '通知发送状态',
    'next_attempt': '下次通知尝试时间，Unix 秒', 'delivered_at': '最近成功发送时间，Unix 秒',
    'access_version': '角色授权模型迁移版本', 'display_name': '成员显示姓名',
    'org_unit_id': '成员所属组织部门标识', 'parent_id': '上级部门标识，根部门为空',
    'code': '内置角色代码或告警规则代码', 'description': '业务说明',
    'permissions': '角色可执行的系统操作权限', 'builtin': '是否内置角色',
    'legacy_subject': '旧授权兼容对象，仅迁移使用', 'user_id': '成员账号', 'role_id': '关联角色标识',
    'inherit_permissions': '资料是否继承知识库默认阅读角色',
}


# 把同一份字典绑定到 ORM 元数据，供新建表和备注迁移复用；特殊 id 的业务含义单独覆盖。
def apply_comments(metadata):
    for table in metadata.tables.values():
        table.comment = TABLES[table.name.removeprefix('v3_')]
        for column in table.columns:
            column.comment = COLUMNS[column.name]
    metadata.tables['v3_credentials'].c.id.comment = '成员账号，与成员表对应'
    metadata.tables['v3_login_sessions'].c.id.comment = '登录令牌 SHA-256 摘要'
    metadata.tables['v3_login_throttles'].c.id.comment = '账号或来源地址的 SHA-256 摘要'
