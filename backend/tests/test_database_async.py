import asyncio
import unittest
from types import SimpleNamespace
from app.connectors.sql_connector import SQLConnector
from app.connectors.mongo_connector import MongoConnector


class DatabaseAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_sql_retrieval_uses_async_model_and_returns_real_rows(self):
        class Model:
            async def ainvoke(self, messages):
                return SimpleNamespace(content='SELECT 7 AS amount')
        connector = SQLConnector('sqlite://')
        rows = await connector.aquery('amount', {}, Model())
        self.assertEqual(rows, [{'amount': 7}])
        self.assertIsNone(connector._engine)

    async def test_cancelling_sql_model_does_not_start_database_query(self):
        waiting = asyncio.Event()
        class Model:
            async def ainvoke(self, messages):
                waiting.set()
                await asyncio.sleep(10)
        connector = SQLConnector('sqlite://')
        task = asyncio.create_task(connector.aquery('question', {}, Model()))
        await waiting.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(connector._engine)

    async def test_cancelling_mongo_model_does_not_start_database_query(self):
        waiting = asyncio.Event()
        class Model:
            async def ainvoke(self, messages):
                waiting.set()
                await asyncio.sleep(10)
        connector = MongoConnector('mongodb://localhost/example')
        task = asyncio.create_task(connector.aquery('question', {}, Model()))
        await waiting.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(connector._client)
