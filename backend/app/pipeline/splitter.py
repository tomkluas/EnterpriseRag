from typing import List

from langchain.text_splitter import RecursiveCharacterTextSplitter

from app.core.config import settings

ZH_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "……", "—", "，", " ", ""]


def create_splitter():
    return RecursiveCharacterTextSplitter(
        separators=ZH_SEPARATORS,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        length_function=len,
    )


def split_pages(pages: List[dict]) -> List[dict]:
    """
    将页面列表分块，返回块列表，每块包含 text 和 metadata。
    """
    splitter = create_splitter()
    chunks = []

    for page in pages:
        texts = splitter.split_text(page["text"])
        for text in texts:
            if text.strip():
                chunks.append({
                    "text": text,
                    "metadata": {
                        "doc_id": page.get("doc_id", ""),
                        "filename": page.get("filename", ""),
                        "page": page.get("page", 1),
                        "kb_id": page.get("kb_id", ""),
                    },
                })

    return chunks