import re
from typing import Optional

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine import URL, make_url

from app.connectors.base import BaseConnector


class SQLConnector(BaseConnector):
    """SQLAlchemy 统一连接器（MySQL/PostgreSQL/MSSQL/Oracle）"""

    def __init__(self, conn_string: str, blocked_tables: Optional[list] = None):
        self._conn_string = conn_string
        self._blocked_tables = blocked_tables or []
        self._engine: Optional[Engine] = None

    def connect(self) -> Engine:
        if self._engine is None:
            if self._conn_string.startswith("mssql+pyodbc:"):
                import pyodbc
                driver = make_url(self._conn_string).query.get("driver")
                if not driver or driver not in pyodbc.drivers():
                    raise ValueError("MSSQL 需要在后端容器中安装 Microsoft ODBC Driver for SQL Server")
            options = {"check_same_thread": False} if self._conn_string.startswith("sqlite:") else {}
            self._engine = create_engine(self._conn_string, connect_args=options)
        return self._engine

    def execute(self, query: str) -> list[dict]:
        # 只允许 SELECT 语句
        query_stripped = query.strip().rstrip(";").strip()
        if not re.match(r"SELECT\b", query_stripped, re.I):
            raise ValueError("仅允许执行 SELECT 查询")
        # Ignore string values when checking statement syntax.
        syntax = re.sub(r"'(?:''|[^'])*'", "''", query_stripped)
        if re.search(r";|--|/\*|\b(INTO|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|EXEC|CALL|GRANT|TRUNCATE)\b", syntax, re.I):
            raise ValueError("仅允许执行单条只读 SELECT 查询")
        for table in self._blocked_tables:
            if re.search(r"(?<!\w)" + re.escape(table) + r"(?!\w)", query_stripped, re.I):
                raise ValueError("查询包含禁止访问的表")

        engine = self.connect()
        with engine.connect() as conn:
            result = conn.execute(text(query))
            columns = result.keys()
            rows = [dict(zip(columns, row)) for row in result.fetchmany(100)]
        return rows

    def get_schema(self) -> dict:
        engine = self.connect()
        inspector = inspect(engine)
        schema = {}

        for table_name in inspector.get_table_names():
            if table_name in self._blocked_tables:
                continue

            columns = []
            for column in inspector.get_columns(table_name):
                columns.append({
                    "name": column["name"],
                    "type": str(column["type"]),
                    "nullable": column["nullable"],
                    "default": column.get("default"),
                })

            primary_keys = inspector.get_pk_constraint(table_name).get("constrained_columns", [])
            foreign_keys = []
            for fk in inspector.get_foreign_keys(table_name):
                foreign_keys.append({
                    "column": fk["constrained_columns"],
                    "referred_table": fk["referred_table"],
                    "referred_columns": fk["referred_columns"],
                })

            schema[table_name] = {
                "columns": columns,
                "primary_keys": primary_keys,
                "foreign_keys": foreign_keys,
            }

        return schema

    def close(self) -> None:
        if self._engine:
            self._engine.dispose()
            self._engine = None


def build_conn_string(
    db_type: str,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
) -> str:
    """构建数据库连接字符串"""
    drivers = {"mysql": "mysql+pymysql", "postgresql": "postgresql+psycopg2",
               "mssql": "mssql+pyodbc", "oracle": "oracle+oracledb"}
    if db_type not in drivers:
        raise ValueError(f"不支持的数据库类型: {db_type}")
    options = {}
    if db_type == "mssql":
        import pyodbc
        installed = [driver for driver in pyodbc.drivers() if "SQL Server" in driver]
        options["driver"] = sorted(installed)[-1] if installed else "ODBC Driver 18 for SQL Server"
    return URL.create(drivers[db_type], username=user, password=password,
                      host=host, port=port, database=database, query=options).render_as_string(hide_password=False)
