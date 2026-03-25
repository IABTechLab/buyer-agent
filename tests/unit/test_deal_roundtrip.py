# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Roundtrip tests for manual deal entry -> DealStore -> inspect_deal.

Verifies that v2 fields set via create_deal_manual survive the full
save/load cycle and appear as first-class columns (not lost in a
metadata JSON blob).

bead: buyer-e3d5
"""

import pytest

from ad_buyer.storage.deal_store import DealStore
from ad_buyer.tools.deal_library.deal_entry import (
    ManualDealEntry,
    create_manual_deal,
)


@pytest.fixture()
def deal_store():
    """In-memory DealStore for testing."""
    store = DealStore("sqlite:///:memory:")
    store.connect()
    yield store
    store.disconnect()


# -- V2 field values used across tests ------------------------------------

V2_DEAL_PARAMS = {
    "display_name": "Premium Video PG",
    "seller_url": "https://nbcu.seller.example.com",
    "product_id": "prod-nbcu-video",
    "deal_type": "PG",
    "status": "active",
    "seller_deal_id": "SELLER-ABC-123",
    "seller_org": "NBCUniversal",
    "seller_domain": "nbcuniversal.com",
    "seller_type": "PUBLISHER",
    "buyer_org": "MediaCo Agency",
    "buyer_id": "buyer-mediaco-001",
    "price": 15.50,
    "fixed_price_cpm": 15.50,
    "bid_floor_cpm": 12.00,
    "price_model": "CPM",
    "currency": "EUR",
    "media_type": "CTV",
    "impressions": 5_000_000,
    "flight_start": "2026-04-01",
    "flight_end": "2026-06-30",
    "description": "Premium CTV video inventory for Q2 campaign",
    "advertiser_id": "adv-quickmeal-001",
    "tags": ["premium", "ctv", "sports"],
}

# Fields that should be stored as dedicated columns on the deals table
# and survive get_deal() roundtrip.
V2_EXPECTED_FIELDS = {
    "display_name": "Premium Video PG",
    "seller_org": "NBCUniversal",
    "seller_domain": "nbcuniversal.com",
    "seller_type": "PUBLISHER",
    "buyer_org": "MediaCo Agency",
    "buyer_id": "buyer-mediaco-001",
    "price_model": "CPM",
    "fixed_price_cpm": 15.50,
    "bid_floor_cpm": 12.00,
    "currency": "EUR",
    "media_type": "CTV",
    "description": "Premium CTV video inventory for Q2 campaign",
}


class TestCreateDealManualRoundtrip:
    """create_manual_deal -> save_deal -> get_deal roundtrip tests."""

    def test_v2_fields_survive_roundtrip(self, deal_store: DealStore):
        """All v2 fields set in create_manual_deal should be readable
        from get_deal after save_deal persists them."""
        entry = ManualDealEntry(**V2_DEAL_PARAMS)
        result = create_manual_deal(entry)
        assert result.success is True

        # Persist via DealStore
        deal_id = deal_store.save_deal(**result.deal_data)

        # Read back
        deal = deal_store.get_deal(deal_id)
        assert deal is not None

        # Verify every v2 field is present as a top-level column value
        for field_name, expected_value in V2_EXPECTED_FIELDS.items():
            actual = deal.get(field_name)
            assert actual == expected_value, (
                f"Field '{field_name}': expected {expected_value!r}, got {actual!r}"
            )

    def test_v1_fields_survive_roundtrip(self, deal_store: DealStore):
        """Core v1 fields should also survive the roundtrip."""
        entry = ManualDealEntry(**V2_DEAL_PARAMS)
        result = create_manual_deal(entry)
        assert result.success is True

        deal_id = deal_store.save_deal(**result.deal_data)
        deal = deal_store.get_deal(deal_id)
        assert deal is not None

        assert deal["seller_url"] == "https://nbcu.seller.example.com"
        assert deal["product_id"] == "prod-nbcu-video"
        assert deal["product_name"] == "Premium Video PG"
        assert deal["deal_type"] == "PG"
        assert deal["status"] == "active"
        assert deal["seller_deal_id"] == "SELLER-ABC-123"
        assert deal["price"] == 15.50
        assert deal["impressions"] == 5_000_000
        assert deal["flight_start"] == "2026-04-01"
        assert deal["flight_end"] == "2026-06-30"

    def test_minimal_deal_roundtrip(self, deal_store: DealStore):
        """A minimal deal (only required fields) should also roundtrip
        cleanly, with v2 optional fields returning None/NULL."""
        entry = ManualDealEntry(
            display_name="Simple Deal",
            seller_url="https://seller.example.com",
        )
        result = create_manual_deal(entry)
        assert result.success is True

        deal_id = deal_store.save_deal(**result.deal_data)
        deal = deal_store.get_deal(deal_id)
        assert deal is not None

        # Required fields present
        assert deal["product_name"] == "Simple Deal"
        assert deal["display_name"] == "Simple Deal"
        assert deal["seller_url"] == "https://seller.example.com"

        # Optional v2 fields should be None (not missing)
        assert deal.get("seller_org") is None
        assert deal.get("seller_domain") is None
        assert deal.get("media_type") is None
        assert deal.get("buyer_org") is None

    def test_search_by_seller_org_after_roundtrip(self, deal_store: DealStore):
        """After roundtrip, searching by seller_org should find the deal
        (this was broken when seller_org was buried in metadata JSON)."""
        entry = ManualDealEntry(**V2_DEAL_PARAMS)
        result = create_manual_deal(entry)
        deal_id = deal_store.save_deal(**result.deal_data)

        # list_deals doesn't filter by seller_org directly, but the
        # data should be a top-level column so portfolio tools can
        # post-filter on it.
        deals = deal_store.list_deals(limit=100)
        matched = [d for d in deals if d.get("seller_org") == "NBCUniversal"]
        assert len(matched) == 1
        assert matched[0]["id"] == deal_id

    def test_filter_by_media_type_after_roundtrip(self, deal_store: DealStore):
        """After roundtrip, filtering by media_type should find the deal
        (this was broken when media_type was buried in metadata JSON)."""
        entry = ManualDealEntry(**V2_DEAL_PARAMS)
        result = create_manual_deal(entry)
        deal_id = deal_store.save_deal(**result.deal_data)

        # list_deals supports media_type as a native filter
        deals = deal_store.list_deals(media_type="CTV")
        assert len(deals) == 1
        assert deals[0]["id"] == deal_id

    def test_deal_data_has_no_metadata_key(self):
        """deal_data from create_manual_deal should not contain a
        'metadata' key -- v2 fields are top-level kwargs now."""
        entry = ManualDealEntry(**V2_DEAL_PARAMS)
        result = create_manual_deal(entry)
        assert result.success is True
        assert "metadata" not in result.deal_data
