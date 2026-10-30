import pytest
from app.core.security import hash_password, verify_password


def test_password_hash():
    password = "test_password_123"
    hashed = hash_password(password)

    assert hashed != password
    assert verify_password(password, hashed)
    assert not verify_password("wrong_password", hashed)


def test_password_different_hashes():
    password = "test_password_123"
    h1 = hash_password(password)
    h2 = hash_password(password)

    # bcrypt 使用随机 salt，所以每次 hash 不同
    assert h1 != h2
    # 但都能验证通过
    assert verify_password(password, h1)
    assert verify_password(password, h2)