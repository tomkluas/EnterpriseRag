import asyncio
import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.security import decrypt
from app.connectors.sql_connector import SQLConnector
from app.connectors.mongo_connector import MongoConnector
from app.db.database import SessionLocal
from app.db.crud import list_db_connections
from app.models.llm_factory import get_llm

logger = logging.getLogger(__name__)


async def db_node(state: dict) -> dict:
    """Generate a read query from the visible schema and retrieve database evidence."""
    if not state.get("scope_database"):
        return {"retrieval_results": []}

    results, errors = [], []
    selected = set(state.get("db_connection_ids", []))
    with SessionLocal() as db:
        connections = [connection for connection in list_db_connections(db)
                       if connection.is_active and (not selected or connection.id in selected)]
        if not connections:
            return {"retrieval_results": []}
        try:
            llm = get_llm(db, state.get("llm_config_id"))
        except Exception:
            logger.exception("Could not initialize database query model")
            return {"retrieval_results": [], "error": "数据库查询模型初始化失败，请检查模型配置"}
        for connection in connections:
            connector = None
            try:
                blocked = json.loads(connection.blocked_tables or "[]")
                secret = decrypt(connection.conn_string_enc)
                if connection.db_type == "mongodb":
                    connector = MongoConnector(**json.loads(secret), blocked_tables=blocked)
                    output_format = '只输出 JSON 对象：{"command":"find","collection":"集合名","filter":{}}，或 command=aggregate 与 pipeline 数组。禁止写操作。'
                else:
                    connector = SQLConnector(secret, blocked)
                    output_format = "只输出一条 SELECT SQL，不要解释，不使用注释、CTE 或写操作。"
                schema = await asyncio.to_thread(connector.get_schema)
                if not schema:
                    continue
                message = await llm.ainvoke([
                    SystemMessage(content=f"你是数据库只读查询助手。数据库类型：{connection.db_type}。仅可访问给定结构中的表或集合。{output_format}\n结构：{json.dumps(schema, ensure_ascii=False, default=str)}"),
                    HumanMessage(content=state["query"]),
                ])
                query = message.content.strip()
                query = re.sub(r"^```(?:sql|json)?\s*|\s*```$", "", query, flags=re.I).strip()
                rows = await asyncio.to_thread(connector.execute, query)
                if rows:
                    results.append({"text": json.dumps(rows, ensure_ascii=False, default=str),
                                    "source_type": "database", "source_name": connection.name,
                                    "source_detail": f"{connection.db_type} · {query}", "score": 1.0})
            except Exception:
                logger.exception("Database retrieval failed for connection %s", connection.id)
                errors.append(f"数据库连接 {connection.name} 检索失败，请检查连接或模型配置")
            finally:
                if connector is not None:
                    await asyncio.to_thread(connector.close)
    output = {"retrieval_results": results}
    if errors:
        output["error"] = "；".join(errors)
    return output
