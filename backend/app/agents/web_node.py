import asyncio

from app.core.config import settings


async def web_node(state: dict) -> dict:
    """联网搜索节点（Tavily）"""
    if not state.get("scope_web"):
        return {"retrieval_results": []}

    query = state["query"]

    if not settings.tavily_api_key:
        return {"retrieval_results": [], "error": "联网搜索未配置 Tavily API Key，请在配置后重试"}

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=settings.tavily_api_key)
        search_results = await asyncio.to_thread(client.search,
            query=query,
            max_results=settings.tavily_max_results,
        )

        results = []
        for item in search_results.get("results", []):
            results.append({
                "text": item.get("content", ""),
                "source_type": "web",
                "source_name": item.get("title", ""),
                "source_detail": item.get("url", ""),
                "score": item.get("score", 0.0),
            })

        return {"retrieval_results": results}
    except Exception:
        return {"retrieval_results": [], "error": "联网搜索失败，请检查配置或稍后重试"}
