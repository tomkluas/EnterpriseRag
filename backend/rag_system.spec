# backend/rag_system.spec
# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

sys.path.insert(0, SPECPATH)
from scripts.packaging_support import pin_windows_runtime

a = Analysis(
    ['launcher.py'],
    pathex=[SPECPATH],
    binaries=[],
    datas=[
        ('frontend_dist', 'frontend_dist'),   # 前端构建产物打包进exe
    ] + collect_data_files('chromadb', includes=['migrations/**/*.sql']),
    hiddenimports=[
        'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto', 'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'chromadb.telemetry.product.posthog',
        'chromadb.segment.impl.metadata.sqlite',
        'sqlalchemy.dialects.sqlite',
        'pymysql', 'psycopg2', 'pymongo',
    ] + collect_submodules('chromadb', filter=lambda name: not name.startswith(('chromadb.test', 'chromadb.cli'))),
    hookspath=[],
    runtime_hooks=[],
    excludes=['matplotlib', 'tkinter'],   # 排除不需要的大型库，减小体积
    noarchive=False,
)

# PATH 上的 JDK / 自动化软件可能带有过旧的同名 DLL；不可混入交付包。
if sys.platform == 'win32':
    a.binaries = pin_windows_runtime(a.binaries, Path(os.environ['SystemRoot']) / 'System32')

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='启动RAG系统',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                # 启用压缩，减小体积
    console=True,            # 必须为True，保留命令行窗口给用户看启动日志
    icon=str(Path(SPECPATH) / 'resources' / 'icon.ico') if (Path(SPECPATH) / 'resources' / 'icon.ico').exists() else None,
)
