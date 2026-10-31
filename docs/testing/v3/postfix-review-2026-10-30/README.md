# V3 修复后独立验收

结论：**通过，限定于本次两项缺陷修复和本机试运行范围**。没有发现该范围内新的已证实阻断问题。最初约定的完整企业生产目标仍未完成，不能把本次通过解释为生产上线批准。

日期：2026-10-30。仓库 `D:\GitHub\EnterpriseRag`，分支 `codex/v3-dual-backend`，HEAD `442c1d1`。V3 尚处于未提交工作区，本轮检查包含相关已修改、未跟踪实现、测试、API／前端入口和之前的验收证据，不只看最后提交。按 tom-validate 执行，本轮没有修改业务实现、重启、部署、修改运行资格或业务资料。

## 验收表

| 需求项 | 验证方法 | 证据 | 结果 |
| --- | --- | --- | --- |
| 租约失效不终止整个 Worker | 原反例、现有回归；补测三次自然领取，不手工改首任务状态 | `test_worker.py:27`；本目录 `probes.py:17` | 通过；首任务进入 needs_reconciliation，下一任务完成，上传各一次 |
| 保留原件与有效旧版，取消失败草稿 | 通过实际 FastAPI 入口完成列表 → 取消 → 发布拒绝 → 过期切换拒绝 → 重新准备 → 切换 → 原文下载 | 本目录 `probes.py:34` | 通过；只剩当前版本，目标 1/1 就绪，原件内容一致 |
| 取消的权限、幂等、版本归属与状态边界 | 现有 API／Domain 回归及调用链检查 | `test_api.py:12`、`test_switches.py:7`、`:31` | 通过；已发布、正在运行和结果未知的任务不能直接取消 |
| 与任务领取并发 | 真实 PostgreSQL 行锁测试，确认等待领取提交后拒绝取消 | `test_postgres.py:24` | 通过 |
| 正常草稿、版本、授权、历史与切换不退化 | 全部现有后端回归，指定隔离 PostgreSQL 测试库 | 59 项，0 跳过，退出 0 | 通过，范围以测试实际断言为限 |
| 当前服务加载了修复 | 实际 OpenAPI、对不存在文档的取消请求、主页资源核对 | 取消 POST 存在；不存在资源返回 404；主页提供 `index-zUQJaF5_.js` | 通过；没有创建或取消真实业务版本 |
| 当前两套底座及模型 | 10 项只读 HTTP／模型实测，已有合成测试资料 | `../recheck-2026-10-30/live-results.json` | 通过；真实检索、精排和引用回答，Embedding 1024 维，原件 SHA256 相符 |
| 前端可构建及流式基础逻辑 | Node 测试、Vite 构建 | 2 项通过，构建成功 | 通过；不等同全部浏览器交互验收 |
| 真实取消恢复后双向切换 | 核查上轮修复执行时的真实隔离业务库证据与脚本；本轮未再改运行路由 | `../fixes-2026-10-30.md`、`tools/v3/enable_local_pilot.py` 及最新两份 local-pilot 报告 | 已有真实通过证据，本轮未重复整个过程 |
| 完整企业生产目标 | 对照 design／validation／operations 的未完成项 | `docs/v3/design.md:224`、`docs/v3/operations.md` | 未完成，不在本次修复通过范围内 |

## 已证实问题

**无新的已证实阻断问题。** 原两个缺陷的回归及新增补充检查均通过。

新增探针首次失败是测试夹具修订号写错：复用了直接设置 active_backend 的夹具，却假设 route_revision=1；实际为 0。错误修订号返回 409 符合接口契约。探针改为从切换状态读取实际修订号后通过；未改业务代码或放宽预期行为。两项原反例的预期保护仍保留。

## 实际命令与结果

工作目录：`D:\GitHub\EnterpriseRag`。

| 命令／执行方式 | 退出码与结果 |
| --- | --- |
| `git branch --show-current`、`git status --short`、`git log -1 --oneline`，读取相关源码与既有报告 | 0；确认工作区范围及入口接入 |
| PowerShell 内联 Python，读取私有 v3.local 并验证 localhost:18433/enterpriserag_v3，再仅将数据库名替换为先前报告的 v3_live_ 隔离库；`unittest.defaultTestLoader.discover('backend/tests/v3')` | 0；59 项，0 跳过，全部通过 |
| `npm --prefix frontend test` | 0；2 项通过，0 跳过 |
| `npm --prefix frontend run build` | 0；Vite 8.3.5 构建成功 |
| `backend/.venv-v3/Scripts/python.exe docs/testing/v3/postfix-review-2026-10-30/probes.py`，PYTHONPATH=backend;backend/tests/v3 | 首次 1，因探针错误修订号；修正探针后 0，两项通过 |
| `backend/.venv-v3/Scripts/python.exe docs/testing/v3/recheck-2026-10-30/adversarial.py` | 0，两项原反例／恢复路径通过 |
| `backend/.venv-v3/Scripts/python.exe docs/testing/v3/recheck-2026-10-30/live_readonly.py` | 0，10 项当前服务／真实底座及模型检查通过 |
| 内联 httpx 核查当前 OpenAPI、不存在资源取消及前端资源 | 0，接口存在、404 拒绝、新资源生效 |
| `git diff --check` | 0，仅 LF／CRLF 转换提示 |

新增本目录探针使用唯一 TemporaryDirectory SQLite 数据库并自动清理；PostgreSQL 测试仅写入已有隔离测试库的唯一测试租户。没有使用真实业务资料向外部模型测试；真实模型调用使用已有合成样本。当前只读实测刷新了其 JSON 结果，不覆盖原历史“不通过”的文字报告。

## 未验证项、限制与下一步

- 本轮未重新执行真实平台上传、重启故障注入、完整双向切换或全部浏览器交互；这些不由只读模型测试替代。真实往返依据上轮记录，当前取消恢复 API 在独立临时数据库实测。
- 租约失效测试包含加速心跳和外部上传边界替身。失效任务可能进入待人工对账，正确性要求是禁止盲目重复上传；本次并未交付自动对账工具。
- 取消失败草稿保留其原件及远端派生记录，不代表已物理删除。后台心跳失效被隔离不代表任意数据库不可用、系统进程被杀等情况都有自动恢复保证。
- 完整生产目标仍缺代表性业务题库与效果评估、扫描件／复杂表格和精确定位、容量压测、端到端恢复、对账与物理清理闭环。需要按既定 validation.md 分项完成后，再做生产准入。
- 无环境阻塞，无需新增配置；继续本机试用可直接使用当前工作台。
