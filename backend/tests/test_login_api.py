import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import router
from app.core.deps import get_db
from app.core.security import hash_password
from app.db.crud import create_user
from app.db.models import Base


class LoginApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(cls.engine)
        cls.session_factory = sessionmaker(bind=cls.engine)
        with cls.session_factory() as db:
            for username in ("test_admin", "disabled"):
                user = create_user(db, username, username, hash_password("test-only-password"), "admin")
                user.is_active = username != "disabled"
            db.commit()
        app = FastAPI()
        app.include_router(router)

        def override_db():
            with cls.session_factory() as db:
                yield db

        app.dependency_overrides[get_db] = override_db
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.engine.dispose()

    def test_json_login_and_authenticated_me(self):
        response = self.client.post("/api/v1/auth/login", json={
            "username": "test_admin", "password": "test-only-password"
        })
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["access_token"])
        self.assertEqual(payload["token_type"], "bearer")
        me = self.client.get("/api/v1/auth/me", headers={
            "Authorization": "Bearer " + payload["access_token"]
        })
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["username"], "test_admin")

    def test_wrong_password_is_unauthorized(self):
        response = self.client.post("/api/v1/auth/login", json={
            "username": "test_admin", "password": "wrong"
        })
        self.assertEqual(response.status_code, 401)

    def test_disabled_user_is_forbidden(self):
        response = self.client.post("/api/v1/auth/login", json={
            "username": "disabled", "password": "test-only-password"
        })
        self.assertEqual(response.status_code, 403)

    def test_missing_password_is_a_body_validation_error(self):
        response = self.client.post("/api/v1/auth/login", json={"username": "test_admin"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"][0]["loc"], ["body", "password"])

    def test_credentials_in_query_alone_are_not_accepted(self):
        response = self.client.post("/api/v1/auth/login", params={
            "username": "test_admin", "password": "test-only-password"
        })
        self.assertEqual(response.status_code, 422)

    def test_expired_invalid_and_disabled_tokens_are_rejected(self):
        from app.core.auth import create_access_token
        for token in ("invalid-token", create_access_token("disabled", "admin"),
                      create_access_token("missing", "user")):
            response = self.client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + token})
            self.assertEqual(response.status_code, 401)
