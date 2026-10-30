import json
from typing import Optional

from pymongo import MongoClient
from pymongo.database import Database

from app.connectors.base import BaseConnector


class MongoConnector(BaseConnector):
    """MongoDB 连接器"""

    def __init__(self, host: str, port: int, user: str, password: str, database: str, blocked_tables: Optional[list] = None):
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._database = database
        self._blocked_tables = blocked_tables or []
        self._client: Optional[MongoClient] = None
        self._db: Optional[Database] = None

    def connect(self) -> Database:
        if self._client is None:
            options = {"username": self._user, "password": self._password, "authSource": self._database} if self._user else {}
            self._client = MongoClient(self._host, self._port, serverSelectionTimeoutMS=5000, **options)
            self._db = self._client[self._database]
        return self._db

    def execute(self, query: str) -> list[dict]:
        """执行 MQL 查询（仅支持 find 和 aggregate）"""
        try:
            mql = json.loads(query)
        except json.JSONDecodeError:
            raise ValueError("MongoDB 查询必须是有效的 JSON")

        if not isinstance(mql, dict):
            raise ValueError("MongoDB 查询必须是 JSON 对象")
        cmd = mql.get("command", "")
        collection_name = mql.get("collection", "")
        filter_cond = mql.get("filter", {})

        if not collection_name:
            raise ValueError("必须指定 collection")
        if collection_name in self._blocked_tables:
            raise ValueError("查询包含禁止访问的集合")
        if cmd not in {"find", "aggregate"}:
            raise ValueError(f"不支持的 MongoDB 命令: {cmd}")
        def validate(value):
            if isinstance(value, dict):
                for key, nested in value.items():
                    if key in {"$out", "$merge"}:
                        raise ValueError("仅允许只读 MongoDB 查询")
                    if key in {"from", "coll", "$unionWith"} and isinstance(nested, str) and nested in self._blocked_tables:
                        raise ValueError("查询包含禁止访问的集合")
                    validate(nested)
            elif isinstance(value, list):
                for nested in value:
                    validate(nested)
        validate(mql)

        db = self.connect()
        collection = db[collection_name]

        if cmd == "find":
            results = list(collection.find(filter_cond).limit(100))
            for r in results:
                r["_id"] = str(r["_id"])
            return results
        elif cmd == "aggregate":
            pipeline = mql.get("pipeline", [])
            if not isinstance(pipeline, list):
                raise ValueError("pipeline 必须是数组")
            results = list(collection.aggregate(pipeline + [{"$limit": 100}]))
            for r in results:
                if "_id" in r:
                    r["_id"] = str(r["_id"])
            return results
        else:
            raise ValueError(f"不支持的 MongoDB 命令: {cmd}")

    def get_schema(self) -> dict:
        db = self.connect()
        schema = {}

        for collection_name in db.list_collection_names():
            if collection_name in self._blocked_tables:
                continue
            collection = db[collection_name]
            # 取一条样本推断字段
            sample = collection.find_one()
            if sample:
                fields = {}
                for key, value in sample.items():
                    if key != "_id":
                        fields[key] = type(value).__name__
                schema[collection_name] = {"fields": fields}

        return schema

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
            self._db = None


def build_mongo_conn_string(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
) -> tuple:
    """返回 (host, port, user, password, database)"""
    return host, port, user, password, database
