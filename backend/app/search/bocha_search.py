"""
博查搜索（默认推荐，国内服务直连，无需科学上网）
API 文档：https://open.bochaai.com
POST https://api.bochaai.com/v1/web-search
Header: Authorization: Bearer {api_key}
Body: {"query": str, "count": int, "summary": true}
"""
from app.search.base import BaseSearchProvider, SearchResult
from app.core.http_retry import async_post_with_retry


class BochaSearchProvider(BaseSearchProvider):
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.bochaai.com/v1"

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        # async_post_with_retry：国内访问偶发 SSLEOFError，自动重试，避免单次抖动导致整次检索失败
        resp = await async_post_with_retry(
            f"{self.base_url}/web-search",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={"query": query, "count": max_results, "summary": True},
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()

        pages = data.get("data", {}).get("webPages", {}).get("value", [])
        return [
            SearchResult(
                title=r.get("name", ""),
                url=r.get("url", ""),
                content=r.get("snippet", ""),
                score=float(r.get("score", 0.5)),
            )
            for r in pages
        ]
