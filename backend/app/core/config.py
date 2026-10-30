"""
配置系统：v4 彻底移除 .env 依赖，所有配置存储在 data/config.json。

优先级：
1. data/config.json 中的值（用户在界面里设置的）
2. 程序首次启动自动生成（secret_key, encryption_key）
3. 代码内置默认值（仅用于未配置时的占位提示，不可用于生产）

secret_key 和 encryption_key 在首次启动时用 secrets.token_hex(32) 自动生成，
写入 config.json，此后固定不变（重新生成会导致已加密数据无法解密）。
"""
import json
import secrets
import threading
from app.core.paths import get_config_path

_lock = threading.Lock()

DEFAULT_CONFIG = {
    "initialized": False,
    "admin_password_hash": None,
    "secret_key": None,
    "encryption_key": None,
    "default_llm": None,      # {"provider","model_string","api_key_enc","base_url"}
    "embedding": None,        # {"provider","model","api_key_enc"}
    "search": None,           # {"provider","api_key_enc"}
}


def _read_raw() -> dict:
    path = get_config_path()
    if not path.exists():
        return dict(DEFAULT_CONFIG)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(DEFAULT_CONFIG)
        merged.update(data)
        return merged
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_CONFIG)


def _write_raw(data: dict):
    path = get_config_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _ensure_keys(data: dict) -> dict:
    """首次启动自动生成密钥，仅生成一次"""
    changed = False
    if not data.get("secret_key"):
        data["secret_key"] = secrets.token_hex(32)
        changed = True
    if not data.get("encryption_key"):
        data["encryption_key"] = secrets.token_hex(32)
        changed = True
    if changed:
        _write_raw(data)
    return data


class ConfigManager:
    """线程安全的配置读写封装"""

    def get_all(self) -> dict:
        with _lock:
            data = _read_raw()
            data = _ensure_keys(data)
            return data

    def get(self, key: str, default=None):
        return self.get_all().get(key, default)

    def update(self, **kwargs):
        with _lock:
            data = _read_raw()
            data = _ensure_keys(data)
            data.update(kwargs)
            _write_raw(data)
            return data

    @property
    def secret_key(self) -> str:
        return self.get_all()["secret_key"]

    @property
    def encryption_key(self) -> str:
        return self.get_all()["encryption_key"]

    @property
    def is_initialized(self) -> bool:
        return bool(self.get_all().get("initialized"))

    # ---- 业务相关默认值（不依赖 config.json，纯代码常量）----
    app_name = "企业RAG系统"
    access_token_expire_minutes = 480
    upload_dir_name = "uploads"
    chunk_size = 512
    chunk_overlap = 64
    max_file_size_mb = 50


config_manager = ConfigManager()
