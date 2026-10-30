from app.search import get_search_provider
from app.core.logging_config import get_logger

logger = get_logger("agents.web")


async def web_node(state: dict) -> dict:
    if not state.get("scope_web"):
        return {"retrieval_results": []}

    db = state.get("_db")
    provider = get_search_provider(db)
    if provider is None:
        return {"retrieval_results": [], "retrieval_errors": ["尚未配置联网搜索服务，请在管理页面配置"]}

    results = []
    try:
        hits = await provider.search(state["query"], max_results=5)
        for r in hits:
            results.append({
                "text": r.content,
                "source_type": "web",
                "source_name": r.title,
                "source_detail": r.url,
                "score": r.score,
            })
    except Exception as exc:
        logger.warning("联网搜索失败：%s", exc)
        return {"retrieval_results": [], "retrieval_errors": [f"联网搜索失败：{exc}"]}

    return {"retrieval_results": results}
