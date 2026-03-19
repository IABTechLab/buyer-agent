# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for campaign data model schema (v4 migration) and CRUD operations.

Covers:
- Schema migration v2->v4 creates all 4 new tables
- Idempotent re-run of migration
- Campaign CRUD: save, get, list, update status
- Pacing snapshot CRUD: save, get, list by campaign
- Creative asset CRUD: save, get, list by campaign
- Ad server campaign CRUD: save, get, list by campaign
- Index creation on campaign_id FKs and campaigns.status
- Status filtering on campaigns
"""

import json
import sqlite3
import uuid

import pytest

from ad_buyer.storage.schema import (
    SCHEMA_VERSION,
    create_tables,
    get_schema_version,
    initialize_schema,
    migrate_v2_to_v4,
    set_schema_version,
)
from ad_buyer.storage.campaign_store import CampaignStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_conn():
    """In-memory SQLite connection with v2 schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    create_tables(conn)
    set_schema_version(conn, 2)
    return conn


@pytest.fixture
def v4_conn(db_conn):
    """In-memory connection after v4 migration."""
    migrate_v2_to_v4(db_conn)
    set_schema_version(db_conn, 4)
    return db_conn


@pytest.fixture
def campaign_store(v4_conn):
    """CampaignStore backed by an in-memory v4 database."""
    store = CampaignStore.__new__(CampaignStore)
    store._conn = v4_conn
    store._lock = __import__("threading").Lock()
    return store


@pytest.fixture
def sample_campaign_data():
    """Minimal valid campaign data dict."""
    return {
        "advertiser_id": "adv-001",
        "campaign_name": "Rivian EV Launch",
        "status": "DRAFT",
        "total_budget": 1200000.0,
        "currency": "USD",
        "flight_start": "2026-04-01",
        "flight_end": "2026-05-26",
        "channels": json.dumps([
            {"channel": "CTV", "budget_pct": 60},
            {"channel": "DISPLAY", "budget_pct": 30},
            {"channel": "AUDIO", "budget_pct": 10},
        ]),
        "target_audience": json.dumps(["auto_intenders", "25-54"]),
        "target_geo": json.dumps(["US", "top_20_dmas"]),
        "kpis": json.dumps([
            {"metric": "CPCV", "target_value": 0.05},
            {"metric": "CTR", "target_value": 0.8},
        ]),
    }


# ===================================================================
# Schema Migration Tests
# ===================================================================


class TestSchemaMigrationV4:
    """Tests that migrate_v2_to_v4 creates the campaign automation tables."""

    def test_schema_version_is_4(self):
        """SCHEMA_VERSION constant should be 4."""
        assert SCHEMA_VERSION == 4

    def test_migration_creates_campaigns_table(self, v4_conn):
        """campaigns table should exist after migration."""
        cursor = v4_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='campaigns'"
        )
        assert cursor.fetchone() is not None

    def test_migration_creates_pacing_snapshots_table(self, v4_conn):
        """pacing_snapshots table should exist after migration."""
        cursor = v4_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pacing_snapshots'"
        )
        assert cursor.fetchone() is not None

    def test_migration_creates_creative_assets_table(self, v4_conn):
        """creative_assets table should exist after migration."""
        cursor = v4_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='creative_assets'"
        )
        assert cursor.fetchone() is not None

    def test_migration_creates_ad_server_campaigns_table(self, v4_conn):
        """ad_server_campaigns table should exist after migration."""
        cursor = v4_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ad_server_campaigns'"
        )
        assert cursor.fetchone() is not None

    def test_migration_is_idempotent(self, v4_conn):
        """Running migration again should not raise."""
        migrate_v2_to_v4(v4_conn)  # second run - should be a no-op

    def test_campaigns_table_columns(self, v4_conn):
        """campaigns table should have all required columns."""
        cursor = v4_conn.execute("PRAGMA table_info(campaigns)")
        cols = {row[1] for row in cursor.fetchall()}
        expected = {
            "campaign_id", "advertiser_id", "campaign_name", "status",
            "total_budget", "currency", "flight_start", "flight_end",
            "channels", "target_audience", "target_geo", "kpis",
            "brand_safety", "approval_config", "created_at", "updated_at",
        }
        assert expected.issubset(cols), f"Missing columns: {expected - cols}"

    def test_pacing_snapshots_table_columns(self, v4_conn):
        """pacing_snapshots table should have all required columns."""
        cursor = v4_conn.execute("PRAGMA table_info(pacing_snapshots)")
        cols = {row[1] for row in cursor.fetchall()}
        expected = {
            "snapshot_id", "campaign_id", "timestamp", "channel",
            "budget_allocated", "budget_spent", "impressions_target",
            "impressions_delivered", "pacing_percentage", "deviation",
            "data_source", "data_freshness",
        }
        assert expected.issubset(cols), f"Missing columns: {expected - cols}"

    def test_creative_assets_table_columns(self, v4_conn):
        """creative_assets table should have all required columns."""
        cursor = v4_conn.execute("PRAGMA table_info(creative_assets)")
        cols = {row[1] for row in cursor.fetchall()}
        expected = {
            "asset_id", "campaign_id", "asset_name", "asset_type",
            "format_spec", "source_url", "validation_status",
            "validation_errors", "created_at",
        }
        assert expected.issubset(cols), f"Missing columns: {expected - cols}"

    def test_ad_server_campaigns_table_columns(self, v4_conn):
        """ad_server_campaigns table should have all required columns."""
        cursor = v4_conn.execute("PRAGMA table_info(ad_server_campaigns)")
        cols = {row[1] for row in cursor.fetchall()}
        expected = {
            "binding_id", "campaign_id", "ad_server",
            "external_campaign_id", "status", "creative_assignments",
            "last_sync_at", "created_at",
        }
        assert expected.issubset(cols), f"Missing columns: {expected - cols}"

    def test_index_campaigns_status(self, v4_conn):
        """Index on campaigns(status) should exist."""
        cursor = v4_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_campaigns_status'"
        )
        assert cursor.fetchone() is not None

    def test_index_pacing_snapshots_campaign_id(self, v4_conn):
        """Index on pacing_snapshots(campaign_id) should exist."""
        cursor = v4_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_pacing_snapshots_campaign_id'"
        )
        assert cursor.fetchone() is not None

    def test_index_creative_assets_campaign_id(self, v4_conn):
        """Index on creative_assets(campaign_id) should exist."""
        cursor = v4_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_creative_assets_campaign_id'"
        )
        assert cursor.fetchone() is not None

    def test_index_ad_server_campaigns_campaign_id(self, v4_conn):
        """Index on ad_server_campaigns(campaign_id) should exist."""
        cursor = v4_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_ad_server_campaigns_campaign_id'"
        )
        assert cursor.fetchone() is not None

    def test_full_initialize_schema_reaches_v4(self):
        """initialize_schema on a fresh DB should reach v4."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        initialize_schema(conn)
        assert get_schema_version(conn) == 4

    def test_migration_from_v2_creates_campaigns(self, db_conn):
        """Migration from v2 to v4 should create campaigns table."""
        migrate_v2_to_v4(db_conn)
        cursor = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='campaigns'"
        )
        assert cursor.fetchone() is not None


# ===================================================================
# Campaign CRUD Tests
# ===================================================================


class TestCampaignCRUD:
    """Tests for CampaignStore campaign operations."""

    def test_save_campaign_returns_id(self, campaign_store, sample_campaign_data):
        """save_campaign should return a campaign_id string."""
        cid = campaign_store.save_campaign(**sample_campaign_data)
        assert isinstance(cid, str)
        assert len(cid) > 0

    def test_save_campaign_with_explicit_id(self, campaign_store, sample_campaign_data):
        """save_campaign should use provided campaign_id."""
        explicit_id = "camp-test-001"
        sample_campaign_data["campaign_id"] = explicit_id
        cid = campaign_store.save_campaign(**sample_campaign_data)
        assert cid == explicit_id

    def test_get_campaign(self, campaign_store, sample_campaign_data):
        """get_campaign should return the saved campaign."""
        cid = campaign_store.save_campaign(**sample_campaign_data)
        campaign = campaign_store.get_campaign(cid)
        assert campaign is not None
        assert campaign["campaign_id"] == cid
        assert campaign["campaign_name"] == "Rivian EV Launch"
        assert campaign["status"] == "DRAFT"
        assert campaign["total_budget"] == 1200000.0
        assert campaign["currency"] == "USD"

    def test_get_campaign_not_found(self, campaign_store):
        """get_campaign should return None for unknown ID."""
        assert campaign_store.get_campaign("nonexistent") is None

    def test_list_campaigns_no_filter(self, campaign_store, sample_campaign_data):
        """list_campaigns should return all campaigns."""
        campaign_store.save_campaign(**sample_campaign_data)
        sample_campaign_data["campaign_name"] = "Second Campaign"
        campaign_store.save_campaign(**sample_campaign_data)
        campaigns = campaign_store.list_campaigns()
        assert len(campaigns) == 2

    def test_list_campaigns_filter_by_status(self, campaign_store, sample_campaign_data):
        """list_campaigns should filter by status."""
        campaign_store.save_campaign(**sample_campaign_data)
        sample_campaign_data["campaign_name"] = "Active Campaign"
        sample_campaign_data["status"] = "ACTIVE"
        campaign_store.save_campaign(**sample_campaign_data)

        drafts = campaign_store.list_campaigns(status="DRAFT")
        assert len(drafts) == 1
        assert drafts[0]["campaign_name"] == "Rivian EV Launch"

        actives = campaign_store.list_campaigns(status="ACTIVE")
        assert len(actives) == 1
        assert actives[0]["campaign_name"] == "Active Campaign"

    def test_list_campaigns_filter_by_advertiser(self, campaign_store, sample_campaign_data):
        """list_campaigns should filter by advertiser_id."""
        campaign_store.save_campaign(**sample_campaign_data)
        sample_campaign_data["advertiser_id"] = "adv-002"
        sample_campaign_data["campaign_name"] = "Other Advertiser"
        campaign_store.save_campaign(**sample_campaign_data)

        result = campaign_store.list_campaigns(advertiser_id="adv-001")
        assert len(result) == 1
        assert result[0]["campaign_name"] == "Rivian EV Launch"

    def test_list_campaigns_respects_limit(self, campaign_store, sample_campaign_data):
        """list_campaigns should respect the limit parameter."""
        for i in range(5):
            sample_campaign_data["campaign_name"] = f"Campaign {i}"
            campaign_store.save_campaign(**sample_campaign_data)
        result = campaign_store.list_campaigns(limit=3)
        assert len(result) == 3

    def test_update_campaign_status(self, campaign_store, sample_campaign_data):
        """update_campaign_status should change the status."""
        cid = campaign_store.save_campaign(**sample_campaign_data)
        result = campaign_store.update_campaign_status(cid, "PLANNING")
        assert result is True
        campaign = campaign_store.get_campaign(cid)
        assert campaign["status"] == "PLANNING"

    def test_update_campaign_status_not_found(self, campaign_store):
        """update_campaign_status should return False for unknown ID."""
        assert campaign_store.update_campaign_status("nonexistent", "ACTIVE") is False

    def test_update_campaign(self, campaign_store, sample_campaign_data):
        """update_campaign should update specified fields."""
        cid = campaign_store.save_campaign(**sample_campaign_data)
        result = campaign_store.update_campaign(
            cid, campaign_name="Updated Name", total_budget=500000.0
        )
        assert result is True
        campaign = campaign_store.get_campaign(cid)
        assert campaign["campaign_name"] == "Updated Name"
        assert campaign["total_budget"] == 500000.0

    def test_update_campaign_not_found(self, campaign_store):
        """update_campaign should return False for unknown ID."""
        assert campaign_store.update_campaign("nonexistent", campaign_name="X") is False

    def test_campaign_json_fields_roundtrip(self, campaign_store, sample_campaign_data):
        """JSON fields should survive a save/get roundtrip."""
        cid = campaign_store.save_campaign(**sample_campaign_data)
        campaign = campaign_store.get_campaign(cid)
        channels = json.loads(campaign["channels"])
        assert len(channels) == 3
        assert channels[0]["channel"] == "CTV"

    def test_campaign_created_at_and_updated_at(self, campaign_store, sample_campaign_data):
        """created_at and updated_at should be populated."""
        cid = campaign_store.save_campaign(**sample_campaign_data)
        campaign = campaign_store.get_campaign(cid)
        assert campaign["created_at"] is not None
        assert campaign["updated_at"] is not None


# ===================================================================
# Pacing Snapshot CRUD Tests
# ===================================================================


class TestPacingSnapshotCRUD:
    """Tests for CampaignStore pacing snapshot operations."""

    def test_save_pacing_snapshot_returns_id(self, campaign_store, sample_campaign_data):
        """save_pacing_snapshot should return a snapshot_id."""
        cid = campaign_store.save_campaign(**sample_campaign_data)
        sid = campaign_store.save_pacing_snapshot(
            campaign_id=cid,
            timestamp="2026-04-10T12:00:00Z",
            channel="CTV",
            budget_allocated=720000.0,
            budget_spent=120000.0,
            impressions_target=5000000,
            impressions_delivered=850000,
            pacing_percentage=95.0,
            deviation=-5.0,
        )
        assert isinstance(sid, str)
        assert len(sid) > 0

    def test_get_pacing_snapshot(self, campaign_store, sample_campaign_data):
        """get_pacing_snapshot should return the saved snapshot."""
        cid = campaign_store.save_campaign(**sample_campaign_data)
        sid = campaign_store.save_pacing_snapshot(
            campaign_id=cid,
            timestamp="2026-04-10T12:00:00Z",
            channel="CTV",
            budget_allocated=720000.0,
            budget_spent=120000.0,
            impressions_target=5000000,
            impressions_delivered=850000,
            pacing_percentage=95.0,
            deviation=-5.0,
        )
        snap = campaign_store.get_pacing_snapshot(sid)
        assert snap is not None
        assert snap["campaign_id"] == cid
        assert snap["channel"] == "CTV"
        assert snap["budget_allocated"] == 720000.0

    def test_get_pacing_snapshot_not_found(self, campaign_store):
        """get_pacing_snapshot should return None for unknown ID."""
        assert campaign_store.get_pacing_snapshot("nonexistent") is None

    def test_list_pacing_snapshots_by_campaign(self, campaign_store, sample_campaign_data):
        """list_pacing_snapshots should filter by campaign_id."""
        cid = campaign_store.save_campaign(**sample_campaign_data)
        for i in range(3):
            campaign_store.save_pacing_snapshot(
                campaign_id=cid,
                timestamp=f"2026-04-{10+i}T12:00:00Z",
                channel="CTV",
                budget_allocated=720000.0,
                budget_spent=120000.0 * (i + 1),
                impressions_target=5000000,
                impressions_delivered=850000 * (i + 1),
                pacing_percentage=95.0 + i,
                deviation=-5.0 + i,
            )
        snaps = campaign_store.list_pacing_snapshots(campaign_id=cid)
        assert len(snaps) == 3

    def test_list_pacing_snapshots_empty(self, campaign_store):
        """list_pacing_snapshots should return empty list for unknown campaign."""
        snaps = campaign_store.list_pacing_snapshots(campaign_id="nonexistent")
        assert snaps == []

    def test_pacing_snapshot_optional_fields(self, campaign_store, sample_campaign_data):
        """Optional fields (data_source, data_freshness) should be storable."""
        cid = campaign_store.save_campaign(**sample_campaign_data)
        sid = campaign_store.save_pacing_snapshot(
            campaign_id=cid,
            timestamp="2026-04-10T12:00:00Z",
            channel="DISPLAY",
            budget_allocated=360000.0,
            budget_spent=50000.0,
            impressions_target=2000000,
            impressions_delivered=300000,
            pacing_percentage=80.0,
            deviation=-20.0,
            data_source="dsp_report",
            data_freshness="2026-04-10T11:55:00Z",
        )
        snap = campaign_store.get_pacing_snapshot(sid)
        assert snap["data_source"] == "dsp_report"
        assert snap["data_freshness"] == "2026-04-10T11:55:00Z"


# ===================================================================
# Creative Asset CRUD Tests
# ===================================================================


class TestCreativeAssetCRUD:
    """Tests for CampaignStore creative asset operations."""

    def test_save_creative_asset_returns_id(self, campaign_store, sample_campaign_data):
        """save_creative_asset should return an asset_id."""
        cid = campaign_store.save_campaign(**sample_campaign_data)
        aid = campaign_store.save_creative_asset(
            campaign_id=cid,
            asset_name="Rivian 30s CTV Spot",
            asset_type="video",
            format_spec=json.dumps({
                "width": 1920, "height": 1080,
                "duration_sec": 30, "vast_version": "4.2",
            }),
            source_url="https://cdn.example.com/rivian_30s.mp4",
        )
        assert isinstance(aid, str)
        assert len(aid) > 0

    def test_get_creative_asset(self, campaign_store, sample_campaign_data):
        """get_creative_asset should return the saved asset."""
        cid = campaign_store.save_campaign(**sample_campaign_data)
        aid = campaign_store.save_creative_asset(
            campaign_id=cid,
            asset_name="Rivian 30s CTV Spot",
            asset_type="video",
            format_spec=json.dumps({"duration_sec": 30}),
            source_url="https://cdn.example.com/rivian_30s.mp4",
        )
        asset = campaign_store.get_creative_asset(aid)
        assert asset is not None
        assert asset["asset_name"] == "Rivian 30s CTV Spot"
        assert asset["asset_type"] == "video"
        assert asset["campaign_id"] == cid

    def test_get_creative_asset_not_found(self, campaign_store):
        """get_creative_asset should return None for unknown ID."""
        assert campaign_store.get_creative_asset("nonexistent") is None

    def test_list_creative_assets_by_campaign(self, campaign_store, sample_campaign_data):
        """list_creative_assets should filter by campaign_id."""
        cid = campaign_store.save_campaign(**sample_campaign_data)
        for name in ["Spot A", "Spot B", "Banner C"]:
            campaign_store.save_creative_asset(
                campaign_id=cid,
                asset_name=name,
                asset_type="video" if "Spot" in name else "display",
                format_spec="{}",
                source_url=f"https://cdn.example.com/{name}.mp4",
            )
        assets = campaign_store.list_creative_assets(campaign_id=cid)
        assert len(assets) == 3

    def test_list_creative_assets_empty(self, campaign_store):
        """list_creative_assets should return empty list for unknown campaign."""
        assets = campaign_store.list_creative_assets(campaign_id="nonexistent")
        assert assets == []

    def test_creative_asset_validation_fields(self, campaign_store, sample_campaign_data):
        """Validation fields should be saved and retrievable."""
        cid = campaign_store.save_campaign(**sample_campaign_data)
        errors = json.dumps([{"severity": "WARNING", "message": "Low bitrate"}])
        aid = campaign_store.save_creative_asset(
            campaign_id=cid,
            asset_name="Low Quality Spot",
            asset_type="video",
            format_spec="{}",
            source_url="https://cdn.example.com/low.mp4",
            validation_status="WARNING",
            validation_errors=errors,
        )
        asset = campaign_store.get_creative_asset(aid)
        assert asset["validation_status"] == "WARNING"
        assert json.loads(asset["validation_errors"])[0]["severity"] == "WARNING"

    def test_update_creative_asset(self, campaign_store, sample_campaign_data):
        """update_creative_asset should update specified fields."""
        cid = campaign_store.save_campaign(**sample_campaign_data)
        aid = campaign_store.save_creative_asset(
            campaign_id=cid,
            asset_name="Draft Spot",
            asset_type="video",
            format_spec="{}",
            source_url="https://cdn.example.com/draft.mp4",
        )
        result = campaign_store.update_creative_asset(
            aid,
            asset_name="Final Spot",
            validation_status="VALID",
        )
        assert result is True
        asset = campaign_store.get_creative_asset(aid)
        assert asset["asset_name"] == "Final Spot"
        assert asset["validation_status"] == "VALID"

    def test_update_creative_asset_not_found(self, campaign_store):
        """update_creative_asset should return False for unknown ID."""
        assert campaign_store.update_creative_asset("nonexistent", asset_name="X") is False


# ===================================================================
# Ad Server Campaign CRUD Tests
# ===================================================================


class TestAdServerCampaignCRUD:
    """Tests for CampaignStore ad server campaign operations."""

    def test_save_ad_server_campaign_returns_id(self, campaign_store, sample_campaign_data):
        """save_ad_server_campaign should return a binding_id."""
        cid = campaign_store.save_campaign(**sample_campaign_data)
        bid = campaign_store.save_ad_server_campaign(
            campaign_id=cid,
            ad_server="innovid",
            external_campaign_id="INV-12345",
            status="PENDING",
        )
        assert isinstance(bid, str)
        assert len(bid) > 0

    def test_get_ad_server_campaign(self, campaign_store, sample_campaign_data):
        """get_ad_server_campaign should return the saved binding."""
        cid = campaign_store.save_campaign(**sample_campaign_data)
        bid = campaign_store.save_ad_server_campaign(
            campaign_id=cid,
            ad_server="innovid",
            external_campaign_id="INV-12345",
            status="ACTIVE",
        )
        binding = campaign_store.get_ad_server_campaign(bid)
        assert binding is not None
        assert binding["ad_server"] == "innovid"
        assert binding["external_campaign_id"] == "INV-12345"
        assert binding["status"] == "ACTIVE"

    def test_get_ad_server_campaign_not_found(self, campaign_store):
        """get_ad_server_campaign should return None for unknown ID."""
        assert campaign_store.get_ad_server_campaign("nonexistent") is None

    def test_list_ad_server_campaigns_by_campaign(self, campaign_store, sample_campaign_data):
        """list_ad_server_campaigns should filter by campaign_id."""
        cid = campaign_store.save_campaign(**sample_campaign_data)
        for server in ["innovid", "flashtalking"]:
            campaign_store.save_ad_server_campaign(
                campaign_id=cid,
                ad_server=server,
                external_campaign_id=f"{server}-001",
                status="PENDING",
            )
        bindings = campaign_store.list_ad_server_campaigns(campaign_id=cid)
        assert len(bindings) == 2

    def test_list_ad_server_campaigns_empty(self, campaign_store):
        """list_ad_server_campaigns should return empty for unknown campaign."""
        bindings = campaign_store.list_ad_server_campaigns(campaign_id="nonexistent")
        assert bindings == []

    def test_update_ad_server_campaign(self, campaign_store, sample_campaign_data):
        """update_ad_server_campaign should update specified fields."""
        cid = campaign_store.save_campaign(**sample_campaign_data)
        bid = campaign_store.save_ad_server_campaign(
            campaign_id=cid,
            ad_server="innovid",
            external_campaign_id="INV-12345",
            status="PENDING",
        )
        result = campaign_store.update_ad_server_campaign(
            bid,
            status="ACTIVE",
            last_sync_at="2026-04-01T10:00:00Z",
            creative_assignments=json.dumps({"asset-1": "line-1"}),
        )
        assert result is True
        binding = campaign_store.get_ad_server_campaign(bid)
        assert binding["status"] == "ACTIVE"
        assert binding["last_sync_at"] == "2026-04-01T10:00:00Z"

    def test_update_ad_server_campaign_not_found(self, campaign_store):
        """update_ad_server_campaign should return False for unknown ID."""
        assert campaign_store.update_ad_server_campaign("nonexistent", status="ACTIVE") is False


# ===================================================================
# Edge Cases
# ===================================================================


class TestEdgeCases:
    """Edge case tests for the campaign data model."""

    def test_campaign_all_statuses_valid(self, campaign_store, sample_campaign_data):
        """All defined campaign statuses should be saveable."""
        valid_statuses = [
            "DRAFT", "PLANNING", "BOOKING", "READY", "ACTIVE",
            "PAUSED", "PACING_HOLD", "COMPLETED", "CANCELED",
        ]
        for status in valid_statuses:
            sample_campaign_data["status"] = status
            sample_campaign_data["campaign_name"] = f"Campaign-{status}"
            cid = campaign_store.save_campaign(**sample_campaign_data)
            campaign = campaign_store.get_campaign(cid)
            assert campaign["status"] == status

    def test_campaign_optional_json_fields_nullable(self, campaign_store):
        """Optional JSON fields (brand_safety, approval_config) should accept None."""
        cid = campaign_store.save_campaign(
            advertiser_id="adv-001",
            campaign_name="Minimal Campaign",
            status="DRAFT",
            total_budget=100000.0,
            currency="USD",
            flight_start="2026-04-01",
            flight_end="2026-04-30",
        )
        campaign = campaign_store.get_campaign(cid)
        assert campaign is not None
        assert campaign["brand_safety"] is None
        assert campaign["approval_config"] is None

    def test_creative_asset_all_types_valid(self, campaign_store, sample_campaign_data):
        """All defined asset types should be saveable."""
        cid = campaign_store.save_campaign(**sample_campaign_data)
        valid_types = ["display", "video", "audio", "interactive", "native"]
        for asset_type in valid_types:
            aid = campaign_store.save_creative_asset(
                campaign_id=cid,
                asset_name=f"Asset-{asset_type}",
                asset_type=asset_type,
                format_spec="{}",
                source_url="https://cdn.example.com/test",
            )
            asset = campaign_store.get_creative_asset(aid)
            assert asset["asset_type"] == asset_type
