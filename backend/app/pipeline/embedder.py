import os
from pathlib import Path

from app.core.config import settings
from app.db.database import SessionLocal
from app.db import crud as db_crud
from app.models import bge_model
from app.pipeline.loader import load_document
from app.pipeline.splitter import split_pages


async def process_document(doc_id: str, kb_id: str, file_path: str) -> None:
    """
    文档处理流水线：
    1. 更新状态为 processing
    2. 加载文档
    3. 分块
    4. 入库到 Qdrant
    5. 更新状态为 done
    6. 更新知识库文档计数
    """
    db = SessionLocal()
    try:
        doc = db_crud.get_doc(db, doc_id)
        if not doc or doc.kb_id != kb_id or doc.status == "done" or not db_crud.get_kb(db, kb_id):
            return
        original_filename = doc.filename
        db_crud.update_doc_status(db, doc_id, "processing")

        pages = load_document(file_path)

        # 为页面添加 doc_id 和 kb_id
        for page in pages:
            page["doc_id"] = doc_id
            page["kb_id"] = kb_id
            # 保存路径使用随机名称；引用保留用户上传的文件名。
            loaded_name = page.get("filename", "")
            stored_name = os.path.basename(file_path)
            suffix = loaded_name.removeprefix(stored_name) if loaded_name.startswith(stored_name) else ""
            page["filename"] = original_filename + suffix

        chunks = split_pages(pages)

        await bge_model.bge_singleton.load()
        bge_model.upsert_chunks(kb_id, chunks)

        db_crud.update_doc_status(db, doc_id, "done", chunk_count=len(chunks), error_msg="")
        db_crud.increment_kb_doc_count(db, kb_id)

    except Exception as e:
        db_crud.update_doc_status(db, doc_id, "failed", error_msg=str(e))
    finally:
        db.close()


def remove_uploaded_file(file_path: str) -> None:
    path = Path(file_path).resolve()
    upload_root = Path(settings.upload_dir).resolve()
    if path.is_relative_to(upload_root) and path.is_file():
        path.unlink()


async def delete_document(doc_id: str, kb_id: str) -> None:
    """
    删除文档：
    1. 清理 Qdrant 中的向量
    2. 删除文档记录
    3. 减少知识库文档计数
    """
    db = SessionLocal()
    try:
        doc = db_crud.get_doc(db, doc_id)
        if not doc or doc.kb_id != kb_id:
            return
        was_done = doc.status == "done"
        bge_model.delete_doc_chunks(kb_id, doc_id)
        remove_uploaded_file(doc.file_path)
        db_crud.delete_doc(db, doc_id)
        if was_done:
            db_crud.decrement_kb_doc_count(db, kb_id)
    finally:
        db.close()
