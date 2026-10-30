import asyncio

from app.models.bge_model import bge_singleton

MAX_CONTEXT_CHUNKS = 8


async def rerank_node(state: dict) -> dict:
    """精排节点"""
    results = state.get("retrieval_results", [])

    if len(results) <= MAX_CONTEXT_CHUNKS:
        return {"reranked_results": results}

    query = state["query"]
    passages = [r["text"] for r in results]

    try:
        top_indices = await asyncio.to_thread(
            bge_singleton.rerank, query, passages, top_k=MAX_CONTEXT_CHUNKS
        )
        reranked = [results[i] for i in top_indices]
        return {"reranked_results": reranked}
    except Exception:
        return {"reranked_results": results[:MAX_CONTEXT_CHUNKS]}
