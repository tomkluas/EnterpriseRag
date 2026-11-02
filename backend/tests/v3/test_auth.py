# 验证企业签名身份与数据库角色的组合；临时生成 RSA 密钥并模拟 JWKS 响应，不访问真实身份提供方。
import json
import tempfile
import time
import unittest
from pathlib import Path
import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from starlette.requests import Request
from app.v3.auth import authenticator
from app.v3.config import Settings
from app.v3.db import Database
from app.v3.domain import Domain,DomainError


class EnterpriseAuthTests(unittest.IsolatedAsyncioTestCase):
    # 使用本轮生成的公钥构造 JWKS；令牌声称的角色不能覆盖库内角色，签名有效也不代表授权通过。
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Database('sqlite:///'+str(Path(self.temp.name)/'test.db'),testing=True)
        self.db.migrate();self.domain=Domain(self.db,Path(self.temp.name)/'objects');self.domain.bootstrap('t','admin')
        self.domain.add_user('t','admin','employee','reader')
        self.key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        jwk=jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(),as_dict=True);jwk.update(kid='key',use='sig',alg='RS256')
        self.client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'keys':[jwk]})))
        self.settings=Settings(tenant_id='t',mode='production',issuer='https://id.test',audience='v3',jwks_url='https://id.test/keys')
        self.authenticate=authenticator(self.settings,self.domain,self.client)

    async def asyncTearDown(self):
        await self.client.aclose();self.db.engine.dispose();self.temp.cleanup()

    def request(self,**changes):
        claims={'sub':'employee','tenant_id':'t','iss':'https://id.test','aud':'v3','exp':time.time()+120,'role':'admin'}
        claims.update(changes)
        token=jwt.encode(claims,self.key,algorithm='RS256',headers={'kid':'key'})
        return Request({'type':'http','headers':[(b'authorization',('Bearer '+token).encode())]})

    async def test_signed_enterprise_identity_keeps_database_role(self):
        user=await self.authenticate(self.request())
        self.assertEqual(user,'employee')
        with self.db.transaction('t',shared=True) as session:
            self.assertEqual(self.domain.user(session,'t',user).role,'reader')

    async def test_wrong_tenant_audience_expiry_and_disabled_account_are_rejected(self):
        for claims in ({'tenant_id':'other'},{'aud':'other'},{'iss':'https://attacker.test'},{'exp':time.time()-60}):
            with self.subTest(claims=claims):
                with self.assertRaises(HTTPException):await self.authenticate(self.request(**claims))
        self.domain.add_user('t','admin','employee','reader',active=False)
        with self.assertRaises(DomainError):await self.authenticate(self.request())
