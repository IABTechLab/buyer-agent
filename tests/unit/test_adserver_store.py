# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for AdServerStore — ad server integration record CRUD operations.

All tests use in-memory SQLite (`:memory:`) for speed and isolation.

bead: buyer-uoz (Ad server integration record storage)
"""

import uuid

import pytest

from ad_buyer.models.campaign import (
    AdServerBinding,
    AdServerCampaign,
    AdServerCampaignStatus,
    AdServerDelivery,
    AdServerType,
    BindingServingStatus,
)
from ad_buyer.storage.adserver_store import AdServerStore

# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------


@pytest.fixture
def adserver_store():
    """Create an AdServerStore backed by in-memory SQLite."""
    store = AdServerStore("sqlite:///:memory:")
    store.connect()
    yield store
    store.disconnect()


def _make_adserver_campaign(**overrides) -> AdServerCampaign:
    """Create an AdServerCampaign with sensible defaults."""
    defaults = dict(
        campaign_id="camp-001",
        ad_server=AdServerType.INNOVID,
        ad_server_campaign_id="inv-camp-abc",
        status=AdServerCampaignStatus.PENDING,
        bindings=[],
        delivery=None,
    )
    defaults.update(overrides)
    return AdServerCampaign(**defaults)


def _make_binding(**overrides) -> AdServerBinding:
    """Create an AdServerBinding with sensible defaults."""
    defaults = dict(
        deal_id="deal-001",
        creative_id="creative-001",
        ad_server_line_id="inv-line-001",
        serving_status=BindingServingStatus.ACTIVE,
    )
    defaults.update(overrides)
    return AdServerBinding(**defaults)


# -----------------------------------------------------------------------
# Model Tests
# -----------------------------------------------------------------------


class TestAdServerCampaignModel:
    """Tests for the AdServerCampaign Pydantic model."""

    def test_create_minimal_record(self):
        """Minimal ad server campaign with required fields only."""
        rec = AdServerCampaign(
            campaign_id="camp-001",
            ad_server=AdServerType.INNOVID,
            ad_server_campaign_id="inv-123",
        )
        assert rec.campaign_id == "camp-001"
        assert rec.id is not None  # auto-generated UUID
        assert rec.ad_server == AdServerType.INNOVID
        assert rec.status == AdServerCampaignStatus.PENDING
        assert rec.bindings == []
        assert rec.delivery is None
        assert rec.created_at is not None

    def test_ad_server_type_enum(self):
        """AdServerType enum has expected values."""
        assert AdServerType.INNOVID.value == "INNOVID"
        assert AdServerType.FLASHTALKING.value == "FLASHTALKING"

    def test_status_enum(self):
        """AdServerCampaignStatus enum has expected values."""
        for status in ["PENDING", "ACTIVE", "PAUSED", "COMPLETED", "ERROR"]:
            AdServerCampaignStatus(status)

    def test_campaign_with_bindings(self):
        """Ad server campaign with deal-creative bindings."""
        binding = _make_binding()
        rec = _make_adserver_campaign(bindings=[binding])
        assert len(rec.bindings) == 1
        assert rec.bindings[0].deal_id == "deal-001"

    def test_campaign_with_delivery(self):
        """Ad server campaign with delivery data."""
        delivery = AdServerDelivery(
            impressions_served=100000,
            spend_reported=5000.0,
            discrepancy_pct=2.5,
        )
        rec = _make_adserver_campaign(delivery=delivery)
        assert rec.delivery is not None
        assert rec.delivery.impressions_served == 100000

    def test_binding_model(self):
        """AdServerBinding with all fields."""
        binding = AdServerBinding(
            deal_id="deal-x",
            creative_id="creative-y",
            ad_server_line_id="line-z",
            serving_status=BindingServingStatus.PAUSED,
        )
        assert binding.deal_id == "deal-x"
        assert binding.serving_status == BindingServingStatus.PAUSED
        assert binding.last_sync_at is not None

    def test_delivery_model(self):
        """AdServerDelivery with all fields."""
        delivery = AdServerDelivery(
            impressions_served=50000,
            spend_reported=2500.0,
            discrepancy_pct=1.5,
        )
        assert delivery.impressions_served == 50000
        assert delivery.last_report_at is not None

    def test_id_is_valid_uuid(self):
        """Auto-generated id should be a valid UUID."""
        rec = _make_adserver_campaign()
        uuid.UUID(rec.id)  # should not raise


# -----------------------------------------------------------------------
# CRUD Tests
# -----------------------------------------------------------------------


class TestAdServerStoreSave:
    """Tests for save_ad_server_campaign."""

    def test_save_and_get(self, adserver_store):
        """Save a record and retrieve it by ID."""
        rec = _make_adserver_campaign()
        adserver_store.save_ad_server_campaign(rec)
        retrieved = adserver_store.get_ad_server_campaign(rec.id)

        assert retrieved is not None
        assert retrieved.id == rec.id
        assert retrieved.campaign_id == rec.campaign_id
        assert retrieved.ad_server == AdServerType.INNOVID
        assert retrieved.ad_server_campaign_id == rec.ad_server_campaign_id
        assert retrieved.status == AdServerCampaignStatus.PENDING

    def test_save_with_bindings(self, adserver_store):
        """Bindings round-trip through storage."""
        binding = _make_binding()
        rec = _make_adserver_campaign(bindings=[binding])
        adserver_store.save_ad_server_campaign(rec)
        retrieved = adserver_store.get_ad_server_campaign(rec.id)

        assert len(retrieved.bindings) == 1
        assert retrieved.bindings[0].deal_id == "deal-001"
        assert retrieved.bindings[0].creative_id == "creative-001"
        assert retrieved.bindings[0].ad_server_line_id == "inv-line-001"
        assert retrieved.bindings[0].serving_status == BindingServingStatus.ACTIVE

    def test_save_with_delivery(self, adserver_store):
        """Delivery data round-trips through storage."""
        delivery = AdServerDelivery(
            impressions_served=75000,
            spend_reported=3750.0,
            discrepancy_pct=1.0,
        )
        rec = _make_adserver_campaign(delivery=delivery)
        adserver_store.save_ad_server_campaign(rec)
        retrieved = adserver_store.get_ad_server_campaign(rec.id)

        assert retrieved.delivery is not None
        assert retrieved.delivery.impressions_served == 75000
        assert retrieved.delivery.spend_reported == 3750.0

    def test_get_nonexistent_returns_none(self, adserver_store):
        """Getting a non-existent record returns None."""
        result = adserver_store.get_ad_server_campaign("nonexistent-id")
        assert result is None


class TestAdServerStoreList:
    """Tests for list_ad_server_campaigns."""

    def test_list_by_campaign_id(self, adserver_store):
        """Filter by campaign_id."""
        rec_a = _make_adserver_campaign(campaign_id="camp-A")
        rec_b = _make_adserver_campaign(campaign_id="camp-B")
        adserver_store.save_ad_server_campaign(rec_a)
        adserver_store.save_ad_server_campaign(rec_b)

        results = adserver_store.list_ad_server_campaigns(campaign_id="camp-A")
        assert len(results) == 1
        assert results[0].campaign_id == "camp-A"

    def test_list_by_ad_server(self, adserver_store):
        """Filter by ad_server type."""
        rec_inv = _make_adserver_campaign(ad_server=AdServerType.INNOVID)
        rec_ft = _make_adserver_campaign(ad_server=AdServerType.FLASHTALKING)
        adserver_store.save_ad_server_campaign(rec_inv)
        adserver_store.save_ad_server_campaign(rec_ft)

        results = adserver_store.list_ad_server_campaigns(ad_server=AdServerType.INNOVID)
        assert len(results) == 1
        assert results[0].ad_server == AdServerType.INNOVID

    def test_list_by_status(self, adserver_store):
        """Filter by status."""
        rec_pending = _make_adserver_campaign(status=AdServerCampaignStatus.PENDING)
        rec_active = _make_adserver_campaign(status=AdServerCampaignStatus.ACTIVE)
        adserver_store.save_ad_server_campaign(rec_pending)
        adserver_store.save_ad_server_campaign(rec_active)

        results = adserver_store.list_ad_server_campaigns(status=AdServerCampaignStatus.ACTIVE)
        assert len(results) == 1
        assert results[0].status == AdServerCampaignStatus.ACTIVE

    def test_list_combined_filters(self, adserver_store):
        """Combined filters narrow results."""
        rec1 = _make_adserver_campaign(
            campaign_id="camp-X",
            ad_server=AdServerType.INNOVID,
            status=AdServerCampaignStatus.ACTIVE,
        )
        rec2 = _make_adserver_campaign(
            campaign_id="camp-X",
            ad_server=AdServerType.FLASHTALKING,
            status=AdServerCampaignStatus.ACTIVE,
        )
        rec3 = _make_adserver_campaign(
            campaign_id="camp-X",
            ad_server=AdServerType.INNOVID,
            status=AdServerCampaignStatus.PENDING,
        )
        adserver_store.save_ad_server_campaign(rec1)
        adserver_store.save_ad_server_campaign(rec2)
        adserver_store.save_ad_server_campaign(rec3)

        results = adserver_store.list_ad_server_campaigns(
            campaign_id="camp-X",
            ad_server=AdServerType.INNOVID,
            status=AdServerCampaignStatus.ACTIVE,
        )
        assert len(results) == 1
        assert results[0].id == rec1.id

    def test_list_empty(self, adserver_store):
        """List returns empty when no records match."""
        results = adserver_store.list_ad_server_campaigns(campaign_id="no-such-campaign")
        assert results == []


class TestAdServerStoreUpdate:
    """Tests for update_ad_server_campaign."""

    def test_update_status(self, adserver_store):
        """Update the status of an ad server campaign."""
        rec = _make_adserver_campaign(status=AdServerCampaignStatus.PENDING)
        adserver_store.save_ad_server_campaign(rec)

        adserver_store.update_ad_server_campaign(rec.id, status=AdServerCampaignStatus.ACTIVE)
        updated = adserver_store.get_ad_server_campaign(rec.id)
        assert updated.status == AdServerCampaignStatus.ACTIVE

    def test_update_ad_server_campaign_id(self, adserver_store):
        """Update the external ad server campaign ID."""
        rec = _make_adserver_campaign(ad_server_campaign_id="old-id")
        adserver_store.save_ad_server_campaign(rec)

        adserver_store.update_ad_server_campaign(rec.id, ad_server_campaign_id="new-id")
        updated = adserver_store.get_ad_server_campaign(rec.id)
        assert updated.ad_server_campaign_id == "new-id"

    def test_update_bindings(self, adserver_store):
        """Update bindings replaces entire list."""
        rec = _make_adserver_campaign(bindings=[])
        adserver_store.save_ad_server_campaign(rec)

        new_binding = _make_binding(deal_id="deal-new")
        adserver_store.update_ad_server_campaign(rec.id, bindings=[new_binding])
        updated = adserver_store.get_ad_server_campaign(rec.id)
        assert len(updated.bindings) == 1
        assert updated.bindings[0].deal_id == "deal-new"

    def test_update_delivery(self, adserver_store):
        """Update delivery data."""
        rec = _make_adserver_campaign()
        adserver_store.save_ad_server_campaign(rec)

        delivery = AdServerDelivery(
            impressions_served=200000,
            spend_reported=10000.0,
            discrepancy_pct=3.0,
        )
        adserver_store.update_ad_server_campaign(rec.id, delivery=delivery)
        updated = adserver_store.get_ad_server_campaign(rec.id)
        assert updated.delivery is not None
        assert updated.delivery.impressions_served == 200000

    def test_update_nonexistent_raises(self, adserver_store):
        """Updating a non-existent record raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            adserver_store.update_ad_server_campaign(
                "nonexistent-id",
                status=AdServerCampaignStatus.ACTIVE,
            )

    def test_update_multiple_fields(self, adserver_store):
        """Update multiple fields at once."""
        rec = _make_adserver_campaign(
            status=AdServerCampaignStatus.PENDING,
            ad_server_campaign_id="old-id",
        )
        adserver_store.save_ad_server_campaign(rec)

        adserver_store.update_ad_server_campaign(
            rec.id,
            status=AdServerCampaignStatus.ACTIVE,
            ad_server_campaign_id="new-id",
        )
        updated = adserver_store.get_ad_server_campaign(rec.id)
        assert updated.status == AdServerCampaignStatus.ACTIVE
        assert updated.ad_server_campaign_id == "new-id"
