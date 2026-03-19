# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for deal library schema v2 — the hybrid approach per D-4.

Tests cover:
- Schema migration from v1 to v2
- New intrinsic columns on the deals table
- New extrinsic tables: portfolio_metadata, deal_activations, performance_cache
- New indexes
- Backward compatibility with existing v1 data
"""

import json
import sqlite3

import pytest

from ad_buyer.storage import DealStore, SCHEMA_VERSION
from ad_buyer.storage.schema import (
    create_tables,
    get_schema_version,
    initialize_schema,
    migrate_v1_to_v2,
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
# Schema Version Tests
# -----------------------------------------------------------------------

class TestSchemaV2Version:
    """Verify schema version reflects the latest migration (v4)."""

    def test_schema_version_is_at_least_2(self):
        """SCHEMA_VERSION constant must be >= 2 (v4 after campaign automation)."""
        assert SCHEMA_VERSION >= 2

    def test_initialize_schema_sets_latest_version(self, raw_conn):
        """initialize_schema records the latest schema version."""
        initialize_schema(raw_conn)
        assert get_schema_version(raw_conn) == SCHEMA_VERSION


# -----------------------------------------------------------------------
# Migration Function Tests
# -----------------------------------------------------------------------

class TestMigrationV1ToV2:
    """Tests for the migrate_v1_to_v2 migration function."""

    def _setup_v1_schema(self, conn):
        """Create v1 schema (tables only, no v2 columns)."""
        # Import v1 table DDL directly
        from ad_buyer.storage.schema import (
            DEALS_TABLE,
            NEGOTIATION_ROUNDS_TABLE,
            BOOKING_RECORDS_TABLE,
            JOBS_TABLE,
            EVENTS_TABLE,
            STATUS_TRANSITIONS_TABLE,
            SCHEMA_VERSION_TABLE,
        )
        cursor = conn.cursor()
        cursor.execute(SCHEMA_VERSION_TABLE)
        for ddl in [
            DEALS_TABLE,
            NEGOTIATION_ROUNDS_TABLE,
            BOOKING_RECORDS_TABLE,
            JOBS_TABLE,
            EVENTS_TABLE,
            STATUS_TRANSITIONS_TABLE,
        ]:
            cursor.execute(ddl)
        conn.commit()
        set_schema_version(conn, 1)

    def test_migrate_v1_to_v2_adds_intrinsic_columns(self, raw_conn):
        """Migration adds all new intrinsic columns to the deals table."""
        self._setup_v1_schema(raw_conn)
        migrate_v1_to_v2(raw_conn)

        # Check that all new columns exist by inserting a row with them
        intrinsic_columns = [
            "display_name", "description", "buyer_org", "buyer_id",
            "seller_org", "seller_id", "seller_domain", "seller_type",
            "price_model", "bid_floor_cpm", "fixed_price_cpm",
            "cpp", "guaranteed_grps", "currency",
            "fee_transparency", "media_type", "formats",
            "content_categories", "publisher_domains", "geo_targets",
            "dayparts", "programs", "networks", "audience_segments",
            "estimated_volume", "deprecated_at", "deprecated_reason",
            "parent_deal_id", "schain_complete", "schain_nodes",
            "sellers_json_url", "is_direct", "hop_count",
            "inventory_fingerprint",
            "makegood_provisions", "cancellation_window",
            "audience_guarantee", "preemption_rights",
            "agency_of_record_status",
        ]

        # Verify columns exist via PRAGMA
        cursor = raw_conn.execute("PRAGMA table_info(deals)")
        existing_cols = {row[1] for row in cursor.fetchall()}

        for col in intrinsic_columns:
            assert col in existing_cols, f"Column '{col}' missing from deals table"

    def test_migrate_v1_to_v2_creates_portfolio_metadata_table(self, raw_conn):
        """Migration creates the portfolio_metadata table."""
        self._setup_v1_schema(raw_conn)
        migrate_v1_to_v2(raw_conn)

        cursor = raw_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='portfolio_metadata'"
        )
        assert cursor.fetchone() is not None, "portfolio_metadata table not created"

        # Check columns
        cursor = raw_conn.execute("PRAGMA table_info(portfolio_metadata)")
        cols = {row[1] for row in cursor.fetchall()}
        expected = {"id", "deal_id", "import_source", "import_date", "tags",
                    "advertiser_id", "agency_id"}
        assert expected.issubset(cols)

    def test_migrate_v1_to_v2_creates_deal_activations_table(self, raw_conn):
        """Migration creates the deal_activations table."""
        self._setup_v1_schema(raw_conn)
        migrate_v1_to_v2(raw_conn)

        cursor = raw_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='deal_activations'"
        )
        assert cursor.fetchone() is not None, "deal_activations table not created"

        cursor = raw_conn.execute("PRAGMA table_info(deal_activations)")
        cols = {row[1] for row in cursor.fetchall()}
        expected = {"id", "deal_id", "platform", "platform_deal_id",
                    "activation_status", "last_sync_at"}
        assert expected.issubset(cols)

    def test_migrate_v1_to_v2_creates_performance_cache_table(self, raw_conn):
        """Migration creates the performance_cache table."""
        self._setup_v1_schema(raw_conn)
        migrate_v1_to_v2(raw_conn)

        cursor = raw_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='performance_cache'"
        )
        assert cursor.fetchone() is not None, "performance_cache table not created"

        cursor = raw_conn.execute("PRAGMA table_info(performance_cache)")
        cols = {row[1] for row in cursor.fetchall()}
        expected = {"id", "deal_id", "impressions_delivered", "spend_to_date",
                    "fill_rate", "win_rate", "avg_effective_cpm",
                    "last_delivery_at", "performance_trend", "cached_at"}
        assert expected.issubset(cols)

    def test_migrate_v1_to_v2_creates_indexes(self, raw_conn):
        """Migration creates the expected indexes."""
        self._setup_v1_schema(raw_conn)
        migrate_v1_to_v2(raw_conn)

        cursor = raw_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        )
        indexes = {row[0] for row in cursor.fetchall()}

        v2_indexes = {
            # Deals table indexes
            "idx_deals_media_type",
            "idx_deals_deal_type",
            "idx_deals_seller_domain",
            "idx_deals_inventory_fingerprint",
            # Extrinsic table indexes
            "idx_portfolio_metadata_deal_id",
            "idx_portfolio_metadata_advertiser_id",
            "idx_deal_activations_deal_id",
            "idx_deal_activations_platform_deal",
            "idx_performance_cache_deal_id",
        }
        for idx in v2_indexes:
            assert idx in indexes, f"Index '{idx}' not created"

    def test_migrate_v1_to_v2_preserves_existing_data(self, raw_conn):
        """Migration preserves existing v1 deal data."""
        self._setup_v1_schema(raw_conn)

        # Insert a v1 deal
        raw_conn.execute(
            """INSERT INTO deals
               (id, seller_url, product_id, product_name, deal_type, status,
                price, created_at, updated_at)
               VALUES ('deal-1', 'http://seller.com', 'prod_1', 'Banner',
                       'PD', 'draft', 12.50,
                       '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"""
        )
        raw_conn.commit()

        migrate_v1_to_v2(raw_conn)

        # Verify the existing deal is intact
        cursor = raw_conn.execute("SELECT * FROM deals WHERE id = 'deal-1'")
        row = cursor.fetchone()
        assert row is not None
        deal = dict(row)
        assert deal["seller_url"] == "http://seller.com"
        assert deal["product_name"] == "Banner"
        assert deal["deal_type"] == "PD"
        assert deal["price"] == 12.50

        # New columns should be NULL (or default)
        assert deal["display_name"] is None
        assert deal["buyer_org"] is None
        assert deal["currency"] == "USD"  # has a default

    def test_migration_is_idempotent(self, raw_conn):
        """Running migrate_v1_to_v2 twice does not raise."""
        self._setup_v1_schema(raw_conn)
        migrate_v1_to_v2(raw_conn)
        # Should not raise on second run
        migrate_v1_to_v2(raw_conn)

    def test_migration_registered_in_migrations_dict(self, raw_conn):
        """run_migrations calls migrate_v1_to_v2 when upgrading from v1."""
        self._setup_v1_schema(raw_conn)
        # run_migrations should bring v1 to latest version (v4)
        run_migrations(raw_conn)
        assert get_schema_version(raw_conn) == SCHEMA_VERSION

        # Verify v2 migration was applied (check for a v2 column)
        cursor = raw_conn.execute("PRAGMA table_info(deals)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "display_name" in cols

    def test_currency_defaults_to_usd(self, raw_conn):
        """New currency column defaults to 'USD'."""
        self._setup_v1_schema(raw_conn)
        migrate_v1_to_v2(raw_conn)

        raw_conn.execute(
            """INSERT INTO deals
               (id, seller_url, product_id, deal_type, status,
                created_at, updated_at)
               VALUES ('deal-new', 'http://seller.com', 'prod_1',
                       'PD', 'draft',
                       '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"""
        )
        raw_conn.commit()

        cursor = raw_conn.execute(
            "SELECT currency FROM deals WHERE id = 'deal-new'"
        )
        assert cursor.fetchone()[0] == "USD"


# -----------------------------------------------------------------------
# Intrinsic Fields Tests (via DealStore)
# -----------------------------------------------------------------------

class TestIntrinsicFieldsViaStore:
    """Test that new intrinsic columns are accessible through DealStore."""

    def test_deal_type_supports_extended_values(self, deal_store):
        """deal_type supports PG, PD, PA, OPEN_AUCTION, UPFRONT, SCATTER."""
        for dt in ("PG", "PD", "PA", "OPEN_AUCTION", "UPFRONT", "SCATTER"):
            did = deal_store.save_deal(
                seller_url="http://seller.com",
                product_id="prod_1",
                deal_type=dt,
            )
            deal = deal_store.get_deal(did)
            assert deal["deal_type"] == dt

    def test_new_intrinsic_columns_default_to_null(self, deal_store):
        """New columns default to NULL for deals saved without them."""
        did = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        deal = deal_store.get_deal(did)

        # Spot-check new columns are NULL
        assert deal["display_name"] is None
        assert deal["buyer_org"] is None
        assert deal["seller_domain"] is None
        assert deal["media_type"] is None
        assert deal["bid_floor_cpm"] is None
        assert deal["schain_complete"] is None

    def test_currency_defaults_to_usd_via_store(self, deal_store):
        """Currency defaults to 'USD' for deals created through DealStore."""
        did = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        deal = deal_store.get_deal(did)
        assert deal["currency"] == "USD"

    def test_intrinsic_fields_round_trip_via_raw_sql(self, deal_store):
        """New intrinsic columns can be set and read via raw SQL."""
        did = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )

        # Update using raw SQL (DealStore doesn't have setters for new
        # fields yet — that's a separate bead)
        with deal_store._lock:
            deal_store._conn.execute(
                """UPDATE deals SET
                    display_name = ?,
                    description = ?,
                    buyer_org = ?,
                    buyer_id = ?,
                    seller_org = ?,
                    seller_id = ?,
                    seller_domain = ?,
                    seller_type = ?,
                    price_model = ?,
                    bid_floor_cpm = ?,
                    fixed_price_cpm = ?,
                    cpp = ?,
                    guaranteed_grps = ?,
                    currency = ?,
                    fee_transparency = ?,
                    media_type = ?,
                    formats = ?,
                    content_categories = ?,
                    publisher_domains = ?,
                    geo_targets = ?,
                    dayparts = ?,
                    programs = ?,
                    networks = ?,
                    audience_segments = ?,
                    estimated_volume = ?,
                    parent_deal_id = ?,
                    schain_complete = ?,
                    schain_nodes = ?,
                    sellers_json_url = ?,
                    is_direct = ?,
                    hop_count = ?,
                    inventory_fingerprint = ?,
                    makegood_provisions = ?,
                    cancellation_window = ?,
                    audience_guarantee = ?,
                    preemption_rights = ?,
                    agency_of_record_status = ?
                   WHERE id = ?""",
                (
                    "ESPN Premium Video PG",
                    "Premium video deal with ESPN",
                    "Acme Agency",
                    "buyer-001",
                    "ESPN",
                    "seller-espn",
                    "espn.com",
                    "PUBLISHER",
                    "CPM",
                    15.00,
                    18.50,
                    None,  # cpp (not linear TV)
                    None,  # guaranteed_grps
                    "USD",
                    0.12,
                    "DIGITAL",
                    json.dumps(["banner_300x250", "video_15s"]),
                    json.dumps(["IAB17"]),
                    json.dumps(["espn.com"]),
                    json.dumps(["US"]),
                    None,  # dayparts
                    None,  # programs
                    None,  # networks
                    json.dumps(["sports_enthusiasts"]),
                    500000,
                    None,  # parent_deal_id
                    1,  # schain_complete (boolean)
                    json.dumps([{"asi": "espn.com", "sid": "001", "hp": 1}]),
                    "https://espn.com/sellers.json",
                    1,  # is_direct (boolean)
                    1,  # hop_count
                    "espn.com|IAB17|banner_300x250|DIGITAL",
                    None,  # makegood_provisions
                    None,  # cancellation_window
                    None,  # audience_guarantee
                    None,  # preemption_rights
                    None,  # agency_of_record_status
                    did,
                ),
            )
            deal_store._conn.commit()

        deal = deal_store.get_deal(did)
        assert deal["display_name"] == "ESPN Premium Video PG"
        assert deal["description"] == "Premium video deal with ESPN"
        assert deal["buyer_org"] == "Acme Agency"
        assert deal["buyer_id"] == "buyer-001"
        assert deal["seller_org"] == "ESPN"
        assert deal["seller_id"] == "seller-espn"
        assert deal["seller_domain"] == "espn.com"
        assert deal["seller_type"] == "PUBLISHER"
        assert deal["price_model"] == "CPM"
        assert deal["bid_floor_cpm"] == 15.00
        assert deal["fixed_price_cpm"] == 18.50
        assert deal["currency"] == "USD"
        assert deal["fee_transparency"] == 0.12
        assert deal["media_type"] == "DIGITAL"
        assert json.loads(deal["formats"]) == ["banner_300x250", "video_15s"]
        assert json.loads(deal["content_categories"]) == ["IAB17"]
        assert json.loads(deal["publisher_domains"]) == ["espn.com"]
        assert json.loads(deal["geo_targets"]) == ["US"]
        assert json.loads(deal["audience_segments"]) == ["sports_enthusiasts"]
        assert deal["estimated_volume"] == 500000
        assert deal["schain_complete"] == 1
        assert deal["is_direct"] == 1
        assert deal["hop_count"] == 1
        assert deal["inventory_fingerprint"] == "espn.com|IAB17|banner_300x250|DIGITAL"
        assert deal["sellers_json_url"] == "https://espn.com/sellers.json"

    def test_linear_tv_fields_round_trip(self, deal_store):
        """Linear TV-specific fields store and retrieve correctly."""
        did = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_tv",
            deal_type="UPFRONT",
        )

        with deal_store._lock:
            deal_store._conn.execute(
                """UPDATE deals SET
                    media_type = ?,
                    cpp = ?,
                    guaranteed_grps = ?,
                    dayparts = ?,
                    programs = ?,
                    networks = ?,
                    makegood_provisions = ?,
                    cancellation_window = ?,
                    audience_guarantee = ?,
                    preemption_rights = ?,
                    agency_of_record_status = ?
                   WHERE id = ?""",
                (
                    "LINEAR_TV",
                    45.00,
                    150.0,
                    json.dumps(["M-F 8p-11p"]),
                    json.dumps(["Sunday Night Football"]),
                    json.dumps(["NBC", "ESPN"]),
                    "Standard makegood within 2 weeks",
                    "30 days written notice",
                    "A18-49 Nielsen Live+3",
                    "Non-preemptible for upfront",
                    "BBDO Worldwide",
                    did,
                ),
            )
            deal_store._conn.commit()

        deal = deal_store.get_deal(did)
        assert deal["media_type"] == "LINEAR_TV"
        assert deal["cpp"] == 45.00
        assert deal["guaranteed_grps"] == 150.0
        assert json.loads(deal["dayparts"]) == ["M-F 8p-11p"]
        assert json.loads(deal["programs"]) == ["Sunday Night Football"]
        assert json.loads(deal["networks"]) == ["NBC", "ESPN"]
        assert deal["makegood_provisions"] == "Standard makegood within 2 weeks"
        assert deal["cancellation_window"] == "30 days written notice"
        assert deal["audience_guarantee"] == "A18-49 Nielsen Live+3"
        assert deal["preemption_rights"] == "Non-preemptible for upfront"
        assert deal["agency_of_record_status"] == "BBDO Worldwide"


# -----------------------------------------------------------------------
# Extrinsic Table Tests
# -----------------------------------------------------------------------

class TestPortfolioMetadata:
    """Tests for the portfolio_metadata extrinsic table."""

    def test_insert_and_query_portfolio_metadata(self, deal_store):
        """portfolio_metadata records can be inserted and queried."""
        did = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )

        with deal_store._lock:
            deal_store._conn.execute(
                """INSERT INTO portfolio_metadata
                   (deal_id, import_source, import_date, tags,
                    advertiser_id, agency_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    did,
                    "CSV",
                    "2026-03-18",
                    json.dumps(["premium", "sports"]),
                    "adv-001",
                    "agency-001",
                ),
            )
            deal_store._conn.commit()

        with deal_store._lock:
            cursor = deal_store._conn.execute(
                "SELECT * FROM portfolio_metadata WHERE deal_id = ?",
                (did,),
            )
            row = cursor.fetchone()

        assert row is not None
        meta = dict(row)
        assert meta["deal_id"] == did
        assert meta["import_source"] == "CSV"
        assert meta["import_date"] == "2026-03-18"
        assert json.loads(meta["tags"]) == ["premium", "sports"]
        assert meta["advertiser_id"] == "adv-001"
        assert meta["agency_id"] == "agency-001"

    def test_portfolio_metadata_foreign_key(self, deal_store):
        """portfolio_metadata deal_id must reference an existing deal."""
        with deal_store._lock:
            with pytest.raises(sqlite3.IntegrityError):
                deal_store._conn.execute(
                    """INSERT INTO portfolio_metadata
                       (deal_id, import_source) VALUES (?, ?)""",
                    ("nonexistent-deal", "CSV"),
                )


class TestDealActivations:
    """Tests for the deal_activations extrinsic table."""

    def test_insert_and_query_deal_activation(self, deal_store):
        """deal_activations records can be inserted and queried."""
        did = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )

        with deal_store._lock:
            deal_store._conn.execute(
                """INSERT INTO deal_activations
                   (deal_id, platform, platform_deal_id,
                    activation_status, last_sync_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    did,
                    "TTD",
                    "TTD-ESP-12345",
                    "ACTIVE",
                    "2026-03-18T10:00:00Z",
                ),
            )
            deal_store._conn.commit()

        with deal_store._lock:
            cursor = deal_store._conn.execute(
                "SELECT * FROM deal_activations WHERE deal_id = ?",
                (did,),
            )
            row = cursor.fetchone()

        assert row is not None
        activation = dict(row)
        assert activation["platform"] == "TTD"
        assert activation["platform_deal_id"] == "TTD-ESP-12345"
        assert activation["activation_status"] == "ACTIVE"

    def test_multiple_activations_per_deal(self, deal_store):
        """A deal can be activated on multiple platforms."""
        did = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )

        platforms = [
            ("TTD", "TTD-001", "ACTIVE"),
            ("DV360", "DV360-001", "PENDING"),
            ("XANDR", "XN-001", "ACTIVE"),
        ]

        with deal_store._lock:
            for platform, pid, status in platforms:
                deal_store._conn.execute(
                    """INSERT INTO deal_activations
                       (deal_id, platform, platform_deal_id, activation_status)
                       VALUES (?, ?, ?, ?)""",
                    (did, platform, pid, status),
                )
            deal_store._conn.commit()

        with deal_store._lock:
            cursor = deal_store._conn.execute(
                "SELECT * FROM deal_activations WHERE deal_id = ?",
                (did,),
            )
            rows = cursor.fetchall()

        assert len(rows) == 3

    def test_deal_activations_foreign_key(self, deal_store):
        """deal_activations deal_id must reference an existing deal."""
        with deal_store._lock:
            with pytest.raises(sqlite3.IntegrityError):
                deal_store._conn.execute(
                    """INSERT INTO deal_activations
                       (deal_id, platform, platform_deal_id, activation_status)
                       VALUES (?, ?, ?, ?)""",
                    ("nonexistent-deal", "TTD", "TTD-001", "ACTIVE"),
                )


class TestPerformanceCache:
    """Tests for the performance_cache extrinsic table."""

    def test_insert_and_query_performance_cache(self, deal_store):
        """performance_cache records can be inserted and queried."""
        did = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )

        with deal_store._lock:
            deal_store._conn.execute(
                """INSERT INTO performance_cache
                   (deal_id, impressions_delivered, spend_to_date,
                    fill_rate, win_rate, avg_effective_cpm,
                    last_delivery_at, performance_trend, cached_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    did,
                    1500000,
                    22500.00,
                    0.85,
                    0.42,
                    15.00,
                    "2026-03-18T09:00:00Z",
                    "IMPROVING",
                    "2026-03-18T10:00:00Z",
                ),
            )
            deal_store._conn.commit()

        with deal_store._lock:
            cursor = deal_store._conn.execute(
                "SELECT * FROM performance_cache WHERE deal_id = ?",
                (did,),
            )
            row = cursor.fetchone()

        assert row is not None
        perf = dict(row)
        assert perf["impressions_delivered"] == 1500000
        assert perf["spend_to_date"] == 22500.00
        assert perf["fill_rate"] == 0.85
        assert perf["win_rate"] == 0.42
        assert perf["avg_effective_cpm"] == 15.00
        assert perf["performance_trend"] == "IMPROVING"

    def test_performance_cache_foreign_key(self, deal_store):
        """performance_cache deal_id must reference an existing deal."""
        with deal_store._lock:
            with pytest.raises(sqlite3.IntegrityError):
                deal_store._conn.execute(
                    """INSERT INTO performance_cache
                       (deal_id, impressions_delivered, spend_to_date)
                       VALUES (?, ?, ?)""",
                    ("nonexistent-deal", 1000, 15.00),
                )


# -----------------------------------------------------------------------
# Backward Compatibility Tests
# -----------------------------------------------------------------------

class TestBackwardCompatibility:
    """Ensure v1 operations still work after schema v2 migration."""

    def test_save_deal_v1_style_still_works(self, deal_store):
        """save_deal with only v1 fields works on v2 schema."""
        did = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
            product_name="Banner Ad",
            deal_type="PD",
            status="draft",
            price=12.50,
        )

        deal = deal_store.get_deal(did)
        assert deal is not None
        assert deal["seller_url"] == "http://seller.com"
        assert deal["product_name"] == "Banner Ad"
        assert deal["price"] == 12.50

    def test_list_deals_still_works(self, deal_store):
        """list_deals works on v2 schema."""
        deal_store.save_deal(
            seller_url="http://a.com",
            product_id="p1",
            status="draft",
        )
        deal_store.save_deal(
            seller_url="http://b.com",
            product_id="p2",
            status="booked",
        )

        all_deals = deal_store.list_deals()
        assert len(all_deals) == 2

        drafts = deal_store.list_deals(status="draft")
        assert len(drafts) == 1

    def test_negotiation_rounds_still_work(self, deal_store):
        """Negotiation rounds work on v2 schema."""
        did = deal_store.save_deal(
            seller_url="http://a.com",
            product_id="p1",
        )
        deal_store.save_negotiation_round(
            deal_id=did,
            proposal_id="prop_1",
            round_number=1,
            buyer_price=10.0,
            seller_price=15.0,
            action="counter",
        )

        history = deal_store.get_negotiation_history(did)
        assert len(history) == 1

    def test_booking_records_still_work(self, deal_store):
        """Booking records work on v2 schema."""
        did = deal_store.save_deal(
            seller_url="http://a.com",
            product_id="p1",
        )
        deal_store.save_booking_record(
            deal_id=did,
            line_id="line_1",
            channel="branding",
            impressions=500000,
            cost=7500.0,
        )

        records = deal_store.get_booking_records(did)
        assert len(records) == 1

    def test_jobs_still_work(self, deal_store):
        """Job operations work on v2 schema."""
        deal_store.save_job(
            job_id="j1",
            status="pending",
            brief='{"name": "Test"}',
        )
        job = deal_store.get_job("j1")
        assert job is not None
        assert job["status"] == "pending"

    def test_cascade_deletes_include_extrinsic_tables(self, deal_store):
        """Deleting a deal cascades to extrinsic tables."""
        did = deal_store.save_deal(
            seller_url="http://a.com",
            product_id="p1",
        )

        # Insert records in extrinsic tables
        with deal_store._lock:
            deal_store._conn.execute(
                "INSERT INTO portfolio_metadata (deal_id, import_source) VALUES (?, ?)",
                (did, "CSV"),
            )
            deal_store._conn.execute(
                """INSERT INTO deal_activations
                   (deal_id, platform, platform_deal_id, activation_status)
                   VALUES (?, ?, ?, ?)""",
                (did, "TTD", "TTD-001", "ACTIVE"),
            )
            deal_store._conn.execute(
                """INSERT INTO performance_cache
                   (deal_id, impressions_delivered, spend_to_date)
                   VALUES (?, ?, ?)""",
                (did, 1000, 15.00),
            )
            deal_store._conn.commit()

        # Delete the deal
        with deal_store._lock:
            deal_store._conn.execute("DELETE FROM deals WHERE id = ?", (did,))
            deal_store._conn.commit()

        # All extrinsic records should be gone
        with deal_store._lock:
            for table in ("portfolio_metadata", "deal_activations", "performance_cache"):
                cursor = deal_store._conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE deal_id = ?",
                    (did,),
                )
                assert cursor.fetchone()[0] == 0, \
                    f"Cascade delete failed for {table}"
