from sqlalchemy import Column, String, Integer, Float, Text, Boolean
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class User(Base):
    __tablename__ = 'users'
    id = Column(String, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)
    role = Column(String(16), default='user')
    is_active = Column(Boolean, default=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)


class LLMConfig(Base):
    __tablename__ = 'llm_configs'
    id = Column(String, primary_key=True)
    name = Column(String(64), nullable=False)
    provider = Column(String(32), nullable=False)
    model_string = Column(String(128), nullable=False)
    api_key_enc = Column(Text)
    base_url = Column(String(256))
    is_default = Column(Boolean, default=False)
    max_tokens = Column(Integer, default=4096)
    temperature = Column(Float, default=0.7)
    created_at = Column(String, nullable=False)


class DBConnection(Base):
    __tablename__ = 'db_connections'
    id = Column(String, primary_key=True)
    name = Column(String(64), nullable=False)
    db_type = Column(String(16), nullable=False)
    conn_string_enc = Column(Text, nullable=False)
    blocked_tables = Column(Text, default='[]')
    schema_cache = Column(Text, default='{}')
    schema_updated = Column(String)
    is_active = Column(Boolean, default=True)
    created_at = Column(String, nullable=False)


class KnowledgeBase(Base):
    __tablename__ = 'knowledge_bases'
    id = Column(String, primary_key=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, default='')
    embedding_model = Column(String(64), default='BAAI/bge-m3')
    allowed_roles = Column(Text, default='["admin","user"]')
    doc_count = Column(Integer, default=0)
    created_at = Column(String, nullable=False)


class Document(Base):
    __tablename__ = 'documents'
    id = Column(String, primary_key=True)
    kb_id = Column(String, nullable=False)
    filename = Column(String(256), nullable=False)
    file_path = Column(String(512), nullable=False)
    file_hash = Column(String(64), nullable=False)
    file_size = Column(Integer)
    status = Column(String(16), default='pending')
    chunk_count = Column(Integer, default=0)
    error_msg = Column(Text)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)