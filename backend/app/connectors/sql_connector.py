import json
import re
import asyncio
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.engine import make_url
from app.connectors.base import BaseConnector


def build_conn_string(db_type: str, host: str, port: int, user: str, password: str, database: str) -> str:
    if db_type == "mysql":
        return f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"
    elif db_type == "postgresql":
        return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{database}"
    elif db_type == "mssql":
        return f"mssql+pyodbc://{user}:{password}@{host}:{port}/{database}?driver=ODBC+Driver+17+for+SQL+Server"
    elif db_type == "oracle":
        return f"oracle+oracledb://{user}:{password}@{host}:{port}/{database}"
    else:
        raise ValueError(f"不支持的 SQL 类型: {db_type}")


class SQLConnector(BaseConnector):
    def __init__(self, conn_string: str, blocked_tables: list[str] = None):
        self.conn_string = conn_string
        self.blocked_tables = blocked_tables or []
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            backend = make_url(self.conn_string).get_backend_name()
            options = {
                "mysql": {"connect_timeout": 5, "read_timeout": 15, "write_timeout": 15},
                "postgresql": {"connect_timeout": 5, "options": "-c statement_timeout=15000", "keepalives": 1, "keepalives_idle": 5, "keepalives_interval": 2, "keepalives_count": 3},
                "mssql": {"timeout": 5},
                "oracle": {"tcp_connect_timeout": 5},
            }
            self._engine = create_engine(self.conn_string, pool_pre_ping=True, connect_args=options.get(backend, {}))
        return self._engine

    def test_connection(self) -> bool:
        try:
            with self._get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def get_schema(self) -> dict:
        engine = self._get_engine()
        inspector = inspect(engine)
        schema = {}
        for table_name in inspector.get_table_names():
            if table_name in self.blocked_tables:
                continue
            cols = inspector.get_columns(table_name)
            schema[table_name] = [
                {"name": c["name"], "type": str(c["type"])} for c in cols
            ]
        return schema

    def query(self, nl_query: str, schema: dict, llm) -> list[dict]:
        response = llm.invoke(self._messages(nl_query, schema))
        return self._execute(response.content)

    async def aquery(self, nl_query: str, schema: dict, llm) -> list[dict]:
        # 模型等待可取消；取消后不会继续进入数据库执行阶段。
        response = await llm.ainvoke(self._messages(nl_query, schema))
        return await asyncio.to_thread(self._execute_and_close, response.content)

    def _execute_and_close(self, content):
        try:
            return self._execute(content)
        finally:
            if self._engine is not None:
                self._engine.dispose()
                self._engine = None

    def _messages(self, nl_query, schema):
        schema_str = json.dumps(schema, ensure_ascii=False, indent=2)
        prompt = (
            f"数据库表结构如下：\n{schema_str}\n\n"
            f"请将以下问题转换为 SQL SELECT 语句（只输出 SQL，不要解释）：\n{nl_query}"
        )
        from langchain_core.messages import HumanMessage
        return [HumanMessage(content=prompt)]

    def _execute(self, content):
        sql = content.strip()
        # 提取 SQL 块
        sql_match = re.search(r"```sql\s*(.*?)\s*```", sql, re.DOTALL)
        if sql_match:
            sql = sql_match.group(1).strip()
        # 安全校验：只允许 SELECT
        if not re.match(r"^\s*SELECT\b", sql, re.IGNORECASE):
            raise ValueError("SQL Agent 只允许执行 SELECT 语句")
        with self._get_engine().connect() as conn:
            backend = conn.dialect.name
            raw = conn.connection.driver_connection
            if backend == "mssql":
                raw.timeout = 15
            elif backend == "oracle":
                raw.call_timeout = 15000
            result = conn.execute(text(sql))
            keys = list(result.keys())
            rows = [dict(zip(keys, row)) for row in result.fetchmany(50)]
        return rows
