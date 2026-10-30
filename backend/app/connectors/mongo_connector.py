import json
import re
import asyncio
from pymongo import MongoClient
from app.connectors.base import BaseConnector


class MongoConnector(BaseConnector):
    def __init__(self, conn_string: str, blocked_tables: list[str] = None):
        self.conn_string = conn_string
        self.blocked_tables = blocked_tables or []
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = MongoClient(self.conn_string, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000, socketTimeoutMS=15000)
        return self._client

    def _get_db(self):
        client = self._get_client()
        # Parse DB name from conn string
        match = re.search(r"/([^/?]+)(\?|$)", self.conn_string)
        db_name = match.group(1) if match else "admin"
        return client[db_name]

    def test_connection(self) -> bool:
        try:
            self._get_client().admin.command("ping")
            return True
        except Exception:
            return False

    def get_schema(self) -> dict:
        db = self._get_db()
        schema = {}
        for name in db.list_collection_names():
            if name in self.blocked_tables:
                continue
            sample = db[name].find_one()
            if sample:
                schema[name] = list(sample.keys())
            else:
                schema[name] = []
        return schema

    def query(self, nl_query: str, schema: dict, llm) -> list[dict]:
        response = llm.invoke(self._messages(nl_query, schema))
        return self._execute(response.content)

    async def aquery(self, nl_query: str, schema: dict, llm) -> list[dict]:
        response = await llm.ainvoke(self._messages(nl_query, schema))
        return await asyncio.to_thread(self._execute_and_close, response.content)

    def _execute_and_close(self, content):
        try:
            return self._execute(content)
        finally:
            if self._client is not None:
                self._client.close()
                self._client = None

    def _messages(self, nl_query, schema):
        schema_str = json.dumps(schema, ensure_ascii=False, indent=2)
        prompt = (
            f"MongoDB 集合结构如下：\n{schema_str}\n\n"
            f"请将以下问题转换为 MongoDB find() 查询（输出 JSON 格式，包含 collection、filter、projection 三个字段）：\n{nl_query}"
        )
        from langchain_core.messages import HumanMessage
        return [HumanMessage(content=prompt)]

    def _execute(self, content):
        content = content.strip()
        json_match = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
        if json_match:
            content = json_match.group(1).strip()
        mql = json.loads(content)
        db = self._get_db()
        col = db[mql["collection"]]
        cursor = col.find(mql.get("filter", {}), mql.get("projection", None)).max_time_ms(15000).limit(50)
        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(doc)
        return results
