# 生产能力补齐验证

结论：本轮新增能力的限定验证通过；完整企业生产准入仍未完成。

## 实际实现

- Worker 删除确认轮询及回执证据；有已知远端 ID 的状态／元数据超时重试，不重复上传。
- 长问及多轮检索改写、独立语义核验、原问题保留；生成前目录／路由变化最多重检一次，权限变化不自动重试。
- 每个检索请求派发时重新鉴权，收到响应头后释放协调锁再读正文；Dify 第二次检索不能越过已提交撤权。
- 管理员聚合运行指标、告警代码和 API 数据库就绪探针。
- 单租户模型行＋原件备份、校验和空目标恢复 CLI；恢复清除旧租约、冻结任务、清空底座生产资格。
- 容量 SSE 工具及人工质量评分汇总；默认不调用服务，缺少预期有效回答校验不能通过容量门槛。

## 本轮实际验证

| 验证 | 结果 | 证据 |
| --- | --- | --- |
| 后端完整回归 | 106 项，0 失败、0 错误、0 跳过，退出 0 | `backend-hardening-2026-10-30.json` |
| PostgreSQL 恢复 | 只备份本轮唯一合成租户，恢复到随机新 schema；原件可读、拒绝权限保留、旧租约清除、资格撤销；测试 schema 已清理 | `test_postgres.py::test_recovery_bundle_real_postgres_retains_permissions_and_revokes_leases`，包含在 106 项中 |
| 两套真实平台删除 | 各创建一份全新合成文件，完成解析后 DELETE，再 GET 404；均通过 | `remote-cleanup-2026-10-30.json`；可复现入口 `tools/v3/verify_remote_cleanup.py` |
| 真实长问改写 | 427 字符改写为 40 字符；两平台受限召回、真实精排与生成均含预期 138 元及引用 | `query-rewrite-live-2026-10-30.json` |
| 真实 HTTP 全链路 | 在隔离库创建临时受限读者与会话，长问及多轮均回答现行新版 246 元；来源只含授权文档，原问保存一致，测试后恢复原授权并停用测试读者 | `chat-http-hardening-2026-10-30.json` |
| 本机新服务 | 重启 API／Worker，两个进程存活；ready 200、匿名 operations 401、管理员统计可读、两底座 verified 仍 false | `runtime-hardening-2026-10-30.json` |
| 独立审查 | 删除、运行指标、问答派发锁、恢复及 CLI 未发现剩余阻断问题；容量误把固定拒答算成功的 P2 已修正并增加回归 | 只读子代理审查；未重复运行其余全量测试 |
| 差异格式 | `git diff --check` 退出 0，仅既有 LF／CRLF 提示 | 当前工作区 |

后端回归使用原记录中的独立 `v3_live_` PostgreSQL 测试库。执行前断言私有连接配置对应 `127.0.0.1:18433/enterpriserag_v3`，然后只替换成报告记录的测试库名；未输出连接串。普通测试使用临时 SQLite 和 HTTP 替身。真实模型调用只使用隔离合成资料，不是企业业务效果评测。

本轮命令包括 `unittest.defaultTestLoader.discover('backend/tests/v3')`（设置隔离 `V3_TEST_DATABASE_URL`）、各新模块定向 unittest、`tools/v3/verify_remote_cleanup.py --run-isolated-probe` 和 `tools/v3/start_local.ps1 -Restart`。所有 Python 测试使用项目 `backend/.venv-v3/Scripts/python.exe`；启动器和 Docker 管道在沙箱内拒绝访问后，经平台批准在沙箱外执行。测试出现 Starlette/httpx 弃用提示，不影响本轮结果。

补充 HTTP 测试首次断言失败：测试沿用了旧版 138 元预期，而原试验已明确发布新版 246 元，最近测试会话实际为 completed 并回答 246 元。定位后依据原试验脚本的新版内容重新核对现行版本 SHA256，改为受限读者及 246 元预期后，两项 HTTP 验证通过。未修改业务实现或已冻结业务题库；原管理员能见同库反例属于其授权范围，不当成越权缺陷。

真实运行镜像：WeKnora `sha256:25b20959b7456f7842ef55ed4feff3694dbd0c6f8af4117c1fb3c5dec6bdf14d`；Dify 兼容构建 `sha256:5a372d832406893b0508982f1011cd1c18ca379d74730dce004391ac70d186d1`，通过只读 Docker inspect 核对。

## 限制

没有执行 30 分钟容量测试或代表性企业业务评分。未完成企业身份实际接入、扫描件／复杂表格精确定位、两套完整 G0、自动未知结果对账、物理清除证明、原件孤立回收、S3／平台完整恢复、企业告警送达及生产发布回退。未更改 production verified 状态，未提交或推送。具体差距见 `docs/v3/production-readiness.md`。
