# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for the DealStore persistence layer.

All tests use in-memory SQLite (`:memory:`) for speed and isolation.
"""

import json
import sqlite3
import threading
import time

import pytest

from ad_buyer.storage.deal_store import DealStore
from ad_buyer.storage.schema import (
    CURRENT_SCHEMA_VERSION,
    apply_migrations,
    create_tables,
    get_schema_version,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store():
    """Create a DealStore backed by in-memory SQLite."""
    s = DealStore("sqlite:///:memory:")
    s.connect()
    yield s
    s.disconnect()


@pytest.fixture
def raw_conn():
    """Provide a raw sqlite3 connection for schema-level tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestSchemaCreation:
    """Verify that tables and indexes are created correctly."""

    def test_create_tables_creates_all_tables(self, raw_conn):
        create_tables(raw_conn)
        cursor = raw_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row["name"] for row in cursor.fetchall()}
        expected = {
            "schema_version",
            "deals",
            "negotiation_rounds",
            "booking_records",
            "jobs",
            "status_transitions",
        }
        assert expected.issubset(tables)

    def test_create_tables_creates_indexes(self, raw_conn):
        create_tables(raw_conn)
        cursor = raw_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        )
        indexes = {row["name"] for row in cursor.fetchall()}
        # Spot-check key indexes
        assert "idx_deals_status" in indexes
        assert "idx_deals_seller_url" in indexes
        assert "idx_neg_rounds_deal_id" in indexes
        assert "idx_booking_deal_id" in indexes
        assert "idx_jobs_status" in indexes
        assert "idx_transitions_entity" in indexes

    def test_create_tables_is_idempotent(self, raw_conn):
        create_tables(raw_conn)
        create_tables(raw_conn)  # Should not raise

    def test_schema_version_starts_at_zero(self, raw_conn):
        # Before any tables exist
        assert get_schema_version(raw_conn) == 0

    def test_apply_migrations_sets_version(self, raw_conn):
        create_tables(raw_conn)
        applied = apply_migrations(raw_conn)
        assert applied >= 1
        assert get_schema_version(raw_conn) == CURRENT_SCHEMA_VERSION

    def test_apply_migrations_is_idempotent(self, raw_conn):
        create_tables(raw_conn)
        first = apply_migrations(raw_conn)
        second = apply_migrations(raw_conn)
        assert first >= 1
        assert second == 0  # Nothing to apply the second time


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


class TestUrlParsing:
    """Verify database_url parsing logic."""

    def test_parse_memory_uri(self):
        assert DealStore._parse_url("sqlite:///:memory:") == ":memory:"

    def test_parse_relative_path(self):
        assert DealStore._parse_url("sqlite:///./ad_buyer.db") == "./ad_buyer.db"

    def test_parse_absolute_path(self):
        assert DealStore._parse_url("sqlite:////tmp/test.db") == "/tmp/test.db"

    def test_parse_raw_memory(self):
        assert DealStore._parse_url(":memory:") == ":memory:"

    def test_parse_raw_path_fallback(self):
        assert DealStore._parse_url("test.db") == "test.db"


# ---------------------------------------------------------------------------
# Deal CRUD
# ---------------------------------------------------------------------------


class TestDealCRUD:
    """Test create, read, update, and list operations on deals."""

    def test_save_and_get_deal(self, store):
        store.save_deal(
            id="d1",
            seller_url="http://seller.example.com",
            product_id="prod_1",
            product_name="Banner Ad",
            deal_type="PG",
            status="draft",
            price=12.50,
        )
        deal = store.get_deal("d1")
        assert deal is not None
        assert deal["id"] == "d1"
        assert deal["seller_url"] == "http://seller.example.com"
        assert deal["product_id"] == "prod_1"
        assert deal["product_name"] == "Banner Ad"
        assert deal["deal_type"] == "PG"
        assert deal["status"] == "draft"
        assert deal["price"] == 12.50

    def test_get_deal_not_found(self, store):
        assert store.get_deal("nonexistent") is None

    def test_save_deal_upsert(self, store):
        store.save_deal(id="d2", seller_url="http://s", product_id="p1", status="draft")
        store.save_deal(id="d2", seller_url="http://s", product_id="p1", status="booked")
        deal = store.get_deal("d2")
        assert deal["status"] == "booked"

    def test_list_deals_no_filter(self, store):
        store.save_deal(id="d1", seller_url="http://s", product_id="p1")
        store.save_deal(id="d2", seller_url="http://s", product_id="p2")
        deals = store.list_deals()
        assert len(deals) == 2

    def test_list_deals_filter_by_status(self, store):
        store.save_deal(id="d1", seller_url="http://s", product_id="p1", status="draft")
        store.save_deal(id="d2", seller_url="http://s", product_id="p2", status="booked")
        drafts = store.list_deals(status="draft")
        assert len(drafts) == 1
        assert drafts[0]["id"] == "d1"

    def test_list_deals_filter_by_seller(self, store):
        store.save_deal(id="d1", seller_url="http://a", product_id="p1")
        store.save_deal(id="d2", seller_url="http://b", product_id="p2")
        result = store.list_deals(seller_url="http://a")
        assert len(result) == 1
        assert result[0]["id"] == "d1"

    def test_list_deals_limit(self, store):
        for i in range(10):
            store.save_deal(id=f"d{i}", seller_url="http://s", product_id="p")
        result = store.list_deals(limit=3)
        assert len(result) == 3

    def test_save_deal_with_all_optional_fields(self, store):
        store.save_deal(
            id="dfull",
            seller_url="http://seller",
            product_id="prod",
            product_name="Full Deal",
            seller_deal_id="seller-123",
            deal_type="PD",
            status="negotiating",
            price=10.0,
            original_price=15.0,
            impressions=1000000,
            flight_start="2026-04-01",
            flight_end="2026-04-30",
            buyer_context='{"identity": "test"}',
            metadata='{"custom": "field"}',
        )
        deal = store.get_deal("dfull")
        assert deal["seller_deal_id"] == "seller-123"
        assert deal["original_price"] == 15.0
        assert deal["impressions"] == 1000000
        assert deal["flight_start"] == "2026-04-01"
        assert deal["buyer_context"] == '{"identity": "test"}'
        assert deal["metadata"] == '{"custom": "field"}'


# ---------------------------------------------------------------------------
# Deal status updates
# ---------------------------------------------------------------------------


class TestDealStatusUpdates:
    """Test update_deal_status writes to both tables."""

    def test_update_deal_status(self, store):
        store.save_deal(id="d1", seller_url="http://s", product_id="p", status="draft")
        result = store.update_deal_status("d1", "booked", triggered_by="user")
        assert result is True
        deal = store.get_deal("d1")
        assert deal["status"] == "booked"

    def test_update_deal_status_records_transition(self, store):
        store.save_deal(id="d1", seller_url="http://s", product_id="p", status="draft")
        store.update_deal_status("d1", "negotiating", triggered_by="agent")
        history = store.get_status_history("deal", "d1")
        assert len(history) == 1
        assert history[0]["from_status"] == "draft"
        assert history[0]["to_status"] == "negotiating"
        assert history[0]["triggered_by"] == "agent"

    def test_update_deal_status_not_found(self, store):
        result = store.update_deal_status("nonexistent", "booked")
        assert result is False

    def test_multiple_status_transitions(self, store):
        store.save_deal(id="d1", seller_url="http://s", product_id="p", status="draft")
        store.update_deal_status("d1", "negotiating")
        store.update_deal_status("d1", "booked")
        store.update_deal_status("d1", "delivering")
        history = store.get_status_history("deal", "d1")
        assert len(history) == 3
        statuses = [(h["from_status"], h["to_status"]) for h in history]
        assert statuses == [
            ("draft", "negotiating"),
            ("negotiating", "booked"),
            ("booked", "delivering"),
        ]


# ---------------------------------------------------------------------------
# Negotiation rounds
# ---------------------------------------------------------------------------


class TestNegotiationRounds:
    """Test negotiation round persistence and queries."""

    def test_save_and_get_negotiation_history(self, store):
        store.save_deal(id="d1", seller_url="http://s", product_id="p")
        store.save_negotiation_round(
            deal_id="d1",
            proposal_id="prop_1",
            round_number=1,
            buyer_price=10.0,
            seller_price=15.0,
            action="counter",
            rationale="Too high",
        )
        store.save_negotiation_round(
            deal_id="d1",
            proposal_id="prop_1",
            round_number=2,
            buyer_price=12.0,
            seller_price=13.0,
            action="accept",
        )
        rounds = store.get_negotiation_history("d1")
        assert len(rounds) == 2
        assert rounds[0]["round_number"] == 1
        assert rounds[1]["round_number"] == 2
        assert rounds[0]["action"] == "counter"
        assert rounds[1]["action"] == "accept"

    def test_negotiation_round_unique_constraint(self, store):
        store.save_deal(id="d1", seller_url="http://s", product_id="p")
        store.save_negotiation_round(
            deal_id="d1",
            proposal_id="prop_1",
            round_number=1,
            buyer_price=10.0,
            seller_price=15.0,
            action="counter",
        )
        with pytest.raises(sqlite3.IntegrityError):
            store.save_negotiation_round(
                deal_id="d1",
                proposal_id="prop_1",
                round_number=1,
                buyer_price=11.0,
                seller_price=14.0,
                action="counter",
            )

    def test_empty_negotiation_history(self, store):
        store.save_deal(id="d1", seller_url="http://s", product_id="p")
        assert store.get_negotiation_history("d1") == []

    def test_save_negotiation_round_returns_id(self, store):
        store.save_deal(id="d1", seller_url="http://s", product_id="p")
        row_id = store.save_negotiation_round(
            deal_id="d1",
            proposal_id="prop_1",
            round_number=1,
            buyer_price=10.0,
            seller_price=15.0,
            action="counter",
        )
        assert isinstance(row_id, int)
        assert row_id > 0


# ---------------------------------------------------------------------------
# Booking records
# ---------------------------------------------------------------------------


class TestBookingRecords:
    """Test booking record persistence and queries."""

    def test_save_and_get_booking_records(self, store):
        store.save_deal(id="d1", seller_url="http://s", product_id="p")
        store.save_booking_record(
            deal_id="d1",
            order_id="order_1",
            line_id="line_1",
            channel="branding",
            impressions=500000,
            cost=7500.00,
            booking_status="confirmed",
        )
        records = store.get_booking_records("d1")
        assert len(records) == 1
        assert records[0]["order_id"] == "order_1"
        assert records[0]["impressions"] == 500000
        assert records[0]["cost"] == 7500.00
        assert records[0]["booking_status"] == "confirmed"

    def test_multiple_booking_records(self, store):
        store.save_deal(id="d1", seller_url="http://s", product_id="p")
        store.save_booking_record(
            deal_id="d1", order_id="o1", line_id="l1", channel="ctv",
        )
        store.save_booking_record(
            deal_id="d1", order_id="o1", line_id="l2", channel="branding",
        )
        records = store.get_booking_records("d1")
        assert len(records) == 2

    def test_empty_booking_records(self, store):
        store.save_deal(id="d1", seller_url="http://s", product_id="p")
        assert store.get_booking_records("d1") == []

    def test_booking_record_unique_constraint(self, store):
        store.save_deal(id="d1", seller_url="http://s", product_id="p")
        store.save_booking_record(
            deal_id="d1", order_id="o1", line_id="l1", channel="ctv",
        )
        with pytest.raises(sqlite3.IntegrityError):
            store.save_booking_record(
                deal_id="d1", order_id="o1", line_id="l1", channel="branding",
            )


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


class TestJobs:
    """Test job persistence with JSON serialization."""

    def test_save_and_get_job(self, store):
        data = {
            "status": "pending",
            "progress": 0.0,
            "brief": {"name": "Test Campaign", "budget": 50000},
            "auto_approve": True,
            "budget_allocations": {"branding": {"budget": 20000}},
            "recommendations": [{"product_id": "p1"}],
            "booked_lines": [],
            "errors": [],
        }
        store.save_job("job-1", data)
        job = store.get_job("job-1")
        assert job is not None
        assert job["status"] == "pending"
        assert job["progress"] == 0.0
        assert job["brief"] == {"name": "Test Campaign", "budget": 50000}
        assert job["auto_approve"] is True
        assert job["recommendations"] == [{"product_id": "p1"}]

    def test_get_job_not_found(self, store):
        assert store.get_job("nonexistent") is None

    def test_save_job_upsert(self, store):
        store.save_job("job-1", {"status": "pending", "progress": 0.0})
        store.save_job("job-1", {"status": "running", "progress": 0.5})
        job = store.get_job("job-1")
        assert job["status"] == "running"
        assert job["progress"] == 0.5

    def test_list_jobs_no_filter(self, store):
        store.save_job("j1", {"status": "pending"})
        store.save_job("j2", {"status": "running"})
        jobs = store.list_jobs()
        assert len(jobs) == 2

    def test_list_jobs_filter_by_status(self, store):
        store.save_job("j1", {"status": "pending"})
        store.save_job("j2", {"status": "running"})
        store.save_job("j3", {"status": "pending"})
        pending = store.list_jobs(status="pending")
        assert len(pending) == 2

    def test_list_jobs_limit(self, store):
        for i in range(10):
            store.save_job(f"j{i}", {"status": "pending"})
        jobs = store.list_jobs(limit=5)
        assert len(jobs) == 5

    def test_job_auto_approve_false(self, store):
        store.save_job("j1", {"status": "pending", "auto_approve": False})
        job = store.get_job("j1")
        assert job["auto_approve"] is False

    def test_job_json_roundtrip(self, store):
        """Verify JSON columns survive serialization/deserialization."""
        errors = ["Error 1", "Error 2"]
        booked = [
            {"line_id": "l1", "cost": 100},
            {"line_id": "l2", "cost": 200},
        ]
        store.save_job("j1", {
            "status": "failed",
            "errors": errors,
            "booked_lines": booked,
        })
        job = store.get_job("j1")
        assert job["errors"] == errors
        assert job["booked_lines"] == booked


# ---------------------------------------------------------------------------
# Status transitions (standalone)
# ---------------------------------------------------------------------------


class TestStatusTransitions:
    """Test the standalone status transition recording."""

    def test_record_and_get_transitions(self, store):
        tid = store.record_status_transition(
            entity_type="booking",
            entity_id="b1",
            from_status=None,
            to_status="pending",
            triggered_by="system",
        )
        assert isinstance(tid, int)
        history = store.get_status_history("booking", "b1")
        assert len(history) == 1
        assert history[0]["from_status"] is None
        assert history[0]["to_status"] == "pending"

    def test_multiple_transitions_ordered(self, store):
        store.record_status_transition(
            entity_type="deal", entity_id="d1",
            from_status=None, to_status="draft",
        )
        store.record_status_transition(
            entity_type="deal", entity_id="d1",
            from_status="draft", to_status="booked",
        )
        history = store.get_status_history("deal", "d1")
        assert len(history) == 2
        assert history[0]["to_status"] == "draft"
        assert history[1]["to_status"] == "booked"

    def test_transitions_isolated_by_entity(self, store):
        store.record_status_transition(
            entity_type="deal", entity_id="d1",
            from_status=None, to_status="draft",
        )
        store.record_status_transition(
            entity_type="deal", entity_id="d2",
            from_status=None, to_status="booked",
        )
        h1 = store.get_status_history("deal", "d1")
        h2 = store.get_status_history("deal", "d2")
        assert len(h1) == 1
        assert len(h2) == 1


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    """Test that concurrent writes do not corrupt the store."""

    def test_concurrent_deal_writes(self, store):
        """Multiple threads writing deals concurrently should not raise."""
        errors = []

        def writer(thread_id: int):
            try:
                for i in range(20):
                    store.save_deal(
                        id=f"t{thread_id}_d{i}",
                        seller_url="http://s",
                        product_id=f"p{i}",
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        deals = store.list_deals(limit=200)
        assert len(deals) == 100  # 5 threads x 20 deals

    def test_concurrent_job_writes(self, store):
        """Multiple threads writing jobs concurrently should not raise."""
        errors = []

        def writer(thread_id: int):
            try:
                for i in range(20):
                    store.save_job(
                        f"t{thread_id}_j{i}",
                        {"status": "pending", "progress": 0.0},
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        jobs = store.list_jobs(limit=200)
        assert len(jobs) == 100


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


class TestConnectionLifecycle:
    """Test connect/disconnect behavior."""

    def test_connect_enables_wal_mode(self):
        s = DealStore("sqlite:///:memory:")
        s.connect()
        cursor = s._conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        # In-memory databases report 'memory' for journal_mode
        assert mode in ("wal", "memory")
        s.disconnect()

    def test_connect_enables_foreign_keys(self):
        s = DealStore("sqlite:///:memory:")
        s.connect()
        cursor = s._conn.execute("PRAGMA foreign_keys")
        assert cursor.fetchone()[0] == 1
        s.disconnect()

    def test_disconnect_sets_conn_to_none(self):
        s = DealStore("sqlite:///:memory:")
        s.connect()
        s.disconnect()
        assert s._conn is None

    def test_disconnect_when_not_connected(self):
        s = DealStore("sqlite:///:memory:")
        s.disconnect()  # Should not raise


# ---------------------------------------------------------------------------
# Flow integration: store injected vs None
# ---------------------------------------------------------------------------


class TestFlowStoreIntegration:
    """Test that flows work with and without the store parameter."""

    def test_deal_booking_flow_accepts_store_none(self):
        """DealBookingFlow should work when store=None (backward compat)."""
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        from ad_buyer.flows.deal_booking_flow import DealBookingFlow

        flow = DealBookingFlow(client=mock_client, store=None)
        assert flow._store is None

    def test_deal_booking_flow_accepts_store(self, store):
        """DealBookingFlow should accept a DealStore instance."""
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        from ad_buyer.flows.deal_booking_flow import DealBookingFlow

        flow = DealBookingFlow(client=mock_client, store=store)
        assert flow._store is store

    def test_dsp_deal_flow_accepts_store_none(self):
        """DSPDealFlow should work when store=None (backward compat)."""
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        mock_context = MagicMock()
        from ad_buyer.flows.dsp_deal_flow import DSPDealFlow

        flow = DSPDealFlow(client=mock_client, buyer_context=mock_context, store=None)
        assert flow._store is None

    def test_dsp_deal_flow_accepts_store(self, store):
        """DSPDealFlow should accept a DealStore instance."""
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        mock_context = MagicMock()
        from ad_buyer.flows.dsp_deal_flow import DSPDealFlow

        flow = DSPDealFlow(client=mock_client, buyer_context=mock_context, store=store)
        assert flow._store is store

    def test_persist_booking_is_best_effort(self, store):
        """_persist_booking should not raise when store fails."""
        from unittest.mock import MagicMock, patch

        mock_client = MagicMock()
        from ad_buyer.flows.deal_booking_flow import DealBookingFlow

        flow = DealBookingFlow(client=mock_client, store=store)

        # Create a mock booked line
        mock_booked = MagicMock()
        mock_booked.order_id = "o1"
        mock_booked.line_id = "l1"
        mock_booked.channel = "branding"
        mock_booked.impressions = 1000
        mock_booked.cost = 50.0
        mock_booked.booking_status = "pending"

        # This should work with a valid store (though deal_id won't match a deal)
        # The FK constraint will raise, but _persist_booking should catch it
        flow._persist_booking("nonexistent_deal", mock_booked)
        # No exception raised -- best effort

    def test_persist_booking_noop_when_store_is_none(self):
        """_persist_booking does nothing when store is None."""
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        from ad_buyer.flows.deal_booking_flow import DealBookingFlow

        flow = DealBookingFlow(client=mock_client, store=None)
        mock_booked = MagicMock()
        # Should not raise
        flow._persist_booking("d1", mock_booked)


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------


class TestGetDealStore:
    """Test the get_deal_store singleton factory."""

    def test_get_deal_store_returns_store(self, monkeypatch):
        """get_deal_store should return a connected DealStore."""
        import sys
        import importlib
        import ad_buyer.storage as storage_mod

        # Reset singleton
        storage_mod._store = None

        # Force-import the settings module to register it in sys.modules
        importlib.import_module("ad_buyer.config.settings")
        settings_module = sys.modules["ad_buyer.config.settings"]

        # Patch the module-level settings instance
        from unittest.mock import MagicMock
        mock_settings = MagicMock()
        mock_settings.database_url = "sqlite:///:memory:"
        monkeypatch.setattr(settings_module, "settings", mock_settings)

        s = storage_mod.get_deal_store()
        assert isinstance(s, DealStore)
        assert s._conn is not None

        # Second call returns same instance
        s2 = storage_mod.get_deal_store()
        assert s is s2

        # Cleanup
        s.disconnect()
        storage_mod._store = None

    def test_get_deal_store_singleton_reset(self, monkeypatch):
        """Verify singleton can be reset for testing."""
        import ad_buyer.storage as storage_mod

        storage_mod._store = None
        assert storage_mod._store is None


# ---------------------------------------------------------------------------
# Query patterns (downstream dashboard queries)
# ---------------------------------------------------------------------------


class TestQueryPatterns:
    """Test queries that downstream beads (ar-31b, ar-j0r) will need."""

    def _seed_data(self, store):
        """Seed the store with representative data."""
        store.save_deal(id="d1", seller_url="http://a", product_id="p1", status="delivering")
        store.save_deal(id="d2", seller_url="http://a", product_id="p2", status="completed")
        store.save_deal(id="d3", seller_url="http://b", product_id="p3", status="draft")
        store.save_deal(
            id="d4", seller_url="http://a", product_id="p4", status="cancelled",
            seller_deal_id="seller-456",
        )

        # Negotiation for d1
        store.save_negotiation_round(
            deal_id="d1", proposal_id="prop_1", round_number=1,
            buyer_price=10.0, seller_price=15.0, action="counter",
        )
        store.save_negotiation_round(
            deal_id="d1", proposal_id="prop_1", round_number=2,
            buyer_price=12.0, seller_price=12.5, action="accept",
        )

        # Bookings for d1
        store.save_booking_record(
            deal_id="d1", order_id="o1", line_id="l1",
            channel="branding", impressions=500000, cost=6000.0,
            booking_status="delivering",
        )
        store.save_booking_record(
            deal_id="d1", order_id="o1", line_id="l2",
            channel="ctv", impressions=200000, cost=5000.0,
            booking_status="confirmed",
        )

        # Status transitions
        store.record_status_transition(
            entity_type="deal", entity_id="d1",
            from_status=None, to_status="draft",
        )
        store.record_status_transition(
            entity_type="deal", entity_id="d1",
            from_status="draft", to_status="negotiating",
        )
        store.record_status_transition(
            entity_type="deal", entity_id="d1",
            from_status="negotiating", to_status="delivering",
        )

    def test_active_deals_query(self, store):
        """Dashboard: show all deals not in terminal state."""
        self._seed_data(store)
        terminal = {"completed", "cancelled", "failed"}
        all_deals = store.list_deals()
        active = [d for d in all_deals if d["status"] not in terminal]
        assert len(active) == 2  # d1 (delivering), d3 (draft)

    def test_deals_by_status(self, store):
        self._seed_data(store)
        delivering = store.list_deals(status="delivering")
        assert len(delivering) == 1
        assert delivering[0]["id"] == "d1"

    def test_deals_by_seller(self, store):
        self._seed_data(store)
        seller_a = store.list_deals(seller_url="http://a")
        assert len(seller_a) == 3  # d1, d2, d4

    def test_deal_with_negotiation_history(self, store):
        self._seed_data(store)
        deal = store.get_deal("d1")
        assert deal is not None
        rounds = store.get_negotiation_history("d1")
        assert len(rounds) == 2

    def test_deal_with_bookings(self, store):
        self._seed_data(store)
        bookings = store.get_booking_records("d1")
        assert len(bookings) == 2
        total_cost = sum(b["cost"] for b in bookings)
        assert total_cost == 11000.0

    def test_deal_status_history(self, store):
        self._seed_data(store)
        history = store.get_status_history("deal", "d1")
        assert len(history) == 3
        assert [h["to_status"] for h in history] == ["draft", "negotiating", "delivering"]

    def test_find_deal_by_seller_deal_id(self, store):
        """ar-j0r: find a deal by the seller's deal ID."""
        self._seed_data(store)
        # Query by seller_deal_id — not a direct method, but can use list_deals + filter
        # or direct SQL. For now, we use the index to verify it works.
        cursor = store._conn.execute(
            "SELECT * FROM deals WHERE seller_deal_id = ?",
            ("seller-456",),
        )
        row = cursor.fetchone()
        assert row is not None
        assert dict(row)["id"] == "d4"
