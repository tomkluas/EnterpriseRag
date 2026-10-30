"""
精排节点：v4 不引入额外的 Reranker API（增加复杂度和成本），
改为按向量相似度 score 直接排序截断。
对中小知识库（数千文档块）该策略已经足够准确。
"""

MAX_CONTEXT_CHUNKS = 8


async def rerank_node(state: dict) -> dict:
    results = state.get("retrieval_results", [])
    sorted_results = sorted(results, key=lambda x: x["score"], reverse=True)
    return {"reranked_results": sorted_results[:MAX_CONTEXT_CHUNKS]}
