import pytest
from app.connectors.sql_connector import SQLConnector, build_conn_string


def test_build_conn_string_mysql():
    conn_str = build_conn_string("mysql", "localhost", 3306, "root", "password", "testdb")
    assert "mysql+pymysql://" in conn_str
    assert "root:password" in conn_str
    assert "localhost:3306" in conn_str
    assert "/testdb" in conn_str


def test_build_conn_string_postgresql():
    conn_str = build_conn_string("postgresql", "localhost", 5432, "postgres", "password", "testdb")
    assert "postgresql+psycopg2://" in conn_str


def test_build_conn_string_unsupported():
    with pytest.raises(ValueError, match="不支持的数据库类型"):
        build_conn_string("unsupported", "localhost", 3306, "user", "pass", "db")


def test_sql_connector_query_validation():
    # 这个测试需要实际的数据库连接，这里只测试参数验证
    conn = SQLConnector("sqlite:///:memory:", [])
    
    # SELECT 应该允许
    # 注意：SQLite in-memory 需要不同写法，这里简化处理
    # 实际测试需要 mock
    
    # 非 SELECT 应该被拒绝
    with pytest.raises(ValueError, match="仅允许执行 SELECT"):
        conn.execute("DROP TABLE users")