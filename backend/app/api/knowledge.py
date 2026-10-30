import hashlib
import os
import uuid
import asyncio
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.core.auth import get_current_user
from app.core.config import config_manager
from app.core.paths import get_data_dir
from app.db import crud
from app.db.models import User
from app.vectorstore.chroma_store import delete_collection

router = APIRouter()


class KBCreate(BaseModel):
    name: str
    description: str = ""


def _upload_dir() -> str:
    upload_dir = get_data_dir() / config_manager.upload_dir_name
    upload_dir.mkdir(parents=True, exist_ok=True)
    return str(upload_dir)


@router.get("/list")
def list_kbs(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    kbs = crud.list_kbs(db)
    return [
        {
            "id": kb.id,
            "name": kb.name,
            "description": kb.description,
            "doc_count": kb.doc_count,
            "created_at": kb.created_at,
        }
        for kb in kbs
    ]


@router.post("/create")
def create_kb(req: KBCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    kb_id = str(uuid.uuid4())
    kb = crud.create_kb(db, id=kb_id, name=req.name, description=req.description)
    return kb


@router.post("/{kb_id}/upload")
async def upload_document(
    kb_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    kb = crud.get_kb(db, kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    # 文件大小检查
    content = await file.read()
    max_size = config_manager.max_file_size_mb * 1024 * 1024
    if len(content) > max_size:
        raise HTTPException(status_code=413, detail=f"文件超过 {config_manager.max_file_size_mb}MB 限制")

    file_hash = hashlib.md5(content).hexdigest()
    upload_dir = _upload_dir()

    doc_id = str(uuid.uuid4())
    save_path = os.path.join(upload_dir, f"{doc_id}_{file.filename}")
    with open(save_path, "wb") as f:
        f.write(content)

    doc = crud.create_doc(
        db,
        id=doc_id,
        kb_id=kb_id,
        filename=file.filename,
        file_path=save_path,
        file_hash=file_hash,
        file_size=len(content),
        status="pending",
    )

    # 后台处理
    from app.db.database import SessionLocal
    from app.pipeline.embedder import process_document
    from app.core.logging_config import get_logger

    bg_logger = get_logger("api.knowledge.upload")

    def run_process():
        bg_db = SessionLocal()
        try:
            asyncio.run(process_document(doc_id, kb_id, save_path, bg_db))
        except Exception as e:
            # 兜底：process_document 内部已有 try/except 会把状态置为 failed，
            # 这里捕获的是更外层的意外（比如 asyncio.run 本身出问题），
            # 防止文档状态永远卡在 processing、用户在界面上看不到任何反馈
            bg_logger.error(f"后台任务异常 (doc_id={doc_id}): {e}", exc_info=True)
            try:
                crud.update_doc_status(bg_db, doc_id, "failed", error_msg=f"后台任务异常: {e}")
            except Exception:
                pass   # 连状态更新都失败就放弃，至少日志文件里已经记录了
        finally:
            bg_db.close()

    background_tasks.add_task(run_process)

    return {"doc_id": doc_id, "filename": file.filename, "status": "pending"}


@router.get("/{kb_id}/documents")
def list_documents(kb_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    docs = crud.list_docs_by_kb(db, kb_id)
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "status": d.status,
            "chunk_count": d.chunk_count,
            "file_size": d.file_size,
            "created_at": d.created_at,
            "error_msg": d.error_msg,
        }
        for d in docs
    ]


@router.delete("/{kb_id}/documents/{doc_id}")
async def delete_document(
    kb_id: str,
    doc_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.pipeline.embedder import delete_document as _delete
    doc = crud.get_doc(db, doc_id)
    if not doc or doc.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="文档不存在或不属于该知识库")
    await _delete(doc_id, kb_id, db)
    return {"ok": True}


@router.delete("/{kb_id}")
async def delete_kb(
    kb_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    docs = crud.list_docs_by_kb(db, kb_id)
    for doc in docs:
        crud.delete_doc(db, doc.id)
    delete_collection(kb_id)
    crud.delete_kb(db, kb_id)
    return {"ok": True}
