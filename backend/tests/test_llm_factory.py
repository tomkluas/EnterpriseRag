import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import HumanMessage

from app.models.llm_factory import get_llm, _build_llm_from_config


class LlmFactoryTests(unittest.IsolatedAsyncioTestCase):
    def test_explicit_missing_model_is_not_silently_replaced(self):
        with patch("app.models.llm_factory.get_llm_config", return_value=None):
            with self.assertRaises(ValueError):
                get_llm(None, "missing")

    async def assert_key_reaches_provider(self, llm):
        async def chunks():
            yield {"choices": [{"delta": {"role": "assistant", "content": "ok"}}]}

        with patch("litellm.acreate", new=AsyncMock(return_value=chunks())) as completion:
            result = "".join([chunk.content async for chunk in llm.astream([
                HumanMessage(content="test")
            ])])
        self.assertEqual(result, "ok")
        self.assertEqual(completion.await_args.kwargs.get("api_key"), "test-only-key")

    async def test_default_key_reaches_provider(self):
        with patch("app.models.llm_factory.get_default_llm_config", return_value=None), patch(
            "app.models.llm_factory.settings",
            SimpleNamespace(deepseek_api_key="test-only-key", deepseek_api_base="http://example.test")
        ):
            await self.assert_key_reaches_provider(get_llm(None))

    async def test_configured_keys_reach_provider(self):
        for provider in ("deepseek", "openai", "custom"):
            with self.subTest(provider=provider), patch(
                "app.models.llm_factory.decrypt", return_value="test-only-key"
            ):
                config = SimpleNamespace(provider=provider, model_string="test-model",
                                         api_key_enc="encrypted", base_url="http://example.test",
                                         max_tokens=64, temperature=0.7)
                await self.assert_key_reaches_provider(_build_llm_from_config(config))
