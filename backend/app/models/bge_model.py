import asyncio
import threading
import uuid
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue, FilterSelector
from FlagEmbedding import BGEM3FlagModel, FlagReranker

from app.core.config import settings

VECTOR_DIM = 1024


class BGESingleton:
    _instance: Optional["BGESingleton"] = None
    _embedding_model: Optional[BGEM3FlagModel] = None
    _reranker_model: Optional[FlagReranker] = None
    _loaded: bool = False
    _load_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def load(self) -> None:
        await asyncio.to_thread(self._ensure_loaded)

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return

        with self._load_lock:
            if self._loaded:
                return
            print(f"[BGE] 加载 Embedding 模型: {settings.bge_model_path}...")
            embedding_model = BGEM3FlagModel(
                settings.bge_model_path,
                device=settings.bge_device,
                use_fp16=False,
            )
            print(f"[BGE] Embedding 模型加载完成")

            print(f"[BGE] 加载 Reranker 模型: {settings.bge_reranker_path}...")
            reranker_model = FlagReranker(
                settings.bge_reranker_path,
                device=settings.bge_device,
                use_fp16=False,
            )
            print(f"[BGE] Reranker 模型加载完成")

            self._embedding_model = embedding_model
            self._reranker_model = reranker_model
            self._loaded = True

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self._ensure_loaded()
        outputs = self._embedding_model.encode(
            texts,
            return_dense=True,
            batch_size=settings.bge_max_batch_size,
        )
        return outputs["dense_vecs"].tolist()

    def embed_query(self, query: str) -> list[float]:
        self._ensure_loaded()
        outputs = self._embedding_model.encode(
            [query],
            return_dense=True,
        )
        return outputs["dense_vecs"][0].tolist()

    def rerank(self, query: str, passages: list[str], top_k: int = 5) -> list[int]:
        self._ensure_loaded()
        scores = self._reranker_model.compute_score([[query, passage] for passage in passages])
        if isinstance(scores, (int, float)):
            scores = [scores]
        sorted_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return sorted_indices[:top_k]


bge_singleton = BGESingleton()


def get_qdrant() -> QdrantClient:
    return QdrantClient(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
        api_key=settings.qdrant_api_key if settings.qdrant_api_key else None,
    )


def ensure_collection(kb_id: str) -> None:
    client = get_qdrant()
    collections = [c.name for c in client.get_collections().collections]
    if kb_id not in collections:
        client.create_collection(
            collection_name=kb_id,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )


def upsert_chunks(kb_id: str, chunks: list[dict]) -> None:
    ensure_collection(kb_id)
    client = get_qdrant()

    points = []
    for idx, chunk in enumerate(chunks):
        text = chunk["text"]
        vector = bge_singleton.embed_texts([text])[0]
        metadata = chunk.get("metadata", {})
        point = PointStruct(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{kb_id}:{metadata.get('doc_id', '')}:{idx}")),
            vector=vector,
            payload={
                "text": text,
                "doc_id": metadata.get("doc_id", ""),
                "filename": metadata.get("filename", ""),
                "page": metadata.get("page", 1),
                "kb_id": metadata.get("kb_id", kb_id),
            },
        )
        points.append(point)

    client.upsert(
        collection_name=kb_id,
        points=points,
    )


def search_chunks(
    kb_id: str,
    query: str,
    top_k: int = 10,
    doc_id_filter: Optional[str] = None,
) -> list[dict]:
    client = get_qdrant()
    query_vector = bge_singleton.embed_query(query)

    search_params = {}
    if doc_id_filter:
        search_params["doc_id"] = doc_id_filter

    results = client.search(
        collection_name=kb_id,
        query_vector=query_vector,
        limit=top_k,
    )

    chunks = []
    for hit in results:
        payload = hit.payload
        chunks.append({
            "text": payload["text"],
            "score": hit.score,
            "filename": payload.get("filename", ""),
            "page": payload.get("page", 1),
            "doc_id": payload.get("doc_id", ""),
        })
    return chunks


def delete_doc_chunks(kb_id: str, doc_id: str) -> None:
    client = get_qdrant()
    client.delete(
        collection_name=kb_id,
        points_selector=FilterSelector(filter=Filter(must=[
            FieldCondition(key="doc_id", match=MatchValue(value=doc_id))
        ])),
        wait=True,
    )
