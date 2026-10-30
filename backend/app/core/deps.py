from functools import lru_cache

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import get_db, engine, create_tables


@lru_cache
def get_settings():
    return settings


__all__ = ["get_db", "get_settings", "create_tables"]