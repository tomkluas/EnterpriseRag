# V3 独立复查报告

后续状态：本报告保留修复前的不通过结论。用户授权修复后，两个缺陷已关闭；复现脚本已增加取消恢复路径，最新结果见 [修复验证](../fixes-2026-10-30.md)。下文命令输出为本次原始复查时的记录。

结论：**不通过**。正常文档问答链路可用，但本轮复现了两个影响故障恢复／双底座切换的缺陷；不能将“主流程通过”扩大为“稳定可用要求已达到”。完整企业生产目标也仍未完成。

复查日期：2026-10-30。仓库 `D:\GitHub\EnterpriseRag`，分支 `codex/v3-dual-backend`，HEAD `442c1d1`。范围覆盖当前工作区 V1/V2 替换为 V3 的相关已修改、删除和未跟踪文件，不只对照最后一个提交。未提交的 V3 实现是本次实际检查对象。

使用 tom-validate：本轮只验收，未修改业务实现、业务数据、运行资格或平台配置，未重启服务。新增本目录复现脚本、只读实测结果和报告；临时 SQLite 由 TemporaryDirectory 自动清理。前端构建仅更新生成产物。

## 验收表

| 需求项 | 验证方法 | 证据 | 结果 |
| --- | --- | --- | --- |
| 当前工作台、登录与访问限制 | 实际 HTTP，匿名拒绝及管理员读取 | `live-results.json`：匿名 401；me／spaces／backends／jobs 成功 | 通过 |
| 原件独立保存、可核对 | 下载合成示例原件，与版本 SHA256 比较 | `live_readonly.py`，`current-original-sha256` | 通过 |
| 真实模型配置 | 实际 Embedding；两套底座各自实际精排和带引用生成 | `live-results.json`：1024 维；两套答案均含合成事实 138 和引用 | 通过，限这些合成问答 |
| 两套受限检索适配 | 已有隔离测试资料的允许文档检索，不修改路由和权限 | 两套事实命中且不出现同库反例 9999；空范围／伪造映射等由回归覆盖 | 通过，非完整 G0 |
| 正常上传、发布、往返切换 | 核查先前真实运行证据及相关当前回归；本轮未向平台再次上传或执行切换 | `../live-pilot/*-local-pilot.json`，`test_switches.py` | 已有真实通过证据；本轮为证据核对及自动回归 |
| 草稿失败后保留现行资料且能完成切换 | 临时库、真实 Domain／Worker、解析失败注入 | `adversarial.py:36`，目标 1/2 就绪，activate 报错 | **不通过** |
| 后台任务租约失效后继续处理其他任务 | 临时库使租约过期，加速真实心跳等待 | `adversarial.py:67`，run_once 抛出 DomainError；CLI 无外层保护 | **不通过** |
| 权限、版本、历史、并发等现有回归 | 全部现有后端测试，PostgreSQL 指向隔离测试库 | 53 项，0 跳过，退出码 0 | 通过，但未覆盖本轮两个反例 |
| 前端基础及可构建 | Node 测试／Vite 构建 | 2 项，0 跳过，构建退出码 0 | 通过；不能等同完整浏览器自动化 |
| Office／PDF 解析质量与定位 | 本轮核对先前六组最小格式证据，未重新跑解析 | `../live-pilot/format-probes.json`；仅基本事实召回 | 基础证据存在；扫描件、复杂表格及精确定位未验证 |
| 企业真实效果、性能、灾备 | 对照原方案与验证清单 | `docs/v3/design.md:224`、`validation.md`、`operations.md` | 未完成 |

## 已证实问题

### P1：租约失效可导致 Worker 进程退出（阻断稳定运行验收）

- 位置：`backend/app/v3/jobs/worker.py:32`、`:40`；调用入口 `backend/app/v3/cli.py:33`。
- 触发条件：任务执行期间心跳遇到已过期／失效的租约。实际租约检查会抛出 DomainError。
- 预期：该任务停止安全写回，但异常不能结束整个后台任务循环。依据为 `docs/v3/design.md:107` 的租约恢复要求和 `validation.md` 故障一致性要求。
- 实际：run_once 的 finally 等待 heartbeat，只处理 CancelledError；已失败心跳的 DomainError 逃出。CLI 直接 await run_once 且没有异常保护，所以会结束该 Worker 进程。当前本机启动脚本只启动进程，没有自动重启监督。容器部署有重启策略也不能替代正确的异常隔离。
- 实测：独立临时数据库，远端上传边界注入租约过期，加速 15 秒心跳为 0.01 秒，执行原始心跳逻辑。run_once 确实抛出“任务租约已失效”。这是故障注入，不是声称当前运行中的 Worker 已经退出。
- 影响：本机后台处理可能停止，后续上传持续等待。没有证据表明本轮当前业务队列已经发生该故障。

### P2：失败草稿会卡住有效现行版本的底座切换（阻断切换完整验收）

- 位置：`backend/app/v3/domain.py:252`、`:278`；解析失败路径 `backend/app/v3/jobs/worker.py:98`；重试状态 `domain.py:321`。
- 触发条件：现行制度已发布且在两个底座均 ready；给同一资料上传无法解析的新版，例如损坏 DOCX。失败耗尽重试后准备切换，目标同样无法解析该草稿。
- 预期：保留旧制度正常服务，能取消／排除失败草稿或修复后重新准备，完成现行资料切换。依据为原方案的失败可处理、旧版本保留、双底座可切换要求；同步正常草稿本身没有问题。
- 实际：失败只记录在 Job，Version 仍是 received；switch_manifest 无条件包含全部未 published 版本。目标状态停在 1/2，activate 拒绝。现有 API 没有取消单个失败草稿的入口；重新准备仍包含它，重试永久损坏文件也不会解决。撤回整个资料虽能移出清单，却同时停止使用有效旧版，不能作为保持现行服务的解决办法。
- 实测：执行真实 Domain／Worker，注入平台明确解析失败，各耗尽五次重试。输出 `target_ready=1,target_total=2,draft_state=received`，现行 scope 仍有一个有效绑定，切换抛“目标索引未全部就绪”。
- 影响：某一失败更新可阻止整个业务知识库的底座切换；这是异常路径缺陷，先前正常双向切换通过并不覆盖它。

## 实际命令与结果

工作目录均为 `D:\GitHub\EnterpriseRag`。

| 实际执行 | 退出码／结果 |
| --- | --- |
| `git status --short`、`git log -3 --oneline`、`git branch --show-current`、相关源码／设计读取 | 0；确认整个未提交 V3 改动范围 |
| PowerShell 设置 PYTHONPATH 后，内联 Python 从 v3.local 读取数据库 URL、确认 localhost:18433/enterpriserag_v3，再改为先前报告中 v3_live_ 开头的隔离测试库，执行 `unittest.defaultTestLoader.discover('backend/tests/v3')` | 0；53 项通过，0 跳过。未输出密钥 |
| `npm --prefix frontend test` | 0；2 项通过，0 跳过 |
| `npm --prefix frontend run build` | 0；Vite 8.3.5 构建成功 |
| `backend/.venv-v3/Scripts/python.exe docs/testing/v3/recheck-2026-10-30/adversarial.py`，PYTHONPATH=backend | **1；2 项均 error**，分别在 Worker 心跳和 activate 位置失败；预期行为没有满足，不能算测试通过 |
| `backend/.venv-v3/Scripts/python.exe docs/testing/v3/recheck-2026-10-30/live_readonly.py`，PYTHONPATH=backend | 0；10 项只读实测通过，结果见 live-results.json |
| `docker ps --format "{{.Names}}: {{.Status}}"` | 0；独立数据库、WeKnora、Dify 所需容器均运行，主要健康检查通过 |
| `docker inspect WeKnora-app enterpriserag-v3-dify-g0-api-1 --format "{{.Name}} {{.Image}}"` | 核对固定运行镜像，详见下方；不读取容器环境密钥 |
| `git diff --check` | 0；仅 CRLF 转换提示，无差异空白错误 |

运行镜像：WeKnora `sha256:25b20959b7456f7842ef55ed4feff3694dbd0c6f8af4117c1fb3c5dec6bdf14d`；Dify 兼容镜像 `sha256:5a372d832406893b0508982f1011cd1c18ca379d74730dce004391ac70d186d1`。不是对官方未经补丁镜像的重新认证。

## 未验证项与下一步

1. 优先修复心跳异常隔离，并用上述租约反例和进程级任务继续执行测试验证；不得放宽租约令旧 Worker 可继续写回。
2. 为失败草稿提供明确状态和取消／排除流程，重新准备同步清单；回归正常草稿同步、现行版本保留、清单失效和两套底座切换。
3. 本轮未重复整套真实双向切换／重新解析文件。修复后应在新的隔离测试空间补跑真实失败草稿与往返切换，保留原业务资料。
4. 完整企业目标仍缺：代表性文件和冻结题库（方案为至少 20 份文件／200 题）、扫描件与复杂表格逐格式质量、精确定位、10 并发持续容量测试、完整原件与平台恢复、未知上传结果对账闭环、物理清理确认及索引重建代次流程。既有最小样本和 PostgreSQL 数据库恢复不能替代这些验证。
5. 当前两套底座 `usable=true, verified=false` 是明确的 development 试运行资格，生产未验收。本次没有发现新的已证实越权泄露，现有权限测试通过不构成全量安全证明。

本轮验证不存在环境阻塞；不通过结论来自上述已复现问题与完整目标的交付缺口，不是服务未启动或模型不可用。
