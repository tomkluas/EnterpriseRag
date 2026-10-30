from app.vectorstore.chroma_store import search_chunks
from app.vectorstore import chroma_store
from app.db import crud
import asyncio
import time
from app.models import embedding_client
from app.core.logging_config import get_logger

logger = get_logger("agents.vector")


async def vector_node(state: dict) -> dict:
    if not state.get("scope_vector"):
        return {"retrieval_results": []}

    query = state["query"]
    knowledge_ids = state.get("knowledge_ids") or []
    db = state.get("_db")

    # 如果未指定知识库，查全部
    if not knowledge_ids and db:
        kbs = crud.list_kbs(db)
        knowledge_ids = [kb.id for kb in kbs]

    if not knowledge_ids:
        return {"retrieval_results": []}

    searchable, errors = [], []
    for kb_id in knowledge_ids:
        try:
            count = await asyncio.to_thread(lambda key=kb_id: chroma_store.get_or_create_collection(key).count())
            if count == 0:
                kb = crud.get_kb(db, kb_id) if db else None
                name = kb.name if kb else kb_id
                errors.append(f"知识库「{name}」的向量索引为空，请重建索引后重试")
            else:
                searchable.append(kb_id)
        except Exception as exc:
            logger.warning("知识库 %s 索引检查失败：%s", kb_id, exc)
            errors.append(f"知识库 {kb_id} 索引读取失败：{exc}")
    if not searchable:
        return {"retrieval_results": [], "retrieval_errors": errors}

    started = time.monotonic()
    logger.info("Embedding 查询开始，知识库数量=%d", len(knowledge_ids))
    try:
        client = embedding_client.get_embedding_client(db)
        query_vector = await client.aembed_one(query)
    except Exception as exc:
        logger.warning("Embedding 查询失败，耗时=%.2fs，错误=%s", time.monotonic() - started, exc)
        return {"retrieval_results": [], "retrieval_errors": errors + [f"知识库检索失败：{exc}"]}
    logger.info("Embedding 查询完成，耗时=%.2fs", time.monotonic() - started)
    results = []
    for kb_id in searchable:
        try:
            # Chroma 的同步磁盘/索引操作不占用事件循环；线程不接触请求的 DB session。
            hits = await asyncio.to_thread(search_chunks, kb_id, query, None, top_k=5, query_vector=query_vector)
            for hit in hits:
                results.append({
                    "text": hit["text"],
                    "source_type": "vector",
                    "source_name": hit.get("filename", "未知文件"),
                    "source_detail": f"第{hit.get('page', 1)}页",
                    "score": float(hit.get("score", 0.0)),
                })
        except Exception as exc:
            logger.warning("知识库 %s 检索失败：%s", kb_id, exc)
            errors.append(f"知识库 {kb_id} 检索失败：{exc}")

    logger.info("向量检索完成，结果=%d，耗时=%.2fs", len(results), time.monotonic() - started)
    return {"retrieval_results": results, "retrieval_errors": errors}
