import json
from app.core.logging_config import get_logger
from app.db import crud
from app.core.security import decrypt
from app.models.llm_factory import get_llm

logger = get_logger("agents.database")


async def db_node(state: dict) -> dict:
    if not state.get("scope_database"):
        return {"retrieval_results": []}

    query = state["query"]
    db = state.get("_db")
    db_connection_ids = state.get("db_connection_ids") or []

    if not db_connection_ids and db:
        conns = crud.list_db_connections(db)
        db_connection_ids = [c.id for c in conns if c.is_active]

    llm = get_llm(db, state.get("llm_config_id"))
    results, errors = [], []

    for conn_id in db_connection_ids:
        conn_record = crud.get_db_connection(db, conn_id)
        if not conn_record:
            continue
        try:
            conn_string = decrypt(conn_record.conn_string_enc)
            schema = json.loads(conn_record.schema_cache or "{}")
            blocked = json.loads(conn_record.blocked_tables or "[]")

            if conn_record.db_type == "mongodb":
                from app.connectors.mongo_connector import MongoConnector
                connector = MongoConnector(conn_string, blocked)
            else:
                from app.connectors.sql_connector import SQLConnector
                connector = SQLConnector(conn_string, blocked)

            rows = await connector.aquery(query, schema, llm)
            if rows:
                text = json.dumps(rows[:10], ensure_ascii=False, indent=2)
                results.append({
                    "text": text,
                    "source_type": "database",
                    "source_name": conn_record.name,
                    "source_detail": f"{conn_record.db_type}·查询结果",
                    "score": 1.0,
                })
        except Exception as e:
            logger.warning("数据库 %s 检索失败：%s", conn_id, e)
            errors.append(f"数据库 {conn_record.name} 检索失败：{e}")

    return {"retrieval_results": results, "retrieval_errors": errors}
