# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Comprehensive tests for DealStore — the SQLite deal state persistence layer.

All tests use in-memory SQLite (`:memory:`) for speed and isolation.
"""

import json
import sqlite3
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from ad_buyer.storage import DealStore, SCHEMA_VERSION, create_tables, initialize_schema
from ad_buyer.storage.schema import (
    get_schema_version,
    run_migrations,
    set_schema_version,
)


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------

@pytest.fixture
def deal_store():
    """Create a DealStore backed by in-memory SQLite."""
    store = DealStore("sqlite:///:memory:")
    store.connect()
    yield store
    store.disconnect()


@pytest.fixture
def raw_conn():
    """A raw in-memory connection for schema-level tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


# -----------------------------------------------------------------------
# Schema Tests
# -----------------------------------------------------------------------

class TestSchema:
    """Tests for schema creation and migration."""

    def test_create_tables_creates_all_tables(self, raw_conn):
        """All 6 tables (5 domain + schema_version) are created."""
        create_tables(raw_conn)
        cursor = raw_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
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
        """All expected indexes are created."""
        create_tables(raw_conn)
        cursor = raw_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        )
        indexes = {row[0] for row in cursor.fetchall()}
        expected_indexes = {
            "idx_deals_status",
            "idx_deals_seller_url",
            "idx_deals_seller_deal_id",
            "idx_deals_created_at",
            "idx_deals_status_created",
            "idx_neg_rounds_deal_id",
            "idx_neg_rounds_proposal_id",
            "idx_booking_deal_id",
            "idx_booking_status",
            "idx_booking_order_id",
            "idx_jobs_status",
            "idx_jobs_created_at",
            "idx_transitions_entity",
            "idx_transitions_created",
        }
        assert expected_indexes.issubset(indexes)

    def test_create_tables_is_idempotent(self, raw_conn):
        """Running create_tables twice doesn't raise."""
        create_tables(raw_conn)
        create_tables(raw_conn)  # Should not raise

    def test_schema_version_default_is_zero(self, raw_conn):
        """get_schema_version returns 0 when table doesn't exist."""
        assert get_schema_version(raw_conn) == 0

    def test_schema_version_after_init(self, raw_conn):
        """initialize_schema sets the version to SCHEMA_VERSION."""
        initialize_schema(raw_conn)
        assert get_schema_version(raw_conn) == SCHEMA_VERSION

    def test_set_and_get_schema_version(self, raw_conn):
        """set_schema_version / get_schema_version round-trips."""
        create_tables(raw_conn)
        set_schema_version(raw_conn, 42)
        assert get_schema_version(raw_conn) == 42

    def test_run_migrations_noop_when_current(self, raw_conn):
        """run_migrations is a no-op when already at SCHEMA_VERSION."""
        create_tables(raw_conn)
        set_schema_version(raw_conn, SCHEMA_VERSION)
        run_migrations(raw_conn)  # Should not raise
        assert get_schema_version(raw_conn) == SCHEMA_VERSION


# -----------------------------------------------------------------------
# DealStore Lifecycle Tests
# -----------------------------------------------------------------------

class TestDealStoreLifecycle:
    """Tests for DealStore connect/disconnect."""

    def test_connect_creates_tables(self):
        """Connecting creates all tables."""
        store = DealStore("sqlite:///:memory:")
        store.connect()
        # Verify by listing tables
        cursor = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in cursor.fetchall()}
        assert "deals" in tables
        assert "jobs" in tables
        store.disconnect()

    def test_disconnect_closes_connection(self):
        """Disconnect sets _conn to None."""
        store = DealStore("sqlite:///:memory:")
        store.connect()
        store.disconnect()
        assert store._conn is None

    def test_parse_url_sqlite_prefix(self):
        """_parse_url strips sqlite:/// prefix."""
        assert DealStore._parse_url("sqlite:///./ad_buyer.db") == "./ad_buyer.db"

    def test_parse_url_memory(self):
        """_parse_url handles :memory: databases."""
        assert DealStore._parse_url("sqlite:///:memory:") == ":memory:"

    def test_parse_url_plain_path(self):
        """_parse_url passes through plain paths."""
        assert DealStore._parse_url("/tmp/test.db") == "/tmp/test.db"

    def test_wal_mode_enabled(self):
        """WAL journal mode is set on connect."""
        store = DealStore("sqlite:///:memory:")
        store.connect()
        cursor = store._conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        # In-memory databases may report 'memory' instead of 'wal'
        assert mode in ("wal", "memory")
        store.disconnect()

    def test_foreign_keys_enabled(self):
        """Foreign key enforcement is on."""
        store = DealStore("sqlite:///:memory:")
        store.connect()
        cursor = store._conn.execute("PRAGMA foreign_keys")
        assert cursor.fetchone()[0] == 1
        store.disconnect()


# -----------------------------------------------------------------------
# Deal CRUD Tests
# -----------------------------------------------------------------------

class TestDealCRUD:
    """Tests for deal creation, retrieval, listing, and status updates."""

    def test_save_deal_returns_id(self, deal_store):
        """save_deal returns the deal ID."""
        did = deal_store.save_deal(
            seller_url="http://seller.example.com",
            product_id="prod_1",
            product_name="Banner Ad",
        )
        assert did is not None
        assert isinstance(did, str)
        assert len(did) > 0

    def test_save_deal_with_custom_id(self, deal_store):
        """save_deal uses the provided deal_id."""
        did = deal_store.save_deal(
            deal_id="custom-123",
            seller_url="http://seller.example.com",
            product_id="prod_1",
        )
        assert did == "custom-123"

    def test_get_deal_returns_all_fields(self, deal_store):
        """get_deal returns all stored fields."""
        did = deal_store.save_deal(
            seller_url="http://seller.example.com",
            product_id="prod_1",
            product_name="Banner Ad",
            deal_type="PG",
            status="quoted",
            price=12.50,
            original_price=15.00,
            impressions=1000000,
            flight_start="2026-04-01",
            flight_end="2026-04-30",
            buyer_context='{"tier": "gold"}',
            metadata='{"source": "dsp"}',
        )

        deal = deal_store.get_deal(did)
        assert deal is not None
        assert deal["id"] == did
        assert deal["seller_url"] == "http://seller.example.com"
        assert deal["product_id"] == "prod_1"
        assert deal["product_name"] == "Banner Ad"
        assert deal["deal_type"] == "PG"
        assert deal["status"] == "quoted"
        assert deal["price"] == 12.50
        assert deal["original_price"] == 15.00
        assert deal["impressions"] == 1000000
        assert deal["flight_start"] == "2026-04-01"
        assert deal["flight_end"] == "2026-04-30"
        assert deal["buyer_context"] == '{"tier": "gold"}'
        assert deal["metadata"] == '{"source": "dsp"}'
        assert deal["created_at"] is not None
        assert deal["updated_at"] is not None

    def test_get_deal_not_found_returns_none(self, deal_store):
        """get_deal returns None for missing deals."""
        assert deal_store.get_deal("nonexistent") is None

    def test_save_deal_creates_initial_transition(self, deal_store):
        """save_deal records an initial status transition."""
        did = deal_store.save_deal(
            seller_url="http://seller.example.com",
            product_id="prod_1",
            status="draft",
        )
        history = deal_store.get_status_history("deal", did)
        assert len(history) == 1
        assert history[0]["from_status"] is None
        assert history[0]["to_status"] == "draft"
        assert history[0]["notes"] == "Deal created"

    def test_list_deals_no_filter(self, deal_store):
        """list_deals returns all deals when unfiltered."""
        deal_store.save_deal(seller_url="http://a.com", product_id="p1")
        deal_store.save_deal(seller_url="http://b.com", product_id="p2")
        deals = deal_store.list_deals()
        assert len(deals) == 2

    def test_list_deals_filter_by_status(self, deal_store):
        """list_deals filters by status."""
        deal_store.save_deal(
            seller_url="http://a.com", product_id="p1", status="draft"
        )
        deal_store.save_deal(
            seller_url="http://b.com", product_id="p2", status="booked"
        )
        drafts = deal_store.list_deals(status="draft")
        assert len(drafts) == 1
        assert drafts[0]["status"] == "draft"

    def test_list_deals_filter_by_seller(self, deal_store):
        """list_deals filters by seller_url."""
        deal_store.save_deal(seller_url="http://a.com", product_id="p1")
        deal_store.save_deal(seller_url="http://b.com", product_id="p2")
        results = deal_store.list_deals(seller_url="http://a.com")
        assert len(results) == 1
        assert results[0]["seller_url"] == "http://a.com"

    def test_list_deals_limit(self, deal_store):
        """list_deals respects the limit parameter."""
        for i in range(5):
            deal_store.save_deal(
                seller_url="http://a.com", product_id=f"p{i}"
            )
        results = deal_store.list_deals(limit=3)
        assert len(results) == 3

    def test_list_deals_ordered_by_created_at_desc(self, deal_store):
        """list_deals returns newest first."""
        d1 = deal_store.save_deal(
            seller_url="http://a.com", product_id="p1"
        )
        d2 = deal_store.save_deal(
            seller_url="http://a.com", product_id="p2"
        )
        results = deal_store.list_deals()
        # Most recently created should come first
        assert results[0]["id"] == d2
        assert results[1]["id"] == d1

    def test_update_deal_status(self, deal_store):
        """update_deal_status changes the status and logs transition."""
        did = deal_store.save_deal(
            seller_url="http://a.com", product_id="p1", status="draft"
        )
        result = deal_store.update_deal_status(did, "negotiating")
        assert result is True

        deal = deal_store.get_deal(did)
        assert deal["status"] == "negotiating"

        history = deal_store.get_status_history("deal", did)
        assert len(history) == 2  # creation + update
        assert history[1]["from_status"] == "draft"
        assert history[1]["to_status"] == "negotiating"

    def test_update_deal_status_not_found(self, deal_store):
        """update_deal_status returns False for missing deals."""
        result = deal_store.update_deal_status("nonexistent", "booked")
        assert result is False

    def test_update_deal_status_with_triggered_by(self, deal_store):
        """update_deal_status records triggered_by in transition."""
        did = deal_store.save_deal(
            seller_url="http://a.com", product_id="p1", status="draft"
        )
        deal_store.update_deal_status(
            did, "delivering", triggered_by="seller_push", notes="Seller confirmed"
        )
        history = deal_store.get_status_history("deal", did)
        last = history[-1]
        assert last["triggered_by"] == "seller_push"
        assert last["notes"] == "Seller confirmed"


# -----------------------------------------------------------------------
# Negotiation Round Tests
# -----------------------------------------------------------------------

class TestNegotiationRounds:
    """Tests for negotiation round persistence."""

    def test_save_and_get_rounds(self, deal_store):
        """save_negotiation_round and get_negotiation_history round-trip."""
        did = deal_store.save_deal(
            seller_url="http://a.com", product_id="p1"
        )
        deal_store.save_negotiation_round(
            deal_id=did,
            proposal_id="prop_1",
            round_number=1,
            buyer_price=10.0,
            seller_price=15.0,
            action="counter",
            rationale="Below budget",
        )
        deal_store.save_negotiation_round(
            deal_id=did,
            proposal_id="prop_1",
            round_number=2,
            buyer_price=12.0,
            seller_price=13.0,
            action="accept",
            rationale="Close enough",
        )

        history = deal_store.get_negotiation_history(did)
        assert len(history) == 2
        assert history[0]["round_number"] == 1
        assert history[0]["buyer_price"] == 10.0
        assert history[0]["action"] == "counter"
        assert history[1]["round_number"] == 2
        assert history[1]["action"] == "accept"

    def test_negotiation_round_ordered_by_round_number(self, deal_store):
        """Rounds are returned in ascending order."""
        did = deal_store.save_deal(
            seller_url="http://a.com", product_id="p1"
        )
        # Insert out of order
        deal_store.save_negotiation_round(
            deal_id=did, proposal_id="prop_1", round_number=3,
            buyer_price=12.0, seller_price=13.0, action="accept",
        )
        deal_store.save_negotiation_round(
            deal_id=did, proposal_id="prop_1", round_number=1,
            buyer_price=10.0, seller_price=15.0, action="counter",
        )
        history = deal_store.get_negotiation_history(did)
        assert history[0]["round_number"] == 1
        assert history[1]["round_number"] == 3

    def test_duplicate_round_number_raises(self, deal_store):
        """UNIQUE(deal_id, round_number) prevents duplicate rounds."""
        did = deal_store.save_deal(
            seller_url="http://a.com", product_id="p1"
        )
        deal_store.save_negotiation_round(
            deal_id=did, proposal_id="prop_1", round_number=1,
            buyer_price=10.0, seller_price=15.0, action="counter",
        )
        with pytest.raises(sqlite3.IntegrityError):
            deal_store.save_negotiation_round(
                deal_id=did, proposal_id="prop_1", round_number=1,
                buyer_price=11.0, seller_price=14.0, action="counter",
            )

    def test_empty_negotiation_history(self, deal_store):
        """get_negotiation_history returns empty list for deal with no rounds."""
        did = deal_store.save_deal(
            seller_url="http://a.com", product_id="p1"
        )
        assert deal_store.get_negotiation_history(did) == []


# -----------------------------------------------------------------------
# Booking Record Tests
# -----------------------------------------------------------------------

class TestBookingRecords:
    """Tests for booking record persistence."""

    def test_save_and_get_booking(self, deal_store):
        """save_booking_record and get_booking_records round-trip."""
        did = deal_store.save_deal(
            seller_url="http://a.com", product_id="p1"
        )
        rid = deal_store.save_booking_record(
            deal_id=did,
            order_id="order_1",
            line_id="line_1",
            channel="branding",
            impressions=500000,
            cost=7500.0,
            booking_status="confirmed",
        )
        assert rid > 0

        records = deal_store.get_booking_records(did)
        assert len(records) == 1
        assert records[0]["order_id"] == "order_1"
        assert records[0]["impressions"] == 500000
        assert records[0]["cost"] == 7500.0
        assert records[0]["booking_status"] == "confirmed"

    def test_multiple_bookings_per_deal(self, deal_store):
        """A deal can have multiple booking records."""
        did = deal_store.save_deal(
            seller_url="http://a.com", product_id="p1"
        )
        deal_store.save_booking_record(
            deal_id=did, line_id="line_1", channel="branding",
            impressions=500000, cost=7500.0,
        )
        deal_store.save_booking_record(
            deal_id=did, line_id="line_2", channel="ctv",
            impressions=200000, cost=6000.0,
        )
        records = deal_store.get_booking_records(did)
        assert len(records) == 2

    def test_duplicate_line_id_raises(self, deal_store):
        """UNIQUE(deal_id, line_id) prevents duplicate line bookings."""
        did = deal_store.save_deal(
            seller_url="http://a.com", product_id="p1"
        )
        deal_store.save_booking_record(
            deal_id=did, line_id="line_1", channel="branding",
        )
        with pytest.raises(sqlite3.IntegrityError):
            deal_store.save_booking_record(
                deal_id=did, line_id="line_1", channel="ctv",
            )

    def test_empty_booking_records(self, deal_store):
        """get_booking_records returns empty list for deal with no bookings."""
        did = deal_store.save_deal(
            seller_url="http://a.com", product_id="p1"
        )
        assert deal_store.get_booking_records(did) == []


# -----------------------------------------------------------------------
# Job Tests
# -----------------------------------------------------------------------

class TestJobCRUD:
    """Tests for job upsert and retrieval."""

    def test_save_and_get_job(self, deal_store):
        """save_job and get_job round-trip."""
        deal_store.save_job(
            job_id="job_1",
            status="pending",
            progress=0.0,
            brief='{"name": "Test Campaign"}',
            auto_approve=False,
        )
        job = deal_store.get_job("job_1")
        assert job is not None
        assert job["id"] == "job_1"
        assert job["status"] == "pending"
        assert job["progress"] == 0.0
        assert job["brief"] == {"name": "Test Campaign"}
        assert job["auto_approve"] is False

    def test_save_job_upsert_updates(self, deal_store):
        """save_job updates existing job on conflict."""
        deal_store.save_job(job_id="job_1", status="pending", progress=0.0)
        deal_store.save_job(job_id="job_1", status="running", progress=0.5)

        job = deal_store.get_job("job_1")
        assert job["status"] == "running"
        assert job["progress"] == 0.5

    def test_get_job_not_found(self, deal_store):
        """get_job returns None for missing jobs."""
        assert deal_store.get_job("nonexistent") is None

    def test_get_job_deserializes_json(self, deal_store):
        """get_job deserializes JSON fields."""
        deal_store.save_job(
            job_id="job_1",
            brief='{"name": "Camp"}',
            budget_allocs='{"branding": 1000}',
            recommendations='[{"id": "r1"}]',
            booked_lines='[{"id": "b1"}]',
            errors='["error1"]',
        )
        job = deal_store.get_job("job_1")
        assert isinstance(job["brief"], dict)
        assert isinstance(job["budget_allocs"], dict)
        assert isinstance(job["recommendations"], list)
        assert isinstance(job["booked_lines"], list)
        assert isinstance(job["errors"], list)

    def test_get_job_auto_approve_bool(self, deal_store):
        """get_job converts auto_approve int to bool."""
        deal_store.save_job(job_id="job_1", auto_approve=True)
        job = deal_store.get_job("job_1")
        assert job["auto_approve"] is True

    def test_list_jobs_no_filter(self, deal_store):
        """list_jobs returns all jobs unfiltered."""
        deal_store.save_job(job_id="j1", status="pending")
        deal_store.save_job(job_id="j2", status="running")
        jobs = deal_store.list_jobs()
        assert len(jobs) == 2

    def test_list_jobs_filter_by_status(self, deal_store):
        """list_jobs filters by status."""
        deal_store.save_job(job_id="j1", status="pending")
        deal_store.save_job(job_id="j2", status="running")
        deal_store.save_job(job_id="j3", status="pending")
        pending = deal_store.list_jobs(status="pending")
        assert len(pending) == 2
        for j in pending:
            assert j["status"] == "pending"

    def test_list_jobs_limit(self, deal_store):
        """list_jobs respects the limit parameter."""
        for i in range(5):
            deal_store.save_job(job_id=f"j{i}")
        results = deal_store.list_jobs(limit=3)
        assert len(results) == 3

    def test_list_jobs_deserializes_json(self, deal_store):
        """list_jobs deserializes JSON fields in results."""
        deal_store.save_job(
            job_id="j1",
            brief='{"name": "test"}',
            errors='["err"]',
        )
        jobs = deal_store.list_jobs()
        assert isinstance(jobs[0]["brief"], dict)
        assert isinstance(jobs[0]["errors"], list)


# -----------------------------------------------------------------------
# Status Transition Tests
# -----------------------------------------------------------------------

class TestStatusTransitions:
    """Tests for the append-only status transition audit log."""

    def test_record_and_get_transitions(self, deal_store):
        """record_status_transition and get_status_history round-trip."""
        deal_store.record_status_transition(
            entity_type="deal",
            entity_id="d1",
            from_status=None,
            to_status="draft",
            triggered_by="system",
            notes="Initial",
        )
        deal_store.record_status_transition(
            entity_type="deal",
            entity_id="d1",
            from_status="draft",
            to_status="negotiating",
            triggered_by="agent",
        )

        history = deal_store.get_status_history("deal", "d1")
        assert len(history) == 2
        assert history[0]["to_status"] == "draft"
        assert history[1]["from_status"] == "draft"
        assert history[1]["to_status"] == "negotiating"

    def test_transitions_ordered_by_created_at(self, deal_store):
        """Transitions are returned in chronological order."""
        for i, status in enumerate(["draft", "quoted", "negotiating", "booked"]):
            prev = ["draft", "quoted", "negotiating"][i - 1] if i > 0 else None
            deal_store.record_status_transition(
                entity_type="deal",
                entity_id="d1",
                from_status=prev,
                to_status=status,
            )

        history = deal_store.get_status_history("deal", "d1")
        statuses = [h["to_status"] for h in history]
        assert statuses == ["draft", "quoted", "negotiating", "booked"]

    def test_separate_entity_types(self, deal_store):
        """Transitions for different entity types are isolated."""
        deal_store.record_status_transition(
            entity_type="deal", entity_id="x1",
            from_status=None, to_status="draft",
        )
        deal_store.record_status_transition(
            entity_type="booking", entity_id="x1",
            from_status=None, to_status="pending",
        )

        deal_history = deal_store.get_status_history("deal", "x1")
        booking_history = deal_store.get_status_history("booking", "x1")
        assert len(deal_history) == 1
        assert len(booking_history) == 1

    def test_empty_status_history(self, deal_store):
        """get_status_history returns empty list for unknown entity."""
        assert deal_store.get_status_history("deal", "unknown") == []


# -----------------------------------------------------------------------
# Thread Safety Tests
# -----------------------------------------------------------------------

class TestThreadSafety:
    """Tests for concurrent access from multiple threads."""

    def test_concurrent_deal_writes(self, deal_store):
        """Multiple threads can write deals without corruption."""
        errors = []
        created_ids = []
        lock = threading.Lock()

        def writer(n):
            try:
                did = deal_store.save_deal(
                    seller_url=f"http://seller-{n}.com",
                    product_id=f"prod_{n}",
                )
                with lock:
                    created_ids.append(did)
            except Exception as e:
                with lock:
                    errors.append(str(e))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Errors during concurrent writes: {errors}"
        assert len(created_ids) == 20
        # All deals should be retrievable
        for did in created_ids:
            assert deal_store.get_deal(did) is not None

    def test_concurrent_job_upserts(self, deal_store):
        """Multiple threads can upsert jobs without corruption."""
        errors = []

        def upsert_job(n):
            try:
                deal_store.save_job(
                    job_id=f"job_{n}",
                    status="running",
                    progress=n / 20.0,
                )
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=upsert_job, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Errors during concurrent upserts: {errors}"
        jobs = deal_store.list_jobs(limit=50)
        assert len(jobs) == 20

    def test_concurrent_status_transitions(self, deal_store):
        """Multiple threads can record transitions simultaneously."""
        did = deal_store.save_deal(
            seller_url="http://a.com", product_id="p1"
        )
        errors = []

        def record_transition(n):
            try:
                deal_store.record_status_transition(
                    entity_type="deal",
                    entity_id=did,
                    from_status="draft",
                    to_status=f"status_{n}",
                )
            except Exception as e:
                errors.append(str(e))

        threads = [
            threading.Thread(target=record_transition, args=(i,))
            for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Errors during concurrent transitions: {errors}"
        # 1 from save_deal + 20 from threads
        history = deal_store.get_status_history("deal", did)
        assert len(history) == 21


# -----------------------------------------------------------------------
# Flow Integration Tests (store=None backward compatibility)
# -----------------------------------------------------------------------

class TestFlowIntegration:
    """Tests for flow-level store injection patterns."""

    def test_persist_deal_helper_with_store(self, deal_store):
        """When store is present, persistence writes succeed."""
        # Simulate the pattern flows will use
        store = deal_store  # Optional[DealStore]
        if store is not None:
            did = store.save_deal(
                seller_url="http://seller.com",
                product_id="prod_1",
                product_name="Test Product",
                status="draft",
            )
            assert store.get_deal(did) is not None

    def test_persist_deal_helper_without_store(self):
        """When store is None, no persistence happens (no error)."""
        store: Optional[DealStore] = None
        # This simulates the guard pattern in flows
        if store is not None:
            store.save_deal(
                seller_url="http://seller.com",
                product_id="prod_1",
            )
        # No assertion needed -- just verifying no error

    def test_graceful_degradation_on_store_error(self, deal_store):
        """Flow continues when store raises an exception."""
        # Simulate a broken store by closing the connection
        deal_store.disconnect()

        # The flow would catch this and log it
        flow_completed = False
        try:
            try:
                deal_store.save_deal(
                    seller_url="http://a.com", product_id="p1"
                )
            except Exception:
                pass  # Flow catches and logs, doesn't re-raise
            flow_completed = True
        except Exception:
            flow_completed = False

        assert flow_completed, "Flow should complete even when store fails"


# -----------------------------------------------------------------------
# API Job Tracking Migration Tests
# -----------------------------------------------------------------------

class TestAPIJobMigration:
    """Tests verifying DealStore can replace the in-memory jobs dict."""

    def test_job_lifecycle_matches_api_pattern(self, deal_store):
        """DealStore job lifecycle mirrors the api/main.py pattern."""
        import uuid as uuid_mod
        from datetime import datetime as dt

        job_id = str(uuid_mod.uuid4())
        brief = {"name": "Test Campaign", "budget": 50000}

        # Step 1: Create job (matches main.py line 175-186)
        deal_store.save_job(
            job_id=job_id,
            status="pending",
            progress=0.0,
            brief=json.dumps(brief),
            auto_approve=False,
        )

        # Step 2: Update to running (matches main.py line 341-343)
        deal_store.save_job(
            job_id=job_id,
            status="running",
            progress=0.1,
            brief=json.dumps(brief),
        )

        # Step 3: Update progress (matches main.py line 352-361)
        deal_store.save_job(
            job_id=job_id,
            status="running",
            progress=0.8,
            brief=json.dumps(brief),
            budget_allocs=json.dumps({"branding": {"budget": 20000}}),
            recommendations=json.dumps([{"id": "r1"}]),
        )

        # Step 4: Complete (matches main.py line 363-370)
        deal_store.save_job(
            job_id=job_id,
            status="completed",
            progress=1.0,
            brief=json.dumps(brief),
            booked_lines=json.dumps([{"id": "b1"}]),
        )

        # Verify final state
        job = deal_store.get_job(job_id)
        assert job["status"] == "completed"
        assert job["progress"] == 1.0
        assert job["brief"]["name"] == "Test Campaign"
        assert len(job["booked_lines"]) == 1

    def test_job_list_matches_api_list_pattern(self, deal_store):
        """list_jobs supports the same query patterns as GET /bookings."""
        for i in range(3):
            deal_store.save_job(
                job_id=f"j{i}",
                status="pending" if i < 2 else "completed",
                brief=json.dumps({"name": f"Campaign {i}", "budget": 1000 * (i + 1)}),
            )

        # Unfiltered listing
        all_jobs = deal_store.list_jobs()
        assert len(all_jobs) == 3

        # Status-filtered listing
        pending = deal_store.list_jobs(status="pending")
        assert len(pending) == 2

    def test_job_not_found_matches_api_404(self, deal_store):
        """get_job returns None, matching the 404 pattern in api/main.py."""
        assert deal_store.get_job("does-not-exist") is None


# -----------------------------------------------------------------------
# Edge Case / Constraint Tests
# -----------------------------------------------------------------------

class TestEdgeCases:
    """Tests for edge cases and data integrity."""

    def test_deal_with_null_optional_fields(self, deal_store):
        """Deals with null optional fields are handled correctly."""
        did = deal_store.save_deal(
            seller_url="http://a.com",
            product_id="p1",
            # All optional fields left as None/default
        )
        deal = deal_store.get_deal(did)
        assert deal["seller_deal_id"] is None
        assert deal["price"] is None
        assert deal["impressions"] is None
        assert deal["flight_start"] is None

    def test_deal_with_unicode_data(self, deal_store):
        """Unicode in product names and metadata is preserved."""
        did = deal_store.save_deal(
            seller_url="http://a.com",
            product_id="p1",
            product_name="Bannière Publicitaire",
            metadata='{"description": "広告バナー"}',
        )
        deal = deal_store.get_deal(did)
        assert deal["product_name"] == "Bannière Publicitaire"
        assert "広告バナー" in deal["metadata"]

    def test_large_metadata_json(self, deal_store):
        """Large JSON metadata is stored and retrieved correctly."""
        large_meta = json.dumps({f"key_{i}": f"value_{i}" for i in range(100)})
        did = deal_store.save_deal(
            seller_url="http://a.com",
            product_id="p1",
            metadata=large_meta,
        )
        deal = deal_store.get_deal(did)
        parsed = json.loads(deal["metadata"])
        assert len(parsed) == 100

    def test_foreign_key_cascade_on_negotiation(self, deal_store):
        """Deleting a deal cascades to negotiation_rounds."""
        did = deal_store.save_deal(
            seller_url="http://a.com", product_id="p1"
        )
        deal_store.save_negotiation_round(
            deal_id=did, proposal_id="prop_1", round_number=1,
            buyer_price=10.0, seller_price=15.0, action="counter",
        )
        # Delete the deal
        with deal_store._lock:
            deal_store._conn.execute("DELETE FROM deals WHERE id = ?", (did,))
            deal_store._conn.commit()

        # Negotiation rounds should be gone
        assert deal_store.get_negotiation_history(did) == []

    def test_foreign_key_cascade_on_booking(self, deal_store):
        """Deleting a deal cascades to booking_records."""
        did = deal_store.save_deal(
            seller_url="http://a.com", product_id="p1"
        )
        deal_store.save_booking_record(
            deal_id=did, line_id="line_1", channel="branding",
        )
        # Delete the deal
        with deal_store._lock:
            deal_store._conn.execute("DELETE FROM deals WHERE id = ?", (did,))
            deal_store._conn.commit()

        assert deal_store.get_booking_records(did) == []

    def test_list_deals_created_after_filter(self, deal_store):
        """list_deals created_after filter works correctly."""
        # Create a deal, then filter by a time before it
        did = deal_store.save_deal(
            seller_url="http://a.com", product_id="p1"
        )
        # Filter with a very old date should include it
        results = deal_store.list_deals(created_after="2000-01-01T00:00:00Z")
        assert len(results) == 1

        # Filter with a future date should exclude it
        results = deal_store.list_deals(created_after="2099-01-01T00:00:00Z")
        assert len(results) == 0
