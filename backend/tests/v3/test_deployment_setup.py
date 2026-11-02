import importlib.util
import tempfile
import unittest
from pathlib import Path

import httpx
from dotenv import dotenv_values

spec = importlib.util.spec_from_file_location('configure_weknora', Path(__file__).resolve().parents[3] / 'tools/v3/configure_weknora.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class SetupTests(unittest.TestCase):
    def config(self, directory):
        path = Path(directory) / '.env'
        path.write_text('V3_MODE=development\n' + '\n'.join(key + '=fixture' for key in (
            'V3_MODEL_BASE_URL', 'V3_CHAT_MODEL', 'V3_SECRET_MODEL',
            'V3_EMBEDDING_BASE_URL', 'V3_EMBEDDING_MODEL', 'V3_SECRET_EMBEDDING', 'WK_ADMIN_PASSWORD')))
        return path

    def test_resume_does_not_create_duplicates_and_changed_model_rejected(self):
        creates = []
        def handler(request):
            if request.url.path.endswith('/login'):
                return httpx.Response(200, json={'token': 'fixture-token', 'active_tenant': {'id': 1}})
            creates.append(request.url.path)
            return httpx.Response(200, json={'data': {'id': str(len(creates)), 'api_key': 'private-key'}})
        with tempfile.TemporaryDirectory() as directory, httpx.Client(base_url='http://test', transport=httpx.MockTransport(handler)) as client:
            path = self.config(directory)
            setup.configure(path, client)
            self.assertEqual(len(creates), 5)
            setup.configure(path, client)
            self.assertEqual(len(creates), 5)
            self.assertEqual(dotenv_values(path)['V3_SECRET_WK_READ'], 'private-key')
            path.write_text(path.read_text().replace('V3_CHAT_MODEL=fixture', 'V3_CHAT_MODEL=changed'))
            with self.assertRaisesRegex(ValueError, 'Model settings changed'):
                setup.configure(path, client)
            self.assertEqual(len(creates), 5)

    def test_unknown_create_outcome_does_not_repeat(self):
        def handler(request):
            if request.url.path.endswith('/login'):
                return httpx.Response(200, json={'token': 'fixture', 'active_tenant': 1})
            raise httpx.ReadTimeout('secret response must not escape')
        with tempfile.TemporaryDirectory() as directory, httpx.Client(base_url='http://test', transport=httpx.MockTransport(handler)) as client:
            path = self.config(directory)
            with self.assertRaises(httpx.ReadTimeout):
                setup.configure(path, client)
            with self.assertRaisesRegex(ValueError, 'unknown outcome'):
                setup.configure(path, client)

    def test_production_and_missing_fields_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as directory, httpx.Client(base_url='http://invalid') as client:
            path = Path(directory) / '.env'
            path.write_text('V3_MODE=production')
            with self.assertRaisesRegex(ValueError, 'Only development'):
                setup.configure(path, client)
            path.write_text('V3_MODE=development')
            with self.assertRaisesRegex(ValueError, 'Missing configuration'):
                setup.configure(path, client)
