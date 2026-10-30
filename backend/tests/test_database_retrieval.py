import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.connectors.sql_connector import SQLConnector, build_conn_string
from app.connectors.mongo_connector import MongoConnector


class ConnectorRegressionTests(unittest.TestCase):
    def test_mssql_has_explicit_driver_and_missing_driver_is_actionable(self):
        with patch("pyodbc.drivers", return_value=[]):
            url = make_url(build_conn_string("mssql", "localhost", 1433, "u", "p", "db"))
            self.assertEqual(url.query["driver"], "ODBC Driver 18 for SQL Server")
            with self.assertRaisesRegex(ValueError, "ODBC"):
                SQLConnector(url.render_as_string(hide_password=False)).connect()

    def test_schema_and_blocked_execution(self):
        connector = SQLConnector("sqlite:///:memory:", ["secrets"])
        with connector.connect().begin() as db:
            db.execute(text("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount INTEGER)"))
            db.execute(text("CREATE TABLE secrets (password TEXT)"))
            db.execute(text("INSERT INTO orders VALUES (1, 42)"))
        self.assertEqual(connector.get_schema()["orders"]["primary_keys"], ["id"])
        self.assertNotIn("secrets", connector.get_schema())
        self.assertEqual(connector.execute("SELECT amount FROM orders"), [{"amount": 42}])
        self.assertEqual(connector.execute("SELECT 'DELETE; value' AS label"), [{"label": "DELETE; value"}])
        for query in ('SELECT * FROM secrets', 'SELECT * FROM "secrets"', "SELECT * FROM 'secrets'",
                      'SELECT * FROM orders; DELETE FROM orders',
                      'SELECT * INTO backup FROM orders'):
            with self.subTest(query=query), self.assertRaises(ValueError):
                connector.execute(query)
        connector.close()

    def test_credentials_round_trip(self):
        for db_type in ("mysql", "postgresql", "mssql", "oracle"):
            url = make_url(build_conn_string(db_type, "localhost", 1234, "u@x", "p@:/?#%", "db"))
            self.assertEqual(url.username, "u@x")
            self.assertEqual(url.password, "p@:/?#%")

    def test_mongo_rejects_writes_and_blocked_collections_before_connect(self):
        connector = MongoConnector("localhost", 27017, "", "", "db", ["secret"])
        for query in ({"command": "find", "collection": "secret"},
                      {"command": "aggregate", "collection": "orders", "pipeline": [{"$out": "copy"}]},
                      {"command": "aggregate", "collection": "orders", "pipeline": [{"$lookup": {"from": "secret", "as": "x"}}]}):
            with self.subTest(query=query), patch.object(connector, "connect") as connect:
                with self.assertRaises(ValueError):
                    connector.execute(json.dumps(query))
                connect.assert_not_called()

    def test_mongo_credentials_and_aggregate_result(self):
        connector = MongoConnector("localhost", 27017, "u@x", "p@:/?#%", "db")
        with patch("app.connectors.mongo_connector.MongoClient") as client:
            connector.connect()
            self.assertEqual(client.call_args.kwargs["password"], "p@:/?#%")
            collection = client.return_value.__getitem__.return_value.__getitem__.return_value
            collection.aggregate.return_value = [{"_id": 1, "total": 42}]
            result = connector.execute(json.dumps({"command": "aggregate", "collection": "orders", "pipeline": []}))
            self.assertEqual(result, [{"_id": "1", "total": 42}])
            self.assertEqual(collection.aggregate.call_args.args[0], [{"$limit": 100}])
            connector.close()
            client.return_value.close.assert_called_once()


class DatabaseNodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_natural_language_reads_active_connections_and_closes(self):
        from app.agents.db_node import db_node
        with tempfile.TemporaryDirectory() as directory:
            uri = "sqlite:///" + str(Path(directory) / "business.db")
            engine = create_engine(uri)
            with engine.begin() as db:
                db.execute(text("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount INTEGER)"))
                db.execute(text("INSERT INTO orders VALUES (1, 42)"))
            engine.dispose()
            active = SimpleNamespace(id="one", name="Sales", db_type="sqlite", is_active=True,
                                     conn_string_enc=uri, blocked_tables="[]")
            inactive = SimpleNamespace(id="two", is_active=False)
            model = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content="```sql\nSELECT amount FROM orders\n```")))
            with patch("app.agents.db_node.SessionLocal") as session, patch(
                "app.agents.db_node.list_db_connections", return_value=[active, inactive]
            ), patch("app.agents.db_node.decrypt", side_effect=lambda value: value), patch(
                "app.agents.db_node.get_llm", return_value=model
            ):
                result = await db_node({"query": "订单金额？", "scope_database": True, "db_connection_ids": []})
                self.assertEqual(len(result["retrieval_results"]), 1)
                source = result["retrieval_results"][0]
                self.assertEqual(source["source_type"], "database")
                self.assertEqual(source["source_name"], "Sales")
                self.assertIn("42", source["text"])
                self.assertEqual(model.ainvoke.await_count, 1)
                result = await db_node({"query": "x", "scope_database": True, "db_connection_ids": ["two"]})
                self.assertEqual(result["retrieval_results"], [])

    async def test_failed_query_is_visible_and_connector_closes(self):
        from app.agents.db_node import db_node
        record = SimpleNamespace(id="one", name="Sales", db_type="sqlite", is_active=True,
                                 conn_string_enc="sqlite:///:memory:", blocked_tables="[]")
        with patch("app.agents.db_node.SessionLocal"), patch(
            "app.agents.db_node.list_db_connections", return_value=[record]
        ), patch("app.agents.db_node.decrypt", side_effect=lambda value: value), patch(
            "app.agents.db_node.get_llm", return_value=SimpleNamespace(ainvoke=AsyncMock(side_effect=RuntimeError("provider failed")))
        ), patch("app.agents.db_node.SQLConnector") as connector:
            connector.return_value.get_schema.return_value = {"orders": {}}
            result = await db_node({"query": "x", "scope_database": True})
            self.assertIn("Sales", result["error"])
            self.assertEqual(result["retrieval_results"], [])
            connector.return_value.close.assert_called_once()

