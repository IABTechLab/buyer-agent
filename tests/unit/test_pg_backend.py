# Donated to IAB Tech Lab

"""Unit tests for the Scope 2 buyer Postgres backend (Req 12.6/12.7).

Offline tests (no live database) covering the three dialect seams:
  1. paramstyle translation  (pg_connection._qmark_to_pyformat)
  2. DDL translation          (schema_pg._to_postgres + inline store DDL)
  3. connection routing        (connection_factory.open_connection / *_for)
  4. secret -> URL assembly    (db_secret.resolve_database_url)

The SQLite path is asserted to be byte-identical by default (the factory
returns a plain sqlite3 connection unless the URL is postgresql://).
"""

from __future__ import annotations

import sqlite3

import pytest

from ad_buyer.storage import connection_factory as cf
from ad_buyer.storage import schema_pg
from ad_buyer.storage.pg_connection import _qmark_to_pyformat


# ---------------------------------------------------------------------------
# 1. Paramstyle translation
# ---------------------------------------------------------------------------
class TestParamstyleTranslation:
    def test_simple_placeholders(self):
        assert _qmark_to_pyformat("INSERT INTO t VALUES (?, ?)") == (
            "INSERT INTO t VALUES (%s, %s)"
        )

    def test_no_placeholders_unchanged(self):
        assert _qmark_to_pyformat("SELECT 1") == "SELECT 1"

    def test_question_mark_inside_string_literal_preserved(self):
        # A ? inside a single-quoted literal must NOT become %s.
        sql = "SELECT * FROM t WHERE label = 'is it? yes' AND id = ?"
        out = _qmark_to_pyformat(sql)
        assert "'is it? yes'" in out
        assert out.endswith("id = %s")
        assert out.count("%s") == 1

    def test_literal_percent_is_escaped(self):
        # Existing % must be escaped so psycopg does not read it as a marker.
        out = _qmark_to_pyformat("SELECT * FROM t WHERE name LIKE 'a%'")
        assert "%%" in out

    def test_on_conflict_upsert_translates_placeholders_only(self):
        # The one upsert in job_store is portable; only ? -> %s changes.
        sql = (
            "INSERT INTO jobs (id, data) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data = excluded.data"
        )
        out = _qmark_to_pyformat(sql)
        assert "ON CONFLICT(id) DO UPDATE SET data = excluded.data" in out
        assert out.count("%s") == 2


# ---------------------------------------------------------------------------
# 2. DDL translation (schema.py-derived + inline store DDL)
# ---------------------------------------------------------------------------
class TestSchemaPgTranslation:
    def test_autoincrement_becomes_bigserial(self):
        ddl = "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, x TEXT)"
        out = schema_pg._to_postgres(ddl)
        assert "BIGSERIAL PRIMARY KEY" in out
        assert "AUTOINCREMENT" not in out

    def test_strftime_default_becomes_now(self):
        ddl = "CREATE TABLE t (ts TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')))"
        out = schema_pg._to_postgres(ddl)
        assert "now()" in out
        assert "strftime" not in out

    def test_real_becomes_double_precision(self):
        out = schema_pg._to_postgres("CREATE TABLE t (v REAL)")
        assert "DOUBLE PRECISION" in out
        assert "REAL" not in out

    def test_schema_derived_statements_have_no_sqlite_isms(self):
        stmts = schema_pg.postgres_ddl_statements()
        assert len(stmts) > 10
        blob = "\n".join(stmts)
        assert "AUTOINCREMENT" not in blob
        assert "strftime" not in blob

    @pytest.mark.parametrize(
        "module_name,table_const,index_const",
        [
            ("order_store", "ORDERS_TABLE", "ORDERS_INDEXES"),
            ("adserver_store", "AD_SERVER_CAMPAIGN_TABLE", "AD_SERVER_CAMPAIGN_INDEXES"),
            ("pacing_store", "PACING_SNAPSHOT_TABLE", "PACING_SNAPSHOT_INDEXES"),
        ],
    )
    def test_self_connecting_store_inline_ddl_translates(
        self, module_name, table_const, index_const
    ):
        # The 4 self-connecting stores carry their own CREATE TABLE DDL; each
        # must translate cleanly via the shared helper (no SQLite-isms left).
        import importlib

        mod = importlib.import_module(f"ad_buyer.storage.{module_name}")
        table_pg = schema_pg._to_postgres(getattr(mod, table_const))
        assert "AUTOINCREMENT" not in table_pg
        assert "strftime" not in table_pg
        idx_pg = schema_pg._translate_all(getattr(mod, index_const))
        assert all("strftime" not in i for i in idx_pg)


# ---------------------------------------------------------------------------
# 3. Connection routing (the seam)
# ---------------------------------------------------------------------------
class TestConnectionFactoryRouting:
    def test_is_postgres_url(self):
        assert cf.is_postgres_url("postgresql://h/db")
        assert cf.is_postgres_url("postgres://h/db")
        assert not cf.is_postgres_url("sqlite:///:memory:")
        assert not cf.is_postgres_url(None)

    def test_sqlite_url_returns_plain_sqlite_connection(self):
        # SQLite path byte-identical: a real sqlite3.Connection, not the adapter.
        conn = cf.open_connection("sqlite:///:memory:")
        assert isinstance(conn, sqlite3.Connection)
        conn.close()

    def test_apply_pragmas_noop_on_postgres(self):
        # Must not touch a PG connection (no PRAGMA in Postgres).
        class _Sentinel:
            def execute(self, *_a, **_k):
                raise AssertionError("PRAGMA should not run on Postgres")

        cf.apply_sqlite_pragmas(_Sentinel(), "postgresql://h/db")  # no raise

    def test_apply_pragmas_runs_on_sqlite(self):
        conn = sqlite3.connect(":memory:")
        cf.apply_sqlite_pragmas(conn, "sqlite:///:memory:")
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode is not None
        conn.close()

    def test_postgres_url_builds_adapter(self, monkeypatch):
        # open_connection on a postgres URL constructs PgConnection; stub psycopg
        # so no real server is needed.
        import ad_buyer.storage.pg_connection as pgmod

        captured = {}

        class _FakePsycopg:
            @staticmethod
            def connect(dsn, **kw):
                captured["dsn"] = dsn
                return object()

        monkeypatch.setattr(pgmod, "psycopg", _FakePsycopg, raising=False)
        # PgConnection.__init__ imports psycopg lazily; patch the module import.
        import sys

        fake_mod = type(sys)("psycopg")
        fake_mod.connect = _FakePsycopg.connect
        rows_mod = type(sys)("psycopg.rows")
        rows_mod.dict_row = object()
        monkeypatch.setitem(sys.modules, "psycopg", fake_mod)
        monkeypatch.setitem(sys.modules, "psycopg.rows", rows_mod)

        conn = cf.open_connection("postgresql://u:p@h:5432/ad_buyer")
        assert conn.__class__.__name__ == "PgConnection"
        assert captured["dsn"].startswith("postgresql://")


# ---------------------------------------------------------------------------
# 4. Secret -> URL assembly
# ---------------------------------------------------------------------------
class TestDbSecretResolution:
    def test_returns_none_without_env(self, monkeypatch):
        for k in ("DB_SECRET_ARN", "AURORA_ENDPOINT"):
            monkeypatch.delenv(k, raising=False)
        from ad_buyer.storage.db_secret import resolve_database_url

        assert resolve_database_url() is None

    def test_assembles_url_from_secret(self, monkeypatch):
        monkeypatch.setenv(
            "DB_SECRET_ARN",
            "arn:aws:secretsmanager:us-west-2:123456789012:secret:rds!cluster-x",
        )
        monkeypatch.setenv("AURORA_ENDPOINT", "aurora.example.us-west-2.rds.amazonaws.com")
        monkeypatch.setenv("AURORA_PORT", "5432")
        monkeypatch.setenv("DB_NAME", "ad_buyer")

        import ad_buyer.storage.db_secret as ds

        class _FakeClient:
            def get_secret_value(self, SecretId):  # noqa: N803
                return {"SecretString": '{"username": "admin", "password": "p@ss/w:rd"}'}

        class _FakeBoto3:
            @staticmethod
            def client(*_a, **_k):
                return _FakeClient()

        import sys

        monkeypatch.setitem(sys.modules, "boto3", _FakeBoto3)
        url = ds.resolve_database_url()
        assert url is not None
        assert url.startswith("postgresql://admin:")
        assert "@aurora.example.us-west-2.rds.amazonaws.com:5432/ad_buyer" in url
        # special chars URL-encoded (no raw / or : in the password segment)
        assert "p%40ss" in url

    def test_missing_password_returns_none(self, monkeypatch):
        monkeypatch.setenv("DB_SECRET_ARN", "arn:secret")
        monkeypatch.setenv("AURORA_ENDPOINT", "h")
        import ad_buyer.storage.db_secret as ds

        class _FakeClient:
            def get_secret_value(self, SecretId):  # noqa: N803
                return {"SecretString": '{"username": "admin"}'}

        class _FakeBoto3:
            @staticmethod
            def client(*_a, **_k):
                return _FakeClient()

        import sys

        monkeypatch.setitem(sys.modules, "boto3", _FakeBoto3)
        assert ds.resolve_database_url() is None
