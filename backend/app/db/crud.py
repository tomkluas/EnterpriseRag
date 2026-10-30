import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import User, LLMConfig, DBConnection, KnowledgeBase, Document


def _now() -> str:
    return datetime.utcnow().isoformat()


# User
def get_user(db: Session, user_id: str) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.query(User).filter(User.username == username).first()


def create_user(db: Session, user_id: str, username: str, password_hash: str, role: str) -> User:
    now = _now()
    user = User(
        id=user_id,
        username=username,
        password_hash=password_hash,
        role=role,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def list_users(db: Session) -> list[User]:
    return db.query(User).all()


def update_user_active(db: Session, user_id: str, is_active: bool) -> User | None:
    user = get_user(db, user_id)
    if user:
        user.is_active = is_active
        user.updated_at = _now()
        db.commit()
        db.refresh(user)
    return user


# LLMConfig
def get_default_llm_config(db: Session) -> LLMConfig | None:
    return db.query(LLMConfig).filter(LLMConfig.is_default == True).first()


def get_llm_config(db: Session, config_id: str) -> LLMConfig | None:
    return db.query(LLMConfig).filter(LLMConfig.id == config_id).first()


def list_llm_configs(db: Session) -> list[LLMConfig]:
    return db.query(LLMConfig).all()


def create_llm_config(db: Session, **kwargs: Any) -> LLMConfig:
    config = LLMConfig(
        id=str(uuid.uuid4()),
        created_at=_now(),
        **kwargs,
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def set_default_llm(db: Session, config_id: str) -> None:
    db.query(LLMConfig).update({LLMConfig.is_default: False})
    config = get_llm_config(db, config_id)
    if config:
        config.is_default = True
        db.commit()


def delete_llm_config(db: Session, config_id: str) -> None:
    config = get_llm_config(db, config_id)
    if config:
        db.delete(config)
        db.commit()


# DBConnection
def list_db_connections(db: Session) -> list[DBConnection]:
    return db.query(DBConnection).all()


def get_db_connection(db: Session, conn_id: str) -> DBConnection | None:
    return db.query(DBConnection).filter(DBConnection.id == conn_id).first()


def create_db_connection(db: Session, **kwargs: Any) -> DBConnection:
    conn = DBConnection(
        id=str(uuid.uuid4()),
        created_at=_now(),
        **kwargs,
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return conn


def update_schema_cache(db: Session, conn_id: str, schema: dict) -> None:
    import json
    conn = get_db_connection(db, conn_id)
    if conn:
        conn.schema_cache = json.dumps(schema)
        conn.schema_updated = _now()
        db.commit()


def delete_db_connection(db: Session, conn_id: str) -> None:
    conn = get_db_connection(db, conn_id)
    if conn:
        db.delete(conn)
        db.commit()


# KnowledgeBase
def list_kbs(db: Session) -> list[KnowledgeBase]:
    return db.query(KnowledgeBase).all()


def get_kb(db: Session, kb_id: str) -> KnowledgeBase | None:
    return db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()


def create_kb(db: Session, **kwargs: Any) -> KnowledgeBase:
    kb = KnowledgeBase(
        id=str(uuid.uuid4()),
        created_at=_now(),
        **kwargs,
    )
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return kb


def increment_kb_doc_count(db: Session, kb_id: str) -> None:
    db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).update(
        {KnowledgeBase.doc_count: KnowledgeBase.doc_count + 1}, synchronize_session=False
    )
    db.commit()


def decrement_kb_doc_count(db: Session, kb_id: str) -> None:
    db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id, KnowledgeBase.doc_count > 0).update(
        {KnowledgeBase.doc_count: KnowledgeBase.doc_count - 1}, synchronize_session=False
    )
    db.commit()


def delete_kb(db: Session, kb_id: str) -> bool:
    kb = get_kb(db, kb_id)
    if kb:
        db.query(Document).filter(Document.kb_id == kb_id).delete(synchronize_session=False)
        db.delete(kb)
        db.commit()
        return True
    return False


# Document
def create_doc(db: Session, **kwargs: Any) -> Document:
    now = _now()
    doc = Document(
        id=str(uuid.uuid4()),
        created_at=now,
        updated_at=now,
        **kwargs,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def get_doc(db: Session, doc_id: str) -> Document | None:
    return db.query(Document).filter(Document.id == doc_id).first()


def list_docs_by_kb(db: Session, kb_id: str) -> list[Document]:
    return db.query(Document).filter(Document.kb_id == kb_id).all()


def update_doc_status(db: Session, doc_id: str, status: str, chunk_count: int | None = None, error_msg: str | None = None) -> Document | None:
    doc = get_doc(db, doc_id)
    if doc:
        doc.status = status
        if chunk_count is not None:
            doc.chunk_count = chunk_count
        if error_msg is not None:
            doc.error_msg = error_msg
        doc.updated_at = _now()
        db.commit()
        db.refresh(doc)
    return doc


def delete_doc(db: Session, doc_id: str) -> bool:
    doc = get_doc(db, doc_id)
    if doc:
        db.delete(doc)
        db.commit()
        return True
    return False
