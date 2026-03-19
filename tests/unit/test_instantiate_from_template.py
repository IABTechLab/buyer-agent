# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for InstantiateDealFromTemplateTool.

Tests cover:
- Successful deal instantiation from a template
- Rejection when max_cpm < seller floor price
- Template not found error
- Override application (pricing, targeting, flight_dates)
- Event emission (deal.template_created)
- Deal stored in portfolio with metadata
- Advertiser ID propagation
"""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ad_buyer.storage import DealStore
from ad_buyer.tools.deal_jockey.instantiate_from_template import (
    InstantiateDealFromTemplateTool,
    TemplateInstantiationResult,
    instantiate_deal_from_template,
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
def deal_store_with_template(deal_store):
    """DealStore with a pre-existing deal template."""
    deal_store.save_deal_template(
        template_id="tmpl-001",
        name="Sports PG Template",
        deal_type_pref="PG",
        inventory_types=json.dumps(["DIGITAL", "CTV"]),
        preferred_publishers=json.dumps(["espn.com", "nfl.com"]),
        max_cpm=25.00,
        min_impressions=500_000,
        default_flight_days=30,
        targeting_defaults=json.dumps({"geo": ["US"], "audience": ["sports"]}),
        supply_path_prefs=json.dumps({"max_hops": 2}),
        advertiser_id="adv-100",
        agency_id="agency-001",
    )
    return deal_store


@pytest.fixture
def tool(deal_store_with_template):
    """Create an InstantiateDealFromTemplateTool with a populated store."""
    return InstantiateDealFromTemplateTool(deal_store=deal_store_with_template)


# Mock the seller API call -- returns a successful deal response
def _mock_seller_success(seller_url, template_id, buyer_params):
    """Simulate a successful seller response."""
    return {
        "success": True,
        "deal": {
            "seller_deal_id": "seller-deal-abc",
            "deal_type": buyer_params.get("deal_type", "PG"),
            "price": buyer_params.get("max_cpm", 25.00),
            "impressions": buyer_params.get("impressions", 500_000),
            "flight_start": buyer_params.get("flight_start"),
            "flight_end": buyer_params.get("flight_end"),
            "product_id": f"prod-from-{template_id}",
            "product_name": "Sports Premium Inventory",
            "seller_url": seller_url,
        },
    }


def _mock_seller_rejection(seller_url, template_id, buyer_params):
    """Simulate seller rejection because max_cpm < seller floor."""
    return {
        "success": False,
        "rejected": True,
        "reason": "max_cpm below floor price",
        "seller_floor_cpm": 30.00,
        "buyer_max_cpm": buyer_params.get("max_cpm", 25.00),
    }


def _mock_seller_error(seller_url, template_id, buyer_params):
    """Simulate a seller API error."""
    raise ConnectionError("Seller API unreachable")


# -----------------------------------------------------------------------
# Unit tests: instantiate_deal_from_template function
# -----------------------------------------------------------------------


class TestInstantiateDealFromTemplate:
    """Tests for the core instantiation function."""

    def test_successful_instantiation(self, deal_store_with_template):
        """A valid template_id with passing max_cpm creates a deal."""
        result = instantiate_deal_from_template(
            deal_store=deal_store_with_template,
            template_id="tmpl-001",
            advertiser_id="adv-100",
            seller_url="http://seller.example.com",
            seller_api_fn=_mock_seller_success,
        )

        assert result.success is True
        assert result.deal_id is not None
        assert result.rejection is None
        assert result.errors == []

    def test_deal_stored_in_portfolio(self, deal_store_with_template):
        """Successful instantiation stores the deal in the deal store."""
        result = instantiate_deal_from_template(
            deal_store=deal_store_with_template,
            template_id="tmpl-001",
            advertiser_id="adv-100",
            seller_url="http://seller.example.com",
            seller_api_fn=_mock_seller_success,
        )

        assert result.success is True
        deal = deal_store_with_template.get_deal(result.deal_id)
        assert deal is not None
        assert deal["deal_type"] == "PG"
        assert deal["status"] == "draft"

    def test_deal_record_returned(self, deal_store_with_template):
        """Successful instantiation returns a deal record dict."""
        result = instantiate_deal_from_template(
            deal_store=deal_store_with_template,
            template_id="tmpl-001",
            advertiser_id="adv-100",
            seller_url="http://seller.example.com",
            seller_api_fn=_mock_seller_success,
        )

        assert result.success is True
        assert result.deal_record is not None
        assert "deal_id" in result.deal_record
        assert result.deal_record["deal_type"] == "PG"
        assert result.deal_record["template_id"] == "tmpl-001"

    def test_rejection_when_below_floor(self, deal_store_with_template):
        """When seller rejects due to floor price, result includes rejection details."""
        result = instantiate_deal_from_template(
            deal_store=deal_store_with_template,
            template_id="tmpl-001",
            advertiser_id="adv-100",
            seller_url="http://seller.example.com",
            seller_api_fn=_mock_seller_rejection,
        )

        assert result.success is False
        assert result.deal_id is None
        assert result.rejection is not None
        assert result.rejection["seller_floor_cpm"] == 30.00

    def test_template_not_found(self, deal_store_with_template):
        """Non-existent template_id returns an error."""
        result = instantiate_deal_from_template(
            deal_store=deal_store_with_template,
            template_id="tmpl-nonexistent",
            advertiser_id="adv-100",
            seller_url="http://seller.example.com",
            seller_api_fn=_mock_seller_success,
        )

        assert result.success is False
        assert len(result.errors) > 0
        assert "not found" in result.errors[0].lower()

    def test_override_pricing(self, deal_store_with_template):
        """Pricing overrides are applied before sending to seller."""
        result = instantiate_deal_from_template(
            deal_store=deal_store_with_template,
            template_id="tmpl-001",
            advertiser_id="adv-100",
            seller_url="http://seller.example.com",
            seller_api_fn=_mock_seller_success,
            overrides={"max_cpm": 30.00},
        )

        assert result.success is True
        # The deal should have used the overridden max_cpm
        assert result.deal_record is not None
        assert result.deal_record["price"] == 30.00

    def test_override_flight_dates(self, deal_store_with_template):
        """Flight date overrides are applied to the deal."""
        result = instantiate_deal_from_template(
            deal_store=deal_store_with_template,
            template_id="tmpl-001",
            advertiser_id="adv-100",
            seller_url="http://seller.example.com",
            seller_api_fn=_mock_seller_success,
            overrides={
                "flight_start": "2026-05-01",
                "flight_end": "2026-06-01",
            },
        )

        assert result.success is True
        deal = deal_store_with_template.get_deal(result.deal_id)
        assert deal is not None
        assert deal["flight_start"] == "2026-05-01"
        assert deal["flight_end"] == "2026-06-01"

    def test_override_targeting(self, deal_store_with_template):
        """Targeting overrides are merged with template defaults."""
        result = instantiate_deal_from_template(
            deal_store=deal_store_with_template,
            template_id="tmpl-001",
            advertiser_id="adv-100",
            seller_url="http://seller.example.com",
            seller_api_fn=_mock_seller_success,
            overrides={
                "targeting": {"geo": ["US", "CA"]},
            },
        )

        assert result.success is True
        assert result.deal_record is not None

    def test_event_emitted(self, deal_store_with_template):
        """Successful instantiation emits a deal.template_created event."""
        result = instantiate_deal_from_template(
            deal_store=deal_store_with_template,
            template_id="tmpl-001",
            advertiser_id="adv-100",
            seller_url="http://seller.example.com",
            seller_api_fn=_mock_seller_success,
        )

        assert result.success is True
        assert result.event_emitted is True

    def test_portfolio_metadata_stored(self, deal_store_with_template):
        """Successful instantiation stores portfolio metadata."""
        result = instantiate_deal_from_template(
            deal_store=deal_store_with_template,
            template_id="tmpl-001",
            advertiser_id="adv-100",
            seller_url="http://seller.example.com",
            seller_api_fn=_mock_seller_success,
        )

        assert result.success is True
        meta = deal_store_with_template.get_portfolio_metadata(result.deal_id)
        assert meta is not None
        assert meta["advertiser_id"] == "adv-100"
        assert meta["import_source"] == "TEMPLATE"

    def test_seller_api_error_handled(self, deal_store_with_template):
        """Seller API errors are caught and returned as errors."""
        result = instantiate_deal_from_template(
            deal_store=deal_store_with_template,
            template_id="tmpl-001",
            advertiser_id="adv-100",
            seller_url="http://seller.example.com",
            seller_api_fn=_mock_seller_error,
        )

        assert result.success is False
        assert len(result.errors) > 0

    def test_default_flight_dates_from_template(self, deal_store_with_template):
        """When no flight date overrides, template default_flight_days are used."""
        result = instantiate_deal_from_template(
            deal_store=deal_store_with_template,
            template_id="tmpl-001",
            advertiser_id="adv-100",
            seller_url="http://seller.example.com",
            seller_api_fn=_mock_seller_success,
        )

        assert result.success is True
        deal = deal_store_with_template.get_deal(result.deal_id)
        assert deal is not None
        assert deal["flight_start"] is not None
        assert deal["flight_end"] is not None


# -----------------------------------------------------------------------
# CrewAI tool wrapper tests
# -----------------------------------------------------------------------


class TestInstantiateDealFromTemplateTool:
    """Tests for the CrewAI tool wrapper."""

    def test_tool_name_and_description(self, tool):
        """Tool has correct name and description."""
        assert tool.name == "instantiate_deal_from_template"
        assert "template" in tool.description.lower()

    def test_tool_successful_run(self, tool):
        """Tool _run with valid params returns success message."""
        params = json.dumps({
            "template_id": "tmpl-001",
            "advertiser_id": "adv-100",
            "seller_url": "http://seller.example.com",
        })

        with patch(
            "ad_buyer.tools.deal_jockey.instantiate_from_template._call_seller_template_api",
            side_effect=_mock_seller_success,
        ):
            result = tool._run(params_json=params)

        assert "success" in result.lower() or "created" in result.lower()

    def test_tool_template_not_found(self, tool):
        """Tool _run with invalid template_id returns error."""
        params = json.dumps({
            "template_id": "tmpl-missing",
            "advertiser_id": "adv-100",
            "seller_url": "http://seller.example.com",
        })

        result = tool._run(params_json=params)
        assert "not found" in result.lower() or "error" in result.lower()

    def test_tool_invalid_json(self, tool):
        """Tool _run with invalid JSON returns error."""
        result = tool._run(params_json="not-json")
        assert "error" in result.lower()

    def test_tool_rejection_message(self, tool):
        """Tool _run returns rejection details when seller rejects."""
        params = json.dumps({
            "template_id": "tmpl-001",
            "advertiser_id": "adv-100",
            "seller_url": "http://seller.example.com",
        })

        with patch(
            "ad_buyer.tools.deal_jockey.instantiate_from_template._call_seller_template_api",
            side_effect=_mock_seller_rejection,
        ):
            result = tool._run(params_json=params)

        assert "reject" in result.lower() or "floor" in result.lower()

    def test_tool_with_overrides(self, tool):
        """Tool _run with overrides applies them."""
        params = json.dumps({
            "template_id": "tmpl-001",
            "advertiser_id": "adv-100",
            "seller_url": "http://seller.example.com",
            "overrides": {
                "max_cpm": 30.00,
                "flight_start": "2026-05-01",
                "flight_end": "2026-06-01",
            },
        })

        with patch(
            "ad_buyer.tools.deal_jockey.instantiate_from_template._call_seller_template_api",
            side_effect=_mock_seller_success,
        ):
            result = tool._run(params_json=params)

        assert "success" in result.lower() or "created" in result.lower()
