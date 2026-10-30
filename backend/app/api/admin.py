import json
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.core.deps import get_db
from app.core.auth import require_admin, get_current_user
from app.core.security import encrypt, decrypt, hash_password
from app.db import crud
from app.db.models import User
from app.models.embedding_client import resolve_embedding_model

router = APIRouter()


# ── LLM Configs ──────────────────────────────────────────────────────────────

class LLMConfigCreate(BaseModel):
    name: str
    provider: str
    model_string: str = ""        # 留空则使用服务商推荐默认模型
    api_key: str = ""
    max_tokens: int = 4096
    temperature: float = 0.7


@router.get("/llm-configs")
def list_llm_configs(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    configs = crud.list_llm_configs(db)
    return [
        {
            "id": c.id, "name": c.name, "provider": c.provider,
            "model_string": c.model_string, "base_url": c.base_url,
            "is_default": c.is_default, "max_tokens": c.max_tokens,
            "temperature": c.temperature, "created_at": c.created_at,
        }
        for c in configs
    ]


@router.post("/llm-configs")
def create_llm_config(req: LLMConfigCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    from app.models.llm_factory import PROVIDER_DEFAULT_MODEL, PROVIDER_BASE_URL
    if req.provider not in PROVIDER_DEFAULT_MODEL:
        raise HTTPException(status_code=400, detail=f"不支持的服务商: {req.provider}")

    model_string = req.model_string.strip() or PROVIDER_DEFAULT_MODEL[req.provider]

    config = crud.create_llm_config(
        db,
        id=str(uuid.uuid4()),
        name=req.name,
        provider=req.provider,
        model_string=model_string,
        api_key_enc=encrypt(req.api_key) if req.api_key else None,
        base_url=PROVIDER_BASE_URL.get(req.provider),
        max_tokens=req.max_tokens,
        temperature=req.temperature,
    )
    return config


@router.put("/llm-configs/{config_id}/set-default")
def set_default_llm(config_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    if not crud.get_llm_config(db, config_id):
        raise HTTPException(status_code=404, detail="配置不存在")
    crud.set_default_llm(db, config_id)
    return {"ok": True}


@router.delete("/llm-configs/{config_id}")
def delete_llm_config(config_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    crud.delete_llm_config(db, config_id)
    return {"ok": True}


@router.post("/llm-configs/test")
async def test_llm_config(
    body: dict,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    from app.models.llm_factory import get_llm
    from langchain_core.messages import HumanMessage
    try:
        llm = get_llm(db, body.get("config_id"))
        response = await llm.ainvoke([HumanMessage(content="你好，请回复'连接成功'")])
        return {"ok": True, "response": response.content}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── DB Connections ────────────────────────────────────────────────────────────

class DBConnCreate(BaseModel):
    name: str
    db_type: str
    host: str
    port: int
    user: str
    password: str
    database: str
    blocked_tables: list[str] = []


@router.get("/db-connections")
def list_db_connections(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    conns = crud.list_db_connections(db)
    return [
        {
            "id": c.id, "name": c.name, "db_type": c.db_type,
            "blocked_tables": json.loads(c.blocked_tables or "[]"),
            "schema_updated": c.schema_updated, "is_active": c.is_active,
            "created_at": c.created_at,
        }
        for c in conns
    ]


@router.post("/db-connections")
def create_db_connection(req: DBConnCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    if req.db_type == "mongodb":
        conn_string = f"mongodb://{req.user}:{req.password}@{req.host}:{req.port}/{req.database}"
    else:
        from app.connectors.sql_connector import build_conn_string
        conn_string = build_conn_string(req.db_type, req.host, req.port, req.user, req.password, req.database)

    conn = crud.create_db_connection(
        db,
        id=str(uuid.uuid4()),
        name=req.name,
        db_type=req.db_type,
        conn_string_enc=encrypt(conn_string),
        blocked_tables=json.dumps(req.blocked_tables),
    )
    return {"id": conn.id, "name": conn.name, "db_type": conn.db_type}


@router.delete("/db-connections/{conn_id}")
def delete_db_connection(conn_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    crud.delete_db_connection(db, conn_id)
    return {"ok": True}


@router.post("/db-connections/{conn_id}/test")
def test_db_connection(conn_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    conn_record = crud.get_db_connection(db, conn_id)
    if not conn_record:
        raise HTTPException(status_code=404, detail="连接不存在")
    conn_string = decrypt(conn_record.conn_string_enc)
    blocked = json.loads(conn_record.blocked_tables or "[]")
    try:
        if conn_record.db_type == "mongodb":
            from app.connectors.mongo_connector import MongoConnector
            ok = MongoConnector(conn_string, blocked).test_connection()
        else:
            from app.connectors.sql_connector import SQLConnector
            ok = SQLConnector(conn_string, blocked).test_connection()
        return {"ok": ok}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/db-connections/{conn_id}/refresh-schema")
def refresh_schema(conn_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    conn_record = crud.get_db_connection(db, conn_id)
    if not conn_record:
        raise HTTPException(status_code=404, detail="连接不存在")
    conn_string = decrypt(conn_record.conn_string_enc)
    blocked = json.loads(conn_record.blocked_tables or "[]")
    try:
        if conn_record.db_type == "mongodb":
            from app.connectors.mongo_connector import MongoConnector
            schema = MongoConnector(conn_string, blocked).get_schema()
        else:
            from app.connectors.sql_connector import SQLConnector
            schema = SQLConnector(conn_string, blocked).get_schema()
        crud.update_schema_cache(db, conn_id, schema)
        return {"ok": True, "tables": list(schema.keys())}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Users ─────────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "user"


@router.get("/users")
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    users = crud.list_users(db)
    return [
        {"id": u.id, "username": u.username, "role": u.role, "is_active": u.is_active, "created_at": u.created_at}
        for u in users
    ]


@router.post("/users")
def create_user(req: UserCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    existing = crud.get_user_by_username(db, req.username)
    if existing:
        raise HTTPException(status_code=400, detail="用户名已存在")
    user = crud.create_user(
        db,
        user_id=str(uuid.uuid4()),
        username=req.username,
        password_hash=hash_password(req.password),
        role=req.role,
    )
    return {"id": user.id, "username": user.username, "role": user.role}


@router.put("/users/{user_id}/toggle-active")
def toggle_user_active(user_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    user = crud.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    crud.update_user_active(db, user_id, not user.is_active)
    return {"ok": True, "is_active": not user.is_active}


# ── System Info（任意已登录用户可访问，非仅 admin）────────────────────────────

@router.get("/system-info")
def system_info(_: User = Depends(get_current_user)):
    """供前端展示当前生效的搜索引擎 / Embedding 提供商"""
    from app.core.config import config_manager
    embedding_cfg = config_manager.get("embedding") or {}
    search_cfg = config_manager.get("search") or {}
    return {
        "search_provider": search_cfg.get("provider"),
        "embedding_provider": embedding_cfg.get("provider"),
    }


# ── Embedding / Search 配置（设置页二次修改用，与 Setup 向导共用同一持久化逻辑）─

class EmbeddingConfigUpdate(BaseModel):
    provider: str
    model: str = ""
    api_key: str = ""


@router.get("/embedding-config")
def get_embedding_config(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    cfg = crud.get_embedding_config(db) or {}
    return {"provider": cfg.get("provider"), "model": cfg.get("model"), "configured": bool(cfg.get("api_key_enc"))}


@router.put("/embedding-config")
def update_embedding_config(
    req: EmbeddingConfigUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    try:
        model = resolve_embedding_model(req.provider, req.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if req.api_key.strip():
        api_key_enc = encrypt(req.api_key.strip())
    else:
        existing = crud.get_embedding_config(db) or {}
        if existing.get("provider") != req.provider or not existing.get("api_key_enc"):
            raise HTTPException(status_code=400, detail="首次配置或切换 Embedding 服务商时，请输入对应的 API Key")
        api_key_enc = existing["api_key_enc"]
    crud.update_embedding_config(db, req.provider, model, api_key_enc)
    return {"ok": True}


class SearchConfigUpdate(BaseModel):
    provider: str
    api_key: str


@router.get("/search-config")
def get_search_config(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    cfg = crud.get_search_config(db) or {}
    return {"provider": cfg.get("provider"), "configured": bool(cfg)}


@router.put("/search-config")
def update_search_config(
    req: SearchConfigUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    crud.update_search_config(db, req.provider, encrypt(req.api_key))
    return {"ok": True}
