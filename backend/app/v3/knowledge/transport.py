from urllib.parse import urlsplit
from contextlib import AsyncExitStack
import httpx
from .contracts import BackendError
from ..http_dispatch import authorized_stream


class Transport:
    def __init__(self, base_url: str, key: str, client: httpx.AsyncClient, options=None):
        url = urlsplit(base_url)
        if url.scheme not in ('https', 'http') or not url.netloc or url.username or url.password or url.query or url.fragment:
            raise BackendError('invalid_configuration')
        self.base_url = base_url.rstrip('/')
        self.key = key
        self.client = client
        self.options = options or {}

    async def request(self, method: str, path: str, *, dispatch_gate=None, **kwargs):
        try:
            if dispatch_gate is None:
                response = await self.client.request(method, self.base_url + path,
                    headers=self.headers, timeout=httpx.Timeout(30, connect=5), follow_redirects=False, **kwargs)
            else:
                async with AsyncExitStack() as stack:
                    response = await stack.enter_async_context(authorized_stream(self.client,method,self.base_url+path,
                        headers=self.headers,timeout=httpx.Timeout(30,connect=5),follow_redirects=False,dispatch_gate=dispatch_gate,**kwargs))
                    await response.aread()
        except httpx.TimeoutException as error:
            raise BackendError('timeout_unknown') from error
        except httpx.HTTPError as error:
            raise BackendError('backend_unavailable') from error
        if response.status_code in (401, 403):
            raise BackendError('permission_denied')
        if response.status_code == 409:
            raise BackendError('conflict')
        if response.status_code == 404:
            raise BackendError('not_found')
        if response.status_code >= 300:
            raise BackendError('backend_unavailable')
        if response.status_code == 204:
            return {}
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError('object required')
            return data
        except ValueError as error:
            raise BackendError('invalid_response') from error
