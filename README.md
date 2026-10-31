# EnterpriseRag V3

> 版本导航：当前为 **V3（企业集中部署 / 双知识底座）** · [V1（Docker / 本地 BGE）](https://github.com/tomkluas/EnterpriseRag/blob/V1/README.md) · [V2（Windows 单机 / 在线 Embedding）](https://github.com/tomkluas/EnterpriseRag/blob/V2/README.md)

企业文档知识问答系统：Vue 3、FastAPI、独立 PostgreSQL、自有原件存储，以及 WeKnora／Dify 两套知识服务适配器。原始资料、权限、会话、引用和版本由 V3 管理，知识底座按业务知识库选择。

## 当前状态与验证边界

**V3 已具备本机试运行能力，完整企业生产验收仍未完成。** 以下是已有报告的结果，本次 README 整理没有重新执行测试：

- **真实服务与失败恢复**：2026-10-31 网络恢复后，真实 Embedding（将文本编码成检索用的向量）和模型回答可用；WeKnora、Dify 的上传丢回执探针均通过自动核对恢复，最终索引达到 ready。ready 仅表示索引就绪，资料仍需发布后才能用于正式问答。
- **限定场景容量**：WeKnora 作为活动底座，在 1 份文档、1 个活动文本块、10 并发持续 30 分钟的场景下，6241 次有效回答全部成功；首段响应 P95 为 3.453 秒，即 95% 的请求在该时间内收到首段。该结果不代表 Dify 持续容量、大规模资料检索或企业业务质量验收。
- **文档范围**：TXT、Markdown、基础 DOCX／XLSX 和文字 PDF 已有最小解析证据；扫描件识别、复杂表格、准确页码和单元格定位未验收。
- **上线前待完成**：实际企业身份接入、代表性业务资料及题库评分、目标规模、多实例持续运行、企业对象存储安全删除及完整灾备恢复等。详见 [生产就绪清单](docs/v3/production-readiness.md)。

最新证据见 [网络恢复后复验](docs/testing/v3/network-restored-2026-10-31.md)，此前结果见 [前轮准入记录](docs/testing/v3/production-completion-2026-10-31.md) 和 [历史试运行报告](docs/testing/v3/live-pilot/README.md)。历史失败报告保留，后续通过只覆盖对应的复验范围。

## V1、V2 与 V3 的对比及选型

V1 侧重本地检索组件的可控部署，V2 侧重降低单机安装门槛，V3 则面向企业集中部署中的授权、资料生命周期和故障恢复。V3 的“知识底座”指负责文档解析、索引和检索的服务；V3 自己管理业务资料、权限和版本，通过适配器调用 WeKnora 或 Dify。

| 比较维度 | V1 | V2 | 当前 V3 |
| --- | --- | --- | --- |
| 主要定位 | 由维护者管理的本地知识问答站点 | Windows 单机试用及轻量交付 | 企业集中部署的文档知识问答系统 |
| 部署方式 | Docker Compose、Nginx、独立 Qdrant、本地 BGE | Windows exe、嵌入式 ChromaDB、在线 API | API、独立后台任务进程、PostgreSQL、原件存储及外部知识底座 |
| 编码与检索 | 本地 BGE 编码、Qdrant 检索及 BGE 精排 | 在线 Embedding、Chroma 检索，按原始分数截取候选 | 底座完成索引与受限检索，V3 统一精排并生成带引用回答 |
| 身份与资料权限 | admin / user，知识库数据权限未完整落实 | admin / user，知识库接口主要按登录态开放 | 企业身份令牌校验、租户／成员／资料授权、原文与历史重新鉴权；实际企业身份接入待验收 |
| 原件与版本 | 上传原件及文档状态，无完整发布版本流程 | 本地文件及文档状态，无完整发布版本流程 | 不可变原件、资料版本、发布／撤回、引用与业务版本关联 |
| 会话 | 浏览器内存，无持久化或多轮上下文 | 浏览器内存，无持久化或多轮上下文 | 会话持久化、多轮检索改写，历史及引用重新检查权限 |
| 后台处理与恢复 | 进程内任务，重启后不自动恢复 | 进程内任务，重启后不自动恢复 | PostgreSQL 持久任务、租约与重试；未知远端结果先核对，无法证明归属时人工处理 |
| 知识底座切换 | 无双底座切换能力 | 无双底座切换能力 | 按知识库准备目标索引，验证就绪后切换 WeKnora／Dify；不是自动故障切换 |
| 问答范围 | 文档、业务数据库、联网搜索 | 文档、业务数据库、联网搜索 | 首期专注文档问答，业务数据库问答和联网搜索尚未纳入 |
| 维护成本 | 维护容器、本地模型和数据卷 | 本机应用及在线服务配置，安装门槛低 | 维护多项服务、企业身份、授权、任务和备份；运维要求更高 |

### 如何选择

- **单机体验、演示、缺少专职运维**：优先考虑 V2，但要接受在线编码的数据外发、网络与额度依赖，并验证真实知识库链路。
- **希望本地掌握编码与精排、沿用旧版多源问答**：考虑 V1；上线前仍需补齐细粒度权限、任务恢复及容量验证。
- **需要部门／资料授权、版本发布、历史留存与失败恢复**：V3 的建设方向更匹配。正式上线仍须完成上述企业环境验收，不能只凭已有功能或单文档压测批准生产。

三版都不能仅凭部署在内网就认定数据不会外发。V1 的远程回答会发送选中的片段，V2 的在线编码还会发送文档分块和查询；V3 的资料会进入所选知识底座，编码、精排和生成的数据去向取决于底座与模型配置。使用敏感资料前，应核对实际服务地址、凭据范围及外发内容。三版没有统一数据集上的质量或总成本对比，版本号不能用来证明效果更好或成本更低。

### 从旧版迁移

当前 V3 工作区已移除 V1/V2 旧源码、API 和打包启动脚本，旧实现可通过上方分支链接或 Git 历史查看。V3 不自动导入旧 SQLite、Qdrant／Chroma 索引、运行配置或密钥，也不自动删除或公开旧运行数据。

切换前先备份旧版原件、数据库及配套配置，再为 V3 准备独立 PostgreSQL、原件存储、身份和模型配置。资料重新导入时须重新建立授权、版本和底座绑定；不要将旧 `data/` 或 `.env` 直接当作 V3 配置使用。具体步骤见 [本机配置说明](docs/v3/local-setup.md) 和 [方案入口](docs/v3/README.md)。

## 技术组成

| 组件 | 当前实现与职责 |
| --- | --- |
| 浏览器工作台 | Vue 3、Vite；知识资料、授权、任务、会话、引用与底座管理 |
| 服务入口 | FastAPI、Uvicorn；业务接口使用 `/api/v3`，构建后的前端由同一服务托管 |
| 企业身份 | PyJWT 校验 RS256 签名令牌，固定身份发行方和接收方；本地用户状态、角色和资料授权由 V3 决定 |
| 业务数据库 | SQLAlchemy、独立 PostgreSQL；保存权限、资料版本、底座映射、任务、会话、审计及运行状态 |
| 原件存储 | `OriginalStore`；开发可用独立本机目录，正式环境使用带独立前缀的 S3 兼容对象存储（按对象键保存文件的服务） |
| 后台处理 | 独立 Worker 进程；从 PostgreSQL 领取任务，以租约和续约防止过期处理者覆盖结果 |
| 知识服务 | WeKnora v0.9.0／Dify 1.18.0 适配器；负责提交处理副本、检查索引、受限检索和远端删除 |
| 模型调用 | `chat/models.py` 通过 HTTP 调用配置的改写、精排和生成模型；精排是对候选资料重新判断相关性 |
| 流式回传 | Fetch + SSE，即服务器持续推送状态、来源和回答片段；前端检查完成或错误事件 |
| 运维与部署 | PostgreSQL 持久指标与告警状态、命令行维护工具、Docker Compose；数据库迁移单独执行 |

依赖版本以 [后端锁定文件](backend/requirements-v3.lock) 和 [前端锁定文件](frontend/package-lock.json) 为准。当前 V3 不沿用 V1 的 LangGraph 编排，也不在应用进程内直接管理 V1/V2 的 Qdrant／Chroma 索引；底座自身的数据库、解析服务和检索存储由各平台独立维护。

## 整体技术路线

整体流程是“浏览器 → V3 业务与权限层 → 所选知识底座 → 模型服务”。V3 保存业务事实，知识底座保存用于解析与检索的处理副本。一个业务知识库选择一个活动底座；两套适配器并不表示每次提问同时查询两套平台。

```mermaid
flowchart TD
    Browser[浏览器：Vue 工作台] -->|页面、业务请求、回答流| API[FastAPI V3 API]
    Identity[企业身份服务：签名公钥] -->|校验身份令牌| API
    API --> Domain[身份、资料授权、版本、会话与引用]
    Domain --> DB[(独立 PostgreSQL：业务记录与任务)]
    Domain --> Originals[自有原件存储：本机目录或 S3]
    DB -->|领取持久任务、租约与续约| Worker[独立 V3 Worker]
    Worker -->|核验后读取原件| Originals
    Worker -->|提交、轮询、对账、清理| Adapters[知识服务适配器]
    Domain --> Chat[授权范围 → 受限检索 → 证据核对]
    Chat --> Adapters
    Adapters --> WK[WeKnora：独立部署与索引]
    Adapters --> Dify[Dify 知识服务：独立部署与索引]
    Chat --> Models[统一问题改写、精排与生成模型]
    Chat -->|保存回答、来源、依赖与耗时| DB
```

### 分层职责与信任边界

- **浏览器只提交业务选择**：例如知识库编号和问题。平台知识库 ID、文档过滤条件及机器密钥由后端维护，不交给浏览器自行构造。
- **V3 决定哪些资料可以用**：先根据用户、资料授权、发布版本和活动底座生成检索范围；召回结果还要核对业务映射和当前权限。空授权范围不发送全库查询。
- **知识底座提供处理与召回**：Dify 在这里作为知识服务使用，不以其工作流或聊天应用代替 V3 的授权、会话和回答流程。平台密钥按登记的凭据引用从服务器环境读取。
- **任务与请求分开运行**：API 接收操作并返回任务状态，Worker 执行较长的同步与清理。API 重启不等于任务记录消失，但恢复仍取决于租约、远端状态与对账结果。
- **部署边界明确**：[Compose](deploy/v3/compose.yaml) 只包含 V3 API、Worker、数据库及按需运行的迁移服务；企业 HTTPS 网关、身份服务、S3、WeKnora／Dify 及模型服务须另行准备。API 和 Worker 必须访问同一套业务库与原件。

## 部署与持久化边界

| 数据 | 权威位置与用途 | 备份／恢复要求 |
| --- | --- | --- |
| 身份映射与授权 | V3 PostgreSQL；用户状态、角色、组及资料访问策略 | 随业务库恢复，不能从平台索引反推权限 |
| 资料与业务版本 | V3 PostgreSQL；原件键、SHA-256 内容校验值、当前版本、发布状态 | 与对应原件和底座绑定保持一致 |
| 原始文件 | `V3_OBJECTS_DIR` 指定的独立目录或 `s3://` 位置；原件不可覆盖 | 备份原件及对象版本，读取时核对内容校验值 |
| 平台处理副本与索引 | 所选 WeKnora／Dify 的独立存储 | 保存平台部署和模型配置；可重建不等于已有自动完整恢复 |
| 任务与底座切换 | V3 PostgreSQL；任务租约、远端回执、副本状态及切换清单 | 恢复后先核对未完成任务和远端结果，避免重复提交 |
| 会话、回答和证据 | V3 PostgreSQL；回答正文、证据快照及资料依赖 | 历史读取仍重新授权，备份本身也需受限访问 |
| 配置与密钥 | 独立运行配置及 `V3_SECRET_` 环境变量；页面保存密钥引用 | 与数据库分开安全保管，不提交 Git 或测试报告 |
| 审计与运行状态 | V3 PostgreSQL；操作审计、Worker 心跳、阶段耗时及告警状态 | 纳入业务库备份，企业通知渠道须另行配置与演练 |

原件、业务库和平台索引之间没有跨服务事务。V3 用持久任务、不可变原件、状态核对及对账处理跨服务失败，不能把上传响应成功当成索引就绪。容器数据库数据在 `postgres-v3` 卷中；API／Worker 镜像本身不包含业务备份。

已有单租户业务库与原件组合恢复验证，不等于平台数据、密钥及完整企业灾备均已验收。备份应覆盖数据库、原件、底座部署与索引、模型配置和独立密钥；恢复到隔离目标后核对权限、原件校验值、版本指针及任务状态，再开放访问。详见 [运维说明](docs/v3/operations.md) 和 [恢复验证说明](deploy/v3/recovery-validation.md)。

## 主要数据流

### 1. 身份识别与资料授权

1. 正式环境接收企业签发的 RS256 JWT，即带签名的身份令牌；服务端核对签名、有效期、身份发行方和接收方。
2. V3 查询本地用户是否启用，以及角色、组和资料授权。身份服务证明“是谁”，不替代 V3 判断“可以读哪些资料”。开发令牌只适用于显式开启的本地开发模式。
3. 提问时只选择有权读取、已发布且有可用索引的版本。模型调用前和正文输出时再次检查权限；历史消息及原件下载也重新检查。
4. 撤权后阻止新的资料派发、正文写出和后续访问。已经交给外部服务或已经写入网络缓冲区的内容无法追回。

实现入口：[身份验证](backend/app/v3/auth.py)、[业务授权与版本](backend/app/v3/domain.py)、[数据库协调](backend/app/v3/db.py)。

### 2. 文件上传、索引就绪与发布

```mermaid
sequenceDiagram
    participant UI as 工作台
    participant API as V3 API
    participant Store as 自有原件存储
    participant DB as PostgreSQL
    participant W as Worker
    participant K as 所选知识底座
    UI->>API: 上传文件、业务知识库、可选原文档编号
    API->>Store: 保存不可变原件
    API->>DB: 创建资料版本与持久任务
    API-->>UI: HTTP 202：已接收，等待处理
    W->>DB: 领取任务并维护租约
    W->>Store: 读取原件并核对内容校验值
    W->>K: 提交处理副本
    W->>K: 轮询状态、核对分块与归属
    W->>DB: 保存远端绑定及 ready 状态
    UI->>API: 管理员发布已就绪版本
    API->>DB: 事务更新当前版本和目录修订号
```

- 上传接口为 `POST /api/v3/documents`，使用 multipart 表单，代码接收上限为 50 MiB；文件格式还受当前底座的已验证能力限制。
- ready 表示处理副本已通过就绪检查，**不等于已发布**。发布后才成为正常问答可用的版本，上传新版本不会直接覆盖正在使用的旧版。
- 远端提交超时不代表上传失败。Worker 先根据已保存的绑定证据寻找唯一候选、回读原件并核对内容；无法证明归属时保留待对账状态，不盲目重传。
- 明确失败的未发布草稿可通过对应入口取消；正在处理或远端结果未知的任务不能随意清除状态重试。

实现入口：[上传与发布 API](backend/app/v3/api.py)、[任务处理](backend/app/v3/jobs/worker.py)、[原件存储](backend/app/v3/storage.py)。

### 3. 授权检索、多轮问答与引用

```mermaid
flowchart TD
    Question[当前问题和本人会话] --> Scope[读取有权使用的已发布版本及活动路由]
    Scope --> Rewrite[按需结合历史改写检索问，并核对语义]
    Rewrite --> Retrieve[按授权绑定分批调用所选底座]
    Retrieve --> Check[核对证据归属、版本与权限]
    Check --> Rank[使用原问题统一精排]
    Rank --> Generate[证据与已授权历史 → 生成模型]
    Generate --> Stream[逐段复查权限并输出来源和正文]
    Stream --> Save[核对引用编号，保存完成状态]
```

1. 聊天入口为 `POST /api/v3/conversations/{conversation_id}/messages/stream`，JSON 请求体包含 `space_id` 和 `query`。会话仅本人可访问。
2. 服务端保存本次消息，读取可用历史；长问题或多轮提问按需生成短检索问，并核对改写语义。精排和回答保留原始问题。
3. 按底座实例、物理库和授权绑定分批召回，返回后核对证据归属；部分批次失败不会冒充完整结果。没有证据时明确回答“根据现有资料无法回答此问题”，不调用模型补常识。
4. 统一精排后生成回答。若生成前资料目录或底座路由变化，最多重新检索一次；再次变化则明确失败。授权变化不会按这一方式静默放行。
5. 输出 `[1]` 等证据编号，保存证据快照和资料依赖。当前只能检查引用编号是否属于本次证据，不能据此证明每句结论都正确；仍需业务题库和人工评分。
6. 正常结束写入 completed；调用失败、取消或异常断流时消息保留失败状态。查看历史时，依赖资料已撤回或不再授权的回答会隐藏。

实现入口：[问答流程](backend/app/v3/chat/service.py)、[模型调用](backend/app/v3/chat/models.py)、[知识适配器](backend/app/v3/knowledge/)。

### 4. 底座准备、同步与切换

管理员先在平台准备物理知识库和受限凭据，在 V3 登记实例并完成对应环境的运行验证。选择目标底座后，Worker 为所需资料补齐处理副本，形成切换清单；激活时再次检查副本就绪、验证资格、目录状态和预期路由修订号，满足条件才事务切换。

准备期间资料变化可能使清单失效，需重新准备。切换不改写自有原件、业务版本和历史会话，也不是源平台故障后自动使用另一平台。未经同步或验证的目标不会作为静默回退。接口见 [V3 API](backend/app/v3/api.py)，规则见 [双底座说明](docs/v3/dual-backend.md)。

### 5. 撤回、删除与恢复

撤回先改变本地可见性和目录状态，使资料停止用于新检索和后续访问，再由 Worker 请求远端清理。远端删除已受理、文档详情 API 返回不存在、平台文件／向量／备份物理清除是不同结果；目前的 API 不可见确认不能代表所有介质均已抹除。

原件回收采用独立维护流程；对象存储不支持安全条件删除时拒绝执行。原件、历史证据及备份保留期限需按企业策略处理。运维步骤见 [故障处理](docs/v3/operations.md) 和 [原件回收说明](docs/v3/original-gc.md)。

## HTTP 接口与流式返回

| 场景 | 当前接口与约定 |
| --- | --- |
| 当前用户、可见知识库 | `GET /api/v3/me`、`GET /api/v3/spaces` |
| 资料上传与授权 | `POST /api/v3/documents` 为 multipart；`POST /api/v3/documents/{id}/grants` 为 JSON |
| 发布、撤回与原件 | 发布使用版本级 POST；撤回使用 `DELETE /api/v3/documents/{id}`；原件由 `/source` 经授权代理返回 |
| 会话与问答 | 创建本人会话，再 POST JSON 到消息流接口；历史由服务端持久保存 |
| 底座切换 | 创建准备任务、查询状态，再提交带 `expected_route_revision` 的激活请求 |
| 任务与运行状态 | `/api/v3/jobs`、`/api/v3/operations`，按服务端角色与权限限制 |
| 健康检查 | `/health` 返回版本与未扩大生产验证的标记；`/health/ready` 仅检查 API 的数据库连通性 |

SSE 的数据帧使用 `data:` 行承载 JSON，事件名称在 JSON 的 `event` 字段中；并非单独的 SSE `event:` 行。例如：

```text
data: {"event":"status","data":"正在检索授权资料","message_id":"..."}

data: {"event":"sources","data":[]}

data: {"event":"token","data":"根据资料[1]，报销标准为……"}

data: {"event":"done","data":{"message_id":"..."}}

```

这里仅展示事件形状，真实 `sources` 在有证据时携带来源对象。正常流包含 `status`、按需出现的 `sources`、若干 `token` 和终止 `done`；失败以 `error` 终止。等待期间约每 5 秒发送注释心跳，接口整体超时为 180 秒。HTTP 200 仅表示流已建立，仍须检查最终事件；前端遇到没有终止事件的断流会报错。反向代理须保留流式传输并配置适当超时。

## 项目目录

```text
EnterpriseRag/
├── backend/
│   ├── app/
│   │   ├── main.py                 # V3 启动、生产配置预检、静态页面入口
│   │   └── v3/
│   │       ├── api.py / auth.py     # 业务接口与身份验证
│   │       ├── domain.py / models.py / db.py  # 授权、版本、业务模型与事务
│   │       ├── storage.py          # 不可变原件访问
│   │       ├── jobs/               # 独立 Worker、入库、对账、清理
│   │       ├── knowledge/          # WeKnora / Dify 契约、适配器和受限传输
│   │       ├── chat/               # 检索、模型调用、流式回答
│   │       ├── recovery.py / orphan_gc.py    # 恢复与原件回收
│   │       ├── operations.py / monitoring.py # 运行状态与告警
│   │       └── cli.py              # 迁移、Worker、签收及维护命令
│   ├── tests/v3/                  # V3 自动化验证
│   └── requirements-v3.lock       # 锁定的后端依赖
├── frontend/src/                 # Vue 工作台、API 与流式读取
├── deploy/v3/                    # Dockerfile、Compose、配置模板与恢复说明
├── tools/v3/                     # 本机启动及隔离验收工具
├── docs/v3/                      # 设计、配置、运维与生产就绪说明
├── docs/testing/v3/              # 历史与当前验证证据
├── v3.local                      # 本机私有配置，不提交 Git
└── README.md
```

## 本机开发

当前机器的模型、两个底座和默认“V3 企业知识库”已配置，无需重新登记。API 与 Worker 一起启动／重载配置：`& .\tools\v3\start_local.ps1 -Restart`。该脚本不会启动已停止的 Docker 依赖；Docker Desktop、独立 PostgreSQL 和所选底座须处于运行状态。

需要 Python 3.11、Node.js 22.12+ 和独立 PostgreSQL。不要使用旧 `data/`、旧 `.env` 或旧 SQLite。本机独立配置填写方法见 [配置说明](docs/v3/local-setup.md)。

```powershell
uv venv --python 3.11 backend/.venv-v3
uv pip install --python backend/.venv-v3/Scripts/python.exe -r backend/requirements-v3.lock
npm --prefix frontend ci
npm --prefix frontend run build
```

创建 `v3.local`（已被 Git 忽略），填写独立数据库，并生成至少 32 字符的随机开发令牌：

```dotenv
V3_MODE=development
V3_DATABASE_URL=postgresql+psycopg2://v3:your_password@127.0.0.1:18433/enterpriserag_v3
V3_TENANT_ID=enterprise
V3_BOOTSTRAP_USER=admin
V3_BOOTSTRAP_TOKEN=replace_with_at_least_32_random_characters
V3_OBJECTS_DIR=D:/your-private-v3-data/objects
```

```powershell
$env:PYTHONPATH = 'backend'
backend/.venv-v3/Scripts/python.exe -m app.v3.cli --env-file v3.local migrate
$env:V3_ENV_FILE = (Resolve-Path v3.local).Path
backend/.venv-v3/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 18000
```

打开 [本机 V3 工作台](http://127.0.0.1:18000)，使用开发令牌登录。在另一终端运行：

```powershell
$env:PYTHONPATH = 'backend'
backend/.venv-v3/Scripts/python.exe -m app.v3.cli --env-file v3.local worker
```

正式环境使用企业身份配置和带独立前缀的 S3 原件存储，禁止开发启动令牌。模型与平台密钥通过 `V3_SECRET_` 环境变量配置，页面只保存密钥引用。生产 API 启动前会执行本地配置预检，缺项停止启动；字段要求和修复位置见 [生产配置说明](docs/v3/production-configuration.md)。预检通过不代表企业生产验收通过。

## 知识服务启用

1. 独立部署并固定 WeKnora v0.9.0／Dify 1.18.0 的镜像及配置，不使用旧数据卷。
2. 管理员先在平台创建知识库并配置 Embedding；Dify 另建字符串元数据字段 `v3_binding_id`。为该知识库配置受限密钥，在 V3 登记平台知识库 ID 和凭据引用。V3 不让机器密钥自行扩大权限。
3. 按 [验证清单](docs/v3/validation.md) 对具体实例执行 G0（启用前的运行验证，检查权限隔离、处理与检索等），由负责人签收运行报告；源码检查 JSON 不可用于启用生产。
4. 执行 `python -m app.v3.cli --env-file v3.local accept-validation --instance <实例ID> --report <已签收报告>`。这是运维签收，不是上传任意 JSON 就能证明安全。
5. 在“知识底座”选择业务知识库，准备目标索引、检查状态、执行切换。空知识库也要先启用已验证底座。
6. 上传资料，由 Worker 处理至 ready 后手动发布，再提问。能力不足、资料不齐或未通过验证时不能强行切换。

完整设计见 [方案入口](docs/v3/README.md)。两套 G0 和真实往返切换均通过后，才能宣布首版双底座目标完成。

## 验证与验收

```powershell
$env:PYTHONPATH = 'backend'
backend/.venv-v3/Scripts/python.exe -m unittest discover -s backend/tests/v3 -v
npm --prefix frontend test
npm --prefix frontend run build
```

真实 PostgreSQL 并发验证需要 `V3_TEST_DATABASE_URL` 指向独立测试库。未设置时该项跳过，不能报告为数据库验证通过。

## 容器部署

复制 `deploy/v3/.env.example` 为 `deploy/v3/.env`，填写身份、数据库、对象存储、模型和服务凭据，然后在仓库根目录执行：

```powershell
docker compose -f deploy/v3/compose.yaml build
docker compose -f deploy/v3/compose.yaml up -d database
docker compose -f deploy/v3/compose.yaml --profile migration run --rm migrate
docker compose -f deploy/v3/compose.yaml up -d api worker
```

默认仅绑定本机 18000，企业网关另行提供 HTTPS。生产发布前必须完成权限并发、恢复、双向切换、模型质量与目标容量验收。

## 日常使用与排障

统一排障入口：[V3 运维与异常处理手册](docs/v3/operations.md)。按故障现象查找启动、身份、任务、上传对账、问答、存储、切换、删除和告警处理；每项包含检查方法、处理步骤和恢复标准，并集中说明备份恢复与升级回退。

1. 按上述开发或容器部署步骤启动依赖、API 和 Worker，打开工作台完成身份识别。
2. 管理员在“知识底座”登记并验证实例，为业务知识库准备并启用活动底座。
3. 上传资料，查看任务；达到 ready 后检查原件和授权，再手动发布。等待索引时确认 Worker 正常运行，不要反复上传同一资料排查。
4. 新建本人会话，选择有权访问的知识库提问，查看来源及原件。返回“无法回答”时先检查资料是否已发布、是否获授权及索引是否可用。
5. 从 `/api/v3/operations` 或工作台查看任务、Worker 心跳和告警；该接口需要管理员权限。`/health/ready` 成功只证明数据库可达，不能代替知识底座、模型和原件存储检查。
6. 上传结果未知时先自动／人工对账；底座切换失败时检查验证资格、目标副本和清单是否过期；密钥与原件不要写入问题、日志截图或测试报告。

详细维护命令见 [运维说明](docs/v3/operations.md)，参数要求见 [生产配置说明](docs/v3/production-configuration.md)。当前机器已有配置不代表新机器也已完成部署；本机配置方法见 [本机说明](docs/v3/local-setup.md)。

## 已知限制

- 完整生产验收尚未完成；企业身份、目标用户及资料规模、业务题库和相关签收仍需落实。
- 现有 30 分钟容量结果仅覆盖 WeKnora 的单文档场景；Dify 持续容量、大授权范围及真正多进程／多节点持续运行未完成验收。
- 基础格式的最小解析通过不代表扫描件、复杂表格或精确定位通过；来源当前以资料片段为主，不能猜测页码或单元格位置。
- 没有自动故障切换。底座切换须准备、同步并重新核验，不能保证两套底座检索效果完全相同。
- 企业目标对象存储的安全条件删除、保留策略、平台数据与密钥的完整恢复仍需验证；远端 API 不可见不代表全链路物理删除。
- 问题改写和引用编号校验不能替代业务正确性评测；外部模型与底座的连通性、额度、限流及数据保存策略仍影响运行。
- 当前只做文档问答，不包含 V1/V2 的业务数据库问答、联网搜索，也不提供旧数据与配置的自动迁移。

以 [生产就绪清单](docs/v3/production-readiness.md) 和具体运行报告为准。设计文档中的目标、代码中已实现的功能、企业环境已验收的能力应分别判断。
