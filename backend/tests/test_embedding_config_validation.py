import unittest
from unittest.mock import patch

from fastapi import HTTPException
from app.api import admin, setup
from app.models.embedding_client import EmbeddingClient


class EmbeddingConfigTests(unittest.TestCase):
    def setUp(self):
        guard = patch("app.core.config.config_manager.update", side_effect=AssertionError("测试不得修改真实配置"))
        guard.start()
        self.addCleanup(guard.stop)

    def test_admin_blank_model_uses_provider_default_and_keeps_key(self):
        existing = {"provider": "qwen", "model": "qwen", "api_key_enc": "saved-key"}
        with patch.object(admin.crud, "get_embedding_config", return_value=existing), \
             patch.object(admin.crud, "update_embedding_config") as save:
            admin.update_embedding_config(admin.EmbeddingConfigUpdate(provider="qwen", model=" ", api_key=""), db=None, _=None)
            save.assert_called_once_with(None, "qwen", "text-embedding-v3", "saved-key")

    def test_admin_rejects_provider_as_model_without_saving(self):
        with patch.object(admin.crud, "update_embedding_config") as save:
            with self.assertRaises(HTTPException) as error:
                admin.update_embedding_config(admin.EmbeddingConfigUpdate(provider="qwen", model="qwen", api_key="key"), db=None, _=None)
            self.assertEqual(error.exception.status_code, 400)
            self.assertIn("text-embedding-v3", error.exception.detail)
            save.assert_not_called()

    def test_admin_provider_change_requires_new_key(self):
        existing = {"provider": "zhipu", "api_key_enc": "old-key"}
        with patch.object(admin.crud, "get_embedding_config", return_value=existing), \
             patch.object(admin.crud, "update_embedding_config") as save:
            with self.assertRaises(HTTPException):
                admin.update_embedding_config(admin.EmbeddingConfigUpdate(provider="qwen", model="", api_key=""), db=None, _=None)
            save.assert_not_called()

    def test_admin_rejects_unknown_provider(self):
        with patch.object(admin.crud, "update_embedding_config") as save:
            with self.assertRaises(HTTPException):
                admin.update_embedding_config(admin.EmbeddingConfigUpdate(provider="invalid", model="", api_key="key"), db=None, _=None)
            save.assert_not_called()

    def test_admin_trims_model_and_encrypts_new_key(self):
        with patch.object(admin, "encrypt", return_value="encrypted") as encrypt, \
             patch.object(admin.crud, "update_embedding_config") as save:
            admin.update_embedding_config(admin.EmbeddingConfigUpdate(provider="qwen", model=" text-embedding-v4 ", api_key="new-key"), db=None, _=None)
            encrypt.assert_called_once_with("new-key")
            save.assert_called_once_with(None, "qwen", "text-embedding-v4", "encrypted")

    def test_setup_rejects_provider_as_model(self):
        with patch.object(setup.crud, "update_embedding_config") as save:
            with self.assertRaises(HTTPException):
                setup.setup_embedding(setup.EmbeddingSetupRequest(provider="qwen", model="qwen", api_key="key"), db=None)
            save.assert_not_called()

    def test_setup_whitespace_uses_default(self):
        with patch.object(setup, "encrypt", return_value="encrypted"), \
             patch.object(setup.crud, "update_embedding_config") as save:
            setup.setup_embedding(setup.EmbeddingSetupRequest(provider="qwen", model=" ", api_key="key"), db=None)
            save.assert_called_once_with(None, "qwen", "text-embedding-v3", "encrypted")

    def test_client_missing_key_reports_configuration_error(self):
        with patch.object(admin.crud, "get_embedding_config", return_value={"provider": "qwen", "model": "text-embedding-v3", "api_key_enc": ""}):
            with self.assertRaisesRegex(RuntimeError, "API Key"):
                EmbeddingClient(None)

    def test_client_rejects_bad_saved_model_before_decryption(self):
        with patch.object(admin.crud, "get_embedding_config", return_value={"provider": "qwen", "model": "qwen", "api_key_enc": "invalid"}):
            with self.assertRaisesRegex(ValueError, "不是 Embedding 模型名"):
                EmbeddingClient(None)


if __name__ == "__main__":
    unittest.main()
