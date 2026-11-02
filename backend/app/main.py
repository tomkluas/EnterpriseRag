# API 进程入口：读取独立 V3 配置、执行生产预检，再装配业务 API 与构建后的前端。
# 这里不自动迁移数据库；未配置数据库时只提供明确的未就绪响应。

"""V3 应用入口，不读取 V1/V2 的运行配置。"""
import os
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from .v3.config import Settings
from .v3.api import create_app
from .v3.preflight import check_settings

if os.getenv('V3_ENV_FILE'): load_dotenv(os.environ['V3_ENV_FILE'],override=False)
settings=Settings.from_env()
if settings.database_url:
    if settings.mode!='development' and not check_settings(settings)['passed']:
        raise RuntimeError('生产配置预检失败；请按 docs/v3/production-configuration.md 修复独立 V3 配置。')
    app=create_app(settings)
else:
    app=FastAPI(title='EnterpriseRag V3 配置入口')
    @app.get('/health')
    # 配置缺失时返回 503，避免仅进程存活被误判为工作台已经可用。
    def health():return JSONResponse({'version':'3.0.0','ready':False,'detail':'请配置 V3_DATABASE_URL，并执行 migrate'},status_code=503)
    @app.get('/api/v3/{path:path}')
    # 统一拦截未配置环境下的业务请求，提示先配置并迁移。
    def missing(path:str):return JSONResponse({'detail':'V3 尚未配置。请按 README 创建独立配置，并执行数据库迁移。'},status_code=503)

frontend=Path(os.getenv('V3_FRONTEND_DIR',str(Path(__file__).resolve().parents[2]/'frontend'/'dist'))).resolve()
if (frontend/'assets').is_dir():app.mount('/assets',StaticFiles(directory=frontend/'assets'),name='assets')
if (frontend/'index.html').is_file():
    @app.get('/')
    # 首页禁用缓存，使前端重新构建后能取得新的资源文件名。
    def index():return FileResponse(frontend/'index.html',headers={'Cache-Control':'no-cache'})
