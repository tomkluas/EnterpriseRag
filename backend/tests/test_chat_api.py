import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.api.chat import router
from app.core.auth import get_current_user
from app.core.deps import get_db


class ChatApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="test-user")
        app.dependency_overrides[get_db] = lambda: None
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_frontend_json_request_is_accepted(self):
        async def stream(state, **kwargs):
            # Check the JSON values reach retrieval without being replaced by defaults.
            self.assertEqual(state["session_id"], "test-session")
            self.assertEqual(state["query"], "test question")
            self.assertFalse(state["scope_vector"])
            self.assertTrue(state["scope_database"])
            self.assertTrue(state["scope_web"])
            self.assertEqual(state["knowledge_ids"], ["kb-1"])
            self.assertEqual(state["db_connection_ids"], ["db-1"])
            self.assertEqual(state["llm_config_id"], "llm-1")
            yield {}

        llm = FakeListChatModel(responses=["test answer"])
        with patch("app.api.chat.build_graph", return_value=SimpleNamespace(astream=stream)), patch(
            "app.api.chat.get_llm", return_value=llm
        ):
            response = self.client.post("/api/v1/chat/stream", json={
                "session_id": "test-session", "query": "test question",
                "scope_vector": False, "scope_database": True, "scope_web": True,
                "knowledge_ids": ["kb-1"], "db_connection_ids": ["db-1"],
                "llm_config_id": "llm-1",
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertIn('"event": "done"', response.text)
        events = [json.loads(line[6:]) for line in response.text.splitlines()
                  if line.startswith("data: ")]
        self.assertEqual("".join(event["data"] for event in events
                                 if event["event"] == "token"), "test answer")
        self.assertNotIn("error", [event["event"] for event in events])

    def test_retrieved_sources_are_included_in_stream(self):
        from app.agents.graph import build_graph

        source = {"text": "post requirements", "source_type": "vector",
                  "source_name": "post.txt", "source_detail": "page 1", "score": 1.0}
        with patch("app.agents.vector_node.vector_node", new=AsyncMock(
            return_value={"retrieval_results": [source]}
        )):
            graph = build_graph()
        with patch("app.api.chat.build_graph", return_value=graph), patch(
            "app.api.chat.get_llm", return_value=FakeListChatModel(responses=["answer"])
        ):
            response = self.client.post("/api/v1/chat/stream", json={
                "session_id": "s", "query": "post question",
            })
        self.assertEqual(response.status_code, 200)
        events = [json.loads(line[6:]) for line in response.text.splitlines()
                  if line.startswith("data: ")]
        self.assertEqual([event["data"] for event in events if event["event"] == "source"],
                         [source])

    def test_missing_query_is_a_body_validation_error(self):
        response = self.client.post("/api/v1/chat/stream", json={"session_id": "s"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"][0]["loc"], ["body", "query"])

    def test_invalid_knowledge_ids_is_a_body_validation_error(self):
        response = self.client.post("/api/v1/chat/stream", json={
            "session_id": "s", "query": "question", "knowledge_ids": "not-a-list",
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"][0]["loc"], ["body", "knowledge_ids"])

    def test_retrieval_failure_is_a_stream_error(self):
        with patch("app.api.chat.build_graph", side_effect=RuntimeError("retrieval unavailable")):
            response = self.client.post("/api/v1/chat/stream", json={
                "session_id": "s", "query": "question",
            })
        self.assertEqual(response.status_code, 200)
        self.assertIn('"event": "error"', response.text)
        self.assertNotIn('"event": "done"', response.text)

    def test_parallel_retrieval_errors_are_reported_without_model_answer(self):
        from app.agents.graph import build_graph

        with patch("app.agents.vector_node.vector_node", new=AsyncMock(return_value={
            "retrieval_results": [], "error": "vector failed",
        })), patch("app.agents.db_node.db_node", new=AsyncMock(return_value={
            "retrieval_results": [], "error": "database failed",
        })):
            graph = build_graph()
        with patch("app.api.chat.build_graph", return_value=graph):
            response = self.client.post("/api/v1/chat/stream", json={
                "session_id": "s", "query": "question", "scope_database": True,
            })
        self.assertEqual(response.status_code, 200)
        self.assertIn('"event": "error"', response.text)
        self.assertIn("vector failed", response.text)
        self.assertIn("database failed", response.text)
        self.assertNotIn('"event": "done"', response.text)

    def test_blank_question_is_rejected(self):
        response = self.client.post("/api/v1/chat/stream", json={
            "session_id": "s", "query": "   ",
        })
        self.assertEqual(response.status_code, 422)

    def test_stream_releases_configuration_database_connection(self):
        import tempfile
        from pathlib import Path
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import sessionmaker

        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine("sqlite:///" + str(Path(directory) / "test.db"),
                                   connect_args={"check_same_thread": False},
                                   pool_size=1, max_overflow=0, pool_timeout=0.1)
            sessions = sessionmaker(bind=engine)
            retained = []
            app = FastAPI()
            app.include_router(router)
            def override_db():
                with sessions() as db:
                    retained.append(db)
                    yield db
            app.dependency_overrides[get_db] = override_db
            app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="test")
            async def stream(*args, **kwargs):
                yield {}
            def model(db, config_id):
                db.execute(text("SELECT 1"))
                return FakeListChatModel(responses=["answer"])
            try:
                with TestClient(app) as client, patch("app.api.chat.build_graph", return_value=SimpleNamespace(astream=stream)), patch(
                    "app.api.chat.get_llm", side_effect=model
                ):
                    for _ in range(2):
                        response = client.post("/api/v1/chat/stream", json={"session_id": "s", "query": "q"})
                        self.assertIn('"event": "done"', response.text)
                        self.assertEqual(engine.pool.checkedout(), 0)
            finally:
                for db in retained:
                    db.close()
                engine.dispose()
