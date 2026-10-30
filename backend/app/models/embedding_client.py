"""
统一 Embedding 客户端：调用国内在线 API（智谱/通义），不在本地加载任何模型。

重要：由于 Embedding 配置存储在 SQLite/config.json 而非环境变量，
embedding_client 不能是模块加载时创建的全局单例，必须在请求上下文中
通过 get_embedding_client(db) 按需创建。
"""
import httpx
from app.core import http_retry
from app.core.security import decrypt
from app.core.http_retry import post_with_retry
from app.db import crud

# 国内 Embedding 服务商接口适配
EMBEDDING_ENDPOINTS = {
    "zhipu": "https://open.bigmodel.cn/api/paas/v4/embeddings",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
}

# 智谱 embedding-3：单次请求最多 64 条文本，单条最长 8192 tokens
# 通义 text-embedding-v3/v4：单次请求最多 10 条文本（v1/v2 才是 25 条，v3 起收紧为 10）
MAX_BATCH_SIZE = {
    "zhipu": 64,
    "qwen": 10,
}

EMBEDDING_DEFAULT_MODEL = {
    "zhipu": "embedding-3",
    "qwen": "text-embedding-v3",
}


def resolve_embedding_model(provider: str, model: str | None) -> str:
    if provider not in EMBEDDING_DEFAULT_MODEL:
        raise ValueError(f"不支持的 Embedding 服务商: {provider}")
    default = EMBEDDING_DEFAULT_MODEL[provider]
    model = (model or "").strip() or default
    if model.lower() in EMBEDDING_DEFAULT_MODEL:
        raise ValueError(f"{model} 是服务商标识，不是 Embedding 模型名；请填写 {default}，或留空使用默认模型")
    return model


class EmbeddingClient:
    """统一 Embedding 客户端，调用在线 API，不在本地加载任何模型"""

    def __init__(self, db):
        cfg = crud.get_embedding_config(db)
        if not cfg or not cfg.get("api_key_enc"):
            raise RuntimeError('尚未配置 Embedding API Key，请前往"管理 → 系统设置"页面配置')
        self.provider = cfg["provider"]
        self.model = resolve_embedding_model(self.provider, cfg.get("model"))
        self.api_key = decrypt(cfg["api_key_enc"])
        self.endpoint = EMBEDDING_ENDPOINTS.get(self.provider)
        if not self.endpoint:
            raise ValueError(f"不支持的 Embedding 服务商: {self.provider}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        """
        智谱/通义的 embeddings 接口均兼容 OpenAI 格式：
        请求: {"model": str, "input": list[str]}
        响应: {"data": [{"embedding": list[float], "index": int}, ...]}

        使用 post_with_retry 而非直接 httpx.post：国内访问阿里云/智谱偶发
        SSLEOFError（连接被中间网络设备意外截断），属于瞬时故障，重试即可恢复，
        不应该让用户第一次遇到网络抖动就看到报错。
        """
        resp = post_with_retry(
            self.endpoint,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": texts},
            timeout=httpx.Timeout(20.0, connect=5.0),
        )
        return self._parse_response(resp)

    async def aembed_one(self, text: str) -> list[float]:
        resp = await http_retry.async_post_with_retry(
            self.endpoint,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": [text]},
            timeout=httpx.Timeout(15.0, connect=5.0),
            max_retries=2,
        )
        return self._parse_response(resp)[0]

    def _parse_response(self, resp: httpx.Response) -> list[list[float]]:
        if resp.status_code >= 400:
            # 直接抛出服务商返回的真实错误原因（如批量超限、模型名错误、余额不足等），
            # 不能只用 raise_for_status()，否则前端只能看到"400 Bad Request"，无法定位问题
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise RuntimeError(f"Embedding API 调用失败 ({self.provider}, HTTP {resp.status_code}): {detail}")
        data = resp.json()
        # 按 index 排序，确保返回顺序与输入一致
        sorted_data = sorted(data["data"], key=lambda x: x["index"])
        if not sorted_data:
            raise RuntimeError("Embedding API 未返回向量")
        return [item["embedding"] for item in sorted_data]

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


def get_embedding_client(db) -> EmbeddingClient:
    """工厂函数：每次按需创建，因为配置可能随时变化"""
    return EmbeddingClient(db)


def embed_in_batches(client: EmbeddingClient, texts: list[str]) -> list[list[float]]:
    """按服务商批量限制分批调用，避免单次请求超限报错"""
    batch_size = MAX_BATCH_SIZE.get(client.provider, 20)
    all_vectors = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        all_vectors.extend(client.embed(batch))
    return all_vectors
