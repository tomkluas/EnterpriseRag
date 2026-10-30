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
    provider      = Column(String(32), nullable=False)     # zhipu|qwen|deepseek|doubao|moonshot
    model_string  = Column(String(128), nullable=False)    # 实际模型名，如 glm-4-plus
    api_key_enc   = Column(Text)                           # AES-256 加密
    base_url      = Column(String(256))                    # 自定义 endpoint（留空用服务商默认值）
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
    id              = Column(String, primary_key=True)     # 同时作为 Chroma collection 名
    name            = Column(String(128), nullable=False)
    description     = Column(Text, default='')
    embedding_model = Column(String(64), default='embedding-3')
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
