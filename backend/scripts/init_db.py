import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.security import hash_password
from app.db.database import create_tables, SessionLocal
from app.db.crud import get_user_by_username, create_user

def init_db():
    create_tables()
    db = SessionLocal()
    try:
        if not get_user_by_username(db, settings.init_admin_username):
            create_user(
                db,
                user_id="admin",
                username=settings.init_admin_username,
                password_hash=hash_password(settings.init_admin_password),
                role="admin",
            )
            print(f"[init_db] 管理员账号已创建: {settings.init_admin_username}")
        else:
            print("[init_db] 管理员账号已存在，跳过")
    finally:
        db.close()


if __name__ == "__main__":
    init_db()