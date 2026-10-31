# EnterpriseRag V3 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 复杂授权与版本发布完成后进行限定范围的独立审阅；不要为每个小任务重复建立审批关卡。

**Goal:** 完成固定版本 WeKnora 与 Dify 的运行时采用验证，首版实现两套适配器及管理员可执行的双向切换，同时保证企业授权、版本、引用和恢复。

**Architecture:** V3 自主管理资料原件、授权、业务版本、会话和任务；WeKnora／Dify 通过两套适配器负责解析索引与受限召回。生成与统一精排留在 V3，按业务知识库路由选择活动底座。目标就绪并验证后事务切换，默认不自动故障切换。用户随后授权删除 V1/V2 实现，该指令覆盖原先的独立保留方案；旧运行数据和密钥保留。

**Tech Stack:** Python/FastAPI、Vue 3、PostgreSQL、对象存储、PostgreSQL 业务任务表、WeKnora v0.9.0 标准版与 Dify 1.18.0 知识库 API；各底座独立固定部署配置。

**Spec:** [V3 技术设计](../../v3/design.md)、[双底座与 Dify 核查](../../v3/dual-backend.md)、[WeKnora 核查](../../v3/weknora-feasibility.md)、[验证清单](../../v3/validation.md)。

## Global Constraints

- 固定 WeKnora 提交 `f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7` 和 Dify 提交 `50abea97062110044f15d0e03ccecadd9ccc87ca`；不把静态核查标作运行时通过。
- 先完成任务 1 的两套 G0。失败时停止对应底座生产绑定，不用降低授权要求换取通过。首版两套都实现，不能只预留 Dify 类名就称可切换。
- V3 使用 `/api/v3` 和独立数据库、存储、配置；不得修改 V2 用户数据或复用运行密钥。
- 每次资料召回必须有后端构造的非空文档授权范围；空范围不调用底座。
- 所有原件、授权、会话和业务版本由 V3 管理；不修改平台数据库，不 fork 平台权限逻辑。
- 本文件最初为设计阶段计划，现已进入实施。当前实际文件与验证结果以 [实施记录](../../v3/progress.md) 为准；未勾选项不能解释为已完成。
- 任务内先写关键行为测试、确认失败，再实现并运行定向验证。完成相关验证后不反复运行无关基线测试。

## Review Focus

- 同一底座库中的受限文件更相关时仍不得进入模型；任务 1、3、5 验证。
- 撤权发生在查询和生成之间，以及历史会话继续提问时不得继续派发依据；任务 3、6 验证。
- 上传超时但远端已接受、409 冲突及 Worker 旧租约晚写回不得重复发布；任务 4 验证。
- 新版处理失败、相同原件重建及发布并发不能破坏旧版服务；任务 1、4 验证。
- 多批次、大授权范围或部分失败不能静默漏资料，底座回退不能复活撤权；任务 5、8 验证。

## 文件结构

V3 后端集中在 `backend/app/v3/`；`backend/app/main.py` 仅按显式 V3 配置挂载。依赖使用 `backend/requirements.txt` 和精确锁定的 `backend/requirements-v3.lock`，开发使用独立 `.venv-v3`。前端工作台位于 `frontend/src/App.vue`，仅通过 V3 API 访问；不再保留旧版页面。

| 路径 | 责任 |
| --- | --- |
| backend/app/v3/config.py、db.py、models.py | 独立配置、数据库连接、业务模型 |
| backend/app/v3/auth/ | 企业身份映射、操作与数据授权、派发协调 |
| backend/app/v3/knowledge/contracts.py | 请求、绑定、状态、证据与能力契约 |
| backend/app/v3/knowledge/weknora.py | 受限平台调用、响应转换和错误映射 |
| backend/app/v3/knowledge/dify.py、registry.py | Dify 元数据受限检索、状态映射和按实例选择实现 |
| backend/app/v3/backends/ | 实例能力、目标同步、水位、路由及切换审计 |
| backend/app/v3/documents/ | 原件、版本、发布、撤回、索引代次 |
| backend/app/v3/jobs/ | 任务领取、租约、重试、对账和 Worker |
| backend/app/v3/chat/ | 召回编排、统一精排、生成、会话、引用 |
| backend/app/v3/api/ | 前端业务接口；不暴露底座接口代理 |
| backend/migrations/v3/ | 数据库版本迁移，单一执行入口 |
| backend/tests/v3/ | 单元、数据库集成与底座集成测试 |
| deploy/v3/ | 隔离部署、配置样例、固定版本与运行手册 |

## 任务 1：验证底座采用条件

**Files:** Create `tools/v3/weknora_probe.py`、`tools/v3/dify_probe.py`、`backend/tests/v3/integration/test_weknora_contract.py`、`test_dify_contract.py`、`deploy/v3/README.md`；结果写入 `docs/testing/v3/`，不得提交密钥或企业原文。

**Interfaces:** 消费固定版本真实 HTTP API；产出能力清单、原始状态映射、上传冲突处理规则、已通过的 G0 报告和可重复部署配置。此任务不对未验证的上游能力做空实现。

- [ ] 在隔离主机分别完成固定源码／镜像来源核对、官方初始化和受限机器凭据配置；Dify 同时固定向量存储、解析／模型插件并核对工作空间许可条件。
- [ ] 编写探针及接口契约测试：A/B 同库、C 跨空间；关键词／向量／混合路径只返回允许文档，空授权实际调用数为 0。
- [ ] 测试真实上传 200、重复 409、状态完成、分块读取、引用定位、删除与重解析；记录响应，不根据猜测固定 schema。
- [ ] 按 [G0 矩阵](../../v3/validation.md#24-阻断用例矩阵) 执行剩余版本、故障和模型探针，尤其验证相同原件新物理库建代次。
- [ ] 对权限与版本原型先用可控故障测试，再用真实 API 重复验证；模型连通性必须至少各执行一次真实 Embedding、精排与生成。
- [ ] 按验证清单 2.5 完成 Dify 元数据标识写入／读取、in 过滤、缺失／重复值、空匹配、250 字符、批次状态、204 删除和引用等级探针；验证同一原件的新代次不破坏旧索引。
- [ ] 运行 `python -m unittest discover -s backend/tests/v3/integration -p "test_*_contract.py" -v`（V3 测试环境，凭据来自环境变量）；确认两套测试实际收集，成功标准是全部阻断用例 passed，缺少配置必须明确 not_run 并使 G0 不通过。
- [ ] 输出 G0 结论。若失败，记录具体底座／适配问题，调整方案或改选底座后重做对应验证；不继续宣称已采用。

## 任务 2：业务存储与接口契约

**Files:** Create `backend/app/v3/config.py`、`db.py`、`models.py`、`knowledge/contracts.py`、`backend/migrations/v3/0001_foundation.sql`、`backend/requirements-v3.txt`、`backend/tests/v3/test_contracts.py`、`backend/tests/v3/test_tenant_storage.py`。

**Interfaces:** 输出 `AuthorizedScope`、`CollectionRequest`、`CollectionBinding`、`IngestRequest`、`Submission`、`BackendBinding`、`BackendStatus`、`RetrievalRequest`、`Evidence`、`DeleteReceipt`、`BackendCapabilities`，字段以设计第 8 节及双底座补充模型为准；输出 `KnowledgeBackend` 六个异步方法。业务数据库 session 不跨异步任务共享。

- [ ] 编写测试：相同资源 ID 在另一 tenant 查询不可见；跨企业绑定拒绝；重复幂等键只创建一条任务；原件对象键不使用上传路径；不完整证据不能作为合法引用。
- [ ] 确认这些测试在模型／约束缺失时失败。
- [ ] 实现设计第 4 节表结构、约束及显式 tenant 条件，建立独立 V3 配置和迁移入口。
- [ ] 增加 backend_instances、space_backend_routes、backend_replica_states、backend_switches；对同业务文档的两底座绑定验证实例内唯一，不能以全局平台 ID 作为唯一依据。
- [ ] 在临时 PostgreSQL 数据库执行迁移、回滚与重放；确认不会连接 V2 SQLite。
- [ ] 运行上述两个定向测试文件，检查真实数据库隔离和唯一性约束，而非仅 mock 查询。

## 任务 3：身份、数据授权与派发边界

**Files:** Create `backend/app/v3/auth/principal.py`、`policy.py`、`dispatch.py`、`backend/tests/v3/test_authorization.py`、`test_revocation_race.py`。

**Interfaces:** `resolve_scope(user_id, requested_space_ids, as_of) -> AuthorizedScope`；`authorize_resource(principal, resource, action) -> None`；`dispatch_authorized(scope, operation)` 和 `send_text_authorized(scope, payload, send)` 以相同共享／独占 advisory lock 协调边界完成复查与模型请求派发／正文写出；失败抛明确权限错误。

- [ ] 写测试覆盖默认拒绝、组授权、显式拒绝优先、停用、系统管理员不自动读业务文档、空授权调用数 0。
- [ ] 用两个并发数据库连接及两个 API 实例测试：撤权先提交则新模型请求不得发出、正文不得继续写出；派发／正文写出先发生则准确记录；仅一次读版本号或定期轮询的实现应失败。共享锁不锁住整个生成过程，撤权使用同键独占锁。
- [ ] 实现角色与资料授权、修订号和派发协调；为资料发布／撤回、路由 activate 预留同一独占协调入口，模型派发共享锁内检查 authz／catalog／route。先对接可配置的测试身份提供器；生产外部身份协议在明确企业系统后接入，不默认自建企业密码体系。
- [ ] 对图片、原文、任务和历史会话使用相同授权入口；拒绝浏览器传入平台 ID／key。
- [ ] 运行授权与并发测试；限范围独立复核“检索、模型派发、历史、原文”四条入口。

## 任务 4：可靠入库、版本发布和清理

**Files:** Create `backend/app/v3/documents/service.py`、`storage.py`、`publication.py`、`backend/app/v3/jobs/worker.py`、`repository.py`、`reconcile.py`、`backend/tests/v3/test_ingestion.py`、`test_publication.py`、`test_job_leases.py`。

**Interfaces:** `receive_document(principal, upload) -> (version_id, job_id)`；`publish_version(principal, version_id) -> Publication`；`withdraw_document(principal, document_id) -> job_id`；`claim_job(worker_id) -> Lease | None`；`complete_job(job_id, lease_token, result)`；所有变更带 tenant 和修订号。

- [ ] 写反例：上传成功后进程退出、远端接受后超时、409 指向其他权限边界、任务重复领取、旧租约写回、新版失败、撤回与发布并发。
- [ ] 确认无幂等约束／租约 token／发布事务时测试失败。
- [ ] 实现不可变原件、原件孤立清理、任务事务、续租、退避和人工对账状态；租约时长与轮询间隔配置化。
- [ ] 实现 ready 检查、当前版本和索引代次切换；已发布映射不原地 reparse；删除先撤回再清理，物理清理与业务隐藏分别记录。
- [ ] 结合任务 1 的真实重复上传与新代次规则实现对账；不虚构平台幂等支持。
- [ ] 运行三个定向测试，并在真底座执行新版失败、发布、重建和删除故障用例。

## 任务 5：知识适配器与受限检索

**Files:** Create `backend/app/v3/knowledge/weknora.py`、`dify.py`、`registry.py`、`backend/app/v3/chat/retrieval.py`、`rerank.py`、`backend/tests/v3/test_weknora_adapter.py`、`test_dify_adapter.py`、`test_retrieval_scope.py`、`test_retrieval_batches.py`。

**Interfaces:** 实现任务 2 的 `KnowledgeBackend`；`retrieve_evidence(query, scope, deadline) -> list[Evidence]`；`rank_evidence(query, evidence, deadline) -> list[Evidence]`。后两者只接受验证后的 Evidence，不返回底座资源直链。

- [ ] 写测试：空范围不发请求、路径／body 全部由 scope 构造、响应出现未知 ID 整次失败、附加图片字段不外泄、409/超时映射准确。
- [ ] 写测试：21 个文档拆分后无漏项，多库按模型分组，不混用原始分数；任一批失败或超预算明确失败；429 退避不能越过整体时限。
- [ ] 两套实现均覆盖 prepare_collection、上传、状态、召回、删除、能力查询；WeKnora 设置 `skip_context_enrichment=true`；Dify 创建并验证 v3_binding_id 元数据，不能无过滤调用；两者按绑定验证每条结果。
- [ ] Dify 适配器保存 batch_id、正确处理 204、仅压缩检索问并保留原问，将 segment.position 映射为片段顺序而非页码。平台精排关闭，混合融合配置单独核对；文件、子块和摘要按白名单处理。
- [ ] 实现批量初值 20、并发初值 4、召回预算初值 15 秒和统一精排；这些是可调参数而非已证明容量。
- [ ] 真正评分前再次检查授权；精排失败不静默按原始排序生成。
- [ ] 两套适配器运行同一契约及各自定向测试，再执行 G0 真 API 范围回归，确认禁止资料没有发送到精排端点。registry 按 backend_instance_id 选择，不能只支持默认 WeKnora。

## 任务 6：会话、引用与流式回答

**Files:** Create `backend/app/v3/chat/service.py`、`generation.py`、`history.py`、`citations.py`、`backend/app/v3/api/chat.py`、`sources.py`、`backend/tests/v3/test_chat_flow.py`、`test_citation_access.py`、`test_history_authorization.py`。

**Interfaces:** `answer(principal, conversation_id, query)` 输出状态／证据／文本／完成／错误事件；`load_authorized_history` 不返回已失去任一依据授权的回答；`open_citation` 每次核验当前权限及原件版本。

- [ ] 写测试：无证据不调用生成；引用编号不存在则拒绝完成；模型断流标失败；客户端断开取消工作；撤权历史不进入下一轮提示词。
- [ ] 实现消息持久化、证据快照、版本修订复查、输出阶段撤权检查和内部错误脱敏。
- [ ] 引用代理只使用自有原件或经 V3 验证的派生资源；缺失定位不生成猜测页码。
- [ ] 运行上述三个测试文件，并记录一次真实完整问答、一次无答案、一次撤权中断。

## 任务 7：前端与业务管理入口

**Files:** Create `backend/app/v3/api/documents.py`、`jobs.py`、`admin.py`、`backend_switches.py`、`frontend/src/api/v3.ts`、`frontend/src/views/v3/Chat.vue`、`Documents.vue`、`Administration.vue`、`BackendSwitch.vue`；Modify `backend/app/main.py`、`frontend/src/router/index.ts`（实施前核实实际文件位置）。

**Interfaces:** 实现设计第 8 节 `/api/v3` 表；前端仅使用业务编号；权限入口由后端保证，前端隐藏不替代授权。

- [ ] 实现上传接收／处理／可用区分、失败重试、版本发布、撤回、历史、引用和反馈；不显示底座凭据或内部栈。
- [ ] 实现底座选择、目标同步进度、能力差异、资格检查和执行／回退入口；服务端 activate 必须重做水位和路由修订核验，不依赖按钮是否禁用。
- [ ] 验证只读用户不能维护资料，管理员不能通过改 URL 读取未授权业务原文。
- [ ] 沿用项目现有前端测试方式，新增流程测试；在 frontend 执行 `npm run build`，并做真实浏览器登录至问答的人工验收。
- [x] 按用户后续授权删除旧实现和启动脚本，不再挂载 V2 路由；旧数据未导入或公开。

## 任务 8：运行、迁移与验收交付

**Files:** Create `deploy/v3/compose.yaml`、`.env.example`、`backup-restore.md`、`operations.md`、`backend/scripts/v3/export_v2.py`、`import_manifest.py`、`backend/tests/v3/test_migration_manifest.py`；结果写 `docs/testing/v3/`。

**Interfaces:** 迁移工具只读 V2 备份，产出带版本和 SHA-256 的清单；V3 导入默认待授权。备份清单关联全部数据库、对象版本、配置和密钥恢复方式，不包含明文密钥。

- [ ] 写测试：缺失原件、未知授权、编号冲突、重复导入和撤回记录；未知权限不能默认公开。
- [ ] 实现只读导出、预检报告、分批导入；不导入旧 Chroma 向量，不修改源数据。
- [ ] 按 [完整验收](../../v3/validation.md#3-上线前完整验收) 执行业务题库、权限、故障、性能和独立环境恢复。
- [ ] 验证两套底座更新与回退仍使用最新撤权记录；保存代码、镜像、schema、模型、配置和证据批次；任务 9 双向切换验收必须先通过。
- [ ] 输出已通过、失败、未执行和排除范围；满足全部准入条件后才安排部门试运行。本文计划交付不代表已获得生产上线批准。

## 任务 9：首版双向切换

**Files:** Create `backend/app/v3/backends/service.py`、`synchronization.py`、`switching.py`、`backend/tests/v3/test_backend_switch.py`、`test_backend_sync_watermark.py`、`backend/tests/v3/integration/test_backend_switch_roundtrip.py`。

**Interfaces:** `prepare_switch(principal, space_id, target_instance_id) -> switch_id`；`inspect_switch(principal, switch_id) -> SwitchReadiness`；`activate_switch(principal, switch_id, expected_route_revision) -> SpaceBackendRoute`。内部沿用 prepare_collection／submit 和业务任务，回退重新走同一流程。

- [ ] 写测试：目标缺副本／过滤标识／required_capabilities 则不能激活；准备期间发布、删除、撤权使旧清单失效；两实例同时激活只允许修订号匹配者提交。
- [ ] 写测试：请求固定路由快照，未生成前路由变化重检一次；activate 与模型派发使用同一协调键，切换先提交时旧证据不得继续派发生成；已输出回答不混合两个底座；新请求使用新路由；源故障不自动使用未同步目标。
- [ ] 实现清单和同步水位、短时暂停发布、变更预检、事务路由切换和审计，撤权／删除不能被暂停或回退复活。
- [ ] 检索绑定使用实例维度，旧引用依赖自有证据／原件；模拟退役源底座后依然能按当前授权打开原文。
- [ ] 运行任务内单元和数据库并发测试；在真实两底座执行 WeKnora → Dify → WeKnora，按验证清单 2.6 保存独立报告。两套 G0 或任一方向未通过则首版双底座目标未完成。

## 实施顺序与完成判断

任务 1 → 任务 2 → 任务 3 → 任务 4／5 → 任务 6 → 任务 9 → 任务 7 → 任务 8。4 与 5 在契约稳定后可分别推进，但集成必须共同验证。优先完成一个部门的端到端双底座链路，再扩大资料量和数据源。两套 G0 加双向切换全部通过，才算完成首版可切换目标。

本轮只交付核查与可实施文档，没有安装依赖、启动平台或修改业务代码。后续实施沿用用户已确认的范围，不重复询问“是否开始”；新增业务歧义或平台硬约束才集中确认。
