# 验证真实回环网络中的发送门禁与锁释放；部分用例需显式隔离 PostgreSQL，其他用例以模拟传输覆盖取消和失败。
"""Loopback HTTP tests: receiving a request must not retain tenant locks until headers."""
import asyncio
import json
import os
import tempfile
import unittest
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from app.v3.chat.models import ModelService
from app.v3.config import Settings
from app.v3.db import Database
from app.v3.domain import Domain


@unittest.skipUnless(os.getenv('V3_TEST_DATABASE_URL'),'需要隔离 PostgreSQL 测试连接')
class DispatchNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_rank_delayed_headers_allow_tenant_write(self):await self._exercise('rank')
    async def test_rewrite_delayed_headers_allow_tenant_write(self):await self._exercise('rewrite')
    async def test_stream_delayed_headers_allow_tenant_write(self):await self._exercise('stream')
    async def test_retrieve_delayed_headers_allow_tenant_write(self):await self._exercise('retrieve')

    # 回环服务收到请求后暂停响应头，再尝试租户写入；写入及时完成才说明发送门禁没有占锁等待远端响应。
    async def _exercise(self,kind):
        db=Database(os.environ['V3_TEST_DATABASE_URL']);db.migrate()
        tenant='dispatch-network-'+uuid.uuid4().hex
        received=asyncio.Event();respond=asyncio.Event();closed=asyncio.Event()
        async def serve(reader,writer):
            try:
                headers=await reader.readuntil(b'\r\n\r\n')
                length=next(int(line.split(b':',1)[1]) for line in headers.split(b'\r\n') if line.lower().startswith(b'content-length:'))
                await reader.readexactly(length)
                received.set()
                await respond.wait()
                body=b'{"results":[{"index":0,"relevance_score":0.9}]}'
                if kind=='rewrite':body=json.dumps({'choices':[{'message':{'content':json.dumps({'query':'q','faithful':True})}}]}).encode()
                if kind=='stream':body=b'data: {"choices":[{"delta":{"content":"answer [1]"}}]}\n\ndata: [DONE]\n\n'
                if kind=='retrieve':body=b'{}'
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '+str(len(body)).encode()+b'\r\nConnection: close\r\n\r\n'+body)
                await writer.drain()
            finally:
                writer.close();await writer.wait_closed();closed.set()
        server=await asyncio.start_server(serve,'127.0.0.1',0)
        port=server.sockets[0].getsockname()[1]
        try:
            with tempfile.TemporaryDirectory() as temp:
                domain=Domain(db,Path(temp));domain.bootstrap(tenant,'admin')
                space=domain.create_space(tenant,'admin','test')
                doc=domain.receive(tenant,'admin',space,'fixture.txt',b'fixture')
                @asynccontextmanager
                async def gate():
                    async with db.atransaction(tenant,shared=True):yield
                settings=Settings(model_base_url=f'http://127.0.0.1:{port}',rerank_model='test',chat_model='test')
                with patch.dict('os.environ',{'V3_SECRET_MODEL':'fixture'}):
                    async with httpx.AsyncClient(trust_env=False) as client:
                        model=ModelService(settings,client)
                        async def send():
                            if kind=='rank':return await model.rank('q',[SimpleNamespace(content='e')],dispatch_gate=gate)
                            if kind=='rewrite':return await model.rewrite('q',[],dispatch_gate=gate)
                            if kind=='stream':return [part async for part in model.stream('q',[SimpleNamespace(content='e')],[],dispatch_gate=gate)]
                            from app.v3.knowledge.transport import Transport
                            class FixtureTransport(Transport):
                                @property
                                def headers(self):return {}
                            return await FixtureTransport(settings.model_base_url,'fixture',client).request('POST','/retrieve',json={'query':'q'},dispatch_gate=gate)
                        task=asyncio.create_task(send())
                        try:
                            await asyncio.wait_for(received.wait(),3)
                            # A real PostgreSQL exclusive transaction must commit while
                            # the server has received the request but sent no headers.
                            await asyncio.wait_for(asyncio.to_thread(domain.create_conversation,tenant,'admin'),2)
                            self.assertFalse(task.done())
                            await asyncio.wait_for(asyncio.to_thread(domain.withdraw,tenant,'admin',doc['document_id']),2)
                        finally:
                            respond.set()
                            await task
            await asyncio.wait_for(closed.wait(),3)
        finally:
            respond.set();server.close();await server.wait_closed();db.engine.dispose()


class DispatchCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_before_headers_releases_gate_once(self):
        from app.v3.http_dispatch import authorized_stream
        entered=asyncio.Event();released=[]
        @asynccontextmanager
        async def gate():
            try:yield
            finally:
                await asyncio.sleep(0)
                released.append(True)
        async def handler(request):
            entered.set()
            await asyncio.Event().wait()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            async def send():
                async with authorized_stream(client,'POST','https://fixture.test',dispatch_gate=gate,json={}):pass
            task=asyncio.create_task(send());await entered.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(released,[True])

    async def test_connect_failure_releases_gate_once(self):
        from app.v3.http_dispatch import authorized_stream
        released=[]
        @asynccontextmanager
        async def gate():
            try:yield
            finally:released.append(True)
        def handler(request):raise httpx.ConnectError('fixture',request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(httpx.ConnectError):
                async with authorized_stream(client,'POST','https://fixture.test',dispatch_gate=gate,json={}):pass
        self.assertEqual(released,[True])

    async def test_http2_send_complete_releases_before_headers(self):
        from app.v3.http_dispatch import authorized_stream
        held=False;exits=[]
        @asynccontextmanager
        async def gate():
            nonlocal held
            held=True
            try:yield
            finally:held=False;exits.append(True)
        async def handler(request):
            self.assertTrue(held)
            await request.extensions['trace']('http2.send_request_body.complete',{})
            self.assertFalse(held)
            return httpx.Response(200,json={})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            async with authorized_stream(client,'POST','https://fixture.test',dispatch_gate=gate,json={}):pass
        self.assertEqual(exits,[True])

    async def test_transparent_http2_retry_after_revocation_is_rejected_before_headers(self):
        from app.v3.http_dispatch import authorized_stream
        authorized=True;held=False;sent=[];exits=[]
        @asynccontextmanager
        async def gate():
            nonlocal held
            if not authorized:raise PermissionError('revoked')
            self.assertFalse(held)
            held=True
            try:yield
            finally:held=False;exits.append(True)
        async def handler(request):
            nonlocal authorized
            trace=request.extensions['trace']
            await trace('http2.send_request_headers.started',{})
            self.assertTrue(held)
            sent.append('first')
            await trace('http2.send_request_body.complete',{})
            self.assertFalse(held)
            authorized=False
            # httpcore may reuse this same Request after GOAWAY.
            await trace('http2.send_request_headers.started',{})
            sent.append('retry')
            return httpx.Response(200,json={})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(httpx.TransportError):
                async with authorized_stream(client,'POST','https://fixture.test',dispatch_gate=gate,json={}):pass
        self.assertEqual(sent,['first'])
        self.assertEqual(exits,[True])
        self.assertFalse(held)

    async def test_proxy_connect_does_not_release_gate_or_count_as_business_send(self):
        from app.v3.http_dispatch import authorized_stream
        held=False;exits=[]
        @asynccontextmanager
        async def gate():
            nonlocal held
            held=True
            try:yield
            finally:held=False;exits.append(True)
        async def handler(request):
            trace=request.extensions['trace']
            await trace('http11.send_request_headers.started',{'request':SimpleNamespace(method=b'CONNECT')})
            await trace('http11.send_request_body.complete',{'return_value':None})
            self.assertTrue(held,'建立代理隧道不能释放业务授权锁')
            await trace('http11.send_request_headers.started',{'request':SimpleNamespace(method=b'POST')})
            self.assertTrue(held)
            await trace('http11.send_request_body.complete',{'return_value':None})
            self.assertFalse(held)
            with self.assertRaises(httpx.TransportError):
                await trace('http11.send_request_headers.started',{'request':SimpleNamespace(method=b'POST')})
            return httpx.Response(200,json={})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            async with authorized_stream(client,'POST','https://fixture.test',dispatch_gate=gate,json={}):pass
        self.assertEqual(exits,[True])

class ConnectProxyNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_connect_tls_post_succeeds_and_releases_only_business_gate(self):
        import ssl
        from datetime import datetime,timedelta,timezone
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        from app.v3.http_dispatch import authorized_stream
        held=False;released=asyncio.Event();received=asyncio.Event();respond=asyncio.Event();closed=asyncio.Event();observed=[];failures=[]
        @asynccontextmanager
        async def gate():
            nonlocal held
            held=True
            try:yield
            finally:held=False;released.set()
        with tempfile.TemporaryDirectory() as temp:
            key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
            name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'fixture.test')])
            now=datetime.now(timezone.utc)
            cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1)).sign(key,hashes.SHA256()))
            root=Path(temp);(root/'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM));(root/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.TraditionalOpenSSL,serialization.NoEncryption()))
            tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(root/'cert.pem',root/'key.pem')
            async def proxy(reader,writer):
                try:
                    headers=await reader.readuntil(b'\r\n\r\n');observed.append(headers.split(b' ',1)[0])
                    self.assertTrue(held,'CONNECT头到达时必须仍有业务门禁')
                    writer.write(b'HTTP/1.1 200 Connection established\r\n\r\n');await writer.drain()
                    await writer.start_tls(tls)
                    headers=await reader.readuntil(b'\r\n\r\n');observed.append(headers.split(b' ',1)[0])
                    length=next(int(line.split(b':',1)[1]) for line in headers.split(b'\r\n') if line.lower().startswith(b'content-length:'))
                    await reader.readexactly(length);received.set()
                    await respond.wait()
                    writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}');await writer.drain()
                except Exception as error:failures.append(type(error).__name__);received.set()
                finally:
                    writer.close()
                    try:await writer.wait_closed()
                    except (ConnectionError,ssl.SSLError):pass
                    closed.set()
            server=await asyncio.start_server(proxy,'127.0.0.1',0)
            port=server.sockets[0].getsockname()[1]
            try:
                async with httpx.AsyncClient(proxy=f'http://127.0.0.1:{port}',verify=False,trust_env=False) as client:
                    async def send():
                        async with authorized_stream(client,'POST','https://fixture.test/path',dispatch_gate=gate,json={'fixture':True},timeout=3) as response:
                            return await response.aread()
                    task=asyncio.create_task(send())
                    try:
                        await asyncio.wait_for(received.wait(),3)
                        await asyncio.wait_for(released.wait(),2)
                    finally:
                        respond.set();result=await task
                    self.assertEqual(result,b'{}');self.assertEqual(observed,[b'CONNECT',b'POST']);self.assertEqual(failures,[])
                await asyncio.wait_for(closed.wait(),3)
            finally:
                respond.set();server.close();await server.wait_closed()

class ConnectGateSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_business_connect_is_explicitly_rejected(self):
        from app.v3.http_dispatch import authorized_stream
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200))) as client:
            with self.assertRaises(ValueError):
                async with authorized_stream(client,'CONNECT','https://fixture.test'):pass

    async def test_cancel_during_proxy_setup_releases_gate_once(self):
        from app.v3.http_dispatch import authorized_stream
        setup=asyncio.Event();exits=[];held=False
        @asynccontextmanager
        async def gate():
            nonlocal held
            held=True
            try:yield
            finally:held=False;exits.append(True)
        async def handler(request):
            trace=request.extensions['trace']
            await trace('http11.send_request_headers.started',{'request':SimpleNamespace(method=b'CONNECT')})
            await trace('http11.send_request_body.complete',{'return_value':None})
            self.assertTrue(held)
            setup.set();await asyncio.Event().wait()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            async def send():
                async with authorized_stream(client,'POST','https://fixture.test',dispatch_gate=gate,json={}):pass
            task=asyncio.create_task(send());await setup.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertFalse(held);self.assertEqual(exits,[True])
