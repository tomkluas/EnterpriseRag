"""Read-only check of persisted Chroma indexes without an embedding service."""
import time
from app.db.database import SessionLocal
from app.db import crud
from app.vectorstore.chroma_store import get_or_create_collection


if __name__ == '__main__':
    with SessionLocal() as db:
        for kb in crud.list_kbs(db):
            started = time.monotonic()
            collection = get_or_create_collection(kb.id)
            count = collection.count()
            docs = crud.list_docs_by_kb(db, kb.id)
            metadata = {'document_records': len(docs), 'recorded_chunks': sum(d.chunk_count or 0 for d in docs), 'recorded_doc_count': kb.doc_count}
            sample = collection.get(limit=1, include=['embeddings'])
            if count and len(sample['embeddings']):
                vector = sample['embeddings'][0]
                result = collection.query(query_embeddings=[vector], n_results=min(5, count), include=['distances'])
                print({'kb_id': kb.id, **metadata, 'chunks': count, 'dimensions': len(vector), 'hits': len(result['ids'][0]), 'seconds': round(time.monotonic()-started, 3)}, flush=True)
            else:
                print({'kb_id': kb.id, **metadata, 'chunks': count}, flush=True)
