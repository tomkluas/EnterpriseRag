import asyncio
import threading
import unittest
from unittest.mock import patch

from app.agents.rerank_node import rerank_node
from app.agents.vector_node import vector_node


class RetrievalNonblockingTests(unittest.IsolatedAsyncioTestCase):
    async def assert_worker_releases_event_loop(self, target, node, state, worker_result):
        started, release = threading.Event(), threading.Event()
        released_by_loop = []

        def blocking_model(*args, **kwargs):
            started.set()
            # The timeout only prevents a regression from deadlocking the test.
            # Success depends on the event loop signaling release, not elapsed time.
            released_by_loop.append(release.wait(timeout=5))
            return worker_result

        with patch(target, side_effect=blocking_model):
            task = asyncio.create_task(node(state))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 5))
                release.set()
                result = await task
            finally:
                release.set()
                await task
        self.assertEqual(released_by_loop, [True], 'model work blocked the event loop')
        return result

    async def test_vector_search_runs_without_blocking_event_loop(self):
        result = await self.assert_worker_releases_event_loop(
            'app.agents.vector_node.search_chunks', vector_node,
            {'scope_vector': True, 'knowledge_ids': ['kb'], 'query': 'question'},
            [{'text': 'evidence', 'filename': 'notes.txt'}],
        )
        self.assertEqual(result['retrieval_results'][0]['text'], 'evidence')

    async def test_reranker_runs_without_blocking_event_loop(self):
        chunks = [{'text': str(index)} for index in range(9)]
        result = await self.assert_worker_releases_event_loop(
            'app.agents.rerank_node.bge_singleton.rerank', rerank_node,
            {'query': 'question', 'retrieval_results': chunks}, [8, 0],
        )
        self.assertEqual(result['reranked_results'], [chunks[8], chunks[0]])
