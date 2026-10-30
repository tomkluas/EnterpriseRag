import hashlib
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.deps import get_db
from app.db.crud import list_kbs, get_kb, create_kb, delete_kb as db_delete_kb
from app.db.crud import create_doc, get_doc, list_docs_by_kb
from app.db.models import User, Document
from app.pipeline.embedder import process_document, delete_document
from app.models.bge_model import ensure_collection

router = APIRouter(prefix="/api/v1/knowledge", tags=["知识库"])


def _ensure_upload_dir():
    os.makedirs(settings.upload_dir, exist_ok=True)


def _compute_file_hash(file_path: str) -> str:
    md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            md5.update(chunk)
    return md5.hexdigest()


@router.get("/list")
def list_knowledge_bases(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list:
    kbs = list_kbs(db)
    completed_counts = dict(db.query(Document.kb_id, func.count(Document.id))
                            .filter(Document.status == "done").group_by(Document.kb_id).all())
    return [
        {
            "id": kb.id,
            "name": kb.name,
            "description": kb.description,
            "doc_count": completed_counts.get(kb.id, 0),
            "created_at": kb.created_at,
        }
        for kb in kbs
    ]


@router.post("/create")
def create_knowledge_base(
    name: str,
    description: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    name = name.strip()
    if not name or len(name) > 128:
        raise HTTPException(status_code=400, detail="知识库名称不能为空且不能超过128个字符")
    kb = create_kb(db, name=name, description=description)
    try:
        ensure_collection(kb.id)
    except Exception as exc:
        db_delete_kb(db, kb.id)
        raise HTTPException(status_code=503, detail="向量服务暂不可用") from exc
    return kb


@router.post("/{kb_id}/upload")
async def upload_document(
    kb_id: str,
    file: UploadFile,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    kb = get_kb(db, kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    filename = file.filename or ""
    suffix = os.path.splitext(filename)[1].lower()
    if not filename or "/" in filename or "\\" in filename or len(filename) > 256:
        raise HTTPException(status_code=400, detail="无效的文件名")
    if suffix not in {".pdf", ".docx", ".xlsx", ".txt", ".md"}:
        raise HTTPException(status_code=400, detail="不支持的文件格式")
    _ensure_upload_dir()

    # 保存文件
    file_path = os.path.join(settings.upload_dir, f"{uuid.uuid4().hex}{suffix}")
    try:
        with open(file_path, "xb") as buffer:
            written = 0
            while chunk := file.file.read(1024 * 1024):
                written += len(chunk)
                if written > settings.max_file_size_mb * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="文件超过大小限制")
                buffer.write(chunk)
        file_size = os.path.getsize(file_path)
        if not file_size:
            raise HTTPException(status_code=400, detail="文件不能为空")
        doc = create_doc(
            db, kb_id=kb_id, filename=filename, file_path=file_path,
            file_hash=_compute_file_hash(file_path), file_size=file_size, status="pending",
        )
    except Exception:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise

    background_tasks.add_task(process_document, doc.id, kb_id, file_path)

    return {"doc_id": doc.id, "filename": file.filename, "status": "pending"}


@router.get("/{kb_id}/documents")
def list_documents(
    kb_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not get_kb(db, kb_id):
        raise HTTPException(status_code=404, detail="知识库不存在")
    docs = list_docs_by_kb(db, kb_id)
    return [
        {
            "id": doc.id,
            "filename": doc.filename,
            "status": doc.status,
            "chunk_count": doc.chunk_count,
            "file_size": doc.file_size,
            "created_at": doc.created_at,
            "error_msg": doc.error_msg,
        }
        for doc in docs
    ]


@router.delete("/{kb_id}/documents/{doc_id}")
async def delete_document_endpoint(
    kb_id: str,
    doc_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    doc = get_doc(db, doc_id)
    if not doc or doc.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    if doc.status == "processing":
        raise HTTPException(status_code=409, detail="文档正在处理中，请稍后删除")

    # 同步删除向量
    try:
        await delete_document(doc_id, kb_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="文档清理失败，请稍后重试") from exc

    return {"message": "删除成功"}


@router.delete("/{kb_id}")
def delete_knowledge_base(
    kb_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")

    kb = get_kb(db, kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    docs = list_docs_by_kb(db, kb_id)
    if any(doc.status == "processing" for doc in docs):
        raise HTTPException(status_code=409, detail="知识库中有文档正在处理中，请稍后删除")
    # 向量服务失败时保留数据库记录，以便重试。
    from app.models.bge_model import get_qdrant
    client = get_qdrant()
    try:
        client.delete_collection(kb_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="向量清理失败，请稍后重试") from exc
    from app.pipeline.embedder import remove_uploaded_file
    for doc in docs:
        remove_uploaded_file(doc.file_path)
    db_delete_kb(db, kb_id)

    return {"message": "删除成功"}
