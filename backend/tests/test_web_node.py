import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.web_node import web_node


class WebNodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_search_without_key_returns_configuration_error(self):
        with patch("app.agents.web_node.settings", SimpleNamespace(tavily_api_key="")):
            result = await web_node({"scope_web": True, "query": "test"})
        self.assertTrue(result.get("error"))

    async def test_unselected_search_does_not_require_key(self):
        self.assertEqual(await web_node({"scope_web": False}), {"retrieval_results": []})

    async def test_search_returns_evidence_and_reports_remote_failure(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.search.return_value = {"results": [{"content": "evidence", "title": "title",
                                                   "url": "https://example.test", "score": 1}]}
        with patch("tavily.TavilyClient", return_value=client), patch(
            "app.agents.web_node.settings", SimpleNamespace(tavily_api_key="test", tavily_max_results=3)
        ):
            result = await web_node({"scope_web": True, "query": "test"})
            self.assertEqual(result["retrieval_results"][0]["source_type"], "web")
            client.search.side_effect = RuntimeError("unavailable")
            self.assertTrue((await web_node({"scope_web": True, "query": "test"})).get("error"))
