# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the deal library application service (EP-2.2).

Exercises ``ad_buyer.services.deal_service`` directly (below the MCP
interface layer): portfolio reads, CSV import, SSP import, and manual
deal entry -- happy paths and edge cases.
"""

from __future__ import annotations

import pytest

from ad_buyer.services import deal_service
from ad_buyer.storage.deal_store import DealStore

DB_URL = "sqlite:///:memory:"


@pytest.fixture
def store():
    s = DealStore(DB_URL)
    s.connect()
    yield s
    s.disconnect()


def _seed(store: DealStore, **overrides) -> str:
    defaults = {
        "seller_url": "https://seller.example.com",
        "product_id": "prod-001",
        "product_name": "Test Deal",
        "display_name": "Test Deal",
        "deal_type": "PD",
        "status": "active",
        "seller_deal_id": "SELL-001",
        "seller_org": "Example Publisher",
        "seller_domain": "example.com",
        "media_type": "DIGITAL",
        "price": 12.50,
        "impressions": 1_000_000,
        "flight_start": "2026-04-01",
        "flight_end": "2026-06-30",
        "currency": "USD",
    }
    defaults.update(overrides)
    return store.save_deal(**defaults)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


class TestListDeals:
    def test_empty(self, store):
        result = deal_service.list_deals(store)
        assert result["total"] == 0
        assert result["deals"] == []
        assert "timestamp" in result

    def test_lists_and_filters(self, store):
        _seed(store, display_name="A", status="active", seller_deal_id="A1")
        _seed(store, display_name="B", status="paused", seller_deal_id="B1")
        assert deal_service.list_deals(store)["total"] == 2
        active = deal_service.list_deals(store, status="active")
        assert active["total"] == 1
        assert active["deals"][0]["display_name"] == "A"


class TestSearchDeals:
    def test_empty_query_returns_error(self, store):
        assert "error" in deal_service.search_deals(store, "   ")

    def test_matches_on_seller_org(self, store):
        _seed(store, display_name="X", seller_org="ESPN", seller_deal_id="X1")
        _seed(store, display_name="Y", seller_org="Hulu", seller_deal_id="Y1")
        result = deal_service.search_deals(store, "espn")
        assert result["total"] == 1
        assert "seller organization" in result["deals"][0]["matched_in"]


class TestInspectDeal:
    def test_not_found(self, store):
        assert "error" in deal_service.inspect_deal(store, "nope")

    def test_returns_full_view(self, store):
        deal_id = _seed(store)
        result = deal_service.inspect_deal(store, deal_id)
        assert result["deal_id"] == deal_id
        assert "portfolio_metadata" in result
        assert "activations" in result
        assert "performance" in result


class TestPortfolioSummary:
    def test_empty(self, store):
        result = deal_service.portfolio_summary(store)
        assert result["total_deals"] == 0
        assert result["by_status"] == {}

    def test_aggregates(self, store):
        _seed(store, status="active", seller_org="ESPN", seller_deal_id="1")
        _seed(store, status="active", seller_org="ESPN", seller_deal_id="2")
        result = deal_service.portfolio_summary(store)
        assert result["total_deals"] == 2
        assert result["by_status"]["active"] == 2
        assert result["top_sellers"][0]["seller"] == "ESPN"


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------


class TestImportDealsCsv:
    def test_valid_rows_persist(self, store):
        csv_data = (
            "deal_name,publisher,seller_domain,cpm\n"
            "Deal One,ESPN,espn.com,15.0\n"
            "Deal Two,Hulu,hulu.com,20.0\n"
        )
        result = deal_service.import_deals_csv(store, csv_data)
        assert result["total_rows"] == 2
        assert result["successful"] == 2
        assert result["failed"] == 0
        assert len(result["deal_ids"]) == 2
        # Deals actually landed in the store.
        assert deal_service.list_deals(store)["total"] == 2

    def test_empty_csv(self, store):
        result = deal_service.import_deals_csv(store, "deal_name,publisher,seller_domain\n")
        assert result["total_rows"] == 0
        assert result["successful"] == 0

    def test_invalid_deal_type_reports_error(self, store):
        csv_data = (
            "deal_name,publisher,seller_domain,deal_type\nBad Deal,ESPN,espn.com,NOT_A_TYPE\n"
        )
        result = deal_service.import_deals_csv(store, csv_data)
        assert result["failed"] >= 1
        assert len(result["errors"]) >= 1

    def test_custom_seller_url(self, store):
        csv_data = "deal_name,publisher,seller_domain\nDeal,Pub,pub.com\n"
        result = deal_service.import_deals_csv(
            store, csv_data, default_seller_url="https://custom.example.com"
        )
        assert result["successful"] == 1
        deals = store.list_deals(limit=10)
        assert deals[0]["seller_url"] == "https://custom.example.com"


class TestCreateManualDeal:
    def test_success(self, store):
        result = deal_service.create_manual_deal(
            store,
            display_name="Manual Deal",
            seller_url="https://seller.example.com",
            deal_type="PD",
        )
        assert result["success"] is True
        assert result["deal_id"]
        assert result["display_name"] == "Manual Deal"

    def test_validation_failure(self, store):
        # Empty display_name should fail validation, not persist a deal.
        result = deal_service.create_manual_deal(
            store,
            display_name="",
            seller_url="https://seller.example.com",
        )
        assert result["success"] is False
        assert result["errors"]


class TestImportDealsSsp:
    def test_persists_connector_deals(self, store):
        class _FakeFetchResult:
            total_fetched = 2
            successful = 2
            failed = 0
            skipped = 0
            errors: list = []
            deals = [
                {
                    "seller_url": "https://ssp.example.com",
                    "product_id": "p1",
                    "display_name": "SSP Deal 1",
                    "deal_type": "PD",
                },
                {
                    "seller_url": "https://ssp.example.com",
                    "product_id": "p2",
                    "display_name": "SSP Deal 2",
                    "deal_type": "PD",
                },
            ]

        class _FakeConnector:
            import_source = "PUBMATIC"

            def fetch_deals(self):
                return _FakeFetchResult()

        result = deal_service.import_deals_ssp(store, _FakeConnector())
        assert result["total_rows"] == 2
        assert result["successful"] == 2
        assert len(result["deal_ids"]) == 2
        assert deal_service.list_deals(store)["total"] == 2
