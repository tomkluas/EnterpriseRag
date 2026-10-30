import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np

from app.models import bge_model
from app.pipeline.embedder import process_document


class BGELoadingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.model = bge_model.bge_singleton
        self.saved = (self.model._loaded, self.model._embedding_model, self.model._reranker_model)
        self.model._loaded = False
        self.model._embedding_model = None
        self.model._reranker_model = None
        self.embedding = MagicMock()
        self.embedding.encode.return_value = {"dense_vecs": np.zeros((1, 1024))}
        self.reranker = MagicMock()
        self.reranker.compute_score.return_value = [0.2, 0.9]
        self.embedding_factory = patch.object(bge_model, "BGEM3FlagModel", return_value=self.embedding).start()
        self.reranker_factory = patch.object(bge_model, "FlagReranker", return_value=self.reranker).start()

    def tearDown(self):
        patch.stopall()
        self.model._loaded, self.model._embedding_model, self.model._reranker_model = self.saved

    async def test_first_embedding_loads_models_and_concurrent_calls_reuse_them(self):
        results = await asyncio.gather(*(
            asyncio.to_thread(self.model.embed_texts, ["document"]) for _ in range(4)
        ))
        self.assertEqual(len(results), 4)
        self.embedding_factory.assert_called_once()
        self.reranker_factory.assert_called_once()

    async def test_query_and_rerank_load_before_use(self):
        self.assertEqual(len(self.model.embed_query("query")), 1024)
        self.assertEqual(self.model.rerank("query", ["one", "two"]), [1, 0])
        self.reranker.compute_score.assert_called_once_with([["query", "one"], ["query", "two"]])

    async def test_single_passage_score_is_supported(self):
        self.reranker.compute_score.return_value = 0.9
        self.assertEqual(self.model.rerank("query", ["one"]), [0])

    async def test_failed_load_can_be_retried(self):
        self.reranker_factory.side_effect = [RuntimeError("load failed"), self.reranker]
        with self.assertRaisesRegex(RuntimeError, "load failed"):
            await self.model.load()
        self.assertFalse(self.model._loaded)
        self.assertIsNone(self.model._embedding_model)
        await self.model.load()
        self.assertTrue(self.model._loaded)

    async def test_pipeline_loads_before_upsert_and_clears_previous_error(self):
        events = []
        async def load():
            events.append("loaded")

        def upsert(*args):
            self.assertEqual(events, ["loaded"])

        with patch("app.pipeline.embedder.SessionLocal") as sessions, \
             patch("app.pipeline.embedder.db_crud") as crud, \
             patch("app.pipeline.embedder.load_document", return_value=[]), \
             patch("app.pipeline.embedder.split_pages", return_value=[{"text": "test"}]), \
             patch.object(self.model, "load", new=AsyncMock(side_effect=load)), \
             patch.object(bge_model, "upsert_chunks", side_effect=upsert):
            crud.get_doc.return_value.kb_id = "kb"
            await process_document("doc", "kb", "unused.txt")
            crud.update_doc_status.assert_any_call(
                sessions.return_value, "doc", "done", chunk_count=1, error_msg=""
            )


class ChunkIdentityTests(unittest.TestCase):
    def test_different_documents_do_not_overwrite_and_retries_keep_ids(self):
        with patch.object(bge_model, "ensure_collection"), \
             patch.object(bge_model, "get_qdrant") as client, \
             patch.object(bge_model.bge_singleton, "embed_texts", return_value=[[0.0] * 1024]):
            ids = []
            for doc_id in ("first", "second", "first"):
                bge_model.upsert_chunks("kb", [{"text": "text", "metadata": {"doc_id": doc_id}}])
                ids.append(client.return_value.upsert.call_args.kwargs["points"][0].id)
            self.assertNotEqual(ids[0], ids[1])
            self.assertEqual(ids[0], ids[2])
