from langchain.text_splitter import RecursiveCharacterTextSplitter
from app.core.config import config_manager

ZH_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "……", "—", "，", " ", ""]

splitter = RecursiveCharacterTextSplitter(
    separators=ZH_SEPARATORS,
    chunk_size=config_manager.chunk_size,
    chunk_overlap=config_manager.chunk_overlap,
    length_function=len,
)


def split_pages(pages: list[dict]) -> list[dict]:
    """
    输入: list[{"text", "page", "filename"}]
    输出: list[{"text", "page", "filename"}]  # 过滤空块
    """
    chunks = []
    for page in pages:
        raw_chunks = splitter.split_text(page["text"])
        for c in raw_chunks:
            if c.strip():
                chunks.append({
                    "text": c,
                    "page": page["page"],
                    "filename": page["filename"],
                })
    return chunks

