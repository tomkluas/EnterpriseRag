import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class StartupTests(unittest.TestCase):
    def test_health_endpoint(self):
        from app.main import app

        with TestClient(app) as client:
            response = client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


class AgentStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_retrieval_results_are_merged(self):
        from app.agents.graph import build_graph

        vector = {"text": "vector", "source_type": "vector", "source_name": "v",
                  "source_detail": "", "score": 1.0}
        database = {"text": "database", "source_type": "database", "source_name": "d",
                    "source_detail": "", "score": 1.0}
        state = {
            "query": "test", "session_id": "test",
            "scope_vector": True, "scope_database": True, "scope_web": False,
            "knowledge_ids": [], "db_connection_ids": [], "llm_config_id": None,
            "retrieval_results": [], "reranked_results": [], "error": None,
        }
        with patch("app.agents.vector_node.vector_node", new=AsyncMock(
            return_value={"retrieval_results": [vector]}
        )), patch("app.agents.db_node.db_node", new=AsyncMock(
            return_value={"retrieval_results": [database]}
        )):
            result = await build_graph().ainvoke(state)
        self.assertCountEqual(result["reranked_results"], [vector, database])
