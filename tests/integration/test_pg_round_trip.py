# Donated to IAB Tech Lab

"""Integration round-trip for the buyer Postgres backend (Req 12.6, task 10.10).

Proves the schema_pg DDL + PgConnection adapter + connection_factory seam work
end-to-end against a REAL PostgreSQL server -- the thing unit tests alone cannot
guarantee (a paramstyle bug, a DDL type mismatch, or the ON CONFLICT upsert can
pass offline and fail on a live server).

Backend selection (no local PG on the dev laptop):
  - Set ``PG_TEST_DATABASE_URL`` (a local Docker Postgres in CI, or the live
    Aurora endpoint via a bastion/tunnel) to run these.
  - Absent -> the whole module is skipped (offline suites stay green).

Each test uses a unique table/id so it is safe against a shared database and
cleans up after itself. The SQLite path is covered by the per-store suites;
this file is exclusively the Postgres proof.
"""

from __future__ import annotations

import os
import uuid

import pytest

PG_URL = os.environ.get("PG_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL,
    reason="PG_TEST_DATABASE_URL not set -- skipping live Postgres round-trip",
)


@pytest.fixture
def pg_conn():
    from ad_buyer.storage.connection_factory import open_connection

    conn = open_connection(PG_URL)
    yield conn
    conn.close()


class TestPostgresRoundTrip:
    def test_connect_and_select_one(self, pg_conn):
        row = pg_conn.execute("SELECT 1 AS one").fetchone()
        assert row["one"] == 1

    def test_qmark_paramstyle_round_trip(self, pg_conn):
        t = f"pg_it_{uuid.uuid4().hex[:8]}"
        pg_conn.execute(f"CREATE TABLE {t} (id TEXT PRIMARY KEY, data TEXT)")
        pg_conn.commit()
        try:
            # ? placeholders must translate + bind correctly on a real server.
            pg_conn.execute(f"INSERT INTO {t} (id, data) VALUES (?, ?)", ("a", "hello"))
            pg_conn.commit()
            row = pg_conn.execute(f"SELECT data FROM {t} WHERE id = ?", ("a",)).fetchone()
            assert row["data"] == "hello"
        finally:
            pg_conn.execute(f"DROP TABLE {t}")
            pg_conn.commit()

    def test_on_conflict_upsert_portable(self, pg_conn):
        t = f"pg_it_{uuid.uuid4().hex[:8]}"
        pg_conn.execute(f"CREATE TABLE {t} (id TEXT PRIMARY KEY, data TEXT)")
        pg_conn.commit()
        try:
            pg_conn.execute(f"INSERT INTO {t} (id, data) VALUES (?, ?)", ("k", "v1"))
            pg_conn.execute(
                f"INSERT INTO {t} (id, data) VALUES (?, ?) "
                f"ON CONFLICT(id) DO UPDATE SET data = excluded.data",
                ("k", "v2"),
            )
            pg_conn.commit()
            row = pg_conn.execute(f"SELECT data FROM {t} WHERE id = ?", ("k",)).fetchone()
            assert row["data"] == "v2"
        finally:
            pg_conn.execute(f"DROP TABLE {t}")
            pg_conn.commit()

    def test_schema_pg_ddl_applies_on_real_server(self, pg_conn):
        # The derived Postgres DDL must actually execute against a live server
        # (idempotent -- all IF NOT EXISTS).
        from ad_buyer.storage.schema_pg import initialize_schema_pg

        initialize_schema_pg(pg_conn)  # no raise = valid PG DDL


# ---------------------------------------------------------------------------
# ALL stores: prove every store's connect()/DDL works on real Postgres
# ---------------------------------------------------------------------------
class TestAllStoresOnPostgres:
    """Connect every buyer store against real Postgres and confirm its tables
    are created. This is the comprehensive "all 17 stores" proof: each store's
    connect() runs its own DDL through the schema_pg translation on a live
    server, so a dialect bug in ANY store's inline DDL surfaces here.
    """

    # Every table the buyer persistence layer defines, and which store creates
    # it. The deal facade (DealStore) creates the schema.py family; the 4
    # self-connecting stores + the audit log create their own.
    _FACADE_TABLES = [
        "deals",
        "negotiation_rounds",
        "booking_records",
        "jobs",
        "events",
        "status_transitions",
        "portfolio_metadata",
        "deal_activations",
        "performance_cache",
        "creative_assets",
        "deal_templates",
        "supply_path_templates",
    ]

    @staticmethod
    def _table_exists(conn, name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ?",
            (name,),
        ).fetchone()
        return row is not None

    def test_deal_facade_and_substores_tables(self):
        from ad_buyer.storage.deal_store import DealStore

        store = DealStore(PG_URL)
        store.connect()
        try:
            for tbl in self._FACADE_TABLES:
                assert self._table_exists(store._conn, tbl), f"missing facade table: {tbl}"
        finally:
            store.disconnect()

    def test_campaign_store_tables(self):
        from ad_buyer.storage.campaign_store import CampaignStore

        store = CampaignStore(PG_URL)
        store.connect()
        try:
            for tbl in ("campaigns", "campaign_events", "approval_requests"):
                assert self._table_exists(store._conn, tbl), f"missing campaign table: {tbl}"
        finally:
            store.disconnect()

    def test_order_store_tables(self):
        from ad_buyer.storage.order_store import OrderStore

        store = OrderStore(PG_URL)
        store.connect()
        try:
            assert self._table_exists(store._conn, "orders")
        finally:
            store.disconnect()

    def test_pacing_store_tables(self):
        from ad_buyer.storage.pacing_store import PacingStore

        store = PacingStore(PG_URL)
        store.connect()
        try:
            assert self._table_exists(store._conn, "pacing_snapshots")
        finally:
            store.disconnect()

    def test_adserver_store_tables(self):
        from ad_buyer.storage.adserver_store import AdServerStore

        store = AdServerStore(PG_URL)
        store.connect()
        try:
            assert self._table_exists(store._conn, "ad_server_campaigns")
        finally:
            store.disconnect()

    def test_audience_audit_log_table(self):
        from ad_buyer.storage import audience_audit_log as aal

        # Module-level connection; configure() points it at PG.
        aal.configure(PG_URL)
        conn = aal._get_conn()
        assert self._table_exists(conn, "audience_audit_log")

    def test_order_store_write_recycle_read(self):
        # Full store round-trip: write, disconnect (recycle), reconnect, read.
        from ad_buyer.storage.order_store import OrderStore

        oid = f"order:{uuid.uuid4().hex[:8]}"
        store = OrderStore(PG_URL)
        store.connect()
        try:
            store._conn.execute(
                "INSERT INTO orders (key, data, status) VALUES (?, ?, ?)",
                (oid, '{"x": 1}', "pending"),
            )
            store._conn.commit()
            store.disconnect()

            store2 = OrderStore(PG_URL)
            store2.connect()
            row = store2._conn.execute(
                "SELECT data, status FROM orders WHERE key = ?", (oid,)
            ).fetchone()
            assert row["status"] == "pending"
            store2.disconnect()
        finally:
            cleanup = OrderStore(PG_URL)
            cleanup.connect()
            cleanup._conn.execute("DELETE FROM orders WHERE key = ?", (oid,))
            cleanup._conn.commit()
            cleanup.disconnect()
