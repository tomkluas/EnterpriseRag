# 单租户逻辑备份与恢复

`app.v3.recovery` 提供可调用工具；只包含 V3 单租户业务模型行及这些版本引用的原件。不会读取环境配置或导出密钥值；底座配置的凭据引用会保留。包内包括权限、会话、正文和审计记录，属于敏感数据，禁止提交 Git 或放进测试报告。

这不是 PostgreSQL 物理备份或完整系统灾备。不包含平台向量、平台文件、平台数据库、S3 版本历史、独立密钥或部署配置，也不备份没有业务版本引用的孤立对象。数据库 schema 版本表由目标迁移创建，不作为单租户数据复制。

接口：

```python
backup_tenant(db, originals, tenant_id, destination) -> dict
restore_tenant(db, tenant_id, bundle, original_destination) -> dict
```

备份目标必须是不存在的目录，父目录应由运维事先配置为可信受限目录。工具在 POSIX 创建 0700 目录，在 Windows 移除继承 ACL 并仅授予当前账号完全控制；ACL 设置失败则停止。文件继承该目录权限。备份并未加密，管理员权限、磁盘快照和离线磁盘访问仍由宿主机及备份系统控制；需另行加密、限定保管人和设置保留周期。不要在其他进程能够修改路径的目录中运行。

备份在租户协调事务锁内查询所有模型，读取本地或 S3 原件并验证版本 SHA256，再生成 `rows.json`、`objects/` 和 `manifest.json`。失败会删除本次新建目录。已有目标从不覆盖。所有原件键拒绝绝对路径、路径穿越、反斜线和冒号；本地软链接不允许。

恢复前停止目标 API、Worker 和其他写入者，迁移一个独立目标数据库，配置一个尚不存在的本地原件目录，或者专用于本次演练的全新空 S3 前缀。目标租户在每张表中都必须为空；其他租户的全局主键或唯一键冲突会使整个事务回滚，不会覆盖既有数据。恢复不能改租户编号。原件键只能位于该租户的 SHA256 命名空间，兼容早期 `tenant_id/` 命名空间，拒绝跨租户键。

恢复先验证清单、schema 字段、租户归属和所有原件 SHA256，然后在租户锁下创建受限原件目录并单事务导入。导入失败撤销数据库写入并删除本次创建的对象目录。数据库与文件系统没有共同的事务：进程被强制终止时可能留下未提交数据库对应的目录；运维应隔离检查、手动处理残留后重新选全新目录，不能直接启动服务。

S3 目标使用 `s3://bucket/private-restore/run-id`，禁止桶根目录。恢复需要 `GetBucketVersioning`、限定前缀的 `ListBucket`、`PutObject`、`GetObject`，不需要 `DeleteObject`。工具拒绝已启用或暂停版本化的桶，防止空的当前对象列表掩盖历史版本；没有权限核验版本状态或完整分页列表时直接失败。完整分页确认 `prefix/` 下为空，每次 PUT 保留 `IfNoneMatch='*'` 防止覆盖，随后 GET 并核对完整内容 SHA256。目标服务必须支持此条件写协议；协议替身测试不能替代目标服务验收。空前缀检查无法原子锁住其他系统，运维必须停止其他写入者并用访问策略独占本次前缀。

写 S3 前，工具在备份目录的父目录创建 `recovery-attempt-<随机ID>/manifest.json`，使用与备份相同的 0700 / Windows 受限 ACL；因此该父目录也必须可写且可信。清单只记本轮目标、对象键、SHA256 和写入状态，不含原件内容或密钥。状态为 `planned`、`write_unknown`、`written`、`verified`；请求发出前先记录未知状态，响应丢失时不能猜测未写入。顶层 `committed` 表示数据库已提交，`failed` 表示恢复发生异常；成功结果的 `recovery_manifest` 给出文件路径。日志、清单和原件一样需要受限保管。

S3 失败时数据库事务回滚，保留本轮清单与隔离前缀，**不会自动删除任何远端对象**。原因是失败或超时无法证明 PUT 是否成功，对象也可能已被其他写入者替换，贸然 DELETE 会删错对象。运维必须依据本轮清单和目标数据库实际状态人工核对，不能批量清空桶或共享前缀；重试使用另一个全新空前缀。进程强制终止、磁盘故障或数据库提交后清单写入故障可能留下 `in_progress`，此时必须同时检查数据库和对象，不能仅据清单推断提交结果。数据库与 S3 没有共同事务。

恢复保留已知远端 ID 及绑定信息以供核对，所有未终态任务都进入 `needs_reconciliation`，包括上传结果未知的 `submitting`；清除租约 token 和期限以阻断旧 Worker 回写。处理中消息改为失败，切换记录失效。所有底座实例清除 `verified` 与旧 report，历史生产验证资格不能沿用。备份中的 Worker 心跳、指标桶和告警送达状态不导入目标，防止把恢复前的新鲜心跳当作活 Worker，或沿用旧告警已送达状态。恢复后必须重新独立验证底座、权限反例、引用原件和路由；不能因为远端 ID 已知就认定底座可用，也不能直接把冻结任务改回 queued 后重传。

清单 SHA256 可发现损坏，不提供数字签名或抵抗有权限修改整个备份包的人。只恢复可信来源的备份包。模块定向测试使用合成数据、临时 SQLite 与 botocore Stubber 的真实 S3 API 请求/响应模型，另有下述 PostgreSQL 隔离测试；它们不代表目标 S3 服务、断电恢复或完整生产灾备验收通过。

运维 CLI（在仓库根目录 PowerShell，父目录先配置为可信受限目录）：

```powershell
$env:PYTHONPATH = 'backend'
backend/.venv-v3/Scripts/python.exe -m app.v3.cli --env-file v3.local backup-bundle --output <不存在的私有备份目录>
backend/.venv-v3/Scripts/python.exe -m app.v3.cli --env-file <独立恢复目标配置> restore-bundle --input <可信备份目录> --confirm-empty-target
```

恢复配置中的数据库必须是独立空数据库，`V3_TENANT_ID` 与备份一致，`V3_OBJECTS_DIR` 指向尚不存在的本地目录或专用全新空 S3 前缀，不能沿用源配置。S3 凭据使用独立的 `V3_SECRET_S3_ACCESS` / `V3_SECRET_S3_SECRET`，端点和区域沿用 `V3_S3_ENDPOINT` / `V3_S3_REGION`。CLI 先做 schema 迁移，再检查整个数据库没有任何 Tenant，之后才恢复；它不调用会预先创建原件目录的 Domain 构造函数。不要先运行会创建 Tenant 的 `migrate` 子命令，也不要启动 API 或 Worker。恢复后按上文重新核验底座和任务，不直接恢复对外访问。可复用演练步骤见 [部署验证说明](../../deploy/v3/recovery-validation.md)。

本轮另在真实 PostgreSQL 隔离测试库的全新随机 schema 做合成恢复：验证权限拒绝保留、原件内容一致、旧租约清除及 verified 撤销，测试 schema 已清理。这仍不代表 S3、平台数据或完整灾备通过。

仓库根目录运行模块定向验证：

```powershell
$env:PYTHONPATH = 'backend'
backend/.venv-v3/Scripts/python.exe -m unittest discover -s backend/tests/v3 -p 'test_recovery*.py'
```
