# 外发授权边界：请求体发送前持有调用方检查门，发完即释放，避免等待模型期间长期占锁。
# trace 只用于识别发送阶段，其原始信息可能含秘密，不能记录。
"""授权锁只保持到 HTTP 请求体发送完成。

httpcore 1.0.9 会在等待响应头前触发 send_request_body.complete 事件。
不支持跟踪事件的自定义传输保守地持锁到响应头返回。
跟踪信息可能包含秘密，不得记录其原始内容。
"""
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import httpx


# 授权门由调用方提供；默认禁止重定向，响应读取无需继续持有发送门。
@asynccontextmanager
async def authorized_stream(client, method, url, *, dispatch_gate=None, **kwargs):
    business_method=method.encode('ascii').upper() if isinstance(method,str) else method.upper()
    if business_method==b'CONNECT':
        raise ValueError('Business CONNECT requests are not supported by authorized_stream')
    kwargs.setdefault('follow_redirects',False)
    gates=AsyncExitStack()
    release_task=None

    # 释放只创建一次任务，并确保取消发生时也完成上下文退出，再传播取消。
    async def release():
        nonlocal release_task
        if release_task is None:
            # trace 回调和 finally 都可能要求释放，复用同一任务避免重复退出事务上下文。
            release_task=asyncio.create_task(gates.aclose())
        try:
            await asyncio.shield(release_task)
        except asyncio.CancelledError:
            # 释放过程被取消时，也要等事务回滚或提交完成后再传播取消。
            await release_task
            raise

    extensions=dict(kwargs.pop('extensions',None) or {})
    previous=extensions.get('trace')
    # 保留调用方原有跟踪回调；下面只使用阶段事件，不记录可能含秘密的 info 内容。
    headers_started=False
    business_send=True
    async def trace(event,info):
        nonlocal headers_started,business_send
        if event in ('http11.send_request_headers.started','http2.send_request_headers.started'):
            request=info.get('request')
            request_method=getattr(request,'method',None)
            # 代理 CONNECT 仅建立隧道，不发送业务证据，不能据此释放资料授权门。
            business_send=request_method!=b'CONNECT'
            if not business_send:
                if previous is not None:await previous(event,info)
                return
            # httpcore 收到 HTTP/2 的 GOAWAY 后可能自动重试，必须在重试请求头写出前拦截。
            # 首次发送门释放后旧授权可能被撤回，因此拒绝底层自动重放同一业务请求。
            if headers_started:
                raise httpx.TransportError('Authorized request replay is disabled')
            headers_started=True
        # 请求体发完即可释放共享锁；不支持 trace 的自定义传输保守地等到响应头。
        if business_send and event in ('http11.send_request_body.complete','http2.send_request_body.complete'):
            await release()
        if previous is not None:
            await previous(event,info)
    if dispatch_gate is not None:
        extensions['trace']=trace
    try:
        if dispatch_gate is not None:
            # 授权检查和发送之间一直持共享门，权限写入只能在请求体发完、门释放后提交。
            await gates.enter_async_context(dispatch_gate())
        async with client.stream(method,url,extensions=extensions,**kwargs) as response:
            # 模拟传输及其他自定义传输可能不触发跟踪事件，此时在收到响应头后释放。
            await release()
            yield response
    finally:
        # 连接失败、正文发送失败或消费者提前关闭时，都必须完成授权事务的退出。
        await release()
