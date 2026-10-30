"""
路径处理：开发态 vs PyInstaller 打包后的资源路径适配。

关键设计：
- exe 本身（含前端静态资源）可以解压到临时目录运行（sys._MEIPASS），只读即可。
- 用户数据（data/）必须与 exe 文件保持同级，使用 sys.executable 而非 sys._MEIPASS，
  确保程序更新/重新打包后用户数据不丢失。
"""
import sys
from pathlib import Path


def is_frozen() -> bool:
    """判断是否运行在 PyInstaller 打包后的环境"""
    return getattr(sys, 'frozen', False)


def get_base_path() -> Path:
    """
    获取资源基础路径：
    - 开发态：项目根目录（backend 的上一级）
    - 打包后：exe 所在目录（不是临时解压目录！这样 data/ 才能持久化）
    """
    if is_frozen():
        return Path(sys.executable).parent
    return Path(__file__).parent.parent.parent.parent


def get_data_dir() -> Path:
    """data/ 目录：存放数据库、Chroma数据、上传文件，与 exe 同级"""
    data_dir = get_base_path() / 'data'
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_config_path() -> Path:
    """data/config.json 路径"""
    return get_data_dir() / 'config.json'


def get_frontend_dist_path() -> Path:
    """
    前端静态文件路径：
    - 打包后：使用 PyInstaller 的 _MEIPASS 临时目录（前端文件随exe打包，只读即可）
    - 开发态：frontend/dist
    """
    if is_frozen():
        return Path(sys._MEIPASS) / 'frontend_dist'
    return get_base_path() / 'frontend' / 'dist'
