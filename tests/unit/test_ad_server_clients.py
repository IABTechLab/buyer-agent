# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for ad server integration clients.

Tests the abstract AdServerClient interface, InnovidClient (CTV) and
FlashtalkingClient (display) stub implementations, and the AdServerManager
that routes operations to the correct client.

bead: buyer-7m8
"""

import pytest

from ad_buyer.clients.ad_server.base import AdServerClient
from ad_buyer.clients.ad_server.innovid import InnovidClient
from ad_buyer.clients.ad_server.flashtalking import FlashtalkingClient
from ad_buyer.clients.ad_server.manager import AdServerManager
from ad_buyer.models.campaign import (
    AdServerType,
    AdServerCampaignStatus,
)
from ad_buyer.storage.adserver_store import AdServerStore


# ---------------------------------------------------------------------------
# AdServerClient abstract base tests
# ---------------------------------------------------------------------------


class TestAdServerClientInterface:
    """Verify that AdServerClient cannot be instantiated directly."""

    def test_cannot_instantiate_abstract_base(self):
        """AdServerClient is abstract and should not be instantiated."""
        with pytest.raises(TypeError):
            AdServerClient()

    def test_subclass_must_implement_all_methods(self):
        """A subclass missing required methods should raise TypeError."""

        class IncompleteClient(AdServerClient):
            pass

        with pytest.raises(TypeError):
            IncompleteClient()


# ---------------------------------------------------------------------------
# InnovidClient tests
# ---------------------------------------------------------------------------


class TestInnovidClient:
    """Test Innovid CTV ad server stub client."""

    @pytest.fixture
    def client(self):
        return InnovidClient()

    def test_ad_server_type_is_innovid(self, client):
        """Client should report its ad server type as INNOVID."""
        assert client.ad_server_type == AdServerType.INNOVID

    def test_create_campaign_returns_external_id(self, client):
        """create_campaign should return a non-empty external campaign ID."""
        campaign_data = {
            "campaign_id": "camp-001",
            "name": "Test CTV Campaign",
            "advertiser": "Test Advertiser",
            "budget": 50000.00,
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
        }
        result = client.create_campaign(campaign_data)
        assert "external_campaign_id" in result
        assert result["external_campaign_id"]  # non-empty
        assert result["status"] == "created"
        assert result["ad_server"] == "INNOVID"

    def test_upload_creative_returns_external_creative_id(self, client):
        """upload_creative should return a non-empty external creative ID."""
        asset_data = {
            "asset_id": "asset-001",
            "asset_name": "CTV 30s Spot",
            "asset_type": "video",
            "format_spec": {
                "vast_version": "4.2",
                "duration_sec": 30,
            },
            "source_url": "https://cdn.example.com/video/spot-30s.mp4",
        }
        result = client.upload_creative(asset_data)
        assert "external_creative_id" in result
        assert result["external_creative_id"]
        assert result["status"] == "uploaded"
        assert result["ad_server"] == "INNOVID"

    def test_assign_creative_to_line_returns_assignment_id(self, client):
        """assign_creative_to_line should return a non-empty assignment ID."""
        rotation_config = {
            "rotation_type": "even",
            "weight": 50,
        }
        result = client.assign_creative_to_line(
            creative_id="innov-creative-001",
            line_id="innov-line-001",
            rotation_config=rotation_config,
        )
        assert "assignment_id" in result
        assert result["assignment_id"]
        assert result["status"] == "assigned"
        assert result["creative_id"] == "innov-creative-001"
        assert result["line_id"] == "innov-line-001"

    def test_get_delivery_data_returns_metrics(self, client):
        """get_delivery_data should return delivery metrics."""
        result = client.get_delivery_data(campaign_id="innov-camp-001")
        assert "impressions" in result
        assert "completions" in result
        assert "spend" in result
        assert isinstance(result["impressions"], int)
        assert isinstance(result["completions"], int)
        assert isinstance(result["spend"], float)
        # CTV-specific metrics
        assert "completion_rate" in result
        assert "household_reach" in result

    def test_sync_status_returns_current_status(self, client):
        """sync_status should return current campaign status."""
        result = client.sync_status(campaign_id="innov-camp-001")
        assert "status" in result
        assert result["status"] in ("active", "paused", "completed", "pending")
        assert "campaign_id" in result
        assert result["campaign_id"] == "innov-camp-001"
        assert "ad_server" in result
        assert result["ad_server"] == "INNOVID"

    def test_upload_creative_with_vast_tag(self, client):
        """upload_creative should handle VAST tag URLs for CTV."""
        asset_data = {
            "asset_id": "asset-002",
            "asset_name": "CTV VAST Wrapper",
            "asset_type": "video",
            "format_spec": {
                "vast_version": "4.2",
                "duration_sec": 15,
                "vast_url": "https://ad.example.com/vast/wrapper.xml",
            },
            "source_url": "https://ad.example.com/vast/wrapper.xml",
        }
        result = client.upload_creative(asset_data)
        assert result["status"] == "uploaded"
        assert "external_creative_id" in result


# ---------------------------------------------------------------------------
# FlashtalkingClient tests
# ---------------------------------------------------------------------------


class TestFlashtalkingClient:
    """Test Flashtalking display ad server stub client."""

    @pytest.fixture
    def client(self):
        return FlashtalkingClient()

    def test_ad_server_type_is_flashtalking(self, client):
        """Client should report its ad server type as FLASHTALKING."""
        assert client.ad_server_type == AdServerType.FLASHTALKING

    def test_create_campaign_returns_external_id(self, client):
        """create_campaign should return a non-empty external campaign ID."""
        campaign_data = {
            "campaign_id": "camp-002",
            "name": "Test Display Campaign",
            "advertiser": "Test Advertiser",
            "budget": 25000.00,
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
        }
        result = client.create_campaign(campaign_data)
        assert "external_campaign_id" in result
        assert result["external_campaign_id"]
        assert result["status"] == "created"
        assert result["ad_server"] == "FLASHTALKING"

    def test_upload_creative_returns_external_creative_id(self, client):
        """upload_creative should return a non-empty external creative ID."""
        asset_data = {
            "asset_id": "asset-003",
            "asset_name": "Display 300x250",
            "asset_type": "display",
            "format_spec": {
                "width": 300,
                "height": 250,
                "format": "html5",
            },
            "source_url": "https://cdn.example.com/display/300x250.zip",
        }
        result = client.upload_creative(asset_data)
        assert "external_creative_id" in result
        assert result["external_creative_id"]
        assert result["status"] == "uploaded"
        assert result["ad_server"] == "FLASHTALKING"

    def test_assign_creative_to_line_returns_assignment_id(self, client):
        """assign_creative_to_line should return a non-empty assignment ID."""
        rotation_config = {
            "rotation_type": "weighted",
            "weight": 75,
        }
        result = client.assign_creative_to_line(
            creative_id="ft-creative-001",
            line_id="ft-placement-001",
            rotation_config=rotation_config,
        )
        assert "assignment_id" in result
        assert result["assignment_id"]
        assert result["status"] == "assigned"
        assert result["creative_id"] == "ft-creative-001"
        assert result["line_id"] == "ft-placement-001"

    def test_get_delivery_data_returns_metrics(self, client):
        """get_delivery_data should return delivery metrics."""
        result = client.get_delivery_data(campaign_id="ft-camp-001")
        assert "impressions" in result
        assert "clicks" in result
        assert "spend" in result
        assert isinstance(result["impressions"], int)
        assert isinstance(result["clicks"], int)
        assert isinstance(result["spend"], float)
        # Display-specific metrics
        assert "ctr" in result
        assert "viewability_rate" in result

    def test_sync_status_returns_current_status(self, client):
        """sync_status should return current campaign status."""
        result = client.sync_status(campaign_id="ft-camp-001")
        assert "status" in result
        assert result["status"] in ("active", "paused", "completed", "pending")
        assert "campaign_id" in result
        assert result["campaign_id"] == "ft-camp-001"
        assert "ad_server" in result
        assert result["ad_server"] == "FLASHTALKING"

    def test_upload_creative_with_dco_feed(self, client):
        """upload_creative should handle DCO (dynamic creative optimization) assets."""
        asset_data = {
            "asset_id": "asset-004",
            "asset_name": "DCO Template",
            "asset_type": "display",
            "format_spec": {
                "width": 300,
                "height": 250,
                "format": "html5",
                "dco_enabled": True,
                "feed_url": "https://feeds.example.com/products.json",
            },
            "source_url": "https://cdn.example.com/dco/template.zip",
        }
        result = client.upload_creative(asset_data)
        assert result["status"] == "uploaded"
        assert "external_creative_id" in result


# ---------------------------------------------------------------------------
# AdServerManager tests
# ---------------------------------------------------------------------------


class TestAdServerManager:
    """Test the AdServerManager routing and workflow logic."""

    @pytest.fixture
    def store(self):
        """Create an in-memory AdServerStore."""
        s = AdServerStore("sqlite:///:memory:")
        s.connect()
        yield s
        s.disconnect()

    @pytest.fixture
    def manager(self, store):
        """Create an AdServerManager with the in-memory store."""
        return AdServerManager(store=store)

    def test_manager_routes_to_innovid(self, manager):
        """Manager should route CTV operations to InnovidClient."""
        client = manager.get_client(AdServerType.INNOVID)
        assert isinstance(client, InnovidClient)

    def test_manager_routes_to_flashtalking(self, manager):
        """Manager should route display operations to FlashtalkingClient."""
        client = manager.get_client(AdServerType.FLASHTALKING)
        assert isinstance(client, FlashtalkingClient)

    def test_manager_raises_for_unknown_ad_server(self, manager):
        """Manager should raise ValueError for unsupported ad server types."""
        with pytest.raises(ValueError, match="Unsupported ad server"):
            manager.get_client("UNKNOWN_SERVER")

    def test_create_ad_server_campaign_creates_record(self, manager, store):
        """create_ad_server_campaign should create a campaign and persist it."""
        campaign_data = {
            "campaign_id": "camp-001",
            "name": "Test CTV Campaign",
            "advertiser": "Test Advertiser",
            "budget": 50000.00,
        }
        result = manager.create_ad_server_campaign(
            campaign_id="camp-001",
            ad_server_type=AdServerType.INNOVID,
            campaign_data=campaign_data,
        )
        assert "record_id" in result
        assert "external_campaign_id" in result
        assert result["ad_server"] == "INNOVID"

        # Verify persisted
        records = store.list_ad_server_campaigns(campaign_id="camp-001")
        assert len(records) == 1
        assert records[0].ad_server == AdServerType.INNOVID
        assert records[0].status == AdServerCampaignStatus.ACTIVE

    def test_upload_and_assign_creative(self, manager, store):
        """upload_and_assign_creative should upload, assign, and create a binding."""
        # First create a campaign
        campaign_data = {
            "campaign_id": "camp-002",
            "name": "Display Campaign",
        }
        camp_result = manager.create_ad_server_campaign(
            campaign_id="camp-002",
            ad_server_type=AdServerType.FLASHTALKING,
            campaign_data=campaign_data,
        )
        record_id = camp_result["record_id"]

        # Now upload and assign a creative
        asset_data = {
            "asset_id": "asset-003",
            "asset_name": "Display 300x250",
            "asset_type": "display",
            "format_spec": {"width": 300, "height": 250},
            "source_url": "https://cdn.example.com/display/300x250.zip",
        }
        rotation_config = {"rotation_type": "even", "weight": 100}

        result = manager.upload_and_assign_creative(
            record_id=record_id,
            deal_id="deal-001",
            asset_data=asset_data,
            rotation_config=rotation_config,
        )
        assert result["upload_status"] == "uploaded"
        assert result["assign_status"] == "assigned"
        assert "external_creative_id" in result
        assert "assignment_id" in result

        # Verify binding was persisted
        record = store.get_ad_server_campaign(record_id)
        assert len(record.bindings) == 1
        assert record.bindings[0].deal_id == "deal-001"
        assert record.bindings[0].creative_id == "asset-003"

    def test_sync_delivery_updates_record(self, manager, store):
        """sync_delivery should fetch delivery data and update the record."""
        # Create a campaign first
        camp_result = manager.create_ad_server_campaign(
            campaign_id="camp-003",
            ad_server_type=AdServerType.INNOVID,
            campaign_data={"campaign_id": "camp-003", "name": "CTV Camp"},
        )
        record_id = camp_result["record_id"]

        # Sync delivery
        result = manager.sync_delivery(record_id=record_id)
        assert "impressions" in result
        assert "spend" in result

        # Verify delivery data was persisted
        record = store.get_ad_server_campaign(record_id)
        assert record.delivery is not None
        assert record.delivery.impressions_served >= 0

    def test_sync_campaign_status(self, manager, store):
        """sync_campaign_status should update the record status from ad server."""
        camp_result = manager.create_ad_server_campaign(
            campaign_id="camp-004",
            ad_server_type=AdServerType.FLASHTALKING,
            campaign_data={"campaign_id": "camp-004", "name": "Display Camp"},
        )
        record_id = camp_result["record_id"]

        result = manager.sync_campaign_status(record_id=record_id)
        assert "status" in result
        assert result["ad_server"] == "FLASHTALKING"

    def test_get_client_caches_instances(self, manager):
        """Manager should reuse client instances for the same ad server type."""
        client1 = manager.get_client(AdServerType.INNOVID)
        client2 = manager.get_client(AdServerType.INNOVID)
        assert client1 is client2

    def test_create_campaign_for_both_ad_servers(self, manager, store):
        """A single campaign can have records for both ad servers."""
        # Create Innovid record
        manager.create_ad_server_campaign(
            campaign_id="camp-multi",
            ad_server_type=AdServerType.INNOVID,
            campaign_data={"campaign_id": "camp-multi", "name": "Multi"},
        )
        # Create Flashtalking record
        manager.create_ad_server_campaign(
            campaign_id="camp-multi",
            ad_server_type=AdServerType.FLASHTALKING,
            campaign_data={"campaign_id": "camp-multi", "name": "Multi"},
        )

        records = store.list_ad_server_campaigns(campaign_id="camp-multi")
        assert len(records) == 2
        ad_servers = {r.ad_server for r in records}
        assert ad_servers == {AdServerType.INNOVID, AdServerType.FLASHTALKING}

    def test_upload_and_assign_raises_for_missing_record(self, manager):
        """upload_and_assign_creative should raise if the record doesn't exist."""
        with pytest.raises(ValueError, match="not found"):
            manager.upload_and_assign_creative(
                record_id="nonexistent",
                deal_id="deal-001",
                asset_data={"asset_id": "x"},
                rotation_config={},
            )

    def test_multiple_bindings_on_same_record(self, manager, store):
        """Multiple creatives can be assigned to the same ad server campaign."""
        camp_result = manager.create_ad_server_campaign(
            campaign_id="camp-multi-bind",
            ad_server_type=AdServerType.INNOVID,
            campaign_data={"campaign_id": "camp-multi-bind", "name": "Multi Bind"},
        )
        record_id = camp_result["record_id"]

        # Assign first creative
        manager.upload_and_assign_creative(
            record_id=record_id,
            deal_id="deal-001",
            asset_data={"asset_id": "asset-a", "asset_type": "video",
                        "format_spec": {"vast_version": "4.2", "duration_sec": 30},
                        "source_url": "https://cdn.example.com/a.mp4"},
            rotation_config={"rotation_type": "even"},
        )

        # Assign second creative
        manager.upload_and_assign_creative(
            record_id=record_id,
            deal_id="deal-002",
            asset_data={"asset_id": "asset-b", "asset_type": "video",
                        "format_spec": {"vast_version": "4.2", "duration_sec": 15},
                        "source_url": "https://cdn.example.com/b.mp4"},
            rotation_config={"rotation_type": "even"},
        )

        record = store.get_ad_server_campaign(record_id)
        assert len(record.bindings) == 2
        deal_ids = {b.deal_id for b in record.bindings}
        assert deal_ids == {"deal-001", "deal-002"}
