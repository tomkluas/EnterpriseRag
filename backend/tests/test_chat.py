import pytest
from app.db.crud import create_user, get_user_by_username
from app.db.database import SessionLocal, create_tables
from app.core.security import hash_password


@pytest.fixture(scope="module")
def db():
    create_tables()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_create_and_get_user(db):
    # 清理可能存在的测试用户
    existing = get_user_by_username(db, "testuser")
    if existing:
        db.delete(existing)
        db.commit()

    user = create_user(
        db,
        user_id="test-user-id",
        username="testuser",
        password_hash=hash_password("TestPass123"),
        role="user",
    )

    assert user.id == "test-user-id"
    assert user.username == "testuser"
    assert user.role == "user"
    assert user.is_active is True

    # 再次获取验证
    fetched = get_user_by_username(db, "testuser")
    assert fetched is not None
    assert fetched.username == "testuser"