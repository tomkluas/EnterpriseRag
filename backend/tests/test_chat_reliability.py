import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

import httpx
from app.agents.vector_node import vector_node
from app.models import embedding_client
from app.api import chat
from app.agents.generate_node import generate_node


class RetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_index_reports_rebuild_needed_without_calling_cloud(self):
        with patch('app.vectorstore.chroma_store.get_or_create_collection') as collection, patch('app.models.embedding_client.get_embedding_client', side_effect=AssertionError('cloud should not be needed')):
            collection.return_value.count.return_value = 0
            result = await vector_node({'scope_vector': True, 'query': 'q', 'knowledge_ids': ['missing-index'], '_db': None})
        self.assertEqual(result['retrieval_results'], [])
        self.assertIn('索引', ' '.join(result.get('retrieval_errors', [])))

    async def test_slow_embedding_does_not_block_other_requests(self):
        ticks = []
        def sync_http(*args, **kwargs):
            time.sleep(.12)
            return httpx.Response(200, json={'data': [{'index': 0, 'embedding': [1., 0.]}]})
        async def async_http(*args, **kwargs):
            await asyncio.sleep(.12)
            return httpx.Response(200, json={'data': [{'index': 0, 'embedding': [1., 0.]}]})
        async def heartbeat():
            await asyncio.sleep(.02)
            ticks.append(time.monotonic())
        cfg = {'provider': 'qwen', 'model': 'text-embedding-v3', 'api_key_enc': 'encrypted'}
        start = time.monotonic()
        with patch.object(embedding_client.crud, 'get_embedding_config', return_value=cfg), \
             patch.object(embedding_client, 'decrypt', return_value='fake'), \
             patch('httpx.post', side_effect=sync_http), \
             patch('app.core.http_retry.async_post_with_retry', side_effect=async_http), \
             patch('app.vectorstore.chroma_store.get_or_create_collection') as collection:
            collection.return_value.query.return_value = {'ids': [[]]}
            await asyncio.gather(vector_node({'scope_vector': True, 'query': 'q', 'knowledge_ids': ['knowledge'], '_db': None}), heartbeat())
        self.assertLess(ticks[0] - start, .08, 'Embedding blocked the event loop')

    async def test_failed_retrieval_is_not_silently_reported_as_empty(self):
        with patch('app.models.embedding_client.get_embedding_client', side_effect=RuntimeError('Embedding connection failed')), \
             patch('app.vectorstore.chroma_store.get_or_create_collection') as collection, \
             patch('app.vectorstore.chroma_store.get_embedding_client', side_effect=RuntimeError('Embedding connection failed')):
            collection.return_value.count.return_value = 1
            result = await vector_node({'scope_vector': True, 'query': 'q', 'knowledge_ids': ['knowledge'], '_db': None})
        self.assertTrue(result.get('retrieval_errors'), 'failure must reach the chat API')


class ChatStreamTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, model, state=None):
        graph = SimpleNamespace(ainvoke=AsyncMock(return_value=state or {'_system_prompt': 'system', '_user_message': 'question'}))
        with patch.object(chat, 'get_llm', return_value=model), patch('app.agents.graph.rag_graph', graph):
            response = await chat.chat_stream(chat.ChatRequest(session_id='s', query='q', scope_vector=False), db=None, current_user=None)
            frames = [frame async for frame in response.body_iterator]
        return [json.loads(frame[6:]) for frame in frames if frame.startswith('data: ')]

    async def test_model_tokens_end_with_done(self):
        class Model:
            async def astream(self, messages):
                yield SimpleNamespace(content='你好')
                yield SimpleNamespace(content='世界')
        events = await self.collect(Model())
        self.assertEqual(''.join(e['data'] for e in events if e['event'] == 'token'), '你好世界')
        self.assertEqual(events[-1]['event'], 'done')

    async def test_empty_model_stream_returns_error(self):
        class Model:
            async def astream(self, messages):
                if False: yield
        events = await self.collect(Model())
        self.assertEqual(events[-1]['event'], 'error')

    async def test_stalled_model_is_cancelled_and_reports_timeout(self):
        cancelled = []
        class Model:
            async def astream(self, messages):
                try:
                    await asyncio.sleep(10)
                    yield SimpleNamespace(content='too late')
                finally:
                    cancelled.append(True)
        with patch.object(chat, 'TOKEN_TIMEOUT', .02), patch.object(chat, 'HEARTBEAT_INTERVAL', .005):
            events = await self.collect(Model())
        self.assertEqual(events[-1]['event'], 'error')
        self.assertIn('超时', events[-1]['data'])
        self.assertTrue(cancelled)

    async def test_failed_retrieval_does_not_produce_an_unsupported_answer(self):
        events = await self.collect(None, {'retrieval_errors': ['Embedding unavailable'], 'reranked_results': []})
        self.assertEqual(events[-1], {'event': 'error', 'data': 'Embedding unavailable'})

    async def test_disconnected_stream_cancels_retrieval(self):
        cancelled = []
        async def slow(state):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.append(True)
        with patch.object(chat, 'get_llm', return_value=None), patch('app.agents.graph.rag_graph', SimpleNamespace(ainvoke=slow)), patch.object(chat, 'HEARTBEAT_INTERVAL', .005):
            response = await chat.chat_stream(chat.ChatRequest(session_id='s', query='q'), db=None, current_user=None)
            iterator = response.body_iterator
            await anext(iterator)  # preparation
            await anext(iterator)  # starts retrieval
            await anext(iterator)  # heartbeat while pending
            await iterator.aclose()
        self.assertTrue(cancelled)

    async def test_plain_model_mode_can_answer_without_retrieved_documents(self):
        state = await generate_node({'query': '你好', 'scope_vector': False, 'scope_database': False, 'scope_web': False})
        self.assertNotIn('只使用提供的资料', state['_system_prompt'])

    async def test_sends_status_before_waiting_for_retrieval(self):
        response = await chat.chat_stream(chat.ChatRequest(session_id='s', query='q'), db=None, current_user=None)
        iterator = response.body_iterator
        first = await anext(iterator)
        event = json.loads(first.removeprefix('data: '))
        self.assertEqual(event['event'], 'status')
        await iterator.aclose()

    async def test_blank_query_is_rejected(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            chat.ChatRequest(session_id='s', query='  ')


if __name__ == '__main__':
    unittest.main()
