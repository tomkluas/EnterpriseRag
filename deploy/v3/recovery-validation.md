# 单租户恢复部署演练

本操作针对可信逻辑备份包，恢复到独立空数据库与全新原件位置。它不恢复 Dify / RAGFlow 文件、向量或平台数据库，不恢复 S3 版本历史，不构成完整灾备签收。

## 离线协议回归

在仓库根目录 PowerShell 运行，不需要真实 S3 凭据或生产配置：

```powershell
$env:PYTHONPATH = 'backend'
backend/.venv-v3/Scripts/python.exe -m unittest discover -s backend/tests/v3 -p 'test_recovery*.py'
```

测试仅使用临时 SQLite、本地合成文件与 botocore Stubber。覆盖分页空前缀、第二页非空拒绝、版本化拒绝、条件写冲突、GET 损坏、S3 权限拒绝、受限清单创建失败、数据库冲突回滚和旧监控状态失效。通过说明接口分支和 S3 协议请求已验证；不说明目标对象存储服务已验证。

## 目标服务演练

1. 运维先准备独立演练数据库、可信受限备份父目录和非版本化桶内的唯一新前缀。前缀不能与源存储、其他租户或其他演练共用，所有 API / Worker / 其他写入者保持停止。
2. 建立独立配置文件，设置与备份一致的 `V3_TENANT_ID`，`V3_DATABASE_URL` 指向演练数据库，`V3_OBJECTS_DIR=s3://<演练桶>/<新随机前缀>`。配置目标端点和独立最小权限凭据。允许查询桶版本状态、完整列举该前缀、条件 PUT 和 GET；不授予 DELETE。备份源配置不得复用。
3. 在仓库根目录执行：

   ```powershell
   $env:PYTHONPATH = 'backend'
   backend/.venv-v3/Scripts/python.exe -m app.v3.cli --env-file <演练配置文件> restore-bundle --input <可信合成备份目录> --confirm-empty-target
   ```

4. 成功输出应包含正确对象数和 `recovery_manifest` 路径，清单为 `committed`、每个对象为 `verified`。从应用授权读取路径核对原件；检查旧任务为冻结/终态、无旧租约、所有底座 `verified=False`、没有旧 Worker 心跳或旧告警送达状态。
5. 用另一个独立演练数据库尝试同一非空前缀，应在 PUT 之前拒绝。使用合成专属前缀和受限测试身份验证条件写冲突、分页、GET/PUT 拒绝行为；不得在业务对象上注入故障。需要另行证明目标服务正确支持 `IfNoneMatch='*'`，否则禁止生产使用。
6. 若失败或进程中断，隔离该前缀，查看备份目录旁最新 `recovery-attempt-*` 清单，并查目标数据库实际状态。`write_unknown` 表示请求可能已经成功，`verified` 只证明当时读回匹配。工具不自动 DELETE；只在证明对象仍属于本轮后由运维单件处理，禁止按桶/共享前缀清空。重试使用另一个空前缀。
7. 底座重新验证、权限反例、路由和冻结任务对账均通过后才允许恢复服务。保留时间、对象数量、SHA256、运行身份、目标服务版本与故障演练结果，禁止在报告内放备份正文、密钥或私有清单内容。

目标服务演练需要部署方执行。仓库协议测试没有向真实 S3 写入，也没有验证断电恢复、平台索引还原或生产容量恢复时间。
