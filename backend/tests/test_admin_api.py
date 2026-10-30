import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.admin import router
from app.core.auth import create_access_token
from app.core.deps import get_db
from app.core.security import hash_password
from app.db.crud import create_user, create_llm_config, get_user, get_llm_config
from app.db.models import Base


class AdminApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions() as db:
            for name, role in (("admin", "admin"), ("other-admin", "admin"), ("member", "user")):
                create_user(db, name, name, hash_password("test-only-password"), role)
        app = FastAPI()
        app.include_router(router)

        def override_db():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = override_db
        self.client = TestClient(app)
        self.headers = {"Authorization": "Bearer " + create_access_token("admin", "admin")}
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.client.close)

    def request(self, method, path, **kwargs):
        if method == "POST" and path in ("/users", "/llm-configs", "/db-connections") and "params" in kwargs:
            kwargs["json"] = kwargs.pop("params")
        return self.client.request(method, "/api/v1/admin" + path, headers=self.headers, **kwargs)

    def test_sensitive_creation_fields_are_not_accepted_in_url(self):
        response = self.client.post("/api/v1/admin/users", headers=self.headers,
                                    params={"username": "url-user", "password": "password"})
        self.assertEqual(response.status_code, 422)

    def test_model_create_list_default_delete(self):
        response = self.request("POST", "/llm-configs", params={
            "name": "test", "provider": "openai", "model_string": "test-model", "api_key": "test-key",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("api_key_enc", response.json())
        config_id = response.json()["id"]
        self.assertEqual(self.request("PUT", f"/llm-configs/{config_id}/set-default").status_code, 200)
        configs = self.request("GET", "/llm-configs").json()
        self.assertTrue(configs[0]["is_default"])
        self.assertNotIn("api_key_enc", configs[0])
        self.assertEqual(self.request("DELETE", f"/llm-configs/{config_id}").status_code, 200)
        self.assertEqual(self.request("GET", "/llm-configs").json(), [])
        self.assertEqual(self.request("DELETE", f"/llm-configs/{config_id}").status_code, 404)

    def test_nonexistent_default_does_not_clear_current_default(self):
        with self.sessions() as db:
            config = create_llm_config(db, name="existing", provider="openai", model_string="test",
                                       is_default=True)
            config_id = config.id
        response = self.request("PUT", "/llm-configs/missing/set-default")
        self.assertEqual(response.status_code, 404)
        with self.sessions() as db:
            self.assertTrue(get_llm_config(db, config_id).is_default)

    def test_model_test_calls_provider_and_reports_failure(self):
        with self.sessions() as db:
            config_id = create_llm_config(db, name="test", provider="openai", model_string="test").id
        with patch("app.models.llm_factory.get_llm", return_value=FakeListChatModel(responses=["OK"])):
            response = self.request("POST", "/llm-configs/test", params={"config_id": config_id})
        self.assertEqual(response.status_code, 200, response.text)
        # The constructor succeeds, but provider communication fails.
        class BrokenModel:
            async def ainvoke(self, *args, **kwargs):
                raise RuntimeError("provider unavailable")
        with patch("app.models.llm_factory.get_llm", return_value=BrokenModel()):
            response = self.request("POST", "/llm-configs/test", params={"config_id": config_id})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.request("POST", "/llm-configs/test", params={"config_id": "missing"}).status_code, 404)

    def test_users_create_duplicate_toggle_and_missing(self):
        params = {"username": "new-user", "password": "test-password", "role": "user"}
        created = self.request("POST", "/users", params=params)
        self.assertEqual(created.status_code, 200)
        user_id = created.json()["id"]
        self.assertEqual(self.request("POST", "/users", params=params).status_code, 409)
        for expected in (False, True):
            self.assertEqual(self.request("PUT", f"/users/{user_id}/toggle-active").status_code, 200)
            with self.sessions() as db:
                self.assertEqual(get_user(db, user_id).is_active, expected)
        self.assertEqual(self.request("PUT", "/users/missing/toggle-active").status_code, 404)
        self.assertEqual(self.request("PUT", "/users/admin/toggle-active").status_code, 400)

    def test_user_roles_and_required_values_are_validated(self):
        self.assertEqual(self.request("POST", "/users", params={
            "username": "long-password", "password": "中" * 25,
        }).status_code, 422)
        self.assertEqual(self.request("POST", "/users", params={
            "username": "", "password": "", "role": "root"
        }).status_code, 422)
        self.assertEqual(self.request("POST", "/llm-configs", params={
            "name": "x", "provider": "invalid", "model_string": "x"
        }).status_code, 422)
        self.assertEqual(self.request("POST", "/db-connections", params={
            "name": "x", "db_type": "invalid", "host": "host", "port": 1234,
            "user": "u", "password": "p", "database": "d",
        }).status_code, 422)

    def test_database_connection_create_list_delete_and_missing(self):
        response = self.request("POST", "/db-connections", params={
            "name": "x", "db_type": "postgresql", "host": "localhost", "port": 5432,
            "user": "u", "password": "p", "database": "d",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("conn_string_enc", response.json())
        conn_id = response.json()["id"]
        self.assertEqual(len(self.request("GET", "/db-connections").json()), 1)
        self.assertEqual(self.request("DELETE", f"/db-connections/{conn_id}").status_code, 200)
        self.assertEqual(self.request("GET", "/db-connections").json(), [])
        for method, path in (("DELETE", f"/db-connections/{conn_id}"),
                             ("POST", "/db-connections/missing/test"),
                             ("POST", "/db-connections/missing/refresh-schema")):
            self.assertEqual(self.request(method, path).status_code, 404)

    def test_database_test_and_schema_refresh(self):
        conn_id = self.request("POST", "/db-connections", params={
            "name": "db", "db_type": "postgresql", "host": "localhost", "port": 5432,
            "user": "u", "password": "p", "database": "d",
        }).json()["id"]
        class Connector:
            closed = False
            def connect(self):
                pass
            def get_schema(self):
                return {"posts": {"columns": [{"name": "id", "type": "INTEGER"}]}}
            def close(self):
                self.closed = True
        for suffix in ("test", "refresh-schema"):
            connector = Connector()
            with patch("app.api.admin.SQLConnector", return_value=connector):
                response = self.request("POST", f"/db-connections/{conn_id}/{suffix}")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(connector.closed)
        class BrokenConnector(Connector):
            def get_schema(self):
                raise RuntimeError("database unavailable")
        connector = BrokenConnector()
        with patch("app.api.admin.SQLConnector", return_value=connector):
            response = self.request("POST", f"/db-connections/{conn_id}/test")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(connector.closed)

    def test_admin_endpoints_reject_regular_user_and_no_token(self):
        self.headers = {"Authorization": "Bearer " + create_access_token("member", "user")}
        paths = [("GET", "/users"), ("POST", "/users"), ("PUT", "/users/x/toggle-active"),
                 ("GET", "/llm-configs"), ("POST", "/llm-configs"),
                 ("DELETE", "/llm-configs/x"), ("PUT", "/llm-configs/x/set-default"),
                 ("POST", "/llm-configs/test"), ("GET", "/db-connections"),
                 ("POST", "/db-connections"), ("DELETE", "/db-connections/x"),
                 ("POST", "/db-connections/x/test"), ("POST", "/db-connections/x/refresh-schema")]
        for method, path in paths:
            with self.subTest(path=path, method=method):
                self.assertEqual(self.request(method, path).status_code, 403)
        self.headers = {}
        self.assertIn(self.request("GET", "/users").status_code, (401, 403))
