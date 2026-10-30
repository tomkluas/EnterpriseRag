import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import require_admin
from app.core.deps import get_db
from app.core.security import encrypt, decrypt
from app.db.crud import (
    list_users, create_user, update_user_active,
    list_llm_configs, create_llm_config, set_default_llm, delete_llm_config,
    list_db_connections, create_db_connection, delete_db_connection,
    update_schema_cache, get_db_connection, get_llm_config, get_user, get_user_by_username,
)
from app.db.models import User
from app.connectors.sql_connector import SQLConnector, build_conn_string
from app.connectors.mongo_connector import MongoConnector

router = APIRouter(prefix="/api/v1/admin", tags=["管理"])


class LLMConfigRequest(BaseModel):
    name: str = Field(min_length=1)
    provider: Literal["deepseek", "openai", "ollama", "custom"]
    model_string: str = Field(min_length=1)
    api_key: str = ""
    base_url: str = ""
    max_tokens: int = Field(default=4096, gt=0)
    temperature: float = Field(default=0.7, ge=0, le=1)


class DBConnectionRequest(BaseModel):
    name: str = Field(min_length=1)
    db_type: Literal["mysql", "postgresql", "mssql", "oracle", "mongodb"]
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    user: str
    password: str
    database: str = Field(min_length=1)
    blocked_tables: list[str] = Field(default_factory=list)


class UserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1)
    role: Literal["user", "admin"] = "user"

    @field_validator("password")
    @classmethod
    def validate_password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("密码不能超过72字节")
        return value


# ========== LLM 配置 ==========

@router.get("/llm-configs")
def get_llm_configs(
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    configs = list_llm_configs(db)
    return [
        {
            "id": c.id,
            "name": c.name,
            "provider": c.provider,
            "model_string": c.model_string,
            "base_url": c.base_url,
            "is_default": c.is_default,
            "max_tokens": c.max_tokens,
            "temperature": c.temperature,
            "created_at": c.created_at,
        }
        for c in configs
    ]


@router.post("/llm-configs")
def add_llm_config(
    request: LLMConfigRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    api_key_enc = encrypt(request.api_key) if request.api_key else ""
    config = create_llm_config(
        db,
        name=request.name,
        provider=request.provider,
        model_string=request.model_string,
        api_key_enc=api_key_enc,
        base_url=request.base_url,
        is_default=False,
        max_tokens=request.max_tokens,
        temperature=request.temperature,
    )
    return {key: getattr(config, key) for key in (
        "id", "name", "provider", "model_string", "base_url", "is_default",
        "max_tokens", "temperature", "created_at",
    )}


@router.put("/llm-configs/{config_id}/set-default")
def set_default_model(
    config_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    if not get_llm_config(db, config_id):
        raise HTTPException(status_code=404, detail="模型配置不存在")
    set_default_llm(db, config_id)
    return {"message": "设置成功"}


@router.delete("/llm-configs/{config_id}")
def remove_llm_config(
    config_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    if not get_llm_config(db, config_id):
        raise HTTPException(status_code=404, detail="模型配置不存在")
    delete_llm_config(db, config_id)
    return {"message": "删除成功"}


@router.post("/llm-configs/test")
async def test_llm_config(
    config_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    from app.models.llm_factory import get_llm
    if not get_llm_config(db, config_id):
        raise HTTPException(status_code=404, detail="模型配置不存在")
    try:
        llm = get_llm(db, config_id)
        await llm.ainvoke([HumanMessage(content="请回复 OK")], max_tokens=32)
        return {"message": "连接成功"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ========== 数据库连接 ==========

@router.get("/db-connections")
def get_db_connections(
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    conns = list_db_connections(db)
    return [
        {
            "id": c.id,
            "name": c.name,
            "db_type": c.db_type,
            "is_active": c.is_active,
            "created_at": c.created_at,
        }
        for c in conns
    ]


@router.post("/db-connections")
def add_db_connection(
    request: DBConnectionRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    name, db_type = request.name, request.db_type
    host, port, user, password, database = request.host, request.port, request.user, request.password, request.database
    blocked_tables = json.dumps(request.blocked_tables)
    if db_type == "mongodb":
        from app.connectors.mongo_connector import build_mongo_conn_string
        h, p, u, pw, d = build_mongo_conn_string(host, port, user, password, database)
        conn = create_db_connection(
            db,
            name=name,
            db_type=db_type,
            conn_string_enc=encrypt(json.dumps({"host": h, "port": p, "user": u, "password": pw, "database": d})),
            blocked_tables=blocked_tables,
        )
    else:
        conn_str = build_conn_string(db_type, host, port, user, password, database)
        conn = create_db_connection(
            db,
            name=name,
            db_type=db_type,
            conn_string_enc=encrypt(conn_str),
            blocked_tables=blocked_tables,
        )
    return {key: getattr(conn, key) for key in (
        "id", "name", "db_type", "is_active", "created_at",
    )}


@router.delete("/db-connections/{conn_id}")
def remove_db_connection(
    conn_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    if not get_db_connection(db, conn_id):
        raise HTTPException(status_code=404, detail="连接不存在")
    delete_db_connection(db, conn_id)
    return {"message": "删除成功"}


@router.post("/db-connections/{conn_id}/test")
def test_db_connection(
    conn_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    conn_record = get_db_connection(db, conn_id)
    if not conn_record:
        raise HTTPException(status_code=404, detail="连接不存在")

    connector = None
    try:
        if conn_record.db_type == "mongodb":
            info = json.loads(decrypt(conn_record.conn_string_enc))
            connector = MongoConnector(**info, blocked_tables=json.loads(conn_record.blocked_tables or "[]"))
        else:
            conn_str = decrypt(conn_record.conn_string_enc)
            blocked = json.loads(conn_record.blocked_tables)
            connector = SQLConnector(conn_str, blocked)

        connector.connect()
        schema = connector.get_schema()
        return {"message": "连接成功", "schema": schema}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if connector is not None:
            connector.close()


@router.post("/db-connections/{conn_id}/refresh-schema")
def refresh_schema(
    conn_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    conn_record = get_db_connection(db, conn_id)
    if not conn_record:
        raise HTTPException(status_code=404, detail="连接不存在")

    connector = None
    try:
        if conn_record.db_type == "mongodb":
            info = json.loads(decrypt(conn_record.conn_string_enc))
            connector = MongoConnector(**info, blocked_tables=json.loads(conn_record.blocked_tables or "[]"))
        else:
            conn_str = decrypt(conn_record.conn_string_enc)
            blocked = json.loads(conn_record.blocked_tables)
            connector = SQLConnector(conn_str, blocked)

        connector.connect()
        schema = connector.get_schema()

        update_schema_cache(db, conn_id, schema)
        return {"message": "Schema 已刷新"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if connector is not None:
            connector.close()


# ========== 用户管理 ==========

@router.get("/users")
def get_users(
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    users = list_users(db)
    return [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "is_active": u.is_active,
            "created_at": u.created_at,
        }
        for u in users
    ]


@router.post("/users")
def add_user(
    request: UserRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    from app.core.security import hash_password
    import uuid
    username, password, role = request.username, request.password, request.role
    if get_user_by_username(db, username):
        raise HTTPException(status_code=409, detail="用户名已存在")
    try:
        new_user = create_user(
            db,
            user_id=str(uuid.uuid4()),
            username=username,
            password_hash=hash_password(password),
            role=role,
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="用户名已存在")
    return {
        "id": new_user.id,
        "username": new_user.username,
        "role": new_user.role,
    }


@router.put("/users/{user_id}/toggle-active")
def toggle_user_active(
    user_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    target = get_user(db, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target.id == user.id and target.is_active:
        raise HTTPException(status_code=400, detail="不能禁用当前登录用户")
    target = update_user_active(db, user_id, not target.is_active)
    return {"message": "操作成功", "is_active": target.is_active}
