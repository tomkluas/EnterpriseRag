# EnterpriseRag V3

当前版本：**V3 / API 3.0.0**，功能说明更新于 **2026-11-02**。

EnterpriseRag 帮助企业把制度、产品资料和业务文档整理成可提问的知识库。员工在浏览器里提问，系统查找其有权查看的资料，给出回答并附上原文出处。管理员可以管理资料版本、设置访问权限、发布更新和查看处理任务。

项目使用 **Vue 3 + FastAPI + PostgreSQL**，支持 **WeKnora / Dify** 两套知识服务。文件解析和检索交给所选服务，原始文件、权限、版本和会话由本项目统一管理。

[项目亮点](#项目亮点) · [界面预览](#界面预览) · [整体技术路线](#整体技术路线) · [部署使用](#部署使用) · [运维手册](docs/v3/operations.md) · [设计文档](docs/v3/README.md)

阅读顺序：先看亮点和核心功能了解用途，再看技术组成、架构与流程；准备使用时从部署章节开始，开发和维护时查项目目录、接口与运维文档。

## 项目亮点

### 1. 回答有出处，方便核对

员工可以直接问“上海出差住宿标准是多少”，系统会查找相关制度，再根据找到的资料回答。回答旁会列出参考片段，也能打开对应原文，方便确认依据。

### 2. 不同的人，可以查不同的资料

共享知识库时，可以按角色设置哪些资料允许查看、哪些明确禁止访问。成员可分配多个角色，部门用于组织人员，不自动授予资料权限。系统会按有效角色查资料，查看原文和历史回答时也会重新检查权限。例如，员工调岗后取消了某份资料的访问权限，之前的聊天记录也会按新的权限处理。

### 3. 新制度准备好后再发布，旧版有记录可查

更新文件时，可以先上传和处理新版，确认后再发布，让后续问答使用新内容。系统会分别保存各版原件，也会记录回答参考了哪个版本，方便日后核对。

### 4. 可以接着问，不必每次重说背景

先问“上海出差住宿标准是多少”，再问“那发票什么时候交”，系统会结合前面的对话理解后续问题。找到资料后，还会再次比较各个片段与问题的相关程度，选出更合适的内容用于回答。

### 5. 支持两套知识服务，使用入口保持一致

可以为不同知识库选择 WeKnora 或 Dify。需要更换时，先把资料同步到目标服务，检查处理结果，再执行切换。资料原件、权限和聊天记录由本项目保存，员工继续在同一个工作台使用。

### 6. 文件处理有进度，出问题有办法查

上传后的处理任务会保存在数据库里，可以查看阶段、执行次数、最近底座状态和分块数量；有阅读权限时，还能分页预览已就绪副本的分块正文。遇到上传超时，系统会先核对文件是否已经收到，再决定如何继续，减少重复上传。维护人员还能查看任务积压、处理耗时和后台服务状态，并使用配套的备份、恢复和排障工具。

### 7. 管理人员能按会话核查问答过程

每轮回答可以查看处理明细（Trace），了解问题改写、检索、精排和生成的耗时及结果。获得“问答审计”权限的人员可以按问题、成员、时间和状态查找会话，再按轮次查看完整上下文、回答来源和反馈；查看详情会留下操作记录。

## 核心功能

| 功能 | 说明 |
| --- | --- |
| 文档知识库 | 按业务知识库组织 TXT、Markdown、DOCX、XLSX 和文字 PDF，支持上传、处理状态查看与原文读取 |
| 带引用的知识问答 | 在授权资料中检索，统一精排后生成流式回答，展示证据片段和来源 |
| 多轮会话 | 保存问题、回答、来源及资料依赖，结合已授权的历史理解后续问题；支持回答复制、反馈和个人会话删除，审计记录保留 |
| 登录与企业管理 | 支持账号密码和企业身份令牌登录，管理当前公司信息、部门树、成员与多个角色 |
| 角色与资料授权 | 内置三种角色并支持自定义操作权限；知识库默认角色、资料继承或单独授权，明确禁止优先 |
| 资料版本管理 | 原件按版本独立保存，新版本处理完成后发布；支持撤回及失败草稿取消 |
| 双知识底座 | 按业务知识库选择 WeKnora 或 Dify，准备目标索引、同步资料后切换 |
| 可靠后台处理 | 持久化入库、同步与删除任务，支持租约续约、失败重试、上传结果对账及只读分块预览 |
| 问答明细与审计 | 个人查看每轮处理明细；授权审计人员按会话筛选、分页查看问答、来源、反馈和阶段耗时 |
| 运行管理 | 操作审计、Worker 心跳、任务积压、阶段耗时、告警通知及备份恢复工具 |

例如，管理员上传新版差旅制度，设置可访问成员并发布。员工提问“上海出差住宿标准是多少”，系统从其有权访问的已发布资料中查找依据，生成回答并附上引用。后续更新制度时，旧版与新版各自保留原件和业务记录。

## 界面预览

以下为当前 V3 的实际界面。资料与审计页面选用已有的合成差旅示例，企业名称以“示例企业”展示。截图使用管理员账号；普通用户仅显示“知识问答”。点击图片可查看原图。

| 知识问答 | 知识资料与版本 |
| --- | --- |
| [![知识问答：选择知识库、查看会话和输入问题](docs/images/v3/chat.png)](docs/images/v3/chat.png) | [![知识资料：筛选文件、查看发布状态与版本详情](docs/images/v3/documents.png)](docs/images/v3/documents.png) |
| 选择有权访问的知识库，从统一入口提问并查看历史会话。 | 按文件名和状态筛选资料，查看当前发布版本与历史记录。 |

| 问答审计 | 登录入口 |
| --- | --- |
| [![问答审计：按问题、成员、状态和时间筛选会话](docs/images/v3/trace-audit.png)](docs/images/v3/trace-audit.png) | [![登录入口：账号密码与企业令牌两种登录方式](docs/images/v3/login.png)](docs/images/v3/login.png) |
| 按会话核查问答轮次、处理结果与反馈，保留完整上下文。 | 支持账号密码或企业令牌，刷新后重新验证并恢复当前标签页登录。 |

## 技术组成

| 层次 | 技术与职责 |
| --- | --- |
| 浏览器工作台 | Vue 3、Vite；资料、会话、引用、企业与权限、问答审计、任务与知识底座管理 |
| 页面展示 | Markdown-it、DOMPurify；回答排版与 HTML 清理 |
| API 服务 | FastAPI、Uvicorn；提供 `/api/v3` 业务接口，并托管构建后的前端页面 |
| 身份与登录 | PyJWT、RS256 JWT（带非对称签名的身份令牌）；本地密码使用 scrypt 摘要，登录会话令牌仅保存摘要，权限由当前成员与角色决定 |
| 业务数据 | SQLAlchemy、PostgreSQL；权限、版本、底座映射、任务、会话、审计和运行状态 |
| 原件存储 | `OriginalStore`；开发使用独立本机目录，生产使用 S3 兼容对象存储 |
| 后台任务 | 独立 Worker 进程；通过 PostgreSQL 任务表领取工作，维护租约并写回处理结果 |
| 知识服务 | WeKnora v0.9.0 / Dify 1.18.0；文档解析、分块、Embedding、索引及受限检索 |
| 模型服务 | HTTP 接入问题改写、精排和生成模型；支持 OpenAI 兼容对话协议及精排接口适配 |
| 流式通信 | Fetch + SSE，即服务端持续推送处理状态、证据来源与回答片段 |
| 部署 | Docker Compose；API、Worker 与单独运行的数据库迁移共用应用镜像 |

Embedding 将文字编码成可检索的向量，由知识服务执行；精排则重新比较候选片段与问题的相关程度，由 V3 统一调用模型完成。后端精确依赖见 [requirements-v3.lock](backend/requirements-v3.lock)，前端依赖见 [package-lock.json](frontend/package-lock.json)。

## 整体技术路线

系统分为业务管理、知识处理和模型回答三部分：

1. **业务管理**：V3 保存资料原件、版本、权限和会话，确定当前用户可以使用哪些资料。
2. **知识处理**：Worker 将资料副本提交给 WeKnora 或 Dify，跟踪解析和索引状态；提问时按授权文档范围检索。
3. **模型回答**：V3 整理召回证据，调用统一精排与生成模型，将回答和引用推送给浏览器并保存到会话。

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

### 模块职责

- **工作台与 API**：浏览器提交业务知识库和问题，API 负责认证、参数校验、业务操作和流式响应。
- **业务层**：将文档、版本、授权、底座绑定和会话组织成统一模型。知识库的活动路由决定本次使用哪套知识服务。
- **知识适配器**：用统一契约封装提交、状态检查、检索、删除和能力查询，将平台返回结果转换为统一证据结构。
- **Worker**：承担入库、同步、状态轮询、结果对账和清理，API 请求与长时间处理分开运行。
- **模型模块**：将多轮或长问题整理为检索问，按原问题统一精排候选，结合证据及历史生成带编号引用的回答。

实现入口：[业务接口](backend/app/v3/api.py)、[业务规则](backend/app/v3/domain.py)、[知识适配器](backend/app/v3/knowledge/)、[模型调用](backend/app/v3/chat/models.py)。

## 核心流程

按“谁能使用 → 上传资料 → 后台处理 → 提问回答 → 更换知识服务”的顺序，下面分别说明。

### 权限是怎么判断的

可以把权限分成两个问题：**这个人能做什么操作，以及能看哪些资料。** 角色中的操作权限决定能否管理知识库、成员或查看问答审计；资料的允许与禁止角色决定能读哪些正文。系统提供“知识使用者”“资料管理员”“系统管理员”三个内置角色，也支持自定义业务角色、给成员分配多个角色。部门上下级不会自动获得资料阅读权。

普通知识使用者只显示“知识问答”，可提问、查看自己的会话及授权的回答来源；不能进入“知识资料”和“处理任务”，直接调用对应列表或任务分块接口也会被拒绝。具备资料创建、资料管理或系统管理权限的维护人员才可进入这两个工作区，具体资料和任务仍按原有范围检查。

下面用员工提问或查看原文的过程说明：

```mermaid
flowchart TD
    A[员工登录并发起请求] --> B{身份有效，账号也已启用？}
    B -->|否| X[拒绝本次访问]
    B -->|是| C{缺少阅读操作权限、资料已撤回，或角色命中禁止规则？}
    C -->|是| X
    C -->|否| D{有效角色命中资料的允许范围？}
    D -->|否| X
    D -->|是| E[将符合发布和索引条件的资料用于回答，或允许读取原文]
    E --> F[生成回答、输出内容、查看历史时再次检查权限]
    F --> G{相关资料仍允许查看？}
    G -->|是| H[继续回答或展示内容]
    G -->|否| I[停止后续输出，或隐藏相关历史回答]
```

例如，小王原来可以看某份制度，调岗后被取消授权，再打开旧对话时，依赖这份资料的回答也会重新检查。明确禁止访问的规则优先；系统管理员身份本身不自动获得所有资料的阅读权。已经发出的内容无法追回，权限变更控制后续输出与访问。

知识库可设置默认允许角色；资料可以继承并追加允许角色，也可以单独设置。知识库和资料的禁止规则始终优先。旧用户／组授权在显式迁移时转换为兼容角色，保留原访问范围；当前管理界面统一选择角色授权。

“问答审计”是独立权限，允许审计人员查看本公司所有成员的已保存问答、证据快照和反馈，应按职责分配。它不自动授予原件下载或新问题检索的阅读权。当前工作台管理服务配置的公司，不提供平台级多公司管理入口。

### 登录与成员开通

1. 初始管理员在登录页选择“企业令牌”：开发环境使用配置中的启动令牌，生产环境使用企业签发的令牌。
2. 在“企业与权限”中维护企业信息、部门和角色，再创建成员，选择所属部门、一个或多个角色，并设置 12 至 128 字符的初始密码。企业令牌用户的账号需对应令牌中的 `sub`。
3. 成员在登录页选择“账号密码”即可登录；管理员也可为自己的账号设置密码。密码会话有效期为 8 小时，当前标签页刷新后会重新验证并恢复登录，关闭标签页或会话失效后需重新登录；退出会撤销当前密码会话，重置密码或停用账号会撤销该成员的密码会话。
4. 给角色配置阅读操作权限后，还需在知识库或资料中允许该角色访问，成员才能使用相应已发布资料。

### 文档入库与版本发布

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

上传使用 `POST /api/v3/documents`，接收文件、知识库编号和可选的原文档编号，单文件上限为 50 MiB。系统保存不可变原件及 SHA-256 内容校验值，再创建资料版本和持久化任务。

Worker 领取任务后提交文件、轮询索引状态并核对远端绑定。资料达到 `ready` 后由管理员发布，发布事务更新当前版本指针和目录修订号。这样可以先准备新版本，再统一将其用于问答。

上传回执丢失时，系统根据已保存的提交证据查找远端候选，回读文件核对内容并恢复处理；需要人工核对的任务保留调查与签收入口。

实现入口：[原件存储](backend/app/v3/storage.py)、[Worker](backend/app/v3/jobs/worker.py)、[版本管理](backend/app/v3/domain.py)。

### 后台任务是怎么处理的

文件上传后，网页先返回任务记录，后台处理程序 Worker 再负责提交、检查进度和保存结果。处理过程记在数据库里，页面可以随时查看。

```mermaid
flowchart TD
    A[员工上传文件] --> B[保存原件，并把处理任务记入数据库]
    B --> C[网页返回任务记录]
    B --> D[后台程序领取任务，持续报告仍在处理]
    D --> E[向知识服务提交文件，并检查处理进度]
    E --> F{这次处理结果如何？}
    F -->|处理完成| G[保存索引结果，资料变为可发布]
    G --> H[管理员确认后发布，员工可以提问]
    F -->|明确发生错误| I[记录原因]
    I --> J{已达到重试上限？}
    J -->|否，等待后重试| E
    J -->|是| M[显示失败，供维护人员处理]
    F -->|超时，不确定文件是否已收到| K[先查远端记录，并核对文件内容]
    K -->|确认是这份文件| E
    K -->|暂时无法确认| L[保留待对账状态，自动继续核对或人工调查]
```

处理中的任务带有有效期限，后台程序会定期续期；程序退出后，过期任务可由后续处理者接手，写回时也会核对领取凭据。上传结果未知时会先核实远端情况，避免接手后重复提交。索引同步和远端删除也使用这套任务机制，具体异常处理见 [运维手册](docs/v3/operations.md)。

### 授权检索与多轮问答

```mermaid
flowchart TD
    A[员工提问] --> B[确认员工能看哪些已发布的资料]
    B --> C[结合前面的聊天，弄清这次在问什么]
    C --> D[在这些资料里查找相关内容]
    D --> E{有没有找到相关内容？}
    E -->|没有| F[告诉员工：现有资料无法回答]
    E -->|有| G[挑出与问题最相关的片段]
    G --> H[根据这些片段组织回答，附上出处]
    H --> I[边回答边检查资料是否仍允许查看]
    I --> J[保存回答和出处，供以后查看和继续提问]
```

1. 创建本人会话，向 `POST /api/v3/conversations/{conversation_id}/messages/stream` 提交 `space_id` 和 `query`。
2. 读取会话历史、当前授权、已发布版本及活动底座，按需改写长问题或多轮检索问，并核对改写语义。
3. 按底座实例、物理知识库和文档绑定分批召回，核对结果归属及版本，合并为统一证据。
4. 使用原问题进行精排，选取相关片段，连同已授权历史交给生成模型；资料不足时返回明确说明。
5. 通过 SSE 输出状态、来源和正文，检查引用编号后保存回答、证据快照、资料依赖和完成状态。

权限检查贯穿检索、模型派发、正文输出和历史读取。资料撤回或授权变化后，后续访问按最新权限处理；生成前遇到目录或路由变化时会重新检索一次。

实现入口：[问答服务](backend/app/v3/chat/service.py)、[身份验证](backend/app/v3/auth.py)、[流式读取](frontend/src/lib/stream.mjs)。

### 知识底座切换

管理员登记平台地址、物理知识库和凭据引用，完成实例验证后，为业务知识库准备目标底座。Worker 同步所需资料并建立副本映射，系统根据资料清单、索引状态和路由修订号检查切换条件，再事务更新活动路由。

WeKnora 与 Dify 使用相同业务资料、授权和会话模型，各自维护处理副本。切换通过“准备 → 同步 → 检查 → 激活”完成，历史引用仍关联 V3 自有资料版本。详见 [双底座设计](docs/v3/dual-backend.md)。

## 数据存储

| 数据 | 存放位置 | 用途 |
| --- | --- | --- |
| 用户与授权 | V3 PostgreSQL | 用户启停、角色、组、资料可见范围 |
| 文档与版本 | V3 PostgreSQL | 原件键、内容校验值、当前版本、发布与撤回状态 |
| 资料原件 | 独立目录或 S3 | 按版本保存文件，供下载、校验及重建使用 |
| 索引与解析副本 | WeKnora / Dify 独立存储 | 文本解析、分块、向量编码与检索 |
| 任务与平台绑定 | V3 PostgreSQL | 处理状态、租约、远端回执、同步及切换记录 |
| 会话与引用 | V3 PostgreSQL | 问题、回答、证据快照和依赖资料 |
| 审计与运行指标 | V3 PostgreSQL | 操作记录、Worker 心跳、任务积压、阶段耗时及告警 |
| 配置与凭据 | 独立配置文件和服务环境变量 | 数据库、身份、存储、平台及模型接入 |

后台任务使用 PostgreSQL 任务表和租约机制：Worker 领取任务后定期续约，写回时校验租约，过期处理者无法覆盖新结果。外部服务调用通过提交记录、状态轮询、失败重试和对账衔接，业务版本与平台索引副本分别记录。

资料撤回先更新业务可见性，再安排远端清理。原件回收、备份和恢复由独立维护命令处理。API 与 Worker 共用业务数据库和原件存储；生产原件位置使用 `V3_OBJECTS_URI=s3://<桶名>/<独立前缀>`，开发本机目录使用 `V3_OBJECTS_DIR`。

## 部署使用

**新电脑建议从 [首次安装与生产部署](docs/v3/quick-start.md) 开始。** 新增 `setup.bat` 可创建独立的 Docker 开发环境，默认包含 WeKnora，无需宿主机 Python / Node.js；`-Action Init` 生成配置，`-Action Up` 构建、迁移并启动。平台建模、知识库绑定及真实验收仍按指南完成。现有机器的 `start.bat` 继续用于原来的源码环境，二者数据独立。

新电脑安装 Docker Desktop 并启动 Linux 容器引擎后，在仓库根目录执行：

```powershell
.\setup.bat -Action Init
# 编辑 deploy/dev/.env，填写回答、精排和 Embedding 模型
.\setup.bat -Action Up
.\setup.bat -Action Configure
.\setup.bat -Action Up
```

`Configure` 为独立开发 WeKnora 创建账号、模型、物理知识库和受限凭据，重复执行复用已创建资源。工作台地址为 [http://127.0.0.1:19000](http://127.0.0.1:19000)，初次选择“企业令牌”，使用 `deploy/dev/.env` 中的 `V3_BOOTSTRAP_TOKEN` 登录。随后仍需登记平台、完成真实验证并签收、激活底座，再上传和发布资料。此流程不自动完成生产验收。

先选择一种运行方式，按对应步骤操作：

| 方式 | 适合什么情况 | 需要准备什么 |
| --- | --- | --- |
| Docker 开发环境（`setup.bat`） | 新电脑体验、独立开发环境，工作台端口 19000 | Docker Desktop；默认同时创建 WeKnora，按上方入口操作 |
| 本机源码启动 | 在 Windows 上开发、调试或体验 | uv、Python 3.11、Node.js 22.12+，以及独立 PostgreSQL |
| Docker Compose 部署 | 用容器统一构建、启动和维护 | Docker Engine / Docker Desktop、Docker Compose；Python 和 Node.js 在镜像中准备 |

所有方式都需要至少一套可用的 WeKnora 或 Dify，以及回答、精排和 Embedding 模型。知识平台负责配置 Embedding；回答和精排服务在 V3 配置。下面详细介绍源码与生产 Compose 启动方式，再按“启用知识服务与首次问答”完成平台登记和资料导入。

以下命令均为 PowerShell，在仓库根目录执行。数据库端口、密码和服务地址以自己的环境为准。

### 方式一：本机源码启动

#### 1. 安装依赖并构建页面（首次运行）

先启动自己的 PostgreSQL，并准备一个独立的 V3 数据库及账号，然后执行：

```powershell
uv venv --python 3.11 backend/.venv-v3
uv pip install --python backend/.venv-v3/Scripts/python.exe -r backend/requirements-v3.lock
npm --prefix frontend ci
npm --prefix frontend run build
```

构建完成后，页面文件位于 `frontend/dist/`，由后端直接提供，无需另开前端开发服务器。

#### 2. 填写本机配置

在仓库根目录创建 `v3.local`。下面是开发环境的基础配置，替换数据库连接、开发令牌和原件目录：

```dotenv
V3_MODE=development
V3_DATABASE_URL=postgresql+psycopg2://v3:your_password@127.0.0.1:18433/enterpriserag_v3
V3_TENANT_ID=enterprise
V3_BOOTSTRAP_USER=admin
V3_BOOTSTRAP_TOKEN=replace_with_at_least_32_random_characters
V3_OBJECTS_DIR=D:/your-private-v3-data/objects
```

这里的 `18433` 是示例端口，数据库需要事先存在。开发令牌自行生成，至少 32 字符，用于登录工作台。`v3.local` 已被 Git 忽略。

继续在同一文件中填写回答和精排模型：

| 配置项 | 填写内容 |
| --- | --- |
| `V3_MODEL_BASE_URL` | 回答服务的 OpenAI 兼容基础地址，通常以 `/v1` 结尾 |
| `V3_CHAT_MODEL` | 回答模型名称 |
| `V3_MODEL_KEY_REF` | 填 `V3_SECRET_MODEL`，表示从这个环境变量名读取密钥 |
| `V3_SECRET_MODEL` | 实际模型密钥 |
| `V3_RERANK_MODEL` | 精排模型名称 |
| `V3_RERANK_BASE_URL`、`V3_RERANK_KEY_REF`、`V3_SECRET_RERANK` | 精排使用独立服务时填写；共用回答服务地址和密钥时可省略 |

模型配置示例及平台填写方法见 [本机配置说明](docs/v3/local-setup.md)。

#### 3. 初始化数据库（首次运行）

```powershell
$env:PYTHONPATH = 'backend'
& ./backend/.venv-v3/Scripts/python.exe -m app.v3.cli --env-file v3.local migrate
```

看到“V3 数据库初始化完成”后，继续下一步。日常启动不需要重复初始化；升级涉及数据库结构时按 [运维手册](docs/v3/operations.md) 执行迁移。

#### 4. 启动并打开工作台

```powershell
& ./tools/v3/start_local.ps1
```

脚本在后台启动 API 和 Worker。打开 [工作台](http://127.0.0.1:18000)，使用 `v3.local` 中的开发令牌登录，然后继续下方的知识服务配置。

确认服务已启动可执行：

```powershell
Invoke-RestMethod 'http://127.0.0.1:18000/health/ready'
```

返回 `ready: true` 表示 API 已连上数据库。Worker 状态和日志检查见 [运维手册](docs/v3/operations.md)，脚本日志位于 `data-v3/logs/`。

#### 日常启动与重启

- Windows 已完成本机配置后，可双击根目录的 `start.bat`：检查 Docker Desktop，启动既有数据库与 WeKnora 容器，再启动 API 和 Worker，成功后打开工作台；已运行的进程不会重复启动。失败时窗口保留错误信息。本入口沿用当前本机容器名称，不启动 Dify，也不负责首次部署。
- 服务未运行时，执行 `& ./tools/v3/start_local.ps1`。
- 修改 `v3.local` 或需要重启时，执行 `& ./tools/v3/start_local.ps1 -Restart`，正在进行的请求会中断。
- 修改前端后，先重新执行 `npm --prefix frontend run build`。
- PostgreSQL 和知识平台需先启动，本机脚本只负责 V3 API 与 Worker。

### 方式二：Docker Compose 部署

Compose 会启动 V3 API、Worker 和 PostgreSQL，并提供独立的数据库迁移命令。知识平台、对象存储、模型服务及企业身份入口单独准备。配置模板默认使用生产模式，通过企业身份令牌登录，原件保存到 S3 兼容对象存储。

#### 1. 创建容器配置

首次部署时，将模板复制为 `deploy/v3/.env`；已有配置时直接编辑，保留原值：

```powershell
if (-not (Test-Path -LiteralPath ./deploy/v3/.env)) {
    Copy-Item -LiteralPath ./deploy/v3/.env.example -Destination ./deploy/v3/.env
}
```

编辑 `deploy/v3/.env`，完成以下配置：

| 配置 | 需要核对的内容 |
| --- | --- |
| 数据库 | `V3_POSTGRES_PASSWORD` 与 `V3_DATABASE_URL` 的密码一致；容器内地址使用 `database:5432` |
| 企业身份 | `V3_OIDC_ISSUER`、`V3_OIDC_AUDIENCE`、`V3_OIDC_JWKS_URL`；初始管理员 `V3_BOOTSTRAP_USER` 对应企业令牌中的 `sub` |
| 原件存储 | `V3_OBJECTS_URI`、S3 服务地址、区域及访问凭据 |
| 回答与精排 | 服务地址、实际模型名称及 `V3_SECRET_` 密钥；独立精排服务另填对应地址和密钥引用 |
| 知识平台 | 平台凭据对应的 `V3_SECRET_` 变量，供后续在工作台登记时引用 |

服务地址须能从容器内部访问。完整字段要求见 [生产配置说明](docs/v3/production-configuration.md)。

#### 2. 构建镜像并启动数据库

```powershell
docker compose --env-file deploy/v3/.env -f deploy/v3/compose.yaml config --quiet
docker compose --env-file deploy/v3/.env -f deploy/v3/compose.yaml build
docker compose --env-file deploy/v3/.env -f deploy/v3/compose.yaml up -d database
```

镜像构建会安装后端依赖并构建前端页面。数据库数据保存在 `postgres-v3` 持久卷中。

#### 3. 执行数据库迁移

```powershell
docker compose --env-file deploy/v3/.env -f deploy/v3/compose.yaml --profile migration run --rm migrate
```

命令等待数据库健康后执行，成功显示“V3 数据库初始化完成”并退出。确认成功后再启动应用；后续升级是否执行迁移，按相应版本说明处理。

#### 4. 启动应用并确认结果

```powershell
docker compose --env-file deploy/v3/.env -f deploy/v3/compose.yaml up -d api worker
docker compose --env-file deploy/v3/.env -f deploy/v3/compose.yaml ps
Invoke-RestMethod 'http://127.0.0.1:18000/health/ready'
```

API、Worker 和数据库应处于运行状态，健康接口返回 `ready: true`。打开 [工作台](http://127.0.0.1:18000)，使用企业签发的访问令牌登录，再完成知识平台登记。对外访问由企业网关转发并提供 HTTPS。

#### 日常启动与更新

服务停止后重新启动：

```powershell
docker compose --env-file deploy/v3/.env -f deploy/v3/compose.yaml up -d api worker
```

修改 `.env` 后重新创建应用容器，使新配置生效：

```powershell
docker compose --env-file deploy/v3/.env -f deploy/v3/compose.yaml up -d --force-recreate api worker
```

更新源码时，重新构建镜像，按版本要求迁移数据库，再启动应用。日志、备份、升级与回退步骤见 [运维手册](docs/v3/operations.md)。

### 启用知识服务与首次问答

各部署方式启动后，都按以下顺序完成配置。Docker 开发环境已执行 `Configure` 时，可复用其物理库和凭据；已经创建并配置好的业务知识库可直接从上传资料开始。

1. **准备平台知识库**：在 WeKnora 或 Dify 创建物理知识库并配置 Embedding，准备对应知识库的受限凭据；Dify 额外配置字符串元数据字段 `v3_binding_id`。
2. **创建 V3 知识库并登记平台**：在工作台创建业务知识库，在“知识底座”登记平台实例，填写平台根地址、物理知识库 ID 和服务器凭据引用。
3. **完成实例验证**：按 [实例验证流程](docs/v3/validation.md) 检查实例，在部署服务器通过 `accept-validation --instance <实例ID> --report <报告路径>` 签收报告；源码方式使用 `v3.local`，容器方式使用容器配置。
4. **启用活动底座**：为业务知识库准备目标索引，检查状态并激活。
5. **上传并发布资料**：上传文件，等待 `ready`，确认内容后点击发布。
6. **配置阅读权限**：在“企业与权限”建立部门、角色和成员，为角色配置阅读操作权限；再到“知识资料”设置知识库默认角色授权或资料访问权限。企业登录用户编号需与其身份令牌中的 `sub` 一致。
7. **开始提问**：使用已授权账号创建会话、选择知识库提问，查看回答来源及对应原文。

## 项目目录

```text
EnterpriseRag/
├── backend/
│   ├── app/
│   │   ├── main.py                 # V3 启动、生产配置预检、静态页面入口
│   │   └── v3/
│   │       ├── api.py / auth.py     # 业务接口与身份验证
│   │       ├── access.py / passwords.py      # 企业、组织、角色与密码会话
│   │       ├── trace_admin.py      # 按会话或消息查询问答审计
│   │       ├── domain.py / models.py / db.py  # 授权、版本、业务模型与事务
│   │       ├── storage.py          # 不可变原件访问
│   │       ├── jobs/               # 独立 Worker、入库、对账、清理与分块预览
│   │       ├── knowledge/          # WeKnora / Dify 契约、适配器和受限传输
│   │       ├── chat/               # 检索、模型调用、流式回答
│   │       ├── recovery.py / orphan_gc.py    # 恢复与原件回收
│   │       ├── operations.py / monitoring.py # 运行状态与告警
│   │       └── cli.py              # 迁移、Worker、签收及维护命令
│   ├── tests/v3/                  # V3 自动化验证
│   └── requirements-v3.lock       # 锁定的后端依赖
├── frontend/src/                 # Vue 工作台、API 与流式读取
├── deploy/dev/                   # 独立开发 Compose、WeKnora 与配置模板
├── deploy/v3/                    # Dockerfile、Compose、配置模板与恢复说明
├── tools/v3/                     # 本机启动及隔离验收工具
├── docs/v3/                      # 设计、配置、运维与生产就绪说明
├── docs/testing/v3/              # 历史与当前验证证据
├── v3.local                      # 本机私有配置，不提交 Git
├── setup.bat                     # Docker 开发／生产配置、部署与检查入口
├── start.bat                     # 既有 Windows 源码环境启动入口
└── README.md
```

## 接口与流式通信

| 场景 | 主要接口 |
| --- | --- |
| 密码登录与退出 | `POST /api/v3/auth/login`、`POST /api/v3/auth/logout` |
| 企业与权限管理 | `/api/v3/tenant`、`/api/v3/organization`、`/api/v3/roles`、`/api/v3/users` |
| 当前用户与知识库 | `GET /api/v3/me`、`GET /api/v3/spaces` |
| 上传文档与授权 | `POST /api/v3/documents`、`POST /api/v3/documents/{id}/grants` |
| 发布版本 | `POST /api/v3/documents/{id}/versions/{version_id}/publish` |
| 撤回与原件 | `DELETE /api/v3/documents/{id}`、`GET /api/v3/documents/{id}/source` |
| 会话 | `POST /api/v3/conversations`、`GET /api/v3/conversations/{id}/messages` |
| 流式问答 | `POST /api/v3/conversations/{id}/messages/stream` |
| 底座切换 | `/api/v3/spaces/{id}/backend-switches` 及其状态、激活接口 |
| 任务与运行状态 | `GET /api/v3/jobs`、`GET /api/v3/operations` |
| 任务分块预览 | `GET /api/v3/jobs/{job_id}/replicas/{replica_id}/chunks` |
| 回答反馈与个人明细 | `POST /api/v3/messages/{message_id}/feedback`、`GET /api/v3/messages/{message_id}/trace` |
| 按会话查看问答审计 | `GET /api/v3/admin/trace-conversations`、`GET /api/v3/admin/trace-conversations/{conversation_id}` |
| 健康检查 | `/health`、`/health/ready`；后者检查 API 到数据库的连接 |

问答通过带身份凭据的 POST 请求建立 SSE 连接，`data:` 行承载 JSON，`event` 字段区分事件：

| 事件 | 内容 |
| --- | --- |
| `status` | 当前处理阶段 |
| `sources` | 本次回答使用的证据来源 |
| `token` | 回答文本增量 |
| `done` | 回答完成及消息编号 |
| `error` | 处理错误及提示 |

等待期间约每 5 秒发送注释心跳，API 整体期限为 180 秒。前端逐帧解析 UTF-8 文本，识别完成与错误事件，并在缺少终止事件的断流中提示异常。

## 开发验证

在仓库根目录执行：

```powershell
$env:PYTHONPATH = 'backend'
backend/.venv-v3/Scripts/python.exe -m unittest discover -s backend/tests/v3 -v
npm --prefix frontend test
npm --prefix frontend run build
```

真实 PostgreSQL 并发测试通过 `V3_TEST_DATABASE_URL` 指定独立测试库，未设置时跳过相关项。测试覆盖业务授权、版本、任务、知识适配、问答、恢复及运维接口；运行记录见 [测试报告目录](docs/testing/v3/)。

## 运维与文档

### 出问题时，先查哪里

维护时先看现象，再定位是哪一段出了问题。任务状态、日志和运行指标用于判断原因，修复后再用实际操作确认恢复。

```mermaid
flowchart TD
    A[发现异常，或收到告警] --> B{哪里不正常？}
    B -->|页面打不开| C[查 API 是否启动、配置和数据库连接]
    B -->|文件一直处理中| D[查后台程序心跳、任务错误和知识服务]
    B -->|能打开页面，但回答失败| E[查资料发布与权限，再查检索和模型服务]
    B -->|原文打不开或清理未完成| F[查资料授权、文件存储和清理任务]
    C --> G[结合日志和运行状态确认原因]
    D --> G
    E --> G
    F --> G
    G --> H[按对应步骤修复，保留处理记录]
    H --> I[重新验证刚才失败的操作，并检查权限是否正常]
    I --> J{已恢复？}
    J -->|否| G
    J -->|是| K[记录恢复结果，检查告警是否解除]
```

`/health/ready` 用于检查 API 到数据库的连接；管理员通过 `/api/v3/operations` 查看后台程序、排队任务、阶段耗时和告警投递状态。本机启动脚本的日志在 `data-v3/logs/`，容器部署查看相应服务日志。备份恢复、上传结果未知、删除确认和告警配置的具体命令集中在下方运维手册中。

### 详细文档

| 文档 | 内容 |
| --- | --- |
| [运维与异常处理手册](docs/v3/operations.md) | 日常检查、日志与告警、9 类故障排查、上传对账、备份恢复、升级回退 |
| [本机配置](docs/v3/local-setup.md) | 本机启动、模型接入与知识服务配置 |
| [生产配置](docs/v3/production-configuration.md) | 企业身份、对象存储、模型凭据及启动预检 |
| [技术设计](docs/v3/design.md) | 业务模型、授权、版本及检索设计 |
| [双底座设计](docs/v3/dual-backend.md) | 适配契约、索引同步及底座切换 |
| [恢复工具](docs/v3/recovery-bundle.md) | 单租户业务数据与原件的备份恢复 |
| [原件回收](docs/v3/original-gc.md) | 孤立对象盘点及安全回收 |

## 版本与适用说明

- **V3（当前）**：企业集中部署，侧重文档问答、资料授权、版本管理、会话持久化与双知识底座。
- **[V1](https://github.com/tomkluas/EnterpriseRag/blob/V1/README.md)**：Docker、Qdrant、本地 BGE 编码与精排，提供文档、业务数据库及联网问答。
- **[V2](https://github.com/tomkluas/EnterpriseRag/blob/V2/README.md)**：Windows 单机交付、ChromaDB 和在线 Embedding，侧重轻量安装与使用。

V3 当前支持上述文档问答流程；扫描件识别、复杂表格及精确定位仍在完善，底座切换由管理员准备和激活。分块预览仅提供底座返回的正文，要求副本就绪且调用者具备相应任务访问权和资料阅读权，不支持编辑或重建索引，也不补猜页码。旧版数据需重新导入并建立授权与索引；外部模型及底座的数据处理方式取决于部署配置。完整企业生产验收尚未完成，目标环境的身份接入、容量、业务效果和灾备验证进展统一记录在 [部署验收清单](docs/v3/production-readiness.md)，各阶段测试结果保留在 [验证记录目录](docs/testing/v3/)。
