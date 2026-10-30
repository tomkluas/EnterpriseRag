"""
ChromaDB 嵌入式向量库封装。

相对 Qdrant 版本的关键差异：
- Chroma 以 Python 库形式运行在同一进程内，数据持久化为本地 SQLite + Parquet 文件，
  不需要单独的服务进程、不需要端口、不需要 Docker。
- search_chunks 的相似度计算从原生 score 改为 1 - distance
  （Chroma 默认返回 cosine distance 而非 similarity，需要这个转换）。
- upsert_chunks / search_chunks 均新增 db 参数，因为 Embedding 客户端的配置
  存储在 config.json 中，需要通过 db 上下文获取（实际未必查表，但保持签名一致）。
"""
import uuid
import threading
import chromadb
from chromadb.config import Settings as ChromaSettings
from chromadb.api.types import EmbeddingFunction
from app.core.paths import get_data_dir
from app.models.embedding_client import get_embedding_client, embed_in_batches

_client = None
_client_lock = threading.Lock()


class DummyEmbeddingFunction(EmbeddingFunction):
    """
    空嵌入函数：不做任何实际嵌入计算，避免 ChromaDB 加载 onnxruntime。
    实际嵌入由我们自己的 embedding_client 处理。
    """
    def __call__(self, input: list[str]) -> list[list[float]]:
        raise NotImplementedError("嵌入计算由外部 embedding_client 处理")


def get_chroma_client():
    """全局单例，数据存储在 data/chroma/ 目录"""
    global _client
    with _client_lock:
        if _client is None:
            chroma_path = get_data_dir() / "chroma"
            chroma_path.mkdir(parents=True, exist_ok=True)
            _client = chromadb.PersistentClient(
                path=str(chroma_path),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
    return _client


def get_or_create_collection(kb_id: str):
    client = get_chroma_client()
    return client.get_or_create_collection(
        name=kb_id,
        metadata={"hnsw:space": "cosine"},
        embedding_function=DummyEmbeddingFunction(),
    )


def upsert_chunks(kb_id: str, chunks: list[dict], db):
    """
    chunks 格式：
    [{"text": str, "metadata": {"doc_id":str,"filename":str,"page":int,"kb_id":str}}]
    """
    collection = get_or_create_collection(kb_id)
    texts = [c["text"] for c in chunks]

    client = get_embedding_client(db)
    vectors = embed_in_batches(client, texts)

    ids = [str(uuid.uuid4()) for _ in chunks]
    metadatas = [c["metadata"] for c in chunks]

    collection.add(
        ids=ids,
        embeddings=vectors,
        documents=texts,
        metadatas=metadatas,
    )


def search_chunks(kb_id: str, query: str, db, top_k: int = 10,
                  doc_id_filter: str | None = None,
                  query_vector: list[float] | None = None) -> list[dict]:
    collection = get_or_create_collection(kb_id)
    q_vec = query_vector if query_vector is not None else get_embedding_client(db).embed_one(query)

    where = {"doc_id": doc_id_filter} if doc_id_filter else None
    results = collection.query(
        query_embeddings=[q_vec],
        n_results=top_k,
        where=where,
    )

    if not results["ids"][0]:
        return []

    output = []
    for i in range(len(results["ids"][0])):
        meta = results["metadatas"][0][i]
        output.append({
            "text": results["documents"][0][i],
            "score": 1 - results["distances"][0][i],   # cosine distance → similarity
            "filename": meta.get("filename", ""),
            "page": meta.get("page", 0),
            "doc_id": meta.get("doc_id", ""),
        })
    return output


def delete_doc_chunks(kb_id: str, doc_id: str):
    collection = get_or_create_collection(kb_id)
    collection.delete(where={"doc_id": doc_id})


def delete_collection(kb_id: str):
    client = get_chroma_client()
    try:
        client.delete_collection(kb_id)
    except Exception:
        pass   # collection 不存在时静默处理
