"""
首次初始化向导接口。

在用户完成 Setup 之前，除 /api/v1/setup/* 外的所有业务接口都应拒绝访问
（前端路由层面拦截为主，这里额外做一层后端保护，见 main.py 的中间件）。
"""
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.core.config import config_manager
from app.core.security import hash_password, encrypt
from app.db import crud
from app.models.llm_factory import PROVIDER_DEFAULT_MODEL, PROVIDER_BASE_URL, PROVIDER_DISPLAY_NAME
from app.models.embedding_client import resolve_embedding_model

router = APIRouter()


def require_setup_open():
    if config_manager.is_initialized:
        raise HTTPException(status_code=403, detail="系统已完成初始化，请登录后在管理页面修改配置")


@router.get("/status")
def setup_status():
    return {"initialized": config_manager.is_initialized}


class AdminSetupRequest(BaseModel):
    password: str


@router.post("/admin", dependencies=[Depends(require_setup_open)])
def setup_admin(req: AdminSetupRequest, db: Session = Depends(get_db)):
    if config_manager.is_initialized:
        raise HTTPException(status_code=400, detail="系统已完成初始化")
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="密码长度至少8位")

    now = datetime.now(timezone.utc).isoformat()
    existing = crud.get_user_by_username(db, "admin")
    if existing:
        # 幂等：允许向导重复提交时更新密码而不是报错
        existing.password_hash = hash_password(req.password)
        db.commit()
    else:
        crud.create_user(
            db,
            user_id=str(uuid.uuid4()),
            username="admin",
            password_hash=hash_password(req.password),
            role="admin",
        )

    config_manager.update(admin_password_hash=hash_password(req.password))
    return {"ok": True}


class LLMSetupRequest(BaseModel):
    provider: str
    api_key: str
    model_string: str | None = None   # 留空则用服务商推荐默认模型


@router.post("/llm", dependencies=[Depends(require_setup_open)])
def setup_llm(req: LLMSetupRequest, db: Session = Depends(get_db)):
    if req.provider not in PROVIDER_DEFAULT_MODEL:
        raise HTTPException(status_code=400, detail=f"不支持的服务商: {req.provider}")

    model_string = req.model_string or PROVIDER_DEFAULT_MODEL[req.provider]

    # 写入 LLMConfig 表，设为默认
    existing_configs = crud.list_llm_configs(db)
    for c in existing_configs:
        if c.is_default:
            crud.delete_llm_config(db, c.id)  # Setup 阶段只保留一个，避免重复提交产生垃圾记录

    crud.create_llm_config(
        db,
        id=str(uuid.uuid4()),
        name=PROVIDER_DISPLAY_NAME.get(req.provider, req.provider),
        provider=req.provider,
        model_string=model_string,
        api_key_enc=encrypt(req.api_key),
        base_url=PROVIDER_BASE_URL.get(req.provider),
        is_default=True,
    )
    return {"ok": True}


@router.post("/llm/test", dependencies=[Depends(require_setup_open)])
async def test_llm_setup(req: LLMSetupRequest, db: Session = Depends(get_db)):
    """向导内"测试连接"按钮：临时构造一次调用，不依赖已保存的配置"""
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage

    if req.provider not in PROVIDER_BASE_URL:
        raise HTTPException(status_code=400, detail=f"不支持的服务商: {req.provider}")

    try:
        llm = ChatOpenAI(
            model=req.model_string or PROVIDER_DEFAULT_MODEL[req.provider],
            api_key=req.api_key,
            base_url=PROVIDER_BASE_URL[req.provider],
            max_tokens=64,
            temperature=0.7,
            timeout=20,
            max_retries=0,
        )
        resp = await llm.ainvoke([HumanMessage(content="你好，请回复'连接成功'")])
        return {"ok": True, "response": resp.content}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"连接失败: {e}")


class EmbeddingSetupRequest(BaseModel):
    provider: str
    api_key: str
    model: str | None = None


@router.post("/embedding", dependencies=[Depends(require_setup_open)])
def setup_embedding(req: EmbeddingSetupRequest, db: Session = Depends(get_db)):
    try:
        model = resolve_embedding_model(req.provider, req.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    crud.update_embedding_config(db, req.provider, model, encrypt(req.api_key))
    return {"ok": True}


class SearchSetupRequest(BaseModel):
    provider: str
    api_key: str


@router.post("/search", dependencies=[Depends(require_setup_open)])
def setup_search(req: SearchSetupRequest, db: Session = Depends(get_db)):
    """可选步骤，前端提供明显的跳过按钮"""
    crud.update_search_config(db, req.provider, encrypt(req.api_key))
    return {"ok": True}


@router.post("/complete", dependencies=[Depends(require_setup_open)])
def setup_complete():
    config_manager.update(initialized=True)
    return {"ok": True}
