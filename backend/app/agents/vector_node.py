import asyncio

from app.db.database import SessionLocal
from app.db.crud import list_kbs
from app.models.bge_model import search_chunks


async def vector_node(state: dict) -> dict:
    """向量检索节点"""
    if not state.get("scope_vector"):
        return {"retrieval_results": []}

    query = state["query"]
    knowledge_ids = state.get("knowledge_ids", [])

    results, errors = [], []
    if not knowledge_ids:
        # 与前端默认范围一致：空数组表示全部知识库。
        with SessionLocal() as db:
            knowledge_ids = [kb.id for kb in list_kbs(db)]

    for kb_id in knowledge_ids:
        try:
            chunks = await asyncio.to_thread(search_chunks, kb_id, query, top_k=10)
            for chunk in chunks:
                results.append({
                    "text": chunk["text"],
                    "source_type": "vector",
                    "source_name": chunk.get("filename", ""),
                    "source_detail": f"第{chunk.get('page', 1)}页",
                    "score": chunk.get("score", 0.0),
                })
        except Exception:
            errors.append("知识库检索失败，请检查向量服务或文档处理状态")

    output = {"retrieval_results": results}
    if errors:
        output["error"] = "；".join(dict.fromkeys(errors))
    return output
