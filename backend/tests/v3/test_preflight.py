import dataclasses
import importlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from app.v3.config import Settings


def configured():
    settings = Settings(database_url='postgresql+psycopg2://user:private@database/rag',
        issuer='https://identity.example/enterprise', audience='enterprise-rag',
        jwks_url='https://identity.example/enterprise/keys', objects_dir='s3://private-originals/production/tenant',
        model_base_url='http://models.internal:8080/v1', chat_model='enterprise-chat', rerank_model='enterprise-rerank')
    env = {'V3_SECRET_S3_ACCESS': 'private-access', 'V3_SECRET_S3_SECRET': 'private-secret',
           'V3_SECRET_MODEL': 'private-model-key'}
    return settings, env


class PreflightTests(unittest.TestCase):
    def check(self, settings, environ):
        return importlib.import_module('app.v3.preflight').check_settings(settings, environ)

    def test_complete_production_configuration_accepts_private_http_models(self):
        settings, env = configured()
        result = self.check(settings, env)
        self.assertTrue(result['passed'])
        self.assertEqual(result['errors'], [])
        self.assertTrue(all(result['checks'].values()))

    def test_development_configuration_needs_no_enterprise_fields(self):
        result = self.check(Settings(mode='development'), {})
        self.assertTrue(result['passed'])

    def test_production_missing_fields_and_bootstrap_token_report_names_only(self):
        settings = Settings(bootstrap_token='private-bootstrap')
        result = self.check(settings, {})
        self.assertFalse(result['passed'])
        for field in ('V3_DATABASE_URL', 'V3_OIDC_ISSUER', 'V3_OIDC_AUDIENCE', 'V3_OIDC_JWKS_URL',
                      'V3_OBJECTS_URI', 'V3_SECRET_S3_ACCESS', 'V3_SECRET_S3_SECRET', 'V3_MODEL_BASE_URL',
                      'V3_MODEL_KEY_REF', 'V3_CHAT_MODEL', 'V3_RERANK_MODEL', 'V3_BOOTSTRAP_TOKEN'):
            self.assertIn(field, result['errors'])
        self.assertNotIn('private-bootstrap', json.dumps(result))

    def test_issuer_and_jwks_require_fixed_https_sources(self):
        settings, env = configured()
        for attribute, field, value in (
            ('issuer', 'V3_OIDC_ISSUER', 'http://identity.example'),
            ('issuer', 'V3_OIDC_ISSUER', 'https://identity.example?issuer=dynamic'),
            ('jwks_url', 'V3_OIDC_JWKS_URL', 'http://identity.example/keys'),
            ('jwks_url', 'V3_OIDC_JWKS_URL', 'https://private-user:private-password@identity.example/keys'),
            ('jwks_url', 'V3_OIDC_JWKS_URL', 'https://identity.example:bad/keys'),
            ('audience', 'V3_OIDC_AUDIENCE', '*')):
            with self.subTest(attribute=attribute, value=value):
                self.assertIn(field, self.check(dataclasses.replace(settings, **{attribute: value}), env)['errors'])

    def test_database_requires_supported_postgresql_and_database_name(self):
        settings, env = configured()
        for value in ('sqlite:///dev.db', 'postgresql://', 'postgresql+asyncpg://db/rag'):
            self.assertIn('V3_DATABASE_URL', self.check(dataclasses.replace(settings, database_url=value), env)['errors'])

    def test_storage_requires_bucket_and_dedicated_safe_prefix(self):
        settings, env = configured()
        for value in (Path('data/objects'), 's3://private-originals', 's3:///tenant',
                      's3://private-originals/../other', 's3://user:secret@private-originals/tenant',
                      's3://private-originals/tenant?secret=value', 's3://private-originals//tenant'):
            with self.subTest(value=value):
                self.assertIn('V3_OBJECTS_URI', self.check(dataclasses.replace(settings, objects_dir=value), env)['errors'])

    def test_separate_reranker_requires_its_own_valid_key_reference(self):
        settings, env = configured()
        settings.rerank_base_url = 'http://reranker.internal:8080/v1'
        self.assertIn('V3_RERANK_KEY_REF', self.check(settings, env)['errors'])
        env['V3_SECRET_RERANK'] = 'private-rerank'
        self.assertTrue(self.check(settings, env)['passed'])
        settings.rerank_key_ref = 'literal-private-key'
        self.assertIn('V3_RERANK_KEY_REF', self.check(settings, env)['errors'])

    def test_all_errors_and_checks_are_fixed_fields_and_never_configuration_values(self):
        settings, env = configured()
        settings.database_url = 'private-db-secret-invalid'
        settings.issuer = 'http://private-issuer-secret'
        settings.jwks_url = 'http://private-jwks-secret?key=private-url-key'
        settings.model_key_ref = 'private-literal-model-secret'
        result = self.check(settings, env)
        serialized = json.dumps(result)
        self.assertNotIn('private-', serialized)
        self.assertTrue(all(field.startswith('V3_') for field in result['checks']))
        self.assertTrue(all(field in result['checks'] for field in result['errors']))

    def test_unknown_mode_cannot_bypass_production_configuration(self):
        self.assertIn('V3_MODE', self.check(Settings(mode='prod'), {})['errors'])

    def test_development_main_keeps_fixture_settings_without_production_preflight(self):
        from fastapi import FastAPI
        settings = Settings(mode='development', database_url='postgresql://db/development')
        sys.modules.pop('app.main', None)
        try:
            with patch('app.v3.config.Settings.from_env', return_value=settings), \
                 patch('dotenv.load_dotenv'), patch('app.v3.api.create_app', return_value=FastAPI()) as create:
                importlib.import_module('app.main')
                create.assert_called_once_with(settings)
        finally:
            sys.modules.pop('app.main', None)

    def test_model_and_reranker_endpoints_reject_non_http_and_embedded_credentials(self):
        settings, env = configured()
        for value in ('file:///private/model', 'http://user:private-pass@models.internal/v1'):
            settings.model_base_url = value
            self.assertIn('V3_MODEL_BASE_URL', self.check(settings, env)['errors'])
        settings.rerank_base_url = 'file:///private/rerank'
        self.assertIn('V3_RERANK_BASE_URL', self.check(settings, env)['errors'])

    def test_main_rejects_invalid_production_before_create_app_without_exposing_values(self):
        settings = Settings(database_url='postgresql://user:private-main-secret@database/rag')
        sys.modules.pop('app.main', None)
        try:
            with patch('app.v3.config.Settings.from_env', return_value=settings), \
                 patch('dotenv.load_dotenv'), \
                 patch('app.v3.api.create_app') as create:
                with self.assertRaises(RuntimeError) as error:
                    importlib.import_module('app.main')
                self.assertIn('docs/v3/production-configuration.md', str(error.exception))
                self.assertNotIn('private-main-secret', str(error.exception))
                create.assert_not_called()
        finally:
            sys.modules.pop('app.main', None)
