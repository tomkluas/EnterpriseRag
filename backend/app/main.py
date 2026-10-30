from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.db.database import create_tables
from app.models.bge_model import bge_singleton


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化
    create_tables()
    # BGE 模型在需要时加载，不在启动时加载
    yield


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# 注册路由
from app.api import auth, chat, knowledge, admin

app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(knowledge.router)
app.include_router(admin.router)