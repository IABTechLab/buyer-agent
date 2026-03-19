# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for quote-then-book flow module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ad_buyer.booking.quote_flow import QuoteFlowClient
from ad_buyer.models.buyer_identity import (
    AccessTier,
    BuyerContext,
    BuyerIdentity,
    DealType,
)


@pytest.fixture
def agency_identity():
    """Create an agency-tier buyer identity."""
    return BuyerIdentity(
        seat_id="ttd-seat-001",
        agency_id="omnicom-456",
        agency_name="OMD",
    )


@pytest.fixture
def agency_context(agency_identity):
    """Create agency buyer context."""
    return BuyerContext(
        identity=agency_identity,
        is_authenticated=True,
    )


class TestQuoteFlowClient:
    """Test the QuoteFlowClient module."""

    def test_instantiation(self, agency_context):
        """QuoteFlowClient can be instantiated with a BuyerContext."""
        client = QuoteFlowClient(
            buyer_context=agency_context,
            seller_base_url="http://localhost:5000",
        )
        assert client is not None

    def test_get_pricing_uses_pricing_calculator(self, agency_context):
        """get_pricing returns a PricingResult using the PricingCalculator."""
        client = QuoteFlowClient(
            buyer_context=agency_context,
            seller_base_url="http://localhost:5000",
        )

        product = {
            "id": "prod-001",
            "name": "Premium Display",
            "basePrice": 20.0,
        }

        result = client.get_pricing(product, volume=5_000_000)
        # Agency tier: 10% discount -> 18.0, then 5% volume discount -> 17.1
        assert result.tiered_price == 18.0
        assert result.volume_discount == 5.0
        assert result.final_price == pytest.approx(17.1)

    def test_get_pricing_with_negotiation(self, agency_context):
        """get_pricing supports negotiation via target_cpm."""
        client = QuoteFlowClient(
            buyer_context=agency_context,
            seller_base_url="http://localhost:5000",
        )

        product = {
            "id": "prod-001",
            "name": "Premium Display",
            "basePrice": 20.0,
            "negotiation_enabled": True,
        }

        result = client.get_pricing(product, target_cpm=17.0)
        # Agency can negotiate, product allows, 17.0 >= floor (16.2)
        assert result.final_price == 17.0

    def test_build_deal_data(self, agency_context):
        """build_deal_data creates a deal data dict from pricing and product."""
        client = QuoteFlowClient(
            buyer_context=agency_context,
            seller_base_url="http://localhost:5000",
        )

        product = {
            "id": "prod-001",
            "name": "Premium Display",
            "basePrice": 20.0,
        }

        deal_data = client.build_deal_data(
            product=product,
            deal_type="PD",
            impressions=1_000_000,
            flight_start="2026-04-01",
            flight_end="2026-04-30",
        )

        assert deal_data["product_id"] == "prod-001"
        assert deal_data["product_name"] == "Premium Display"
        assert deal_data["deal_type"] == "PD"
        assert deal_data["impressions"] == 1_000_000
        assert deal_data["flight_start"] == "2026-04-01"
        assert deal_data["flight_end"] == "2026-04-30"
        assert "deal_id" in deal_data
        assert deal_data["deal_id"].startswith("DEAL-")
        assert "price" in deal_data
        assert "original_price" in deal_data
        assert "access_tier" in deal_data
        assert "activation_instructions" in deal_data

    def test_build_deal_data_default_flight_dates(self, agency_context):
        """build_deal_data uses default dates when none provided."""
        client = QuoteFlowClient(
            buyer_context=agency_context,
            seller_base_url="http://localhost:5000",
        )

        product = {
            "id": "prod-001",
            "name": "Premium Display",
            "basePrice": 20.0,
        }

        deal_data = client.build_deal_data(
            product=product,
            deal_type="PD",
        )

        assert deal_data["flight_start"] is not None
        assert deal_data["flight_end"] is not None

    def test_activation_instructions_contain_deal_id(self, agency_context):
        """Activation instructions reference the generated deal ID."""
        client = QuoteFlowClient(
            buyer_context=agency_context,
            seller_base_url="http://localhost:5000",
        )

        product = {
            "id": "prod-001",
            "name": "Premium Display",
            "basePrice": 20.0,
        }

        deal_data = client.build_deal_data(
            product=product,
            deal_type="PD",
        )

        deal_id = deal_data["deal_id"]
        instructions = deal_data["activation_instructions"]
        assert deal_id in instructions["ttd"]
        assert deal_id in instructions["dv360"]
        assert deal_id in instructions["amazon"]
        assert deal_id in instructions["xandr"]
        assert deal_id in instructions["yahoo"]
