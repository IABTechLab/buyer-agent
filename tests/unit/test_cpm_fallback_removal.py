# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for CPM hallucination fix — Layer 2a: remove hardcoded price fallbacks.


These tests verify that the buyer agent no longer fabricates pricing
when sellers have not provided it. Each test targets a specific
fallback that was previously hardcoded in the codebase.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ad_buyer.booking.quote_flow import QuoteFlowClient
from ad_buyer.models.buyer_identity import (
    BuyerContext,
    BuyerIdentity,
)
from ad_buyer.tools.buyer_deals import DiscoverInventoryTool, RequestDealTool

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agency_identity():
    """Agency-tier identity for testing."""
    return BuyerIdentity(
        seat_id="ttd-seat-100",
        agency_id="omnicom-200",
        agency_name="OMD",
    )


@pytest.fixture
def agency_context(agency_identity):
    """Agency buyer context."""
    return BuyerContext(identity=agency_identity, is_authenticated=True)


@pytest.fixture
def mock_client():
    """Mock UnifiedClient."""
    client = MagicMock()
    client.get_product = AsyncMock()
    client.search_products = AsyncMock()
    client.list_products = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# 1. request_deal.py — no $20 fallback
# ---------------------------------------------------------------------------


class TestRequestDealNoFallback:
    """request_deal must return an error string when no pricing is available,
    not silently use $20.00 as a fallback CPM."""

    @pytest.mark.asyncio
    async def test_no_base_price_returns_error(self, mock_client, agency_context):
        """Product with no basePrice or price should return an error string."""
        product_no_price = {
            "id": "prod-001",
            "name": "Premium CTV",
            "channel": "ctv",
            # No basePrice, no price
        }
        mock_client.get_product.return_value = MagicMock(success=True, data=product_no_price)

        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="prod-001")

        # Must return an error string, not a deal with $20 CPM
        assert isinstance(result, str)
        assert "error" in result.lower() or "pricing" in result.lower()
        assert "$20" not in result
        assert "DEAL CREATED" not in result

    @pytest.mark.asyncio
    async def test_non_numeric_base_price_returns_error(self, mock_client, agency_context):
        """Product with non-numeric basePrice should return an error string,
        not silently fall back to $20."""
        product_bad_price = {
            "id": "prod-002",
            "name": "Premium Display",
            "basePrice": "contact_sales",
        }
        mock_client.get_product.return_value = MagicMock(success=True, data=product_bad_price)

        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="prod-002")

        assert isinstance(result, str)
        assert "error" in result.lower() or "pricing" in result.lower()
        assert "$20" not in result
        assert "DEAL CREATED" not in result

    @pytest.mark.asyncio
    async def test_null_base_price_returns_error(self, mock_client, agency_context):
        """Product with basePrice=None should return an error string."""
        product_null_price = {
            "id": "prod-003",
            "name": "Premium Audio",
            "basePrice": None,
        }
        mock_client.get_product.return_value = MagicMock(success=True, data=product_null_price)

        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="prod-003")

        assert isinstance(result, str)
        assert "error" in result.lower() or "pricing" in result.lower()
        assert "DEAL CREATED" not in result

    @pytest.mark.asyncio
    async def test_valid_price_still_works(self, mock_client, agency_context):
        """Product with a valid basePrice should still create a deal normally."""
        product_with_price = {
            "id": "prod-004",
            "name": "Premium Display",
            "basePrice": 25.0,
        }
        mock_client.get_product.return_value = MagicMock(success=True, data=product_with_price)

        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="prod-004")

        assert "DEAL CREATED" in result


# ---------------------------------------------------------------------------
# 2. discover_inventory.py — no 0 fallback
# ---------------------------------------------------------------------------


class TestDiscoverInventoryNoFallback:
    """discover_inventory must show None/unavailable price when no
    basePrice exists, not silently default to 0."""

    @pytest.mark.asyncio
    async def test_no_base_price_shows_unavailable(self, mock_client, agency_context):
        """Product with no basePrice should show pricing as unavailable."""
        product_no_price = {
            "id": "prod-001",
            "name": "Premium CTV",
            "channel": "ctv",
            "availableImpressions": 5_000_000,
            # No basePrice, no price
        }
        mock_client.list_products.return_value = MagicMock(success=True, data=[product_no_price])

        tool = DiscoverInventoryTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun()

        # Must NOT show $0.00 as the price
        assert "$0.00" not in result
        # Should indicate pricing is unavailable
        assert "unavailable" in result.lower() or "request" in result.lower() or "N/A" in result

    @pytest.mark.asyncio
    async def test_null_base_price_shows_unavailable(self, mock_client, agency_context):
        """Product with basePrice=None should show pricing as unavailable."""
        product_null_price = {
            "id": "prod-002",
            "name": "Premium Display",
            "basePrice": None,
            "channel": "display",
            "availableImpressions": 3_000_000,
        }
        mock_client.list_products.return_value = MagicMock(success=True, data=[product_null_price])

        tool = DiscoverInventoryTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun()

        assert "$0.00" not in result

    @pytest.mark.asyncio
    async def test_valid_price_still_displays(self, mock_client, agency_context):
        """Product with valid basePrice should still show the price normally."""
        product_with_price = {
            "id": "prod-003",
            "name": "Premium Display",
            "basePrice": 20.0,
            "channel": "display",
            "availableImpressions": 5_000_000,
        }
        mock_client.list_products.return_value = MagicMock(success=True, data=[product_with_price])

        tool = DiscoverInventoryTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun()

        # Should show the actual price
        assert "$" in result


# ---------------------------------------------------------------------------
# 3. quote_flow.py — no 0 fallback
# ---------------------------------------------------------------------------


class TestQuoteFlowNoFallback:
    """quote_flow.get_pricing must return an error/unavailable indicator
    when no basePrice exists, not silently default to 0."""

    def test_no_base_price_returns_unavailable(self, agency_context):
        """Product with no basePrice should return pricing_source=unavailable."""
        client = QuoteFlowClient(
            buyer_context=agency_context,
            seller_base_url="http://localhost:5000",
        )

        product_no_price = {
            "id": "prod-001",
            "name": "Premium CTV",
        }

        result = client.get_pricing(product_no_price)

        # Must NOT return a PricingResult with base_price=0
        # Should indicate pricing is unavailable
        assert result is None or (
            hasattr(result, "pricing_source") and result.pricing_source == "unavailable"
        )

    def test_null_base_price_returns_unavailable(self, agency_context):
        """Product with basePrice=None should return unavailable."""
        client = QuoteFlowClient(
            buyer_context=agency_context,
            seller_base_url="http://localhost:5000",
        )

        product_null_price = {
            "id": "prod-002",
            "name": "Premium Display",
            "basePrice": None,
        }

        result = client.get_pricing(product_null_price)

        assert result is None or (
            hasattr(result, "pricing_source") and result.pricing_source == "unavailable"
        )

    def test_non_numeric_base_price_returns_unavailable(self, agency_context):
        """Product with non-numeric basePrice should return unavailable."""
        client = QuoteFlowClient(
            buyer_context=agency_context,
            seller_base_url="http://localhost:5000",
        )

        product_bad_price = {
            "id": "prod-003",
            "name": "Premium Audio",
            "basePrice": "contact_sales",
        }

        result = client.get_pricing(product_bad_price)

        assert result is None or (
            hasattr(result, "pricing_source") and result.pricing_source == "unavailable"
        )

    def test_valid_price_returns_pricing_result(self, agency_context):
        """Product with valid basePrice should return normal PricingResult."""
        client = QuoteFlowClient(
            buyer_context=agency_context,
            seller_base_url="http://localhost:5000",
        )

        product_with_price = {
            "id": "prod-004",
            "name": "Premium Display",
            "basePrice": 20.0,
        }

        result = client.get_pricing(product_with_price)

        # Should return a valid PricingResult with the actual price
        assert result is not None
        assert result.base_price == 20.0

    def test_build_deal_data_no_price_returns_error(self, agency_context):
        """build_deal_data with no pricing should return error indicator."""
        client = QuoteFlowClient(
            buyer_context=agency_context,
            seller_base_url="http://localhost:5000",
        )

        product_no_price = {
            "id": "prod-001",
            "name": "Premium CTV",
        }

        result = client.build_deal_data(product_no_price)

        # Should indicate pricing is unavailable, not create a deal with $0
        assert result is None or result.get("pricing_source") == "unavailable"


# ---------------------------------------------------------------------------
# 4. canonical booking path — no fabricated impressions
# ---------------------------------------------------------------------------
# The retired CampaignPipeline._estimate_impressions (and its assumed_cpm
# fallback) was deleted with the pipeline. On the canonical
# path, impression counts flow from the approved recommendation into
# DealParams verbatim — asserted by
# tests/unit/test_deal_booking_flow.py::TestApprovalAndBooking::
# test_handoff_passes_binding_approved_terms — so there is no estimation
# step left that could fabricate impressions from a made-up CPM.
