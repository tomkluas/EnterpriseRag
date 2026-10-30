import json
from datetime import datetime, timezone
from app.db.models import User, LLMConfig, DBConnection, KnowledgeBase, Document


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── User ──────────────────────────────────────────────────────────────────────

def get_user(db, user_id: str) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def get_user_by_username(db, username: str) -> User | None:
    return db.query(User).filter(User.username == username).first()


def create_user(db, user_id, username, password_hash, role) -> User:
    now = _now()
    user = User(id=user_id, username=username, password_hash=password_hash,
                role=role, created_at=now, updated_at=now)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def list_users(db) -> list[User]:
    return db.query(User).all()


def update_user_active(db, user_id: str, is_active: bool):
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.is_active = is_active
        user.updated_at = _now()
        db.commit()


# ── LLMConfig ─────────────────────────────────────────────────────────────────

def get_default_llm_config(db) -> LLMConfig | None:
    return db.query(LLMConfig).filter(LLMConfig.is_default == True).first()


def get_llm_config(db, config_id: str) -> LLMConfig | None:
    return db.query(LLMConfig).filter(LLMConfig.id == config_id).first()


def list_llm_configs(db) -> list[LLMConfig]:
    return db.query(LLMConfig).all()


def create_llm_config(db, **kwargs) -> LLMConfig:
    kwargs.setdefault("created_at", _now())
    config = LLMConfig(**kwargs)
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def set_default_llm(db, config_id: str):
    db.query(LLMConfig).update({"is_default": False})
    db.query(LLMConfig).filter(LLMConfig.id == config_id).update({"is_default": True})
    db.commit()


def delete_llm_config(db, config_id: str):
    db.query(LLMConfig).filter(LLMConfig.id == config_id).delete()
    db.commit()


# ── DBConnection ──────────────────────────────────────────────────────────────

def list_db_connections(db) -> list[DBConnection]:
    return db.query(DBConnection).all()


def get_db_connection(db, conn_id: str) -> DBConnection | None:
    return db.query(DBConnection).filter(DBConnection.id == conn_id).first()


def create_db_connection(db, **kwargs) -> DBConnection:
    kwargs.setdefault("created_at", _now())
    conn = DBConnection(**kwargs)
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return conn


def update_schema_cache(db, conn_id: str, schema: dict):
    db.query(DBConnection).filter(DBConnection.id == conn_id).update({
        "schema_cache": json.dumps(schema, ensure_ascii=False),
        "schema_updated": _now(),
    })
    db.commit()


def delete_db_connection(db, conn_id: str):
    db.query(DBConnection).filter(DBConnection.id == conn_id).delete()
    db.commit()


# ── KnowledgeBase ─────────────────────────────────────────────────────────────

def list_kbs(db) -> list[KnowledgeBase]:
    return db.query(KnowledgeBase).all()


def get_kb(db, kb_id: str) -> KnowledgeBase | None:
    return db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()


def create_kb(db, **kwargs) -> KnowledgeBase:
    kwargs.setdefault("created_at", _now())
    kb = KnowledgeBase(**kwargs)
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return kb


def increment_kb_doc_count(db, kb_id: str):
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
    if kb:
        kb.doc_count = (kb.doc_count or 0) + 1
        db.commit()


def decrement_kb_doc_count(db, kb_id: str):
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
    if kb and kb.doc_count > 0:
        kb.doc_count -= 1
        db.commit()


def delete_kb(db, kb_id: str):
    db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).delete()
    db.commit()


# ── Document ──────────────────────────────────────────────────────────────────

def create_doc(db, **kwargs) -> Document:
    now = _now()
    kwargs.setdefault("created_at", now)
    kwargs.setdefault("updated_at", now)
    doc = Document(**kwargs)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def get_doc(db, doc_id: str) -> Document | None:
    return db.query(Document).filter(Document.id == doc_id).first()


def list_docs_by_kb(db, kb_id: str) -> list[Document]:
    return db.query(Document).filter(Document.kb_id == kb_id).all()


def update_doc_status(db, doc_id: str, status: str, chunk_count=None, error_msg=None):
    updates = {"status": status, "updated_at": _now()}
    if chunk_count is not None:
        updates["chunk_count"] = chunk_count
    if error_msg is not None:
        updates["error_msg"] = error_msg
    db.query(Document).filter(Document.id == doc_id).update(updates)
    db.commit()


def delete_doc(db, doc_id: str):
    db.query(Document).filter(Document.id == doc_id).delete()
    db.commit()


# ── 系统级配置（存储在 config.json，非 SQL 表）─────────────────────────────────
# 注：embedding/search 配置由用户在 Setup 向导或设置页填写，
# 与 LLMConfig/DBConnection 不同，这两类是"系统单一全局配置"而非多条记录，
# 因此持久化在 data/config.json 而非数据库表中。db 参数保留是为了调用方接口
# 一致（未来若迁移为可多选配置，可平滑切换到数据库存储）。

def get_embedding_config(db) -> dict | None:
    from app.core.config import config_manager
    return config_manager.get("embedding")


def update_embedding_config(db, provider: str, model: str, api_key_enc: str):
    from app.core.config import config_manager
    config_manager.update(embedding={
        "provider": provider, "model": model, "api_key_enc": api_key_enc,
    })


def get_search_config(db) -> dict | None:
    from app.core.config import config_manager
    return config_manager.get("search")


def update_search_config(db, provider: str, api_key_enc: str):
    from app.core.config import config_manager
    config_manager.update(search={"provider": provider, "api_key_enc": api_key_enc})
