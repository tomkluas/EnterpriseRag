"""
应用入口：托管前端静态文件 + API + 自动打开浏览器。

关键设计：
- 单文件可执行程序，无需 Docker/Node.js/Python 环境依赖（用户视角）
- console=True 必须保留（PyInstaller spec 中），用户需要看到运行状态
- 用户数据（data/）与 exe 同级，见 core/paths.py
- Setup 向导完成前，除 /api/v1/setup/* 外的业务接口全部拦截
"""
import sys
from pathlib import Path

if __name__ == "__main__" and __package__ is None:
    root_dir = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root_dir))

import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging_config import setup_logging, get_logger

setup_logging()   # 必须在其他模块 import 之前完成，确保启动早期的日志也能落盘
logger = get_logger("main")

from app.db.database import create_tables
from app.core.config import config_manager
from app.core.paths import get_frontend_dist_path
from app.api import setup, auth, chat, knowledge, admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("应用启动中...")
    create_tables()
    logger.info("数据库表初始化完成")
    yield
    logger.info("应用关闭")


app = FastAPI(title="企业RAG系统", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Setup 强制保护中间件 ──────────────────────────────────────────────────────
# 未完成初始化向导前，禁止访问除 /setup 相关接口、健康检查外的任何业务接口
ALLOWED_PREFIXES_BEFORE_SETUP = ("/api/v1/setup", "/api/health")


@app.middleware("http")
async def enforce_setup_gate(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and not path.startswith(ALLOWED_PREFIXES_BEFORE_SETUP):
        if not config_manager.is_initialized:
            return JSONResponse(
                status_code=403,
                content={"detail": "系统尚未完成初始化，请先访问 /setup 完成设置"},
            )
    return await call_next(request)


app.include_router(setup.router,     prefix="/api/v1/setup",     tags=["setup"])
app.include_router(auth.router,      prefix="/api/v1/auth",      tags=["auth"])
app.include_router(chat.router,      prefix="/api/v1/chat",      tags=["chat"])
app.include_router(knowledge.router, prefix="/api/v1/knowledge", tags=["knowledge"])
app.include_router(admin.router,     prefix="/api/v1/admin",     tags=["admin"])


@app.get("/api/health")
def health():
    return {"status": "ok"}


# ── 托管前端静态文件（必须放在所有 API 路由注册之后）──────────────────────────
frontend_path = get_frontend_dist_path()


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and path.strip("/") in {"login", "setup", "knowledge", "admin"}:
                return await super().get_response("index.html", scope)
            raise


if frontend_path.exists():
    app.mount("/", SPAStaticFiles(directory=str(frontend_path), html=True), name="frontend")


def open_browser_delayed(port=8000):
    """延迟2秒后自动打开浏览器，确保服务已启动"""
    time.sleep(2)
    webbrowser.open(f"http://127.0.0.1:{port}")


def run(port=8000, open_browser=True):
    import uvicorn
    from app.core.paths import get_data_dir

    # 先检查端口；不能在启动失败时打开另一个已运行实例的页面。
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(('127.0.0.1', port))
        except OSError as exc:
            raise RuntimeError(f'端口 {port} 已被占用，请先关闭已运行的 RAG 后端，或使用 --port 指定其他端口') from exc

    if open_browser:
        threading.Thread(target=open_browser_delayed, args=(port,), daemon=True).start()
    print("=" * 50)
    print("企业RAG系统正在启动...")
    print("浏览器将自动打开，如未打开请手动访问:")
    print(f"http://127.0.0.1:{port}")
    print("")
    print(f"日志文件位置: {get_data_dir() / 'logs' / 'app.log'}")
    print("如遇到问题，可查看该文件获取详细错误信息")
    print("")
    print("要关闭系统，请直接关闭本窗口")
    print("=" * 50)
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    run()
