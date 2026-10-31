# WeKnora 对 EnterpriseRag V3 的能力核查

## 结论与证据范围

**有条件可行。** 固定版本的实际处理链路支持“V3 提交文件 → WeKnora 解析建索引 → V3 限定文档检索 → V3 自己生成回答”。企业部门权限、业务版本发布、历史会话重授权及跨平台迁移需要 V3 实现。

本次完成官方固定版本源码、路由、类型、服务实现、PostgreSQL 检索实现及相关上游测试代码的审阅；运行了 25 项源码结构检查。没有启动 WeKnora，没有执行上游 Go 测试，也没有调用真实模型验证检索效果。

环境实测：Docker CLI 存在；尝试启动已安装的 Docker Desktop 后，`docker version --format '{{.Server.Version}}'` 仍无法连接 `dockerDesktopLinuxEngine`，没有取得可用服务端版本。未进一步改动 Docker／WSL 系统配置，也未据此推断根因。本机 `go version` 为 `go1.21.5 windows/amd64`，该版本 `go.mod` 要求 `go 1.26.0`。这些限制阻止本轮本地运行时验收，不代表 WeKnora 本身不支持。

版本由官方 `refs/tags/v0.9.0` 固定为 `f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7`，不以可变化的 main 或 latest 作为验收对象。下列链接均指向该提交。

## 能力对照

| V3 需要的能力 | 源码核查结果 | 实际接入决策 |
| --- | --- | --- |
| 自己生成回答 | 检索接口独立返回正文与来源，不必启动平台聊天 | 使用独立检索，生成和会话由 V3 管理 |
| 文件入库和状态查询 | 有文件上传、文档详情、处理阶段接口 | 上传返回不等于可检索；轮询最终状态并检查分块 |
| 关键词与向量检索 | 服务同时传递文档范围给两个检索器，PostgreSQL 实现包含范围条件 | 首期只对固定的 PostgreSQL 检索后端做验收；其他后端不自动继承结论 |
| 文档级范围 | `knowledge_ids` 进入检索参数和查询条件 | V3 计算允许访问的已发布文档 ID；这不是平台自动识别部门权限 |
| 空范围安全 | 空 `knowledge_ids` 不追加文档过滤 | V3 空授权直接返回空结果，禁止向底座发送空范围 |
| 多知识库 | 服务校验每个库的访问权与 Embedding 一致性 | 分组调用；不能将不同向量模型的分数直接比较 |
| 精排 | hybrid-search 默认不精排，支持显式 `rerank`；失败可能降级为原排序 | V3 首期统一做最终精排，避免跨批次分数混用；底座精排作为可选路径 |
| 原文定位 | 返回 `source_locators`，定义页码、表格行、文本位置等；含 partial 标识 | 具备定位接口条件；不同格式定位准确度仍须实测，缺失时只显示可证明的位置 |
| 删除与重建 | 有删除、重解析、整库重建接口与清理实现 | V3 先撤回访问，再调用清理并对账；不能承诺跨存储原子删除 |
| 重启与任务恢复 | 标准版 Redis/Asynq 持久化队列、重试与巡检；巡检可将卡住任务置失败 | V3 定期对账、有限重试和人工处理；不能宣称全部中断任务自动成功 |
| 企业数据权限 | 有空间、知识库级授权和机器凭据能力，不能替代 V3 的部门／文档授权 | 自有授权、受限服务凭据、内网隔离、返回结果复核共同落实 |
| 业务版本原子发布 | 未发现可直接替代 V3 发布事务的通用契约 | 新旧版本使用独立映射，在 V3 事务切换可检索指针 |
| 更换底座 | 可通过 API 接入，但解析、分块、人工修正及定位格式仍有差异 | 保留原件和业务元数据，重建索引并重新验收 |

## 已审阅的实际调用链

### 独立检索与授权范围

`POST /api/v1/knowledge-bases/{id}/hybrid-search` → handler 参数解析 → `hybridSearchCandidates` → 知识库授权和模型一致性检查 → 向量／关键词召回 → 融合 → 返回正文和定位。

- [路由与守卫](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/router/routes_knowledge.go)：检索、上传、删除、重解析、分块读取、预览和下载。
- [检索 handler](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/handler/knowledgebase.go)：独立 JSON 响应，不调用回答生成。
- [请求和响应字段](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/types/search.go)：`knowledge_ids`、`knowledge_base_ids`、`skip_context_enrichment`、`source_locators`。
- [检索服务](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/service/knowledgebase_search.go)：授权检查、Embedding 一致性、文档 ID 向两个检索器传递。
- [PostgreSQL 实现](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/repository/retriever/postgres/repository.go)：知识库和文档条件按 AND 组合；空列表没有相应过滤。数据库近似索引内部执行策略仍可能影响召回率，这里确认的是查询结果的范围约束。

**安全边界：** 底座 API Key 是 V3 服务的身份，不是最终员工身份。即使 key 能访问整个知识库，也不能省略文档范围。范围必须由后端根据员工身份生成，不能信任浏览器传入的平台 ID。`knowledge_base_ids` 能覆盖路径中的单库范围，适配器必须自行构造该字段。

首期设置 `skip_context_enrichment=true`，关闭自动追加父块、邻块和关联块。返回仍需逐条核对文档映射、已发布版本和访问权；关联图片等附加字段不直接交给模型或浏览器。[结果组装实现](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/service/knowledgebase_search_results.go)

### 精排与引用

[精排实现](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/service/knowledgebase_search_rerank.go) 和 [诊断类型](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/types/rerank.go) 明确区分成功、无模型、模型不可用和调用失败。HTTP 成功不能代表精排成功。若后续启用底座精排，必须读取 `meta.rerank`，不能只检查 200。

[定位类型](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/types/source_locator.go) 包含位置、来源指纹和 partial 标识。字段存在不证明任何解析器都会准确填充；扫描 PDF、跨页表格和 DOCX 必须用真实原件验收。

### 文件、重复上传与版本

[文件 handler](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/handler/knowledge.go) 的文件上传成功返回 200，重复文件可能返回 409 及既有文档；不是所有操作都使用 201。V3 必须单独处理重复响应和“请求超时但远端已创建”的情况。

重新解析可能改变当前索引，不等同于“旧版继续服务、新版就绪再发布”。V3 将业务版本与底座索引代次分开记录；线上版本重建也采用新代次，不在当前服务记录上原地重解析。

### 恢复与删除

- [任务参数](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/service/knowledge_task_options.go)：文档处理使用有限重试。
- [启动处理](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/container/reset_pending_tasks.go)：通过 `REDIS_ADDR` 区分分布式队列与内存任务，标准版不在启动时随意重置其他实例仍在运行的任务。
- [任务巡检](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/service/knowledge_housekeeping.go)：检查长期卡住状态，部分场景转为失败，不是通用的自动重做。
- [删除实现](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/application/service/knowledge_delete.go)：涉及索引、分块、文件和派生资源；某些资源清理错误仅记录日志。业务不可见与物理清理完成必须区分。

### 部署要求

[官方 Compose](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/docker-compose.yml) 的默认 PostgreSQL 检索组合使用 ParadeDB（包含全文检索能力的 PostgreSQL 发行组合），不是任意普通 PostgreSQL 加向量扩展就能等价替代。Redis 配置 AOF，即把写操作记录到磁盘以便恢复；它不等于零数据丢失承诺。

[配置实现](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/config/config.go) 中 RBAC 默认开启，但可以显式关闭。V3 部署必须验证实际生效配置为 `WEKNORA_TENANT_ENABLE_RBAC=true`，不能只依赖默认值。[机器凭据](https://github.com/Tencent/WeKnora/blob/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7/internal/types/tenant_api_key.go) 支持知识库白名单及 `retrieve`、`ingest` 等能力；这仍不是文档级员工授权。

## 上游测试代码的使用边界

已审阅 `knowledgebase_hybrid_search_test.go`、`knowledge_api_key_scope_test.go`、`knowledgebase_search_rerank_test.go`、`knowledge_rebuild_index_test.go` 等上游用例，作为接口及异常行为的辅助依据。**本机未运行这些测试，不能表述为上游测试通过。** V3 真实验收步骤见 [validation.md](validation.md)。

## 采用条件

允许据此开展适配器和隔离环境验证设计。生产采用必须先通过验证清单中的 G0：真实 API 授权范围、空范围、多知识库伪造、版本发布、撤权并发、删除、重启恢复和原文定位。任何跨权限数据进入精排或生成模型，均为阻断项。

## 重复核查

在可联网环境获取官方固定提交源码，检查 `git rev-parse HEAD` 等于上述 SHA，再执行：

```powershell
node docs/v3/verify-weknora-source.mjs --source-dir <源码目录> --output <检查结果.json>
```

预期输出 `checked=25, found=25`，退出码 0。检查结果记录引用位置和文件 SHA-256；它检查源码结构，没有自动证明调用链正确或运行时安全。升级版本后需更新审阅证据和完整集成验证，不能只修改脚本中的 SHA。
