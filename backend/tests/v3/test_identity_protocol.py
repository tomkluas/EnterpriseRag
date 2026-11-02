# 验证身份协议边界和密钥轮换；使用临时证书与回环 HTTPS 服务执行真实 TLS/JWKS 请求，不接触企业身份源。
"""Synthetic OIDC boundary checks: real TLS/JWKS/RSA and actual API/database.

No external IdP, enterprise credentials, network mocks, or production data.
"""
import datetime
import ipaddress
import json
from pathlib import Path
import ssl
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import httpx
import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.v3.api import create_app
from app.v3.config import Settings
from app.v3.db import Database
from app.v3.domain import Domain


class IdentityProtocolTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    # 临时自签证书和两把签名密钥支持真实 TLS/JWKS 与轮换场景；服务只绑定回环地址，不借用企业身份源。
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='v3-synthetic-identity-')
        cls.root = Path(cls.temp.name)
        cls.old_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.new_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        tls_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(tls_key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-datetime.timedelta(minutes=1))
                .not_valid_after(now+datetime.timedelta(hours=1))
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName([
                    x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
                .sign(tls_key, hashes.SHA256()))
        (cls.root/'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (cls.root/'key.pem').write_bytes(tls_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
        cls.state = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                cls.state['paths'].append(self.path)
                if cls.state['mode'] == 'timeout':
                    # The application's actual five-second timeout must fire.
                    if cls.stop.wait(6):
                        return
                if cls.state['mode'] == 'redirect' and self.path == '/keys':
                    self.send_response(302)
                    self.send_header('Location', cls.url+'/redirect-target')
                    self.end_headers()
                    return
                payload = (b'not-json' if cls.state['mode'] == 'bad-json'
                           else json.dumps({'keys': cls.state['keys']}).encode())
                self.send_response(503 if cls.state['mode'] == 'unavailable' else 200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                    pass

        cls.stop = threading.Event()
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cls.root/'cert.pem', cls.root/'key.pem')
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        cls.url = 'https://127.0.0.1:'+str(cls.server.server_port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.stop.set()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.temp.cleanup()

    @classmethod
    def jwk(cls, key, kid):
        result = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
        return dict(result, kid=kid, use='sig', alg='RS256')

    async def asyncSetUp(self):
        self.case_temp = tempfile.TemporaryDirectory(dir=self.root)
        root = Path(self.case_temp.name)
        self.db = Database('sqlite:///'+str(root/'identity.db'), testing=True)
        self.db.migrate()
        self.domain = Domain(self.db, root/'objects')
        self.domain.bootstrap('synthetic', 'admin')
        self.domain.add_user('synthetic', 'admin', 'employee', 'reader')
        self.state.update(mode='ok', keys=[self.jwk(self.old_key, 'old')], paths=[])
        settings = Settings(tenant_id='synthetic', mode='production', issuer=self.url,
                            audience='synthetic-v3', jwks_url=self.url+'/keys')
        # Only inject the temporary CA trust. HTTPX's actual socket/TLS transport,
        # request timeout and redirect behavior remain the production defaults.
        context = ssl.create_default_context(cafile=str(self.root/'cert.pem'))
        self.jwks_client = httpx.AsyncClient(verify=context, trust_env=False)
        with patch('app.v3.api.httpx.AsyncClient', return_value=self.jwks_client):
            self.app = create_app(settings, self.domain)
        self.api = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                    base_url='http://synthetic-api')

    async def asyncTearDown(self):
        await self.api.aclose()
        await self.jwks_client.aclose()
        self.db.engine.dispose()
        self.case_temp.cleanup()

    def token(self, changes=None, key=None, kid='old', algorithm='RS256', remove=()):
        claims = {'sub':'employee', 'tenant_id':'synthetic', 'iss':self.url,
                  'aud':'synthetic-v3', 'exp':int(time.time())+120, 'role':'admin'}
        claims.update(changes or {})
        for claim in remove:
            claims.pop(claim, None)
        signing_key = key if key is not None else self.old_key
        return jwt.encode(claims, signing_key, algorithm=algorithm, headers={'kid':kid})

    async def request(self, token=None, path='/api/v3/me', **kwargs):
        return await self.api.request('POST' if kwargs else 'GET', path,
            headers={'Authorization':'Bearer '+(token or self.token())}, **kwargs)

    # 统一核对拒绝码和安全错误文本，并检查令牌不出现在响应中，避免只拒绝请求却泄露凭据。
    async def assert_rejected(self, token=None):
        response = await self.request(token)
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json(), {'detail':'访问令牌无效或已过期'})
        if token:
            self.assertNotIn(token, response.text)

    async def test_real_tls_identity_and_database_role_control_admin_route(self):
        response = await self.request()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['role'], 'reader')
        denied = await self.request(path='/api/v3/users',
            json={'id':'attacker', 'role':'admin'})
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(self.state['paths'], ['/keys', '/keys'])

    async def test_key_rotation_overlap_then_old_key_retirement(self):
        self.assertEqual((await self.request()).status_code, 200)
        self.state['keys'].append(self.jwk(self.new_key, 'new'))
        self.assertEqual((await self.request(self.token(key=self.new_key, kid='new'))).status_code, 200)
        self.assertEqual((await self.request()).status_code, 200)
        self.state['keys'] = [self.jwk(self.new_key, 'new')]
        await self.assert_rejected()
        self.assertEqual((await self.request(self.token(key=self.new_key, kid='new'))).status_code, 200)

    async def test_unknown_and_duplicate_kid_and_wrong_signature(self):
        await self.assert_rejected(self.token(kid='unknown'))
        await self.assert_rejected(self.token(key=self.new_key))
        self.state['keys'].append(self.jwk(self.new_key, 'old'))
        await self.assert_rejected()

    async def test_hs256_and_none_are_rejected(self):
        await self.assert_rejected(self.token(key='synthetic-secret-at-least-32-bytes', algorithm='HS256'))
        await self.assert_rejected(self.token(key='', algorithm='none'))

    async def test_claim_constraints_and_required_claims(self):
        changes = [
            {'exp':time.time()-30}, {'nbf':time.time()+120}, {'iss':'https://wrong.invalid'},
            {'aud':'wrong'}, {'tenant_id':'other'}, {'tenant_id':None},
            {'tenant_id':['synthetic']}, {'tenant_id':1},
            {'sub':None}, {'sub':42}, {'sub':['employee']}, {'sub':{'id':'employee'}},
        ]
        for claims in changes:
            with self.subTest(claims=claims):
                await self.assert_rejected(self.token(changes=claims))
        for claim in ('exp', 'sub', 'aud', 'iss', 'tenant_id'):
            with self.subTest(missing=claim):
                await self.assert_rejected(self.token(remove=(claim,)))

    async def test_disabled_and_unknown_accounts_fail_after_valid_signature(self):
        token = self.token()
        self.assertEqual((await self.request(token)).status_code, 200)
        self.domain.add_user('synthetic', 'admin', 'employee', 'reader', active=False)
        self.assertEqual((await self.request(token)).status_code, 403)
        self.assertEqual((await self.request(self.token(changes={'sub':'unregistered'}))).status_code, 404)

    async def test_bad_json_and_http_failure_fail_closed(self):
        for mode in ('bad-json', 'unavailable'):
            with self.subTest(mode=mode):
                self.state['mode'] = mode
                await self.assert_rejected()

    async def test_redirect_is_rejected_without_fetching_target(self):
        self.state['mode'] = 'redirect'
        await self.assert_rejected()
        self.assertEqual(self.state['paths'], ['/keys'])

    async def test_real_jwks_timeout_fails_closed(self):
        self.state['mode'] = 'timeout'
        start = time.monotonic()
        await self.assert_rejected()
        self.assertGreaterEqual(time.monotonic()-start, 4.5)


if __name__ == '__main__':
    unittest.main()
