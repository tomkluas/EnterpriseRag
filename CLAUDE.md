# CLAUDE.md — 企业级增强 RAG 系统 · 实现契约

> **本文件是唯一权威实现规格。所有代码实现必须严格遵守此文件定义的目录结构、接口契约、技术选型和实现规范。禁止自行引入未列出的依赖或架构模式。**

---

## 0. 快速索引

| 需要实现 | 跳转章节 |
|---|---|
| 目录结构 & 文件清单 | §1 |
| 环境变量 & 配置 | §2 |
| 数据库表结构 | §3 |
| 后端核心模块 | §4 |
| LangGraph Agent 编排 | §5 |
| 文档处理流水线 | §6 |
| 对话 SSE 接口 | §7 |
| 前端组件 | §8 |
| Docker 部署 | §9 |
| 依赖包版本锁定 | §10 |
| 实现顺序 & 验收 | §11 |

---

## 1. 目录结构（完整文件清单）

```
rag-system/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                   # 应用入口
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py               # 登录、/me
│   │   │   ├── chat.py               # POST /chat/stream (SSE)
│   │   │   ├── knowledge.py          # 知识库 CRUD + 文档上传
│   │   │   └── admin.py              # 模型/DB/用户管理
│   │   ├── agents/
│   │   │   ├── __init__.py
│   │   │   ├── graph.py              # LangGraph StateGraph
│   │   │   ├── router_node.py        # 路由节点
│   │   │   ├── vector_node.py        # 向量检索节点
│   │   │   ├── db_node.py            # 数据库查询节点
│   │   │   ├── web_node.py           # Tavily 联网节点
│   │   │   ├── rerank_node.py        # BGE Reranker 精排节点
│   │   │   └── generate_node.py      # LiteLLM 流式生成节点
│   │   ├── connectors/
│   │   │   ├── __init__.py
│   │   │   ├── base.py               # 抽象基类
│   │   │   ├── sql_connector.py      # SQLAlchemy 五库统一
│   │   │   └── mongo_connector.py    # PyMongo + NL→MQL
│   │   ├── pipeline/
│   │   │   ├── __init__.py
│   │   │   ├── loader.py             # 文档加载
│   │   │   ├── splitter.py           # 文本分块
│   │   │   └── embedder.py           # 向量化 + 入库
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── llm_factory.py        # LiteLLM 工厂
│   │   │   └── bge_model.py          # BGE-M3 + Qdrant 操作
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── config.py             # Pydantic Settings
│   │   │   ├── security.py           # AES-256 + bcrypt
│   │   │   ├── auth.py               # JWT 中间件
│   │   │   └── deps.py               # FastAPI Depends 汇总
│   │   └── db/
│   │       ├── __init__.py
│   │       ├── database.py           # SQLAlchemy engine (SQLite)
│   │       ├── models.py             # ORM 5张表
│   │       └── crud.py               # CRUD 函数
│   ├── scripts/
│   │   └── init_db.py                # 幂等初始化
│   ├── tests/
│   │   ├── test_auth.py
│   │   ├── test_chat.py
│   │   ├── test_pipeline.py
│   │   └── test_connectors.py
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── main.js
│   │   ├── App.vue
│   │   ├── router/
│   │   │   └── index.js              # 路由 + 守卫
│   │   ├── stores/
│   │   │   ├── auth.js               # Pinia：Token/登录态
│   │   │   ├── chat.js               # Pinia：会话/消息
│   │   │   └── config.js             # Pinia：模型配置缓存
│   │   ├── api/
│   │   │   ├── http.js               # Axios 实例 + 拦截器
│   │   │   ├── auth.js
│   │   │   ├── chat.js               # SSE 请求封装
│   │   │   ├── knowledge.js
│   │   │   └── admin.js
│   │   ├── views/
│   │   │   ├── Login.vue
│   │   │   ├── Chat.vue              # 对话主页面
│   │   │   ├── KnowledgeBase.vue
│   │   │   └── Admin.vue
│   │   ├── components/
│   │   │   ├── ChatSidebar.vue       # 会话列表
│   │   │   ├── ChatInput.vue         # 输入框 + 检索范围选择器
│   │   │   ├── MessageBubble.vue     # 消息气泡 + Markdown
│   │   │   ├── SourceCard.vue        # 来源标注卡片
│   │   │   └── ModelSelector.vue     # 模型切换下拉
│   │   └── utils/
│   │       ├── sse.js                # SSE 客户端（POST + JWT）
│   │       └── markdown.js           # markdown-it 配置
│   ├── index.html
│   ├── vite.config.js
│   ├── Dockerfile
│   └── package.json
├── nginx/
│   └── nginx.conf
├── docker-compose.yml
├── docker-compose.dev.yml
├── .env.example
└── README.md
```

---

## 2. 环境变量（.env.example）

> **规则：所有密钥从环境变量读取，禁止硬编码在任何源文件中。**

```env
# 应用
APP_NAME=企业RAG系统
APP_ENV=production

# 安全（生产必须替换为随机值）
# 生成命令：python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=replace_with_random_32_byte_hex
ENCRYPTION_KEY=replace_with_random_32_byte_hex
ACCESS_TOKEN_EXPIRE_MINUTES=480

# 系统数据库
DATABASE_URL=sqlite:///./data/rag_system.db

# Qdrant
QDRANT_HOST=qdrant
QDRANT_PORT=6333
QDRANT_API_KEY=

# BGE 模型（本地，首次自动下载）
BGE_MODEL_PATH=BAAI/bge-m3
BGE_RERANKER_PATH=BAAI/bge-reranker-v2-m3
BGE_DEVICE=cpu
BGE_MAX_BATCH_SIZE=32
# 中国大陆服务器请设置：
# HF_ENDPOINT=https://hf-mirror.com

# 默认大模型：DeepSeek R1
DEFAULT_LLM_PROVIDER=deepseek
DEFAULT_LLM_MODEL=deepseek-reasoner
DEEPSEEK_API_KEY=replace_with_your_deepseek_api_key
DEEPSEEK_API_BASE=https://api.deepseek.com

# 联网搜索
TAVILY_API_KEY=replace_with_your_tavily_api_key
TAVILY_MAX_RESULTS=5

# 文件上传
UPLOAD_DIR=./data/uploads
CHUNK_SIZE=512
CHUNK_OVERLAP=64
MAX_FILE_SIZE_MB=50

# LangSmith（可选）
LANGCHAIN_TRACING_V2=false
LANGCHAIN_API_KEY=
LANGCHAIN_PROJECT=rag-system

# 初始管理员（init_db.py 使用）
INIT_ADMIN_USERNAME=admin
INIT_ADMIN_PASSWORD=Admin@ChangeMe123
```

---

## 3. 数据库表结构

> **规则：使用 SQLAlchemy ORM，不允许裸 SQL 建表。所有表在 `db/models.py` 定义，`init_db.py` 统一初始化。**

### 3.1 完整 ORM 模型（backend/app/db/models.py）

```python
from sqlalchemy import Column, String, Integer, Float, Text, Boolean
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id            = Column(String, primary_key=True)       # UUID
    username      = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)    # bcrypt
    role          = Column(String(16), default='user')     # admin | user
    is_active     = Column(Boolean, default=True)
    created_at    = Column(String, nullable=False)         # ISO8601
    updated_at    = Column(String, nullable=False)

class LLMConfig(Base):
    __tablename__ = 'llm_configs'
    id            = Column(String, primary_key=True)
    name          = Column(String(64), nullable=False)     # 显示名
    provider      = Column(String(32), nullable=False)     # deepseek|openai|ollama|custom
    model_string  = Column(String(128), nullable=False)    # LiteLLM model 字符串
    api_key_enc   = Column(Text)                           # AES-256 加密
    base_url      = Column(String(256))                    # 自定义 endpoint
    is_default    = Column(Boolean, default=False)
    max_tokens    = Column(Integer, default=4096)
    temperature   = Column(Float, default=0.7)
    created_at    = Column(String, nullable=False)

class DBConnection(Base):
    __tablename__ = 'db_connections'
    id              = Column(String, primary_key=True)
    name            = Column(String(64), nullable=False)
    db_type         = Column(String(16), nullable=False)   # mysql|postgresql|mssql|oracle|mongodb
    conn_string_enc = Column(Text, nullable=False)         # AES-256 加密
    blocked_tables  = Column(Text, default='[]')           # JSON 数组
    schema_cache    = Column(Text, default='{}')           # JSON，表结构缓存
    schema_updated  = Column(String)
    is_active       = Column(Boolean, default=True)
    created_at      = Column(String, nullable=False)

class KnowledgeBase(Base):
    __tablename__ = 'knowledge_bases'
    id              = Column(String, primary_key=True)     # 同时作为 Qdrant collection 名
    name            = Column(String(128), nullable=False)
    description     = Column(Text, default='')
    embedding_model = Column(String(64), default='BAAI/bge-m3')
    allowed_roles   = Column(Text, default='["admin","user"]')  # JSON
    doc_count       = Column(Integer, default=0)
    created_at      = Column(String, nullable=False)

class Document(Base):
    __tablename__ = 'documents'
    id          = Column(String, primary_key=True)
    kb_id       = Column(String, nullable=False)           # FK → knowledge_bases.id
    filename    = Column(String(256), nullable=False)
    file_path   = Column(String(512), nullable=False)
    file_hash   = Column(String(64), nullable=False)       # MD5，增量更新检测
    file_size   = Column(Integer)
    status      = Column(String(16), default='pending')    # pending|processing|done|failed
    chunk_count = Column(Integer, default=0)
    error_msg   = Column(Text)
    created_at  = Column(String, nullable=False)
    updated_at  = Column(String, nullable=False)
```

### 3.2 CRUD 函数签名（backend/app/db/crud.py）

> 实现以下所有函数，参数和返回类型必须匹配，供 Agent 层调用。

```python
# User
def get_user(db, user_id: str) -> User | None
def get_user_by_username(db, username: str) -> User | None
def create_user(db, user_id, username, password_hash, role) -> User
def list_users(db) -> list[User]
def update_user_active(db, user_id: str, is_active: bool)

# LLMConfig
def get_default_llm_config(db) -> LLMConfig | None
def get_llm_config(db, config_id: str) -> LLMConfig | None
def list_llm_configs(db) -> list[LLMConfig]
def create_llm_config(db, **kwargs) -> LLMConfig
def set_default_llm(db, config_id: str)               # 先清除其他 is_default，再设置
def delete_llm_config(db, config_id: str)

# DBConnection
def list_db_connections(db) -> list[DBConnection]
def get_db_connection(db, conn_id: str) -> DBConnection | None
def create_db_connection(db, **kwargs) -> DBConnection
def update_schema_cache(db, conn_id: str, schema: dict)
def delete_db_connection(db, conn_id: str)

# KnowledgeBase
def list_kbs(db) -> list[KnowledgeBase]
def get_kb(db, kb_id: str) -> KnowledgeBase | None
def create_kb(db, **kwargs) -> KnowledgeBase
def increment_kb_doc_count(db, kb_id: str)
def decrement_kb_doc_count(db, kb_id: str)
def delete_kb(db, kb_id: str)

# Document
def create_doc(db, **kwargs) -> Document
def get_doc(db, doc_id: str) -> Document | None
def list_docs_by_kb(db, kb_id: str) -> list[Document]
def update_doc_status(db, doc_id: str, status: str, chunk_count=None, error_msg=None)
def delete_doc(db, doc_id: str)
```

---

## 4. 后端核心模块实现规范

### 4.1 main.py

```python
# 必须实现：
# 1. lifespan 上下文管理器：启动时 create_tables() + bge_singleton.load()
# 2. CORSMiddleware：allow_origins=["*"]（生产可配置）
# 3. 路由注册：
#    /api/v1/auth      → auth.router
#    /api/v1/chat      → chat.router
#    /api/v1/knowledge → knowledge.router
#    /api/v1/admin     → admin.router
# 4. GET /api/health → {"status": "ok"}
```

### 4.2 core/security.py

```python
# 必须实现：
# hash_password(password: str) -> str          使用 bcrypt
# verify_password(plain: str, hashed: str) -> bool
# encrypt(plaintext: str) -> str               AES-256-GCM，返回 base64(nonce+ciphertext)
# decrypt(token: str) -> str                   对应解密

# 加密实现细节：
# - 使用 cryptography 库的 AESGCM
# - nonce = os.urandom(12)，每次加密随机
# - 密钥来自 settings.encryption_key（hex decode）
# - 输出格式：base64.b64encode(nonce + ciphertext).decode()
```

### 4.3 core/auth.py

```python
# 必须实现：
# create_access_token(user_id: str, role: str) -> str
#   payload: {"sub": user_id, "role": role, "exp": utcnow + expire_minutes}
#   algorithm: HS256，密钥来自 settings.secret_key

# get_current_user(creds: HTTPAuthorizationCredentials) -> User
#   使用 HTTPBearer() 提取 Token
#   Token 无效或用户不存在 → raise HTTP 401
#   用户 is_active=False → raise HTTP 401

# require_admin(user=Depends(get_current_user)) -> User
#   role != 'admin' → raise HTTP 403
```

### 4.4 models/bge_model.py

```python
# BGESingleton 类：
#   - 全局单例，模块级 bge_singleton = BGESingleton()
#   - async load()：在线程池中加载 BGEM3FlagModel 和 FlagReranker
#   - embed_texts(texts: list[str]) -> list[list[float]]
#       return_dense=True，向量维度 1024
#   - embed_query(query: str) -> list[float]
#   - rerank(query: str, passages: list[str], top_k: int=5) -> list[int]
#       返回排序后的原始索引列表

# Qdrant 操作函数（模块级，非类方法）：
#   VECTOR_DIM = 1024
#   get_qdrant() -> QdrantClient
#   ensure_collection(kb_id: str)      不存在则创建，Distance.COSINE
#   upsert_chunks(kb_id: str, chunks: list[dict])
#     chunks 格式: [{"text": str, "metadata": {"doc_id":str,"filename":str,"page":int,"kb_id":str}}]
#   search_chunks(kb_id, query, top_k=10, doc_id_filter=None) -> list[dict]
#     返回: [{"text","score","filename","page","doc_id"}]
#   delete_doc_chunks(kb_id: str, doc_id: str)
```

### 4.5 models/llm_factory.py

```python
# get_llm(db, config_id: str | None = None) -> ChatLiteLLM
#
# 优先级：
#   1. 传入 config_id → 从 DB 读取对应配置
#   2. config_id=None → 读取 is_default=True 的配置
#   3. DB 无配置 → 使用 .env 中的 DeepSeek 默认值
#
# DeepSeek R1 调用方式（LiteLLM）：
#   model="openai/deepseek-reasoner"
#   api_key=DEEPSEEK_API_KEY
#   api_base="https://api.deepseek.com"
#
# Ollama 调用方式：
#   model="ollama/模型名"
#   api_base="http://ollama:11434"
#
# 所有实例必须设置 streaming=True
```

---

## 5. LangGraph Agent 编排

### 5.1 State 定义

```python
from typing import TypedDict, Annotated
import operator

class RetrievalResult(TypedDict):
    text: str
    source_type: str    # "vector" | "database" | "web"
    source_name: str    # 文件名 | DB连接名 | 网页标题
    source_detail: str  # "第N页" | "MySQL·orders表" | "https://..."
    score: float

class RAGState(TypedDict):
    # 请求输入
    query: str
    session_id: str
    scope_vector: bool
    scope_database: bool
    scope_web: bool
    knowledge_ids: list[str]
    db_connection_ids: list[str]
    llm_config_id: str | None

    # 并行节点追加（operator.add 允许并发写入）
    retrieval_results: Annotated[list[RetrievalResult], operator.add]

    # 精排后结果（rerank_node 覆盖写入）
    reranked_results: list[RetrievalResult]

    # 输出
    error: str | None
```

### 5.2 图结构

```
入口: router
router --条件路由--> [vector, database, web]（按 scope 并行激活）
vector  ──┐
database ─┼──> rerank ──> generate ──> END
web     ──┘
```

### 5.3 路由函数

```python
def _route_fn(state: RAGState) -> list[str]:
    targets = []
    if state["scope_vector"]:   targets.append("vector")
    if state["scope_database"]: targets.append("database")
    if state["scope_web"]:      targets.append("web")
    return targets or ["vector"]  # 至少激活一个
```

### 5.4 各节点职责

| 节点 | 输入 | 写入 State | 边界条件 |
|---|---|---|---|
| router | scope_* | 无（仅路由） | 至少保证一个节点激活 |
| vector_node | query, knowledge_ids | retrieval_results += [...] | scope_vector=False 返回 [] |
| db_node | query, db_connection_ids | retrieval_results += [...] | scope_database=False 返回 [] |
| web_node | query | retrieval_results += [...] | scope_web=False 返回 [] |
| rerank_node | retrieval_results | reranked_results | 结果<=8 时跳过精排直接赋值 |
| generate_node | reranked_results, query | 无（SSE 在 API 层流式输出） | 无结果时输出"未找到相关资料" |

### 5.5 rerank_node 实现约定

```python
MAX_CONTEXT_CHUNKS = 8

async def rerank_node(state: RAGState) -> dict:
    results = state.get("retrieval_results", [])
    if len(results) <= MAX_CONTEXT_CHUNKS:
        return {"reranked_results": results}
    query = state["query"]
    passages = [r["text"] for r in results]
    top_indices = bge_singleton.rerank(query, passages, top_k=MAX_CONTEXT_CHUNKS)
    return {"reranked_results": [results[i] for i in top_indices]}
```

### 5.6 generate_node System Prompt（不可修改）

```
你是企业智能助手。请根据以下检索到的资料回答用户问题。
要求：
1. 只使用提供的资料，不要凭空捏造
2. 如资料不足以回答，明确说明"根据现有资料无法回答此问题"
3. 回答使用中文，结构清晰，适当使用列表或分段
4. 不需要在正文中重复来源标记，来源信息将在界面上单独展示
```

---

## 6. 文档处理流水线

### 6.1 loader.py 统一返回格式

```python
# 所有格式统一返回：
# list[{"text": str, "page": int, "filename": str}]
# page 从 1 开始；不支持分页的格式设 page=1

# 支持格式和依赖库：
# .pdf  → pdfplumber
# .docx → python-docx
# .xlsx / .xls → openpyxl
# .txt / .md  → open() 直接读取
# 其他格式 → raise ValueError(f"不支持的文件格式: {suffix}")
```

### 6.2 splitter.py 分块参数

```python
# 固定使用以下参数，不可修改：
ZH_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "……", "—", "，", " ", ""]

splitter = RecursiveCharacterTextSplitter(
    separators=ZH_SEPARATORS,
    chunk_size=settings.chunk_size,       # 默认 512
    chunk_overlap=settings.chunk_overlap, # 默认 64
    length_function=len,
)
# 过滤空块：chunk.strip() 为空则丢弃
```

### 6.3 embedder.py 流水线函数

```python
async def process_document(doc_id: str, kb_id: str, file_path: str, db):
    """
    执行顺序：
    1. crud.update_doc_status(db, doc_id, "processing")
    2. pages = load_document(file_path)
    3. chunks = split_pages(pages)
    4. 为每个 chunk 附加 metadata = {doc_id, filename, page, kb_id}
    5. upsert_chunks(kb_id, chunks)
    6. crud.update_doc_status(db, doc_id, "done", chunk_count=len(chunks))
    7. crud.increment_kb_doc_count(db, kb_id)
    异常时：crud.update_doc_status(db, doc_id, "failed", error_msg=str(e))
    """

async def delete_document(doc_id: str, kb_id: str, db):
    """
    执行顺序：
    1. delete_doc_chunks(kb_id, doc_id)   # 清理 Qdrant
    2. crud.delete_doc(db, doc_id)
    3. crud.decrement_kb_doc_count(db, kb_id)
    """
```

---

## 7. API 接口规范

### 7.1 认证接口

```
POST /api/v1/auth/login
请求: {"username": str, "password": str}
响应: {"access_token": str, "token_type": "bearer", "user_id": str, "username": str, "role": str}
错误: 401 用户名或密码错误 | 403 账号已被禁用

GET /api/v1/auth/me
Header: Authorization: Bearer <token>
响应: {"user_id": str, "username": str, "role": str}
```

### 7.2 对话 SSE 接口（核心）

```
POST /api/v1/chat/stream
Header: Authorization: Bearer <token>
Content-Type: application/json

请求体：
{
  "session_id": "uuid-string",
  "query": "用户问题",
  "scope_vector": true,
  "scope_database": false,
  "scope_web": false,
  "knowledge_ids": [],        // 空数组 = 全部知识库
  "db_connection_ids": [],    // 空数组 = 全部已激活DB连接
  "llm_config_id": null       // null = 使用默认模型
}

响应：Content-Type: text/event-stream
每行格式：data: {JSON}\n\n

事件类型（按顺序）：
1. {"event": "source", "data": RetrievalResult}    // 每个来源一条，先于 token 发送
2. {"event": "token",  "data": "文字片段"}          // 逐 token 流式
3. {"event": "done",   "data": {"total_tokens": N}} // 生成完成
4. {"event": "error",  "data": "错误信息"}           // 异常时发送

来源数据结构（RetrievalResult）：
{
  "text": "原始文本片段",
  "source_type": "vector|database|web",
  "source_name": "文件名 或 DB连接名 或 网页标题",
  "source_detail": "第3页 或 MySQL·orders表 或 https://...",
  "score": 0.85
}
```

### 7.3 知识库接口

```
GET    /api/v1/knowledge/list
响应: [{"id","name","description","doc_count","created_at"}]

POST   /api/v1/knowledge/create
请求: {"name": str, "description": str}
响应: KnowledgeBase 对象

POST   /api/v1/knowledge/{kb_id}/upload
Content-Type: multipart/form-data
字段: file (UploadFile)
响应: {"doc_id": str, "filename": str, "status": "pending"}
副作用: 后台异步执行 process_document()

GET    /api/v1/knowledge/{kb_id}/documents
响应: [{"id","filename","status","chunk_count","file_size","created_at","error_msg"}]

DELETE /api/v1/knowledge/{kb_id}/documents/{doc_id}
副作用: 同步执行 delete_document()

DELETE /api/v1/knowledge/{kb_id}
副作用: 删除所有文档向量 + Qdrant collection
```

### 7.4 管理接口（需 admin 角色）

```
# 模型配置
GET    /api/v1/admin/llm-configs
POST   /api/v1/admin/llm-configs         请求体: {name,provider,model_string,api_key,base_url,max_tokens,temperature}
PUT    /api/v1/admin/llm-configs/{id}/set-default
DELETE /api/v1/admin/llm-configs/{id}
POST   /api/v1/admin/llm-configs/test    请求体: {config_id} 发送测试消息验证连通性

# 数据库连接
GET    /api/v1/admin/db-connections
POST   /api/v1/admin/db-connections
  请求体: {name, db_type, host, port, user, password, database, blocked_tables:[]}
  实现: build_conn_string() → encrypt() → 存储
DELETE /api/v1/admin/db-connections/{id}
POST   /api/v1/admin/db-connections/{id}/test   测试连接可用性
POST   /api/v1/admin/db-connections/{id}/refresh-schema  重新内省表结构

# 用户管理
GET    /api/v1/admin/users
POST   /api/v1/admin/users               请求体: {username, password, role}
PUT    /api/v1/admin/users/{id}/toggle-active
```

---

## 8. 前端实现规范

### 8.1 技术约束

```
框架: Vue 3 Composition API（<script setup> 语法）
状态: Pinia
路由: Vue Router 4（history 模式）
HTTP: Axios
SSE:  原生 fetch + ReadableStream（禁止使用 EventSource，不支持 POST + Authorization Header）
Markdown: markdown-it + highlight.js
样式: 原生 CSS 或 CSS Modules，禁止引入 Element Plus / Ant Design 等组件库
```

### 8.2 路由配置

```javascript
// router/index.js
const routes = [
  { path: '/login',          component: Login,         meta: { requiresAuth: false } },
  { path: '/',               component: Chat,           meta: { requiresAuth: true } },
  { path: '/knowledge',      component: KnowledgeBase,  meta: { requiresAuth: true } },
  { path: '/admin',          component: Admin,           meta: { requiresAuth: true, requiresAdmin: true } },
]

// 路由守卫：未登录跳 /login；非 admin 访问 /admin 跳首页
```

### 8.3 SSEClient（utils/sse.js）完整实现

```javascript
export class SSEClient {
  constructor(url, body, token) {
    this.url = url
    this.body = body
    this.token = token
    this.handlers = {}
    this.controller = new AbortController()
  }

  on(event, handler) { this.handlers[event] = handler; return this }
  abort() { this.controller.abort() }

  async start() {
    const resp = await fetch(this.url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${this.token}`,
      },
      body: JSON.stringify(this.body),
      signal: this.controller.signal,
    })
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}))
      this.handlers['error']?.(err.detail || '请求失败')
      return
    }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n\n')
      buffer = lines.pop()
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const msg = JSON.parse(line.slice(6))
          this.handlers[msg.event]?.(msg.data)
        } catch {}
      }
    }
  }
}
```

### 8.4 Chat Store（stores/chat.js）必须实现的方法

```javascript
// newSession() -> string (session_id)
// currentSession() -> Session | undefined
// addMessage(msg: {role, content, sources?}) -> string (msg_id)
// appendToken(msgId: string, token: string)    // 追加到 msg.content
// addSource(msgId: string, source: object)     // 去重后 push 到 msg.sources
```

### 8.5 ChatInput.vue 检索范围选择器规范

```
UI 结构：
┌─────────────────────────────────────────────────┐
│ [✓] 📚 知识库  [下拉多选：全部/具体知识库名]        │
│ [ ] 🗄️ 数据库  [下拉多选：全部/具体DB连接名]        │
│ [ ] 🌐 联网搜索                                   │
├─────────────────────────────────────────────────┤
│ [textarea: 多行输入，Enter发送，Shift+Enter换行]   │
│                                    [发送] / [停止] │
└─────────────────────────────────────────────────┘

行为规范：
- 三个 scope 至少一个勾选（知识库默认勾选）
- 发送后 textarea 清空，isStreaming=true
- isStreaming=true 时显示停止按钮，点击调用 sse.abort()
- 发送时构建 body 传入当前选中的 knowledge_ids 和 db_connection_ids
```

### 8.6 SourceCard.vue 展示规范

```
来源卡片格式：
┌────────────────────────────────────────┐
│ [图标] [source_name]  [source_detail]  ▼ │  ← 点击展开
├────────────────────────────────────────┤
│ 原始文本片段（预格式化）                    │  ← 展开后显示
│ [🔗 查看原文]（仅 web 类型显示）           │
└────────────────────────────────────────┘

图标映射：
  vector   → 📄
  database → 🗄️
  web      → 🌐

去重规则：source_name + source_detail 相同则不重复显示
```

### 8.7 markdown.js 配置

```javascript
// utils/markdown.js
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'

const md = new MarkdownIt({
  html: false,        // 禁止原始 HTML（XSS 防护）
  linkify: true,
  typographer: true,
  highlight(str, lang) {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(str, { language: lang }).value
    }
    return hljs.highlightAuto(str).value
  }
})

export function renderMarkdown(text) {
  return md.render(text)
}

// MessageBubble.vue 中使用 v-html 渲染时必须用 DOMPurify 净化：
// import DOMPurify from 'dompurify'
// const renderedContent = computed(() => DOMPurify.sanitize(renderMarkdown(props.message.content)))
```

---

## 9. Docker 部署配置

### 9.1 docker-compose.yml（完整内容）

```yaml
version: '3.9'

services:
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
    depends_on:
      - backend
      - frontend
    restart: always

  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    expose:
      - "3000"
    restart: always

  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    expose:
      - "8000"
    volumes:
      - ./data:/app/data
      - bge_cache:/root/.cache/huggingface
    env_file:
      - .env
    depends_on:
      - qdrant
    restart: always

  qdrant:
    image: qdrant/qdrant:v1.9.3
    expose:
      - "6333"
    volumes:
      - qdrant_data:/qdrant/storage
    restart: always

volumes:
  qdrant_data:
  bge_cache:
```

### 9.2 backend/Dockerfile

```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev curl unixodbc-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/data/uploads

ENTRYPOINT ["sh", "-c", "python scripts/init_db.py && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
```

### 9.3 frontend/Dockerfile

```dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json .
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
RUN printf 'server {\n  listen 3000;\n  location / {\n    root /usr/share/nginx/html;\n    try_files $uri $uri/ /index.html;\n  }\n}\n' \
    > /etc/nginx/conf.d/default.conf
EXPOSE 3000
```

### 9.4 nginx/nginx.conf

```nginx
events { worker_connections 1024; }

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;
    sendfile on;
    gzip on;
    gzip_types text/plain application/json application/javascript text/css;

    upstream backend  { server backend:8000; }
    upstream frontend { server frontend:3000; }

    server {
        listen 80;
        server_name _;

        location /api/ {
            proxy_pass         http://backend;
            proxy_http_version 1.1;
            proxy_set_header   Host $host;
            proxy_set_header   X-Real-IP $remote_addr;
            # SSE 必须：禁用缓冲
            proxy_buffering    off;
            proxy_cache        off;
            proxy_read_timeout 300s;
            proxy_set_header   Connection '';
            chunked_transfer_encoding on;
        }

        location / {
            proxy_pass http://frontend;
        }
    }
}
```

---

## 10. 依赖包版本锁定

### 10.1 backend/requirements.txt

```
# Web 框架
fastapi==0.111.0
uvicorn[standard]==0.29.0
python-multipart==0.0.9
pydantic-settings==2.2.1

# 系统数据库
sqlalchemy==2.0.30
aiosqlite==0.20.0

# 业务数据库驱动
pymysql==1.1.1
psycopg2-binary==2.9.9
pyodbc==5.1.0
oracledb==2.2.0
pymongo==4.7.3

# LangChain / LangGraph
langchain==0.2.5
langchain-community==0.2.5
langgraph==0.1.19
langchain-openai==0.1.13

# 模型
litellm==1.53.1
FlagEmbedding==1.2.10
torch==2.3.0
numpy==1.26.4
pyarrow==16.1.0
datasets==2.19.2

# 向量库
qdrant-client==1.9.1

# 文档解析
pdfplumber==0.11.1
python-docx==1.1.2
openpyxl==3.1.4

# 联网搜索
tavily-python==0.3.3

# 认证 & 加密
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
cryptography==42.0.8

# 工具
httpx==0.27.0
python-dotenv==1.0.1
```

### 10.2 frontend/package.json

```json
{
  "name": "rag-frontend",
  "version": "1.0.0",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "vue": "^3.4.27",
    "vue-router": "^4.3.2",
    "pinia": "^2.1.7",
    "axios": "^1.7.2",
    "markdown-it": "^14.1.0",
    "highlight.js": "^11.9.0",
    "dompurify": "^3.1.5",
    "uuid": "^9.0.1"
  },
  "devDependencies": {
    "@vitejs/plugin-vue": "^5.0.5",
    "vite": "^5.3.1"
  }
}
```

### 10.3 vite.config.js

```javascript
import { defineConfig } from 'vite'
import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) }
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  }
})
```

---

## 11. 实现顺序与验收标准

> **Trae 必须按以下顺序实现，每个阶段完成后执行验收命令确认通过再进入下一阶段。**

### Phase 1 — 工程骨架（优先完成）

**实现文件：**
- `.env.example`
- `docker-compose.yml`
- `backend/requirements.txt`
- `backend/Dockerfile`
- `backend/app/core/config.py`
- `backend/app/db/database.py`
- `backend/app/db/models.py`
- `backend/app/db/crud.py`
- `backend/app/core/security.py`
- `backend/app/core/auth.py`
- `backend/app/core/deps.py`
- `backend/app/api/auth.py`
- `backend/app/main.py`
- `backend/scripts/init_db.py`
- `nginx/nginx.conf`

**验收命令：**
```bash
# 1. 复制环境变量
cp .env.example .env
# 填写 SECRET_KEY, ENCRYPTION_KEY（随机hex），DEEPSEEK_API_KEY

# 2. 仅启动后端进行测试
cd backend && pip install -r requirements.txt
python scripts/init_db.py
uvicorn app.main:app --port 8000 &

# 3. 验收
curl http://localhost:8000/api/health
# 期望: {"status": "ok"}

curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"Admin@ChangeMe123"}'
# 期望: 返回包含 access_token 的 JSON
```

---

### Phase 2 — 数据接入层

**实现文件：**
- `backend/app/models/bge_model.py`（BGE + Qdrant）
- `backend/app/pipeline/loader.py`
- `backend/app/pipeline/splitter.py`
- `backend/app/pipeline/embedder.py`
- `backend/app/connectors/base.py`
- `backend/app/connectors/sql_connector.py`
- `backend/app/connectors/mongo_connector.py`
- `backend/app/api/knowledge.py`
- `backend/app/api/admin.py`（DB连接部分）

**验收命令：**
```bash
# 启动 Qdrant
docker compose up qdrant -d

# 测试 BGE Embedding
python -c "
from app.models.bge_model import bge_singleton
import asyncio
asyncio.run(bge_singleton.load())
v = bge_singleton.embed_query('测试文本')
assert len(v) == 1024, f'向量维度错误: {len(v)}'
print('✓ BGE-M3 Embedding 维度正确:', len(v))
"

# 测试文档上传接口
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"Admin@ChangeMe123"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 创建知识库
curl -X POST http://localhost:8000/api/v1/knowledge/create \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"测试知识库","description":"验收测试"}'
# 期望: 返回包含 id 的知识库对象
```

---

### Phase 3 — Agent 编排

**实现文件：**
- `backend/app/models/llm_factory.py`
- `backend/app/agents/graph.py`
- `backend/app/agents/router_node.py`
- `backend/app/agents/vector_node.py`
- `backend/app/agents/db_node.py`
- `backend/app/agents/web_node.py`
- `backend/app/agents/rerank_node.py`
- `backend/app/agents/generate_node.py`
- `backend/app/api/chat.py`

**验收命令：**
```bash
# 测试 SSE 流式输出
curl -N -X POST http://localhost:8000/api/v1/chat/stream \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-001",
    "query": "你好，请介绍一下你自己",
    "scope_vector": true,
    "scope_database": false,
    "scope_web": false
  }'
# 期望: 看到逐行输出 data: {"event":"token","data":"..."} 
# 最后一行: data: {"event":"done","data":{"total_tokens":N}}
```

---

### Phase 4 — 前端

**实现文件：**
- `frontend/package.json`
- `frontend/vite.config.js`
- `frontend/index.html`
- `frontend/src/main.js`
- `frontend/src/App.vue`
- `frontend/src/router/index.js`
- `frontend/src/stores/auth.js`
- `frontend/src/stores/chat.js`
- `frontend/src/stores/config.js`
- `frontend/src/api/http.js`
- `frontend/src/api/auth.js`
- `frontend/src/api/chat.js`
- `frontend/src/api/knowledge.js`
- `frontend/src/api/admin.js`
- `frontend/src/utils/sse.js`
- `frontend/src/utils/markdown.js`
- `frontend/src/views/Login.vue`
- `frontend/src/views/Chat.vue`
- `frontend/src/views/KnowledgeBase.vue`
- `frontend/src/views/Admin.vue`
- `frontend/src/components/ChatSidebar.vue`
- `frontend/src/components/ChatInput.vue`
- `frontend/src/components/MessageBubble.vue`
- `frontend/src/components/SourceCard.vue`
- `frontend/src/components/ModelSelector.vue`

**验收步骤：**
```bash
cd frontend && npm install && npm run dev
# 浏览器打开 http://localhost:5173
# 验收清单：
# ✓ 可以用 admin 账号登录
# ✓ 对话框可以发送消息并看到流式输出
# ✓ 来源卡片显示在回答下方，可点击展开
# ✓ 检索范围选择器三个选项可以勾选
# ✓ 知识库页面可以上传 PDF 并看到状态变为 done
# ✓ 未登录访问 / 自动跳转到 /login
```

---

### Phase 5 — 完整部署验收

```bash
# 一键部署
docker compose up -d --build

# 等待约 2 分钟（BGE 模型加载）
docker compose logs backend -f
# 等待看到: [BGE] 加载 Embedding 模型... 完成

# 全链路验收
curl http://localhost/api/health
# 期望: {"status": "ok"}

# 浏览器访问 http://<服务器IP>
# 完整流程：登录 → 创建知识库 → 上传文档 → 提问 → 看到来源标注答案
```

---

## 12. 禁止事项

> 以下行为会导致系统不符合规格，禁止在实现中出现：

1. **禁止**在代码中硬编码任何 API Key、密码、密钥
2. **禁止**使用 EventSource 实现 SSE 客户端（不支持带 Authorization Header 的 POST）
3. **禁止**引入 Element Plus、Ant Design Vue、Quasar 等 UI 组件库
4. **禁止**修改 §5.6 中定义的 System Prompt 内容
5. **禁止**在 Agent 层跨节点共享可变状态（通过 RAGState 传递）
6. **禁止**SQL Agent 执行非 SELECT 语句（须在 db_node 中校验）
7. **禁止**使用非列出版本的依赖包（可升级补丁版本，不可升级大/小版本）
8. **禁止** `v-html` 渲染未经 DOMPurify 净化的内容
9. **禁止**将 SQLite 文件存储在容器内（必须挂载到 `./data` 卷）
10. **禁止**Qdrant 端口（6333）对外暴露（只能通过 backend 内部访问）

---

*CLAUDE.md — 版本 v2.0 · 企业级增强 RAG 系统实现契约*
