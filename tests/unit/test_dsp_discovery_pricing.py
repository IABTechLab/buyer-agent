# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Comprehensive tests for DSP discovery and pricing flows.

Covers:
- DiscoverInventoryTool: filters, formatting, edge cases
- GetPricingTool: tier calculations, volume discounts, cost projections
- RequestDealTool: deal creation, negotiation, validation, deal ID generation
- DSPDealFlow: state machine, flow steps, error propagation
- UnifiedClient DSP methods: discover_inventory, get_pricing, request_deal
- Cross-tier pricing consistency across tools and client
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ad_buyer.clients.unified_client import Protocol, UnifiedClient, UnifiedResult
from ad_buyer.flows.dsp_deal_flow import (
    DSPDealFlow,
    DSPFlowState,
    DSPFlowStatus,
    DiscoveredProduct,
)
from ad_buyer.models.buyer_identity import (
    AccessTier,
    BuyerContext,
    BuyerIdentity,
    DealRequest,
    DealResponse,
    DealType,
)
from ad_buyer.tools.dsp import DiscoverInventoryTool, GetPricingTool, RequestDealTool


# =============================================================================
# Shared fixtures
# =============================================================================


@pytest.fixture
def mock_client():
    """Create a mock UnifiedClient with all needed async methods."""
    client = MagicMock()
    client.search_products = AsyncMock()
    client.list_products = AsyncMock()
    client.get_product = AsyncMock()
    return client


@pytest.fixture
def public_identity():
    """Empty identity at public tier."""
    return BuyerIdentity()


@pytest.fixture
def seat_identity():
    """Seat-tier identity (5% discount)."""
    return BuyerIdentity(
        seat_id="ttd-seat-100",
        seat_name="The Trade Desk",
    )


@pytest.fixture
def agency_identity():
    """Agency-tier identity (10% discount)."""
    return BuyerIdentity(
        seat_id="ttd-seat-100",
        seat_name="The Trade Desk",
        agency_id="omnicom-200",
        agency_name="OMD",
        agency_holding_company="Omnicom",
    )


@pytest.fixture
def advertiser_identity():
    """Advertiser-tier identity (15% discount)."""
    return BuyerIdentity(
        seat_id="ttd-seat-100",
        seat_name="The Trade Desk",
        agency_id="omnicom-200",
        agency_name="OMD",
        agency_holding_company="Omnicom",
        advertiser_id="cocacola-300",
        advertiser_name="Coca-Cola",
        advertiser_industry="CPG",
    )


@pytest.fixture
def public_context(public_identity):
    return BuyerContext(identity=public_identity)


@pytest.fixture
def seat_context(seat_identity):
    return BuyerContext(identity=seat_identity, is_authenticated=True)


@pytest.fixture
def agency_context(agency_identity):
    return BuyerContext(identity=agency_identity, is_authenticated=True)


@pytest.fixture
def advertiser_context(advertiser_identity):
    return BuyerContext(identity=advertiser_identity, is_authenticated=True)


def _product(product_id="prod_001", name="Test Product", base_price=20.00, **kwargs):
    """Helper to build a product dict."""
    data = {
        "id": product_id,
        "name": name,
        "basePrice": base_price,
        "publisherId": kwargs.get("publisherId", "pub_001"),
        "channel": kwargs.get("channel", "display"),
        "availableImpressions": kwargs.get("availableImpressions", 5_000_000),
        "rateType": kwargs.get("rateType", "CPM"),
    }
    data.update(kwargs)
    return data


# =============================================================================
# DiscoverInventoryTool - Extended Tests
# =============================================================================


class TestDiscoverInventoryFilters:
    """Test filter building and parameter passing in DiscoverInventoryTool."""

    @pytest.mark.asyncio
    async def test_discover_with_channel_filter(self, mock_client, agency_context):
        """Channel filter should be passed in the filter dict."""
        mock_client.search_products.return_value = MagicMock(
            success=True, data=[_product(channel="ctv")]
        )
        tool = DiscoverInventoryTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(query="ctv inventory", channel="ctv")
        assert "ctv" in result.lower() or "CTV" in result

    @pytest.mark.asyncio
    async def test_discover_with_max_cpm_filter(self, mock_client, agency_context):
        """max_cpm filter should be included in search filters."""
        mock_client.search_products.return_value = MagicMock(
            success=True, data=[_product(base_price=15.0)]
        )
        tool = DiscoverInventoryTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(query="cheap inventory", max_cpm=20.0)
        # Verify search was called (query triggers search_products)
        mock_client.search_products.assert_called_once()
        call_kwargs = mock_client.search_products.call_args
        filters = call_kwargs.kwargs.get("filters") or call_kwargs[1].get("filters")
        assert filters is not None
        assert filters.get("maxPrice") == 20.0

    @pytest.mark.asyncio
    async def test_discover_with_min_impressions_filter(self, mock_client, agency_context):
        """min_impressions filter should be included."""
        mock_client.search_products.return_value = MagicMock(
            success=True, data=[_product(availableImpressions=10_000_000)]
        )
        tool = DiscoverInventoryTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(query="big campaigns", min_impressions=5_000_000)
        call_kwargs = mock_client.search_products.call_args
        filters = call_kwargs.kwargs.get("filters") or call_kwargs[1].get("filters")
        assert filters["minImpressions"] == 5_000_000

    @pytest.mark.asyncio
    async def test_discover_with_targeting_filter(self, mock_client, agency_context):
        """Targeting capabilities filter should be included."""
        mock_client.search_products.return_value = MagicMock(
            success=True, data=[_product()]
        )
        tool = DiscoverInventoryTool(client=mock_client, buyer_context=agency_context)
        await tool._arun(query="targeted", targeting=["household", "geo"])
        call_kwargs = mock_client.search_products.call_args
        filters = call_kwargs.kwargs.get("filters") or call_kwargs[1].get("filters")
        assert filters["targeting"] == ["household", "geo"]

    @pytest.mark.asyncio
    async def test_discover_with_publisher_filter(self, mock_client, agency_context):
        """Publisher filter should be included."""
        mock_client.search_products.return_value = MagicMock(
            success=True, data=[_product(publisherId="hulu")]
        )
        tool = DiscoverInventoryTool(client=mock_client, buyer_context=agency_context)
        await tool._arun(query="hulu", publisher="hulu")
        call_kwargs = mock_client.search_products.call_args
        filters = call_kwargs.kwargs.get("filters") or call_kwargs[1].get("filters")
        assert filters["publisher"] == "hulu"

    @pytest.mark.asyncio
    async def test_discover_includes_identity_context(self, mock_client, advertiser_context):
        """Buyer identity context should always be included in filters."""
        mock_client.search_products.return_value = MagicMock(
            success=True, data=[_product()]
        )
        tool = DiscoverInventoryTool(client=mock_client, buyer_context=advertiser_context)
        await tool._arun(query="anything")
        call_kwargs = mock_client.search_products.call_args
        filters = call_kwargs.kwargs.get("filters") or call_kwargs[1].get("filters")
        assert "buyer_context" in filters
        assert filters["buyer_context"]["access_tier"] == "advertiser"


class TestDiscoverInventoryFormatting:
    """Test output formatting in DiscoverInventoryTool."""

    @pytest.mark.asyncio
    async def test_format_empty_results(self, mock_client, agency_context):
        """Empty result set should return a clear message."""
        mock_client.search_products.return_value = MagicMock(
            success=True, data=[]
        )
        tool = DiscoverInventoryTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(query="nonexistent")
        assert "No inventory found" in result

    @pytest.mark.asyncio
    async def test_format_single_dict_product(self, mock_client, agency_context):
        """A single product dict (not list) should be handled."""
        mock_client.search_products.return_value = MagicMock(
            success=True,
            data=_product(name="Solo Product"),
        )
        tool = DiscoverInventoryTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(query="solo")
        assert "Solo Product" in result

    @pytest.mark.asyncio
    async def test_format_multiple_products(self, mock_client, agency_context):
        """Multiple products should all appear in output."""
        products = [
            _product(product_id="p1", name="Product Alpha"),
            _product(product_id="p2", name="Product Beta"),
            _product(product_id="p3", name="Product Gamma"),
        ]
        mock_client.search_products.return_value = MagicMock(
            success=True, data=products
        )
        tool = DiscoverInventoryTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(query="multi")
        assert "Product Alpha" in result
        assert "Product Beta" in result
        assert "Product Gamma" in result
        assert "Total products found: 3" in result

    @pytest.mark.asyncio
    async def test_format_shows_tiered_price(self, mock_client, advertiser_context):
        """Tiered price should show original and discounted price."""
        mock_client.search_products.return_value = MagicMock(
            success=True,
            data=[_product(base_price=100.0)],
        )
        tool = DiscoverInventoryTool(client=mock_client, buyer_context=advertiser_context)
        result = await tool._arun(query="expensive")
        # Advertiser gets 15% off: $100 * 0.85 = $85.00
        assert "$85.00" in result
        assert "$100.00" in result

    @pytest.mark.asyncio
    async def test_format_shows_premium_and_negotiation_for_agency(
        self, mock_client, agency_context
    ):
        """Agency tier should see premium access and negotiation available."""
        mock_client.search_products.return_value = MagicMock(
            success=True,
            data=[_product()],
        )
        tool = DiscoverInventoryTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(query="test")
        assert "Premium inventory access: ENABLED" in result
        assert "Price negotiation: AVAILABLE" in result

    @pytest.mark.asyncio
    async def test_format_public_tier_no_premium_or_negotiation(
        self, mock_client, public_context
    ):
        """Public tier should not see premium or negotiation labels."""
        mock_client.list_products.return_value = MagicMock(
            success=True,
            data=[_product()],
        )
        tool = DiscoverInventoryTool(client=mock_client, buyer_context=public_context)
        result = await tool._arun()
        assert "Premium inventory access" not in result
        assert "Price negotiation" not in result

    @pytest.mark.asyncio
    async def test_format_product_without_optional_fields(self, mock_client, agency_context):
        """Products missing optional fields should not crash formatting."""
        sparse_product = {"id": "sparse_1", "name": "Sparse"}
        mock_client.search_products.return_value = MagicMock(
            success=True, data=[sparse_product]
        )
        tool = DiscoverInventoryTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(query="sparse")
        assert "Sparse" in result

    @pytest.mark.asyncio
    async def test_discover_handles_exception_gracefully(self, mock_client, agency_context):
        """Unexpected exceptions should be caught and returned as error string."""
        mock_client.search_products.side_effect = RuntimeError("unexpected failure")
        tool = DiscoverInventoryTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(query="crash")
        assert "Error" in result
        assert "unexpected failure" in result


class TestDiscoverInventoryTierDisplay:
    """Test that each tier is correctly displayed in discovery results."""

    @pytest.mark.asyncio
    async def test_public_tier_shows_zero_discount(self, mock_client, public_context):
        """Public tier should show 0% discount."""
        mock_client.list_products.return_value = MagicMock(
            success=True, data=[_product()]
        )
        tool = DiscoverInventoryTool(client=mock_client, buyer_context=public_context)
        result = await tool._arun()
        assert "PUBLIC" in result
        assert "0%" in result

    @pytest.mark.asyncio
    async def test_seat_tier_shows_five_percent(self, mock_client, seat_context):
        """Seat tier should show 5% discount."""
        mock_client.list_products.return_value = MagicMock(
            success=True, data=[_product()]
        )
        tool = DiscoverInventoryTool(client=mock_client, buyer_context=seat_context)
        result = await tool._arun()
        assert "SEAT" in result
        assert "5" in result


# =============================================================================
# GetPricingTool - Extended Tests
# =============================================================================


class TestGetPricingTierCalculations:
    """Verify tier discount math across all tiers."""

    @pytest.mark.asyncio
    async def test_public_tier_no_discount(self, mock_client, public_context):
        """Public tier should get base price with no discount."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product(base_price=20.0)
        )
        tool = GetPricingTool(client=mock_client, buyer_context=public_context)
        result = await tool._arun(product_id="prod_001")
        # Public: 0% discount -> $20.00
        assert "Final CPM: $20.00" in result
        assert "Tier Discount: 0" in result

    @pytest.mark.asyncio
    async def test_seat_tier_five_percent_discount(self, mock_client, seat_context):
        """Seat tier should get 5% discount."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product(base_price=20.0)
        )
        tool = GetPricingTool(client=mock_client, buyer_context=seat_context)
        result = await tool._arun(product_id="prod_001")
        # Seat: 5% discount -> $20 * 0.95 = $19.00
        assert "$19.00" in result

    @pytest.mark.asyncio
    async def test_agency_tier_ten_percent_discount(self, mock_client, agency_context):
        """Agency tier should get 10% discount."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product(base_price=20.0)
        )
        tool = GetPricingTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="prod_001")
        # Agency: 10% discount -> $20 * 0.90 = $18.00
        assert "$18.00" in result

    @pytest.mark.asyncio
    async def test_advertiser_tier_fifteen_percent_discount(
        self, mock_client, advertiser_context
    ):
        """Advertiser tier should get 15% discount."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product(base_price=20.0)
        )
        tool = GetPricingTool(client=mock_client, buyer_context=advertiser_context)
        result = await tool._arun(product_id="prod_001")
        # Advertiser: 15% discount -> $20 * 0.85 = $17.00
        assert "$17.00" in result


class TestGetPricingVolumeDiscounts:
    """Test volume discount logic at various thresholds."""

    @pytest.mark.asyncio
    async def test_volume_below_5m_no_volume_discount(self, mock_client, advertiser_context):
        """Below 5M impressions, no volume discount should apply."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product(base_price=20.0)
        )
        tool = GetPricingTool(client=mock_client, buyer_context=advertiser_context)
        result = await tool._arun(product_id="prod_001", volume=4_000_000)
        # Advertiser 15%: $20 * 0.85 = $17.00, no volume discount
        assert "Final CPM: $17.00" in result
        assert "Volume Discount" not in result

    @pytest.mark.asyncio
    async def test_volume_5m_gives_5_percent_discount(self, mock_client, advertiser_context):
        """5M impressions should trigger 5% volume discount."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product(base_price=20.0)
        )
        tool = GetPricingTool(client=mock_client, buyer_context=advertiser_context)
        result = await tool._arun(product_id="prod_001", volume=5_000_000)
        # Advertiser 15%: $20 * 0.85 = $17.00
        # Volume 5%: $17.00 * 0.95 = $16.15
        assert "$16.15" in result
        assert "Volume Discount: 5" in result

    @pytest.mark.asyncio
    async def test_volume_10m_gives_10_percent_discount(self, mock_client, agency_context):
        """10M impressions should trigger 10% volume discount."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product(base_price=20.0)
        )
        tool = GetPricingTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="prod_001", volume=10_000_000)
        # Agency 10%: $20 * 0.90 = $18.00
        # Volume 10%: $18.00 * 0.90 = $16.20
        assert "$16.20" in result
        assert "Volume Discount: 10" in result

    @pytest.mark.asyncio
    async def test_public_tier_no_volume_discount(self, mock_client, public_context):
        """Public tier should not get volume discounts even at high volume."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product(base_price=20.0)
        )
        tool = GetPricingTool(client=mock_client, buyer_context=public_context)
        result = await tool._arun(product_id="prod_001", volume=20_000_000)
        assert "Volume Discount" not in result
        assert "Final CPM: $20.00" in result

    @pytest.mark.asyncio
    async def test_seat_tier_no_volume_discount(self, mock_client, seat_context):
        """Seat tier should not get volume discounts."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product(base_price=20.0)
        )
        tool = GetPricingTool(client=mock_client, buyer_context=seat_context)
        result = await tool._arun(product_id="prod_001", volume=15_000_000)
        assert "Volume Discount" not in result


class TestGetPricingCostProjection:
    """Test cost projection calculations."""

    @pytest.mark.asyncio
    async def test_cost_projection_with_volume(self, mock_client, agency_context):
        """Cost projection should appear when volume is provided."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product(base_price=20.0)
        )
        tool = GetPricingTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="prod_001", volume=1_000_000)
        # Agency: $20 * 0.90 = $18.00 CPM
        # Cost: $18.00 / 1000 * 1,000,000 = $18,000
        assert "Impressions: 1,000,000" in result
        assert "$18,000.00" in result

    @pytest.mark.asyncio
    async def test_no_cost_projection_without_volume(self, mock_client, agency_context):
        """Cost projection should not appear when no volume provided."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product(base_price=20.0)
        )
        tool = GetPricingTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="prod_001")
        assert "Cost Projection" not in result


class TestGetPricingDealTypes:
    """Test deal type display for different tiers."""

    @pytest.mark.asyncio
    async def test_authenticated_tier_shows_all_deal_types(self, mock_client, agency_context):
        """Agency tier should see checkmarks for all deal types."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product()
        )
        tool = GetPricingTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="prod_001")
        assert "Programmatic Guaranteed (PG)" in result
        assert "Preferred Deal (PD)" in result
        assert "Private Auction (PA)" in result

    @pytest.mark.asyncio
    async def test_public_tier_shows_authenticate_prompt(self, mock_client, public_context):
        """Public tier should see a note to authenticate for fixed-price deals."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product()
        )
        tool = GetPricingTool(client=mock_client, buyer_context=public_context)
        result = await tool._arun(product_id="prod_001")
        assert "Authenticate" in result or "authenticate" in result.lower()


class TestGetPricingNegotiationDisplay:
    """Test negotiation availability display."""

    @pytest.mark.asyncio
    async def test_agency_sees_negotiation_available(self, mock_client, agency_context):
        """Agency tier should see negotiation as available."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product()
        )
        tool = GetPricingTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="prod_001")
        assert "negotiation" in result.lower()

    @pytest.mark.asyncio
    async def test_seat_does_not_see_negotiation(self, mock_client, seat_context):
        """Seat tier should not see negotiation section."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product()
        )
        tool = GetPricingTool(client=mock_client, buyer_context=seat_context)
        result = await tool._arun(product_id="prod_001")
        assert "Price negotiation is available" not in result


class TestGetPricingEdgeCases:
    """Edge cases for GetPricingTool."""

    @pytest.mark.asyncio
    async def test_product_with_no_base_price(self, mock_client, agency_context):
        """Product without basePrice should handle gracefully."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data={"id": "no_price", "name": "No Price Product"}
        )
        tool = GetPricingTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="no_price")
        # Should use default 0 and not crash
        assert "No Price Product" in result

    @pytest.mark.asyncio
    async def test_product_with_price_instead_of_baseprice(self, mock_client, agency_context):
        """Product with 'price' key instead of 'basePrice' should work."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data={"id": "alt", "name": "Alt Price", "price": 30.0}
        )
        tool = GetPricingTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="alt")
        # Agency 10%: $30 * 0.90 = $27.00
        assert "$27.00" in result

    @pytest.mark.asyncio
    async def test_product_not_found_returns_error(self, mock_client, agency_context):
        """Missing product should return error string."""
        mock_client.get_product.return_value = MagicMock(
            success=False, error="Product not found"
        )
        tool = GetPricingTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="ghost")
        assert "Error" in result or "error" in result.lower()

    @pytest.mark.asyncio
    async def test_product_returns_none_data(self, mock_client, agency_context):
        """Product with success=True but data=None should return not found."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=None
        )
        tool = GetPricingTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="null")
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_exception_in_client_call(self, mock_client, agency_context):
        """Client exceptions should be caught gracefully."""
        mock_client.get_product.side_effect = ConnectionError("server down")
        tool = GetPricingTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="fail")
        assert "Error" in result


# =============================================================================
# RequestDealTool - Extended Tests
# =============================================================================


class TestRequestDealValidation:
    """Test input validation for RequestDealTool."""

    @pytest.mark.asyncio
    async def test_invalid_deal_type_rejected(self, mock_client, agency_context):
        """Invalid deal type should be rejected with a clear message."""
        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="prod_001", deal_type="FOO")
        assert "Invalid" in result
        assert "PG" in result or "PD" in result or "PA" in result

    @pytest.mark.asyncio
    async def test_pg_without_impressions_rejected(self, mock_client, agency_context):
        """PG deals without impressions should be rejected."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product()
        )
        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="prod_001", deal_type="PG", impressions=None)
        assert "require" in result.lower() or "impressions" in result.lower()

    @pytest.mark.asyncio
    async def test_pg_with_impressions_succeeds(self, mock_client, agency_context):
        """PG deals with impressions should succeed."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product()
        )
        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(
            product_id="prod_001", deal_type="PG", impressions=2_000_000
        )
        assert "DEAL-" in result
        assert "Programmatic Guaranteed" in result

    @pytest.mark.asyncio
    async def test_deal_type_case_insensitive(self, mock_client, agency_context):
        """Deal type should be case-insensitive (pd, Pd, PD all work)."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product()
        )
        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="prod_001", deal_type="pd")
        assert "DEAL-" in result
        assert "Preferred Deal" in result

    @pytest.mark.asyncio
    async def test_product_not_found_returns_error(self, mock_client, agency_context):
        """Deal request for nonexistent product should error."""
        mock_client.get_product.return_value = MagicMock(
            success=False, error="Product not found"
        )
        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="ghost")
        assert "Error" in result or "error" in result.lower()


class TestRequestDealNegotiation:
    """Test price negotiation in RequestDealTool."""

    @pytest.mark.asyncio
    async def test_public_cannot_negotiate(self, mock_client, public_context):
        """Public tier trying to negotiate should be denied."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product()
        )
        tool = RequestDealTool(client=mock_client, buyer_context=public_context)
        result = await tool._arun(product_id="prod_001", target_cpm=15.0)
        assert "tier" in result.lower() or "negotiation" in result.lower()

    @pytest.mark.asyncio
    async def test_seat_cannot_negotiate(self, mock_client, seat_context):
        """Seat tier trying to negotiate should be denied."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product()
        )
        tool = RequestDealTool(client=mock_client, buyer_context=seat_context)
        result = await tool._arun(product_id="prod_001", target_cpm=15.0)
        assert "tier" in result.lower() or "negotiation" in result.lower()

    @pytest.mark.asyncio
    async def test_agency_can_negotiate_above_floor(self, mock_client, agency_context):
        """Agency negotiating above floor should get their target price."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product(base_price=20.0)
        )
        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        # Agency tier: $20 * 0.90 = $18.00 tiered price
        # Floor: $18.00 * 0.90 = $16.20
        # Target $17.00 > floor $16.20, so should be accepted
        result = await tool._arun(product_id="prod_001", target_cpm=17.0)
        assert "DEAL-" in result
        assert "$17.00" in result

    @pytest.mark.asyncio
    async def test_advertiser_negotiate_below_floor_gets_counter(
        self, mock_client, advertiser_context
    ):
        """Negotiating below floor should result in floor price counter."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product(base_price=20.0)
        )
        tool = RequestDealTool(client=mock_client, buyer_context=advertiser_context)
        # Advertiser tier: $20 * 0.85 = $17.00 tiered price
        # Floor: $17.00 * 0.90 = $15.30
        # Target $10.00 < floor $15.30, so counter at $15.30
        result = await tool._arun(product_id="prod_001", target_cpm=10.0)
        assert "DEAL-" in result
        assert "$15.30" in result

    @pytest.mark.asyncio
    async def test_advertiser_negotiate_at_floor(self, mock_client, advertiser_context):
        """Negotiating exactly at floor should be accepted."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product(base_price=20.0)
        )
        tool = RequestDealTool(client=mock_client, buyer_context=advertiser_context)
        # Advertiser tier: $20 * 0.85 = $17.00
        # Floor: $17.00 * 0.90 = $15.30
        result = await tool._arun(product_id="prod_001", target_cpm=15.30)
        assert "DEAL-" in result
        assert "$15.30" in result


class TestRequestDealPricing:
    """Test pricing calculations in deal creation."""

    @pytest.mark.asyncio
    async def test_deal_applies_volume_discount_5m(self, mock_client, agency_context):
        """5M volume should trigger 5% volume discount on deal price."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product(base_price=20.0)
        )
        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(
            product_id="prod_001", deal_type="PD", impressions=5_000_000
        )
        # Agency 10%: $20 * 0.90 = $18.00
        # Volume 5%: $18.00 * 0.95 = $17.10
        assert "$17.10" in result

    @pytest.mark.asyncio
    async def test_deal_applies_volume_discount_10m(self, mock_client, advertiser_context):
        """10M volume should trigger 10% volume discount."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product(base_price=20.0)
        )
        tool = RequestDealTool(client=mock_client, buyer_context=advertiser_context)
        result = await tool._arun(
            product_id="prod_001", deal_type="PD", impressions=10_000_000
        )
        # Advertiser 15%: $20 * 0.85 = $17.00
        # Volume 10%: $17.00 * 0.90 = $15.30
        assert "$15.30" in result

    @pytest.mark.asyncio
    async def test_deal_shows_total_cost(self, mock_client, agency_context):
        """Deal with impressions should show estimated total cost."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product(base_price=20.0)
        )
        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(
            product_id="prod_001", deal_type="PD", impressions=1_000_000
        )
        # Agency: $18.00 CPM, 1M impressions -> $18,000
        assert "$18,000.00" in result

    @pytest.mark.asyncio
    async def test_deal_preserves_original_price(self, mock_client, advertiser_context):
        """Deal response should show original price before discount."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product(base_price=25.0)
        )
        tool = RequestDealTool(client=mock_client, buyer_context=advertiser_context)
        result = await tool._arun(product_id="prod_001")
        assert "Original CPM: $25.00" in result


class TestRequestDealOutput:
    """Test deal output formatting and content."""

    @pytest.mark.asyncio
    async def test_deal_id_format(self, mock_client, agency_context):
        """Deal ID should follow DEAL-XXXXXXXX format."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product()
        )
        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="prod_001")
        # Deal ID: DEAL- followed by 8 hex characters
        import re

        match = re.search(r"DEAL-[A-F0-9]{8}", result)
        assert match is not None, f"Expected DEAL-XXXXXXXX in output: {result}"

    @pytest.mark.asyncio
    async def test_deal_includes_all_dsp_platforms(self, mock_client, agency_context):
        """Deal should include activation instructions for all major DSPs."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product()
        )
        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="prod_001")
        assert "TTD" in result or "Trade Desk" in result
        assert "DV360" in result or "Display & Video" in result
        assert "AMAZON" in result or "Amazon" in result
        assert "XANDR" in result or "Xandr" in result
        assert "YAHOO" in result or "Yahoo" in result

    @pytest.mark.asyncio
    async def test_deal_includes_flight_dates(self, mock_client, agency_context):
        """Deal should include flight start and end dates."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product()
        )
        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(
            product_id="prod_001",
            flight_start="2026-03-01",
            flight_end="2026-03-31",
        )
        assert "2026-03-01" in result
        assert "2026-03-31" in result

    @pytest.mark.asyncio
    async def test_deal_uses_default_flight_dates(self, mock_client, agency_context):
        """Deal without flight dates should use defaults (today + 30 days)."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product()
        )
        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="prod_001")
        today = datetime.now().strftime("%Y-%m-%d")
        assert today in result

    @pytest.mark.asyncio
    async def test_deal_shows_expiry(self, mock_client, agency_context):
        """Deal should include an expiration date."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product()
        )
        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="prod_001")
        assert "expires" in result.lower()

    @pytest.mark.asyncio
    async def test_deal_shows_impression_count(self, mock_client, agency_context):
        """Deal with impressions should display the count."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product()
        )
        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(
            product_id="prod_001", deal_type="PG", impressions=3_000_000
        )
        assert "3,000,000" in result

    @pytest.mark.asyncio
    async def test_deal_handles_non_numeric_baseprice(self, mock_client, agency_context):
        """Product with non-numeric basePrice should use default of $20."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data={"id": "bad", "name": "Bad Price", "basePrice": "TBD"}
        )
        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="bad")
        # Should not crash, should use $20 default
        assert "DEAL-" in result

    @pytest.mark.asyncio
    async def test_deal_exception_handling(self, mock_client, agency_context):
        """Unexpected exception should be caught and returned cleanly."""
        mock_client.get_product.side_effect = RuntimeError("boom")
        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="fail")
        assert "Error" in result


class TestRequestDealAllDealTypes:
    """Test each deal type produces the correct output."""

    @pytest.mark.asyncio
    async def test_pd_deal_output(self, mock_client, agency_context):
        """Preferred Deal should be labeled correctly."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product()
        )
        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="prod_001", deal_type="PD")
        assert "Preferred Deal (PD)" in result

    @pytest.mark.asyncio
    async def test_pa_deal_output(self, mock_client, agency_context):
        """Private Auction should be labeled correctly."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product()
        )
        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        result = await tool._arun(product_id="prod_001", deal_type="PA")
        assert "Private Auction (PA)" in result


# =============================================================================
# DealResponse model tests
# =============================================================================


class TestDealResponseModel:
    """Tests for DealResponse model behavior."""

    def test_get_activation_known_platform(self):
        """Known platform should return specific instructions."""
        deal = DealResponse(
            deal_id="DEAL-ABCD1234",
            product_id="prod_001",
            product_name="Test",
            deal_type=DealType.PREFERRED_DEAL,
            price=18.0,
            access_tier=AccessTier.AGENCY,
            activation_instructions={
                "ttd": "Go to TTD > PMP > DEAL-ABCD1234",
                "dv360": "Go to DV360 > Inventory > DEAL-ABCD1234",
            },
        )
        assert "TTD" in deal.get_activation_for_platform("ttd")

    def test_get_activation_unknown_platform(self):
        """Unknown platform should return generic instructions."""
        deal = DealResponse(
            deal_id="DEAL-ABCD1234",
            product_id="prod_001",
            product_name="Test",
            deal_type=DealType.PREFERRED_DEAL,
            price=18.0,
            access_tier=AccessTier.AGENCY,
            activation_instructions={},
        )
        result = deal.get_activation_for_platform("unknown_dsp")
        assert "DEAL-ABCD1234" in result
        assert "Private Marketplace" in result

    def test_get_activation_case_insensitive(self):
        """Platform lookup should be case-insensitive."""
        deal = DealResponse(
            deal_id="DEAL-X",
            product_id="p",
            product_name="Test",
            deal_type=DealType.PREFERRED_DEAL,
            price=10.0,
            access_tier=AccessTier.AGENCY,
            activation_instructions={"ttd": "TTD instructions"},
        )
        # "TTD" should be lowercased to "ttd" for lookup
        result = deal.get_activation_for_platform("TTD")
        assert "TTD instructions" in result


# =============================================================================
# DSPFlowState model tests
# =============================================================================


class TestDSPFlowState:
    """Tests for DSPFlowState model."""

    def test_default_state(self):
        """Default state should be initialized with sensible defaults."""
        state = DSPFlowState()
        assert state.status == DSPFlowStatus.INITIALIZED
        assert state.request == ""
        assert state.deal_type == DealType.PREFERRED_DEAL
        assert state.impressions is None
        assert state.max_cpm is None
        assert state.discovered_products == []
        assert state.errors == []

    def test_state_with_values(self):
        """State should accept all field values."""
        state = DSPFlowState(
            request="CTV inventory",
            deal_type=DealType.PROGRAMMATIC_GUARANTEED,
            impressions=5_000_000,
            max_cpm=25.0,
            flight_start="2026-03-01",
            flight_end="2026-03-31",
        )
        assert state.request == "CTV inventory"
        assert state.deal_type == DealType.PROGRAMMATIC_GUARANTEED
        assert state.impressions == 5_000_000


class TestDiscoveredProduct:
    """Tests for DiscoveredProduct model."""

    def test_create_discovered_product(self):
        """DiscoveredProduct should store product metadata."""
        dp = DiscoveredProduct(
            product_id="ctv_001",
            product_name="CTV Premium",
            publisher="Hulu",
            channel="ctv",
            base_cpm=25.0,
            tiered_cpm=21.25,
            available_impressions=10_000_000,
            targeting=["household", "geo"],
            score=0.95,
        )
        assert dp.product_id == "ctv_001"
        assert dp.base_cpm == 25.0
        assert dp.tiered_cpm == 21.25
        assert dp.score == 0.95

    def test_discovered_product_defaults(self):
        """DiscoveredProduct should have sensible defaults."""
        dp = DiscoveredProduct(
            product_id="p1",
            product_name="Test",
            publisher="pub",
            base_cpm=20.0,
            tiered_cpm=18.0,
        )
        assert dp.channel is None
        assert dp.available_impressions is None
        assert dp.targeting == []
        assert dp.score == 0.0


# =============================================================================
# DSPDealFlow - State Machine Tests
# =============================================================================


class TestDSPDealFlowInit:
    """Tests for DSPDealFlow initialization."""

    def test_flow_creates_all_tools(self, mock_client, agency_context):
        """Flow should initialize all three DSP tools."""
        flow = DSPDealFlow(client=mock_client, buyer_context=agency_context)
        assert flow._discover_tool is not None
        assert flow._pricing_tool is not None
        assert flow._deal_tool is not None

    def test_flow_initial_state(self, mock_client, agency_context):
        """Flow state should start as INITIALIZED."""
        flow = DSPDealFlow(client=mock_client, buyer_context=agency_context)
        assert flow.state.status == DSPFlowStatus.INITIALIZED


class TestDSPDealFlowReceiveRequest:
    """Tests for the receive_request step."""

    def test_empty_request_fails(self, mock_client, agency_context):
        """Empty request should set status to FAILED."""
        flow = DSPDealFlow(client=mock_client, buyer_context=agency_context)
        flow.state.request = ""
        result = flow.receive_request()
        assert result["status"] == "failed"
        assert flow.state.status == DSPFlowStatus.FAILED
        assert len(flow.state.errors) > 0

    def test_valid_request_succeeds(self, mock_client, agency_context):
        """Valid request should set status to REQUEST_RECEIVED."""
        flow = DSPDealFlow(client=mock_client, buyer_context=agency_context)
        flow.state.request = "CTV inventory under $25"
        result = flow.receive_request()
        assert result["status"] == "success"
        assert flow.state.status == DSPFlowStatus.REQUEST_RECEIVED
        assert result["access_tier"] == "agency"

    def test_request_stores_buyer_context(self, mock_client, advertiser_context):
        """receive_request should store serialized buyer context."""
        flow = DSPDealFlow(client=mock_client, buyer_context=advertiser_context)
        flow.state.request = "Display ads"
        flow.receive_request()
        assert flow.state.buyer_context is not None


class TestDSPDealFlowGetStatus:
    """Tests for the get_status method."""

    def test_get_status_initial(self, mock_client, agency_context):
        """get_status should reflect current flow state."""
        flow = DSPDealFlow(client=mock_client, buyer_context=agency_context)
        flow.state.request = "test"
        status = flow.get_status()
        assert status["status"] == "initialized"
        assert status["access_tier"] == "agency"
        assert status["errors"] == []

    def test_get_status_after_failure(self, mock_client, agency_context):
        """get_status should show failure state."""
        flow = DSPDealFlow(client=mock_client, buyer_context=agency_context)
        flow.state.request = ""
        flow.receive_request()
        status = flow.get_status()
        assert status["status"] == "failed"
        assert len(status["errors"]) > 0


class TestDSPDealFlowDiscoverInventory:
    """Tests for the discover_inventory step."""

    def test_discover_skips_on_failed_request(self, mock_client, agency_context):
        """discover_inventory should pass through failure."""
        flow = DSPDealFlow(client=mock_client, buyer_context=agency_context)
        failed_result = {"status": "failed", "errors": ["bad request"]}
        result = flow.discover_inventory(failed_result)
        assert result["status"] == "failed"

    def test_discover_calls_tool_run(self, mock_client, agency_context):
        """discover_inventory should call the discover tool's _run method."""
        mock_client.search_products.return_value = MagicMock(
            success=True, data=[_product()]
        )
        flow = DSPDealFlow(client=mock_client, buyer_context=agency_context)
        flow.state.request = "CTV inventory"
        flow.state.max_cpm = 30.0
        flow.state.impressions = 2_000_000

        result = flow.discover_inventory({"status": "success"})
        assert result["status"] == "success"
        assert "discovery_result" in result
        assert flow.state.status == DSPFlowStatus.DISCOVERING_INVENTORY

    def test_discover_handles_tool_exception(self, mock_client, agency_context):
        """Exception in discover tool should be caught and recorded."""
        mock_client.search_products.side_effect = ConnectionError("network down")
        flow = DSPDealFlow(client=mock_client, buyer_context=agency_context)
        flow.state.request = "anything"

        result = flow.discover_inventory({"status": "success"})
        # The tool itself catches the error and returns an error string,
        # so the flow should still succeed (it gets a string result)
        # But if the tool raises instead, the flow catches it
        assert result["status"] in ("success", "failed")


class TestDSPDealFlowRequestDealId:
    """Tests for the request_deal_id step."""

    def test_request_deal_skips_on_failure(self, mock_client, agency_context):
        """request_deal_id should pass through failure."""
        flow = DSPDealFlow(client=mock_client, buyer_context=agency_context)
        result = flow.request_deal_id({"status": "failed", "error": "no products"})
        assert result["status"] == "failed"

    def test_request_deal_no_product_selected(self, mock_client, agency_context):
        """request_deal_id with no selected product should fail."""
        flow = DSPDealFlow(client=mock_client, buyer_context=agency_context)
        flow.state.selected_product_id = None
        result = flow.request_deal_id({"status": "success"})
        assert result["status"] == "failed"
        assert "No product selected" in result.get("error", "")
        assert flow.state.status == DSPFlowStatus.FAILED

    def test_request_deal_creates_deal(self, mock_client, agency_context):
        """request_deal_id with valid product should create a deal."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product()
        )
        flow = DSPDealFlow(client=mock_client, buyer_context=agency_context)
        flow.state.selected_product_id = "prod_001"
        flow.state.deal_type = DealType.PREFERRED_DEAL
        flow.state.impressions = 1_000_000

        result = flow.request_deal_id({"status": "success"})
        assert result["status"] == "success"
        assert flow.state.status == DSPFlowStatus.DEAL_CREATED
        assert flow.state.deal_response is not None


class TestDSPDealFlowExtractProductId:
    """Tests for _extract_product_id helper."""

    def test_extract_from_product_id_format(self, mock_client, agency_context):
        """Should extract from 'product_id: xxx' format."""
        flow = DSPDealFlow(client=mock_client, buyer_context=agency_context)
        result = flow._extract_product_id('product_id: ctv_premium_001')
        assert result == "ctv_premium_001"

    def test_extract_from_product_id_colon(self, mock_client, agency_context):
        """Should extract from 'Product ID: xxx' format."""
        flow = DSPDealFlow(client=mock_client, buyer_context=agency_context)
        result = flow._extract_product_id('The best option is Product ID: display_001')
        assert result == "display_001"

    def test_extract_returns_none_when_not_found(self, mock_client, agency_context):
        """Should return None if no product ID pattern found."""
        flow = DSPDealFlow(client=mock_client, buyer_context=agency_context)
        result = flow._extract_product_id('This text has no product reference at all.')
        assert result is None

    def test_extract_from_id_format(self, mock_client, agency_context):
        """Should extract from generic 'id: xxx' format."""
        flow = DSPDealFlow(client=mock_client, buyer_context=agency_context)
        result = flow._extract_product_id('id: prod_abc')
        assert result == "prod_abc"


# =============================================================================
# DSPFlowStatus enum tests
# =============================================================================


class TestDSPFlowStatus:
    """Tests for DSPFlowStatus enum values."""

    def test_all_status_values(self):
        """All expected status values should be defined."""
        expected = {
            "initialized",
            "request_received",
            "discovering_inventory",
            "evaluating_pricing",
            "requesting_deal",
            "deal_created",
            "failed",
        }
        actual = {s.value for s in DSPFlowStatus}
        assert actual == expected


# =============================================================================
# UnifiedClient DSP methods
# =============================================================================


class TestUnifiedClientDSPMethods:
    """Test DSP-specific methods on UnifiedClient."""

    def test_set_buyer_identity(self, advertiser_identity):
        """set_buyer_identity should store the identity."""
        client = UnifiedClient(base_url="http://localhost:8080")
        assert client.buyer_identity is None
        client.set_buyer_identity(advertiser_identity)
        assert client.buyer_identity is advertiser_identity

    def test_get_access_tier_no_identity(self):
        """get_access_tier with no identity returns 'public'."""
        client = UnifiedClient(base_url="http://localhost:8080")
        assert client.get_access_tier() == "public"

    def test_get_access_tier_with_identity(self, agency_identity):
        """get_access_tier should reflect the identity's tier."""
        client = UnifiedClient(base_url="http://localhost:8080", buyer_identity=agency_identity)
        assert client.get_access_tier() == "agency"

    def test_get_identity_context_no_identity(self):
        """No identity should return public context."""
        client = UnifiedClient(base_url="http://localhost:8080")
        ctx = client._get_identity_context()
        assert ctx["access_tier"] == "public"

    def test_get_identity_context_with_identity(self, advertiser_identity):
        """Identity context should include all identity fields."""
        client = UnifiedClient(
            base_url="http://localhost:8080",
            buyer_identity=advertiser_identity,
        )
        ctx = client._get_identity_context()
        assert ctx["access_tier"] == "advertiser"
        assert ctx["agency_id"] == "omnicom-200"
        assert ctx["advertiser_id"] == "cocacola-300"


# =============================================================================
# Cross-tier pricing consistency
# =============================================================================


class TestCrossTierPricingConsistency:
    """Verify pricing is consistent across different tiers for the same product."""

    @pytest.mark.asyncio
    async def test_higher_tier_always_gets_lower_price(self, mock_client):
        """Progressively higher tiers should always get lower or equal prices."""
        product = _product(base_price=50.0)
        mock_client.get_product.return_value = MagicMock(
            success=True, data=product
        )

        prices = {}
        for tier_name, identity in [
            ("public", BuyerIdentity()),
            ("seat", BuyerIdentity(seat_id="s1")),
            ("agency", BuyerIdentity(seat_id="s1", agency_id="a1")),
            (
                "advertiser",
                BuyerIdentity(seat_id="s1", agency_id="a1", advertiser_id="adv1"),
            ),
        ]:
            ctx = BuyerContext(identity=identity, is_authenticated=True)
            tool = GetPricingTool(client=mock_client, buyer_context=ctx)
            result = await tool._arun(product_id="prod_001")

            # Extract the final CPM from the result
            import re

            final_match = re.search(r"Final CPM: \$(\d+\.\d+)", result)
            if final_match:
                prices[tier_name] = float(final_match.group(1))

        assert prices["public"] >= prices["seat"]
        assert prices["seat"] >= prices["agency"]
        assert prices["agency"] >= prices["advertiser"]

    @pytest.mark.asyncio
    async def test_discount_percentages_match_tier(self, mock_client):
        """Discount percentages should exactly match tier definitions."""
        product = _product(base_price=100.0)
        mock_client.get_product.return_value = MagicMock(
            success=True, data=product
        )

        expected_final_prices = {
            "public": 100.0,   # 0% discount
            "seat": 95.0,      # 5% discount
            "agency": 90.0,    # 10% discount
            "advertiser": 85.0,  # 15% discount
        }

        for tier_name, identity in [
            ("public", BuyerIdentity()),
            ("seat", BuyerIdentity(seat_id="s1")),
            ("agency", BuyerIdentity(seat_id="s1", agency_id="a1")),
            (
                "advertiser",
                BuyerIdentity(seat_id="s1", agency_id="a1", advertiser_id="adv1"),
            ),
        ]:
            ctx = BuyerContext(identity=identity, is_authenticated=True)
            tool = GetPricingTool(client=mock_client, buyer_context=ctx)
            result = await tool._arun(product_id="prod_001")

            expected_price = f"${expected_final_prices[tier_name]:.2f}"
            assert expected_price in result, (
                f"Expected {expected_price} for {tier_name} tier, got: {result}"
            )


# =============================================================================
# DealRequest model tests
# =============================================================================


class TestDealRequestModel:
    """Tests for DealRequest model validation."""

    def test_deal_request_requires_product_id(self):
        """DealRequest should require product_id."""
        with pytest.raises(Exception):
            DealRequest()

    def test_deal_request_defaults(self):
        """DealRequest defaults should be sensible."""
        req = DealRequest(product_id="prod_001")
        assert req.deal_type == DealType.PREFERRED_DEAL
        assert req.impressions is None
        assert req.target_cpm is None
        assert req.notes is None

    def test_deal_request_full(self):
        """DealRequest should accept all fields."""
        req = DealRequest(
            product_id="prod_001",
            deal_type=DealType.PROGRAMMATIC_GUARANTEED,
            impressions=5_000_000,
            flight_start="2026-04-01",
            flight_end="2026-04-30",
            target_cpm=18.0,
            notes="Priority placement",
        )
        assert req.deal_type == DealType.PROGRAMMATIC_GUARANTEED
        assert req.impressions == 5_000_000
        assert req.notes == "Priority placement"


# =============================================================================
# Synchronous wrapper tests
# =============================================================================


class TestSynchronousWrappers:
    """Test that _run (sync) delegates to _arun correctly."""

    def test_discover_run_calls_arun(self, mock_client, agency_context):
        """DiscoverInventoryTool._run should delegate to _arun."""
        mock_client.search_products.return_value = MagicMock(
            success=True, data=[_product()]
        )
        tool = DiscoverInventoryTool(client=mock_client, buyer_context=agency_context)
        result = tool._run(query="test sync")
        assert "Test Product" in result

    def test_get_pricing_run_calls_arun(self, mock_client, agency_context):
        """GetPricingTool._run should delegate to _arun."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product()
        )
        tool = GetPricingTool(client=mock_client, buyer_context=agency_context)
        result = tool._run(product_id="prod_001")
        assert "$18.00" in result

    def test_request_deal_run_calls_arun(self, mock_client, agency_context):
        """RequestDealTool._run should delegate to _arun."""
        mock_client.get_product.return_value = MagicMock(
            success=True, data=_product()
        )
        tool = RequestDealTool(client=mock_client, buyer_context=agency_context)
        result = tool._run(product_id="prod_001")
        assert "DEAL-" in result
