from app.pipeline.loader import load_document
from app.pipeline.splitter import split_pages
from app.vectorstore.chroma_store import upsert_chunks, delete_doc_chunks
from app.db import crud
from app.core.logging_config import get_logger

logger = get_logger("pipeline.embedder")


async def process_document(doc_id: str, kb_id: str, file_path: str, db):
    doc = crud.get_doc(db, doc_id)
    filename = doc.filename if doc else doc_id

    logger.info(f"[{filename}] 开始处理文档 (doc_id={doc_id}, kb_id={kb_id})")

    try:
        crud.update_doc_status(db, doc_id, "processing")

        logger.info(f"[{filename}] 步骤1/3：解析文档内容")
        pages = load_document(file_path)
        logger.info(f"[{filename}] 解析完成，共 {len(pages)} 页/段")

        logger.info(f"[{filename}] 步骤2/3：切分文本块")
        chunks = split_pages(pages)
        logger.info(f"[{filename}] 切分完成，共 {len(chunks)} 个文本块")

        if not chunks:
            logger.warning(f"[{filename}] 警告：文档解析后未产生任何文本块（可能是空文件或格式不支持提取文字）")
            crud.update_doc_status(db, doc_id, "done", chunk_count=0)
            crud.increment_kb_doc_count(db, kb_id)
            return

        enriched = [
            {
                "text": c["text"],
                "metadata": {
                    "doc_id": doc_id,
                    "filename": filename,
                    "page": c["page"],
                    "kb_id": kb_id,
                },
            }
            for c in chunks
        ]

        logger.info(f"[{filename}] 步骤3/3：调用 Embedding 服务并写入向量库（共 {len(enriched)} 块，将分批调用）")
        # upsert_chunks 内部调用 embedding_client，需要 db session 获取配置
        upsert_chunks(kb_id, enriched, db)

        crud.update_doc_status(db, doc_id, "done", chunk_count=len(chunks))
        crud.increment_kb_doc_count(db, kb_id)
        logger.info(f"[{filename}] ✓ 处理完成，已写入 {len(chunks)} 个文本块")

    except Exception as e:
        logger.error(f"[{filename}] ✗ 处理失败: {e}", exc_info=True)
        crud.update_doc_status(db, doc_id, "failed", error_msg=str(e))
        raise


async def delete_document(doc_id: str, kb_id: str, db):
    doc = crud.get_doc(db, doc_id)
    filename = doc.filename if doc else doc_id
    logger.info(f"[{filename}] 删除文档及其向量 (doc_id={doc_id}, kb_id={kb_id})")
    delete_doc_chunks(kb_id, doc_id)
    crud.delete_doc(db, doc_id)
    crud.decrement_kb_doc_count(db, kb_id)
    logger.info(f"[{filename}] 删除完成")
