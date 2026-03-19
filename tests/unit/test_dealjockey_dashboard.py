# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the DealJockey demo dashboard.

Covers:
  - Seed data populates the expected number of deals
  - API routes return 200 with expected JSON structure
  - CSV import endpoint parses correctly
  - Manual entry endpoint validates correctly
  - Event log returns Phase 1 event types
  - Schema endpoint returns version info
"""

import csv
import io
import json
import tempfile
from pathlib import Path

import pytest

from ad_buyer.demo.dealjockey_dashboard import create_app
from ad_buyer.demo.seed_data import seed_demo_data
from ad_buyer.storage.deal_store import DealStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store():
    """Create an in-memory DealStore for testing."""
    s = DealStore("sqlite:///:memory:")
    s.connect()
    yield s
    s.disconnect()


@pytest.fixture
def seeded_store(store):
    """DealStore with seed data loaded."""
    seed_demo_data(store)
    return store


@pytest.fixture
def app(seeded_store):
    """Flask test app with seeded data."""
    application = create_app("sqlite:///:memory:")
    # Replace the store with our seeded one
    application.config["DEAL_STORE"] = seeded_store
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


# ---------------------------------------------------------------------------
# Seed Data Tests
# ---------------------------------------------------------------------------


class TestSeedData:
    """Tests for the seed_data module."""

    def test_seed_creates_expected_deal_count(self, store):
        """seed_demo_data should create exactly 14 deals."""
        ids = seed_demo_data(store)
        assert len(ids) == 14

    def test_seed_creates_unique_ids(self, store):
        """All seeded deal IDs should be unique."""
        ids = seed_demo_data(store)
        assert len(set(ids)) == 14

    def test_seed_creates_mixed_statuses(self, store):
        """Seeded deals should include multiple statuses."""
        seed_demo_data(store)
        deals = store.list_deals(limit=100)
        statuses = {d["status"] for d in deals}
        assert "active" in statuses
        assert "draft" in statuses
        assert "paused" in statuses
        assert "expired" in statuses
        assert "canceled" in statuses

    def test_seed_creates_mixed_media_types(self, store):
        """Seeded deals should include multiple media types."""
        seed_demo_data(store)
        deals = store.list_deals(limit=100)
        media_types = {d.get("media_type") for d in deals if d.get("media_type")}
        assert "DIGITAL" in media_types
        assert "CTV" in media_types
        assert "LINEAR_TV" in media_types
        assert "AUDIO" in media_types
        assert "DOOH" in media_types

    def test_seed_creates_portfolio_metadata(self, store):
        """Some seeded deals should have portfolio metadata."""
        ids = seed_demo_data(store)
        meta_count = sum(
            1 for did in ids
            if store.get_portfolio_metadata(did) is not None
        )
        assert meta_count >= 8  # at least 8 deals have metadata

    def test_seed_creates_activations(self, store):
        """Some seeded deals should have deal activations."""
        ids = seed_demo_data(store)
        act_count = sum(
            1 for did in ids
            if len(store.get_deal_activations(did)) > 0
        )
        assert act_count >= 5  # at least 5 deals have activations

    def test_seed_creates_performance_cache(self, store):
        """Some seeded deals should have performance cache entries."""
        ids = seed_demo_data(store)
        perf_count = sum(
            1 for did in ids
            if store.get_performance_cache(did) is not None
        )
        assert perf_count >= 5  # at least 5 deals have perf data

    def test_seed_emits_events(self, store):
        """Each seeded deal should emit a deal.imported event."""
        ids = seed_demo_data(store)
        events = store.list_events(limit=100)
        import_events = [
            e for e in events if e["event_type"] == "deal.imported"
        ]
        assert len(import_events) >= len(ids)


# ---------------------------------------------------------------------------
# API Route Tests
# ---------------------------------------------------------------------------


class TestAPIRoutes:
    """Tests for the dashboard API routes."""

    def test_index_returns_html(self, client):
        """GET / should return the dashboard HTML page."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"DealJockey" in resp.data

    def test_api_schema(self, client):
        """GET /api/schema should return version and table info."""
        from ad_buyer.storage.schema import SCHEMA_VERSION as CURRENT_VERSION
        resp = client.get("/api/schema")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["schema_version"] == CURRENT_VERSION
        assert isinstance(data["tables"], list)
        assert len(data["tables"]) > 0
        assert isinstance(data["v2_columns"], list)
        assert len(data["v2_columns"]) > 0

    def test_api_deals_list(self, client):
        """GET /api/deals should return a list of deals."""
        resp = client.get("/api/deals")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "deals" in data
        assert "count" in data
        assert data["count"] > 0

    def test_api_deals_filter_by_status(self, client):
        """GET /api/deals?status=active should filter correctly."""
        resp = client.get("/api/deals?status=active")
        assert resp.status_code == 200
        data = resp.get_json()
        for deal in data["deals"]:
            assert deal["status"] == "active"

    def test_api_deals_filter_by_media_type(self, client):
        """GET /api/deals?media_type=CTV should filter correctly."""
        resp = client.get("/api/deals?media_type=CTV")
        assert resp.status_code == 200
        data = resp.get_json()
        for deal in data["deals"]:
            assert deal["media_type"] == "CTV"

    def test_api_deal_detail(self, client):
        """GET /api/deals/<id> should return deal with metadata."""
        # Get a deal ID first
        deals_resp = client.get("/api/deals?limit=1")
        deal_id = deals_resp.get_json()["deals"][0]["id"]

        resp = client.get(f"/api/deals/{deal_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "deal" in data
        assert data["deal"]["id"] == deal_id
        assert "portfolio_metadata" in data
        assert "activations" in data
        assert "performance_cache" in data

    def test_api_deal_detail_not_found(self, client):
        """GET /api/deals/<nonexistent> should return 404."""
        resp = client.get("/api/deals/nonexistent-id-000")
        assert resp.status_code == 404

    def test_api_search(self, client):
        """GET /api/search?q=ESPN should find deals."""
        resp = client.get("/api/search?q=ESPN")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] >= 1
        assert any("ESPN" in str(d.values()) for d in data["results"])

    def test_api_search_empty_query(self, client):
        """GET /api/search with empty query should return 400."""
        resp = client.get("/api/search?q=")
        assert resp.status_code == 400

    def test_api_summary(self, client):
        """GET /api/summary should return aggregate statistics."""
        resp = client.get("/api/summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total_deals"] > 0
        assert "by_status" in data
        assert "by_media_type" in data
        assert "by_deal_type" in data
        assert "top_sellers" in data
        assert "total_value" in data

    def test_api_events(self, client):
        """GET /api/events should return Phase 1 events."""
        resp = client.get("/api/events")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "events" in data
        # All returned events should be Phase 1 types
        phase1_types = {
            "deal.imported", "deal.template_created",
            "portfolio.inspected", "deal.manual_action_required",
        }
        for event in data["events"]:
            assert event["event_type"] in phase1_types

    def test_api_agent_info(self, client):
        """GET /api/agent-info should return agent configuration."""
        resp = client.get("/api/agent-info")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["role"] == "Deal Jockey - Portfolio Manager"
        assert "l1_routing" in data
        assert "phase1_tools" in data
        assert "phase1_event_types" in data

    def test_api_enums(self, client):
        """GET /api/enums should return valid enum values."""
        resp = client.get("/api/enums")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "PG" in data["deal_types"]
        assert "CTV" in data["media_types"]
        assert "CPM" in data["price_models"]
        assert "PUBLISHER" in data["seller_types"]


# ---------------------------------------------------------------------------
# CSV Import Tests
# ---------------------------------------------------------------------------


class TestCSVImport:
    """Tests for the CSV import endpoint."""

    def _make_csv_bytes(self, rows: list[list[str]]) -> bytes:
        """Build CSV bytes from a list of rows."""
        output = io.StringIO()
        writer = csv.writer(output)
        for row in rows:
            writer.writerow(row)
        return output.getvalue().encode("utf-8")

    def test_import_valid_csv(self, client):
        """POST /api/import with valid CSV should parse deals."""
        csv_data = self._make_csv_bytes([
            ["deal_id", "name", "publisher", "deal_type", "media_type", "cpm", "start_date", "end_date"],
            ["TEST-001", "Test Deal 1", "TestPub", "PG", "DIGITAL", "15.00", "2026-04-01", "2026-06-30"],
            ["TEST-002", "Test Deal 2", "TestPub2", "PD", "CTV", "25.00", "2026-05-01", "2026-07-31"],
        ])

        data = {
            "file": (io.BytesIO(csv_data), "test_deals.csv"),
        }
        resp = client.post("/api/import", data=data, content_type="multipart/form-data")
        assert resp.status_code == 200
        result = resp.get_json()
        assert result["successful"] == 2
        assert result["failed"] == 0
        assert len(result["deals"]) == 2

    def test_import_csv_with_errors(self, client):
        """POST /api/import with invalid rows should report errors."""
        csv_data = self._make_csv_bytes([
            ["deal_id", "name", "publisher", "deal_type"],
            # Missing seller info (no publisher value)
            ["ERR-001", "Bad Deal", "", "PG"],
        ])

        data = {
            "file": (io.BytesIO(csv_data), "bad_deals.csv"),
        }
        resp = client.post("/api/import", data=data, content_type="multipart/form-data")
        assert resp.status_code == 200
        result = resp.get_json()
        assert result["failed"] >= 1
        assert len(result["errors"]) >= 1

    def test_import_no_file(self, client):
        """POST /api/import without a file should return 400."""
        resp = client.post("/api/import", content_type="multipart/form-data")
        assert resp.status_code == 400

    def test_import_save(self, client):
        """POST /api/import/save should persist parsed deals."""
        deals = [
            {
                "seller_deal_id": "SAVE-001",
                "display_name": "Saved Deal",
                "seller_org": "SavePub",
                "deal_type": "PD",
                "media_type": "DIGITAL",
                "fixed_price_cpm": 10.0,
                "status": "draft",
            },
        ]
        resp = client.post(
            "/api/import/save",
            data=json.dumps({"deals": deals}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        result = resp.get_json()
        assert result["saved"] == 1
        assert len(result["deal_ids"]) == 1


# ---------------------------------------------------------------------------
# Manual Entry Tests
# ---------------------------------------------------------------------------


class TestManualEntry:
    """Tests for the manual deal entry endpoint."""

    def test_create_valid_deal(self, client):
        """POST /api/deals with valid data should create a deal."""
        resp = client.post(
            "/api/deals",
            data=json.dumps({
                "display_name": "Manual Test Deal",
                "seller_url": "https://test.seller.example.com",
                "deal_type": "PD",
                "media_type": "DIGITAL",
                "status": "draft",
            }),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "deal_id" in data

    def test_create_deal_missing_required(self, client):
        """POST /api/deals missing required fields should return 400."""
        resp = client.post(
            "/api/deals",
            data=json.dumps({
                "deal_type": "PD",
            }),
            content_type="application/json",
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False

    def test_create_deal_invalid_deal_type(self, client):
        """POST /api/deals with invalid deal_type should return 400."""
        resp = client.post(
            "/api/deals",
            data=json.dumps({
                "display_name": "Bad Type Deal",
                "seller_url": "https://test.seller.example.com",
                "deal_type": "INVALID",
            }),
            content_type="application/json",
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert any("deal_type" in e for e in data["errors"])

    def test_create_deal_with_metadata(self, client):
        """POST /api/deals with tags and advertiser should succeed."""
        resp = client.post(
            "/api/deals",
            data=json.dumps({
                "display_name": "Tagged Deal",
                "seller_url": "https://test.seller.example.com",
                "deal_type": "PG",
                "media_type": "CTV",
                "tags": ["premium", "test"],
                "advertiser_id": "ADV-TEST-001",
            }),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

        # Verify the deal exists and has metadata
        detail_resp = client.get(f"/api/deals/{data['deal_id']}")
        detail = detail_resp.get_json()
        assert detail["deal"]["media_type"] == "CTV"
