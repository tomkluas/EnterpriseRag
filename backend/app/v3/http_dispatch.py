"""Hold authorization only until HTTP request bytes have been sent.

httpcore 1.0.9 emits send_request_body.complete before waiting for response
headers. Custom transports without trace support conservatively hold the gate
until headers arrive. Trace info may include secrets and must never be logged.
"""
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import httpx


@asynccontextmanager
async def authorized_stream(client, method, url, *, dispatch_gate=None, **kwargs):
    business_method=method.encode('ascii').upper() if isinstance(method,str) else method.upper()
    if business_method==b'CONNECT':
        raise ValueError('Business CONNECT requests are not supported by authorized_stream')
    kwargs.setdefault('follow_redirects',False)
    gates=AsyncExitStack()
    release_task=None

    async def release():
        nonlocal release_task
        if release_task is None:
            release_task=asyncio.create_task(gates.aclose())
        try:
            await asyncio.shield(release_task)
        except asyncio.CancelledError:
            # Complete rollback/commit even when cancellation interrupts release.
            await release_task
            raise

    extensions=dict(kwargs.pop('extensions',None) or {})
    previous=extensions.get('trace')
    headers_started=False
    business_send=True
    async def trace(event,info):
        nonlocal headers_started,business_send
        if event in ('http11.send_request_headers.started','http2.send_request_headers.started'):
            request=info.get('request')
            request_method=getattr(request,'method',None)
            # HTTPS proxy setup uses the same trace extension for its CONNECT.
            # It neither sends business evidence nor completes business dispatch.
            business_send=request_method!=b'CONNECT'
            if not business_send:
                if previous is not None:await previous(event,info)
                return
            # httpcore can transparently retry an HTTP/2 request after GOAWAY.
            # Once the initial dispatch gate was released, that retry would
            # reuse authorization from before a possible revocation. Reject
            # same-request replays before any retry headers are written.
            if headers_started:
                raise httpx.TransportError('Authorized request replay is disabled')
            headers_started=True
        if business_send and event in ('http11.send_request_body.complete','http2.send_request_body.complete'):
            await release()
        if previous is not None:
            await previous(event,info)
    if dispatch_gate is not None:
        extensions['trace']=trace
    try:
        if dispatch_gate is not None:
            await gates.enter_async_context(dispatch_gate())
        async with client.stream(method,url,extensions=extensions,**kwargs) as response:
            # MockTransport and other custom transports may not emit trace events.
            await release()
            yield response
    finally:
        await release()
