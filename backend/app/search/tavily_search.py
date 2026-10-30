"""
Tavily 搜索（备选，国际服务，需科学上网才能稳定访问）
"""
from tavily import TavilyClient
import asyncio
from app.search.base import BaseSearchProvider, SearchResult


class TavilySearchProvider(BaseSearchProvider):
    def __init__(self, api_key: str):
        self.client = TavilyClient(api_key=api_key)

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        resp = await asyncio.to_thread(self.client.search,
            query=query,
            max_results=max_results,
            search_depth="advanced",
            include_answer=True,
        )
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                content=r.get("content", ""),
                score=r.get("score", 0.5),
            )
            for r in resp.get("results", [])
        ]
