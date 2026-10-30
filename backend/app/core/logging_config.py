"""
统一日志配置。

设计考虑（v4 单 exe 架构下的特殊性）：
- 用户双击运行，出错时大概率第一反应是直接关闭那个黑色窗口，
  控制台输出就永久丢失了——必须同时落盘到文件，方便事后排查或截图反馈。
- 日志文件放在 data/logs/ 下，与 data/ 其他用户数据一样跟 exe 同级持久化
  （见 core/paths.py），程序更新/重新打包不会丢失历史日志。
- 用 RotatingFileHandler 限制单文件大小，避免长期运行后日志无限增长占满磁盘。
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from app.core.paths import get_data_dir

_configured = False


def setup_logging():
    """整个应用只需调用一次（main.py 启动时），后续各模块用 get_logger(__name__) 取用"""
    global _configured
    if _configured:
        return
    _configured = True

    log_dir = get_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    root_logger = logging.getLogger("ragapp")
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    root_logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    """各模块统一通过这个函数取 logger，自动挂在 ragapp 命名空间下"""
    setup_logging()
    return logging.getLogger(f"ragapp.{name}")
