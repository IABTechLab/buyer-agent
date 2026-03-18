# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for manual deal entry tool.

Tests the ManualDealEntry Pydantic model, create_manual_deal() validation
function, and the CrewAI ManualDealEntryTool wrapper.
"""

import json

import pytest


# ---------------------------------------------------------------------------
# ManualDealEntry model tests
# ---------------------------------------------------------------------------


class TestManualDealEntryModel:
    """Tests for the ManualDealEntry Pydantic input model."""

    def test_minimal_required_fields(self):
        """Model should accept only the two required fields."""
        from ad_buyer.tools.deal_jockey.deal_entry import ManualDealEntry

        entry = ManualDealEntry(
            display_name="ESPN Sports PMP",
            seller_url="https://espn.seller.example.com",
        )
        assert entry.display_name == "ESPN Sports PMP"
        assert entry.seller_url == "https://espn.seller.example.com"

    def test_default_values(self):
        """Default values should be applied when not provided."""
        from ad_buyer.tools.deal_jockey.deal_entry import ManualDealEntry

        entry = ManualDealEntry(
            display_name="Test Deal",
            seller_url="https://seller.example.com",
        )
        assert entry.product_id == "manual-entry"
        assert entry.deal_type == "PD"
        assert entry.status == "draft"
        assert entry.currency == "USD"

    def test_all_fields_populated(self):
        """Model should accept all fields when fully populated."""
        from ad_buyer.tools.deal_jockey.deal_entry import ManualDealEntry

        entry = ManualDealEntry(
            display_name="Premium Video PG",
            seller_url="https://nbcu.seller.example.com",
            product_id="prod-nbcu-video",
            deal_type="PG",
            status="active",
            seller_deal_id="SELLER-ABC-123",
            seller_org="NBCUniversal",
            seller_domain="nbcuniversal.com",
            seller_type="PUBLISHER",
            buyer_org="MediaCo Agency",
            buyer_id="buyer-mediaco-001",
            price=15.50,
            fixed_price_cpm=15.50,
            bid_floor_cpm=12.00,
            price_model="CPM",
            currency="EUR",
            media_type="CTV",
            impressions=5_000_000,
            flight_start="2026-04-01",
            flight_end="2026-06-30",
            description="Premium CTV video inventory for Q2 campaign",
            advertiser_id="adv-quickmeal-001",
            tags=["premium", "ctv", "sports"],
        )
        assert entry.display_name == "Premium Video PG"
        assert entry.deal_type == "PG"
        assert entry.seller_type == "PUBLISHER"
        assert entry.price_model == "CPM"
        assert entry.media_type == "CTV"
        assert entry.impressions == 5_000_000
        assert entry.tags == ["premium", "ctv", "sports"]
        assert entry.currency == "EUR"

    def test_optional_fields_default_none(self):
        """Optional fields should default to None."""
        from ad_buyer.tools.deal_jockey.deal_entry import ManualDealEntry

        entry = ManualDealEntry(
            display_name="Test",
            seller_url="https://seller.example.com",
        )
        assert entry.seller_deal_id is None
        assert entry.seller_org is None
        assert entry.seller_domain is None
        assert entry.seller_type is None
        assert entry.buyer_org is None
        assert entry.buyer_id is None
        assert entry.price is None
        assert entry.fixed_price_cpm is None
        assert entry.bid_floor_cpm is None
        assert entry.price_model is None
        assert entry.media_type is None
        assert entry.impressions is None
        assert entry.flight_start is None
        assert entry.flight_end is None
        assert entry.description is None
        assert entry.advertiser_id is None
        assert entry.tags is None


# ---------------------------------------------------------------------------
# create_manual_deal() validation tests
# ---------------------------------------------------------------------------


class TestCreateManualDeal:
    """Tests for the create_manual_deal() validation function."""

    def test_valid_minimal_deal(self):
        """Valid deal with minimal fields should succeed."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="ESPN Sports PMP",
            seller_url="https://espn.seller.example.com",
        )
        result = create_manual_deal(entry)

        assert result.success is True
        assert result.errors == []
        assert result.deal_data is not None
        assert result.metadata is not None

    def test_valid_full_deal(self):
        """Valid deal with all fields should succeed."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="Premium Video PG",
            seller_url="https://nbcu.seller.example.com",
            product_id="prod-nbcu-video",
            deal_type="PG",
            status="active",
            seller_deal_id="SELLER-ABC-123",
            seller_org="NBCUniversal",
            seller_domain="nbcuniversal.com",
            seller_type="PUBLISHER",
            buyer_org="MediaCo Agency",
            buyer_id="buyer-mediaco-001",
            price=15.50,
            fixed_price_cpm=15.50,
            bid_floor_cpm=12.00,
            price_model="CPM",
            currency="EUR",
            media_type="CTV",
            impressions=5_000_000,
            flight_start="2026-04-01",
            flight_end="2026-06-30",
            description="Premium CTV video inventory",
            advertiser_id="adv-quickmeal-001",
            tags=["premium", "ctv"],
        )
        result = create_manual_deal(entry)

        assert result.success is True
        assert result.errors == []
        assert result.deal_data is not None

    def test_invalid_deal_type(self):
        """Invalid deal_type should produce validation error."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="Test Deal",
            seller_url="https://seller.example.com",
            deal_type="INVALID",
        )
        result = create_manual_deal(entry)

        assert result.success is False
        assert result.deal_data is None
        assert any("deal_type" in e for e in result.errors)

    def test_invalid_media_type(self):
        """Invalid media_type should produce validation error."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="Test Deal",
            seller_url="https://seller.example.com",
            media_type="HOLOGRAM",
        )
        result = create_manual_deal(entry)

        assert result.success is False
        assert result.deal_data is None
        assert any("media_type" in e for e in result.errors)

    def test_invalid_seller_type(self):
        """Invalid seller_type should produce validation error."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="Test Deal",
            seller_url="https://seller.example.com",
            seller_type="UNICORN",
        )
        result = create_manual_deal(entry)

        assert result.success is False
        assert result.deal_data is None
        assert any("seller_type" in e for e in result.errors)

    def test_invalid_price_model(self):
        """Invalid price_model should produce validation error."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="Test Deal",
            seller_url="https://seller.example.com",
            price_model="BARTER",
        )
        result = create_manual_deal(entry)

        assert result.success is False
        assert result.deal_data is None
        assert any("price_model" in e for e in result.errors)

    def test_invalid_status(self):
        """Invalid status should produce validation error."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="Test Deal",
            seller_url="https://seller.example.com",
            status="expired",
        )
        result = create_manual_deal(entry)

        assert result.success is False
        assert result.deal_data is None
        assert any("status" in e for e in result.errors)

    def test_empty_display_name(self):
        """Empty display_name should produce validation error."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="",
            seller_url="https://seller.example.com",
        )
        result = create_manual_deal(entry)

        assert result.success is False
        assert result.deal_data is None
        assert any("display_name" in e for e in result.errors)

    def test_whitespace_only_display_name(self):
        """Whitespace-only display_name should produce validation error."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="   ",
            seller_url="https://seller.example.com",
        )
        result = create_manual_deal(entry)

        assert result.success is False
        assert result.deal_data is None
        assert any("display_name" in e for e in result.errors)

    def test_flight_end_before_start(self):
        """flight_end before flight_start should produce validation error."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="Test Deal",
            seller_url="https://seller.example.com",
            flight_start="2026-06-01",
            flight_end="2026-03-01",
        )
        result = create_manual_deal(entry)

        assert result.success is False
        assert result.deal_data is None
        assert any("flight" in e.lower() for e in result.errors)

    def test_flight_start_only_is_valid(self):
        """Providing only flight_start (no flight_end) should be valid."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="Test Deal",
            seller_url="https://seller.example.com",
            flight_start="2026-04-01",
        )
        result = create_manual_deal(entry)

        assert result.success is True

    def test_flight_end_only_is_valid(self):
        """Providing only flight_end (no flight_start) should be valid."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="Test Deal",
            seller_url="https://seller.example.com",
            flight_end="2026-12-31",
        )
        result = create_manual_deal(entry)

        assert result.success is True

    def test_multiple_validation_errors(self):
        """Multiple invalid fields should produce multiple errors."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="",
            seller_url="https://seller.example.com",
            deal_type="INVALID",
            media_type="HOLOGRAM",
        )
        result = create_manual_deal(entry)

        assert result.success is False
        assert len(result.errors) >= 3


# ---------------------------------------------------------------------------
# Output structure tests - deal_data matches DealStore.save_deal()
# ---------------------------------------------------------------------------


class TestDealDataStructure:
    """Tests that deal_data output matches DealStore.save_deal() kwargs."""

    def test_deal_data_has_required_save_deal_fields(self):
        """deal_data must contain all fields needed by DealStore.save_deal()."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="Test Deal",
            seller_url="https://seller.example.com",
        )
        result = create_manual_deal(entry)

        assert result.success is True
        data = result.deal_data

        # Required fields for save_deal()
        assert "seller_url" in data
        assert "product_id" in data
        assert "product_name" in data
        assert "deal_type" in data
        assert "status" in data

    def test_deal_data_maps_display_name_to_product_name(self):
        """display_name should map to product_name for save_deal()."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="ESPN Sports PMP",
            seller_url="https://espn.seller.example.com",
        )
        result = create_manual_deal(entry)

        assert result.deal_data["product_name"] == "ESPN Sports PMP"

    def test_deal_data_includes_optional_fields_when_provided(self):
        """Optional fields should be included in deal_data when provided."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="Full Deal",
            seller_url="https://seller.example.com",
            seller_deal_id="SELLER-123",
            price=15.50,
            impressions=1_000_000,
            flight_start="2026-04-01",
            flight_end="2026-06-30",
        )
        result = create_manual_deal(entry)

        data = result.deal_data
        assert data["seller_deal_id"] == "SELLER-123"
        assert data["price"] == 15.50
        assert data["impressions"] == 1_000_000
        assert data["flight_start"] == "2026-04-01"
        assert data["flight_end"] == "2026-06-30"

    def test_deal_data_includes_v2_fields_in_metadata(self):
        """V2 deal library fields should be included in deal_data metadata JSON."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="V2 Deal",
            seller_url="https://seller.example.com",
            seller_org="NBCUniversal",
            seller_domain="nbcuniversal.com",
            seller_type="PUBLISHER",
            buyer_org="MediaCo",
            buyer_id="buyer-001",
            price_model="CPM",
            fixed_price_cpm=15.50,
            bid_floor_cpm=12.00,
            currency="EUR",
            media_type="CTV",
            description="Premium CTV",
        )
        result = create_manual_deal(entry)

        data = result.deal_data
        # V2 fields should be present as top-level metadata JSON keys
        metadata = json.loads(data["metadata"])
        assert metadata["display_name"] == "V2 Deal"
        assert metadata["seller_org"] == "NBCUniversal"
        assert metadata["seller_domain"] == "nbcuniversal.com"
        assert metadata["seller_type"] == "PUBLISHER"
        assert metadata["buyer_org"] == "MediaCo"
        assert metadata["buyer_id"] == "buyer-001"
        assert metadata["price_model"] == "CPM"
        assert metadata["fixed_price_cpm"] == 15.50
        assert metadata["bid_floor_cpm"] == 12.00
        assert metadata["currency"] == "EUR"
        assert metadata["media_type"] == "CTV"
        assert metadata["description"] == "Premium CTV"


# ---------------------------------------------------------------------------
# Metadata extraction tests
# ---------------------------------------------------------------------------


class TestMetadataExtraction:
    """Tests that portfolio metadata is correctly extracted."""

    def test_metadata_import_source_is_manual(self):
        """metadata.import_source should always be 'MANUAL'."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="Test Deal",
            seller_url="https://seller.example.com",
        )
        result = create_manual_deal(entry)

        assert result.metadata["import_source"] == "MANUAL"

    def test_metadata_includes_tags(self):
        """metadata should include tags when provided."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="Test Deal",
            seller_url="https://seller.example.com",
            tags=["premium", "sports"],
        )
        result = create_manual_deal(entry)

        assert result.metadata["tags"] == ["premium", "sports"]

    def test_metadata_includes_advertiser_id(self):
        """metadata should include advertiser_id when provided."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="Test Deal",
            seller_url="https://seller.example.com",
            advertiser_id="adv-001",
        )
        result = create_manual_deal(entry)

        assert result.metadata["advertiser_id"] == "adv-001"

    def test_metadata_tags_none_when_not_provided(self):
        """metadata.tags should be None when not provided."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="Test Deal",
            seller_url="https://seller.example.com",
        )
        result = create_manual_deal(entry)

        assert result.metadata["tags"] is None

    def test_metadata_advertiser_id_none_when_not_provided(self):
        """metadata.advertiser_id should be None when not provided."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="Test Deal",
            seller_url="https://seller.example.com",
        )
        result = create_manual_deal(entry)

        assert result.metadata["advertiser_id"] is None


# ---------------------------------------------------------------------------
# CrewAI tool wrapper tests
# ---------------------------------------------------------------------------


class TestManualDealEntryTool:
    """Tests for the CrewAI ManualDealEntryTool wrapper."""

    def test_tool_has_correct_name(self):
        """Tool should have the expected name."""
        from ad_buyer.tools.deal_jockey.deal_entry import ManualDealEntryTool

        tool = ManualDealEntryTool()
        assert tool.name == "manual_deal_entry"

    def test_tool_has_description(self):
        """Tool should have a non-empty description."""
        from ad_buyer.tools.deal_jockey.deal_entry import ManualDealEntryTool

        tool = ManualDealEntryTool()
        assert len(tool.description) > 0

    def test_tool_accepts_json_string(self):
        """Tool should accept a JSON string of deal parameters."""
        from ad_buyer.tools.deal_jockey.deal_entry import ManualDealEntryTool

        tool = ManualDealEntryTool()
        result = tool._run(
            deal_params=json.dumps({
                "display_name": "ESPN Sports PMP",
                "seller_url": "https://espn.seller.example.com",
            })
        )

        # Should return a string with success indication
        assert "ESPN Sports PMP" in result
        assert "success" in result.lower() or "created" in result.lower()

    def test_tool_returns_errors_for_invalid_input(self):
        """Tool should return validation errors for invalid input."""
        from ad_buyer.tools.deal_jockey.deal_entry import ManualDealEntryTool

        tool = ManualDealEntryTool()
        result = tool._run(
            deal_params=json.dumps({
                "display_name": "",
                "seller_url": "https://seller.example.com",
                "deal_type": "INVALID",
            })
        )

        assert "error" in result.lower() or "invalid" in result.lower()

    def test_tool_returns_errors_for_malformed_json(self):
        """Tool should handle malformed JSON gracefully."""
        from ad_buyer.tools.deal_jockey.deal_entry import ManualDealEntryTool

        tool = ManualDealEntryTool()
        result = tool._run(deal_params="not valid json {{{")

        assert "error" in result.lower()

    def test_tool_returns_errors_for_missing_required_fields(self):
        """Tool should return errors when required fields are missing."""
        from ad_buyer.tools.deal_jockey.deal_entry import ManualDealEntryTool

        tool = ManualDealEntryTool()
        result = tool._run(
            deal_params=json.dumps({
                "deal_type": "PD",
            })
        )

        assert "error" in result.lower()

    def test_tool_has_args_schema(self):
        """Tool should have a Pydantic args_schema for CrewAI."""
        from ad_buyer.tools.deal_jockey.deal_entry import ManualDealEntryTool

        tool = ManualDealEntryTool()
        assert hasattr(tool, "args_schema")
        assert tool.args_schema is not None


# ---------------------------------------------------------------------------
# DealEntryResult dataclass tests
# ---------------------------------------------------------------------------


class TestDealEntryResult:
    """Tests for the DealEntryResult output dataclass."""

    def test_success_result_structure(self):
        """Successful result should have all expected fields populated."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="Test Deal",
            seller_url="https://seller.example.com",
        )
        result = create_manual_deal(entry)

        assert result.success is True
        assert isinstance(result.deal_data, dict)
        assert isinstance(result.metadata, dict)
        assert isinstance(result.errors, list)
        assert len(result.errors) == 0

    def test_failure_result_structure(self):
        """Failed result should have None for data fields and populated errors."""
        from ad_buyer.tools.deal_jockey.deal_entry import (
            ManualDealEntry,
            create_manual_deal,
        )

        entry = ManualDealEntry(
            display_name="",
            seller_url="https://seller.example.com",
        )
        result = create_manual_deal(entry)

        assert result.success is False
        assert result.deal_data is None
        assert result.metadata is None
        assert len(result.errors) > 0
