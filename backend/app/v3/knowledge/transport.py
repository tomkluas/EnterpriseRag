# 底座共用 HTTP 层：校验服务地址，接入发送时授权门，并统一错误码供任务层决策。
from urllib.parse import urlsplit
from contextlib import AsyncExitStack
import httpx
from .contracts import BackendError
from ..http_dispatch import authorized_stream


class Transport:
    # 地址禁止嵌入账号、查询参数及片段，避免在地址中携带秘密或改变固定接口路径。
    def __init__(self, base_url: str, key: str, client: httpx.AsyncClient, options=None):
        url = urlsplit(base_url)
        if url.scheme not in ('https', 'http') or not url.netloc or url.username or url.password or url.query or url.fragment:
            raise BackendError('invalid_configuration')
        self.base_url = base_url.rstrip('/')
        self.key = key
        self.client = client
        self.options = options or {}

    # 有授权门时用流式包装器控制外发边界；读完整响应后统一解析 JSON 对象。
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
        # 超时表示结果未知，写操作可能已在远端成功，任务层不能直接盲目重传。
        except httpx.TimeoutException as error:
            raise BackendError('timeout_unknown') from error
        except httpx.HTTPError as error:
            raise BackendError('backend_unavailable') from error
        if response.status_code in (401, 403):
            # 用固定错误码分类，不透传远端响应正文，避免泄露凭据、内部地址或平台细节。
            raise BackendError('permission_denied')
        if response.status_code == 409:
            raise BackendError('conflict')
        if response.status_code == 404:
            raise BackendError('not_found')
        if response.status_code >= 300:
            raise BackendError('backend_unavailable')
        if response.status_code == 204:
            # 删除接口可能成功却没有响应体，此时不尝试解析 JSON。
            return {}
        try:
            data = response.json()
            if not isinstance(data, dict):
                # 上层适配器依赖对象字段；数组或标量即使是合法 JSON 也不满足本层契约。
                raise ValueError('object required')
            return data
        except ValueError as error:
            raise BackendError('invalid_response') from error
