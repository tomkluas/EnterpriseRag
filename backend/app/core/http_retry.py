"""
HTTP 请求重试工具：处理国内访问境外/云服务商 API 时常见的瞬时网络故障。

典型场景：
  SSLEOFError: [SSL: UNEXPECTED_EOF_WHILE_READING]
  ConnectionResetError
  httpx.ReadTimeout / httpx.ConnectTimeout

这类错误绝大多数是网络链路上的中间设备（运营商/防火墙）粗暴掐断了连接，
并非服务端真的拒绝请求，重试 2-3 次通常就能成功，不应该让用户第一次失败
就看到报错——尤其是文档批量入库时，几十个分片只要有一个瞬时抖动就会导致
整个任务失败，体验很差。
"""
import time
import httpx


def post_with_retry(
    url: str,
    *,
    headers: dict,
    json: dict,
    timeout: float = 30.0,
    max_retries: int = 3,
    backoff_seconds: float = 1.5,
) -> httpx.Response:
    """
    带重试的 POST 请求（同步版）。对网络层异常（SSL/连接重置/超时）自动重试，
    对服务端明确返回的 4xx/5xx 错误不重试（重试也没用，直接把错误抛给上层处理）。
    """
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            return httpx.post(url, headers=headers, json=json, timeout=timeout)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            # TransportError 覆盖 SSLError/ConnectError/ReadError 等底层网络异常
            last_exc = e
            if attempt < max_retries:
                time.sleep(backoff_seconds * attempt)   # 简单线性退避
            continue
    raise RuntimeError(
        f"网络请求失败，已尝试 {max_retries} 次仍无法连接 {url}: {type(last_exc).__name__}: {last_exc}"
    ) from last_exc


async def async_post_with_retry(
    url: str,
    *,
    headers: dict,
    json: dict,
    timeout: float = 30.0,
    max_retries: int = 3,
    backoff_seconds: float = 1.5,
) -> httpx.Response:
    """带重试的 POST 请求（异步版），逻辑同 post_with_retry，供 async def 调用方使用"""
    import asyncio
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                return await client.post(url, headers=headers, json=json)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            last_exc = e
            if attempt < max_retries:
                await asyncio.sleep(backoff_seconds * attempt)
            continue
    raise RuntimeError(
        f"网络请求失败，已尝试 {max_retries} 次仍无法连接 {url}: {type(last_exc).__name__}: {last_exc}"
    ) from last_exc
