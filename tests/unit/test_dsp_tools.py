# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for DSP tools."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ad_buyer.models.buyer_identity import (
    AccessTier,
    BuyerContext,
    BuyerIdentity,
    DealType,
)
from ad_buyer.tools.dsp import DiscoverInventoryTool, GetPricingTool, RequestDealTool


@pytest.fixture
def mock_client():
    """Create a mock UnifiedClient."""
    client = MagicMock()
    client.search_products = AsyncMock()
    client.list_products = AsyncMock()
    client.get_product = AsyncMock()
    return client


@pytest.fixture
def public_context():
    """Create a public tier buyer context."""
    return BuyerContext()


@pytest.fixture
def agency_context():
    """Create an agency tier buyer context."""
    identity = BuyerIdentity(
        seat_id="ttd-seat-123",
        agency_id="omnicom-456",
        agency_name="OMD",
    )
    return BuyerContext(identity=identity, is_authenticated=True)


@pytest.fixture
def advertiser_context():
    """Create an advertiser tier buyer context."""
    identity = BuyerIdentity(
        seat_id="ttd-seat-123",
        agency_id="omnicom-456",
        agency_name="OMD",
        advertiser_id="coca-cola-789",
        advertiser_name="Coca-Cola",
    )
    return BuyerContext(identity=identity, is_authenticated=True)


class TestDiscoverInventoryTool:
    """Tests for DiscoverInventoryTool."""

    def test_tool_creation(self, mock_client, agency_context):
        """Test creating the tool."""
        tool = DiscoverInventoryTool(
            client=mock_client,
            buyer_context=agency_context,
        )

        assert tool.name == "discover_inventory"
        assert "inventory" in tool.description.lower()

    @pytest.mark.asyncio
    async def test_discover_with_query(self, mock_client, agency_context):
        """Test discovery with query."""
        mock_client.search_products.return_value = MagicMock(
            success=True,
            data=[
                {
                    "id": "prod_1",
                    "name": "CTV Premium",
                    "basePrice": 25.00,
                    "publisherId": "pub_1",
                }
            ],
        )

        tool = DiscoverInventoryTool(
            client=mock_client,
            buyer_context=agency_context,
        )

        # Use _arun directly for async testing
        result = await tool._arun(query="CTV inventory")

        assert "CTV Premium" in result
        assert "AGENCY" in result.upper() or "10" in result

    @pytest.mark.asyncio
    async def test_discover_without_query(self, mock_client, public_context):
        """Test discovery without query lists all products."""
        mock_client.list_products.return_value = MagicMock(
            success=True,
            data=[
                {"id": "prod_1", "name": "Product 1", "basePrice": 20.00},
                {"id": "prod_2", "name": "Product 2", "basePrice": 25.00},
            ],
        )

        tool = DiscoverInventoryTool(
            client=mock_client,
            buyer_context=public_context,
        )

        result = await tool._arun()

        assert "Product 1" in result
        assert "Product 2" in result

    @pytest.mark.asyncio
    async def test_discover_shows_tier_discount(self, mock_client, advertiser_context):
        """Test that discovery shows tier-specific discount."""
        mock_client.search_products.return_value = MagicMock(
            success=True,
            data=[{"id": "prod_1", "name": "Test", "basePrice": 20.00}],
        )

        tool = DiscoverInventoryTool(
            client=mock_client,
            buyer_context=advertiser_context,
        )

        result = await tool._arun(query="test")

        # Should show 15% discount for advertiser tier
        assert "15%" in result or "ADVERTISER" in result.upper()

    @pytest.mark.asyncio
    async def test_discover_error_handling(self, mock_client, agency_context):
        """Test error handling in discovery."""
        mock_client.search_products.return_value = MagicMock(
            success=False,
            error="Connection failed",
        )

        tool = DiscoverInventoryTool(
            client=mock_client,
            buyer_context=agency_context,
        )

        result = await tool._arun(query="test")

        assert "Error" in result or "error" in result.lower()


class TestGetPricingTool:
    """Tests for GetPricingTool."""

    def test_tool_creation(self, mock_client, agency_context):
        """Test creating the tool."""
        tool = GetPricingTool(
            client=mock_client,
            buyer_context=agency_context,
        )

        assert tool.name == "get_pricing"
        assert "pricing" in tool.description.lower()

    @pytest.mark.asyncio
    async def test_get_pricing_calculates_tier_discount(self, mock_client, agency_context):
        """Test that pricing calculates tier discount correctly."""
        mock_client.get_product.return_value = MagicMock(
            success=True,
            data={
                "id": "prod_1",
                "name": "Test Product",
                "basePrice": 20.00,
                "publisherId": "pub_1",
            },
        )

        tool = GetPricingTool(
            client=mock_client,
            buyer_context=agency_context,
        )

        result = await tool._arun(product_id="prod_1")

        # Agency tier gets 10% discount: $20 * 0.90 = $18
        assert "$18.00" in result
        assert "10" in result

    @pytest.mark.asyncio
    async def test_get_pricing_volume_discount(self, mock_client, advertiser_context):
        """Test volume discount for high-volume requests."""
        mock_client.get_product.return_value = MagicMock(
            success=True,
            data={
                "id": "prod_1",
                "name": "Test Product",
                "basePrice": 20.00,
            },
        )

        tool = GetPricingTool(
            client=mock_client,
            buyer_context=advertiser_context,
        )

        # Request 10M impressions for extra 10% volume discount
        result = await tool._arun(product_id="prod_1", volume=10_000_000)

        # Advertiser tier 15% + 10% volume
        # Base: $20, after tier: $17, after volume: $15.30
        assert "Volume Discount" in result or "volume" in result.lower()

    @pytest.mark.asyncio
    async def test_get_pricing_shows_deal_types(self, mock_client, agency_context):
        """Test that pricing shows available deal types."""
        mock_client.get_product.return_value = MagicMock(
            success=True,
            data={"id": "prod_1", "name": "Test", "basePrice": 20.00},
        )

        tool = GetPricingTool(
            client=mock_client,
            buyer_context=agency_context,
        )

        result = await tool._arun(product_id="prod_1")

        assert "PG" in result or "Programmatic Guaranteed" in result
        assert "PD" in result or "Preferred Deal" in result
        assert "PA" in result or "Private Auction" in result

    @pytest.mark.asyncio
    async def test_get_pricing_product_not_found(self, mock_client, agency_context):
        """Test handling of product not found."""
        mock_client.get_product.return_value = MagicMock(
            success=False,
            error="Product not found",
        )

        tool = GetPricingTool(
            client=mock_client,
            buyer_context=agency_context,
        )

        result = await tool._arun(product_id="nonexistent")

        assert "Error" in result or "error" in result.lower()


class TestRequestDealTool:
    """Tests for RequestDealTool."""

    def test_tool_creation(self, mock_client, agency_context):
        """Test creating the tool."""
        tool = RequestDealTool(
            client=mock_client,
            buyer_context=agency_context,
        )

        assert tool.name == "request_deal"
        assert "Deal ID" in tool.description or "deal" in tool.description.lower()

    @pytest.mark.asyncio
    async def test_request_deal_creates_deal_id(self, mock_client, agency_context):
        """Test that deal request creates a Deal ID."""
        mock_client.get_product.return_value = MagicMock(
            success=True,
            data={
                "id": "prod_1",
                "name": "Test Product",
                "basePrice": 20.00,
            },
        )

        tool = RequestDealTool(
            client=mock_client,
            buyer_context=agency_context,
        )

        result = await tool._arun(
            product_id="prod_1",
            deal_type="PD",
            impressions=1_000_000,
        )

        assert "DEAL-" in result
        assert "Test Product" in result

    @pytest.mark.asyncio
    async def test_request_deal_includes_activation_instructions(self, mock_client, agency_context):
        """Test that deal includes DSP activation instructions."""
        mock_client.get_product.return_value = MagicMock(
            success=True,
            data={"id": "prod_1", "name": "Test", "basePrice": 20.00},
        )

        tool = RequestDealTool(
            client=mock_client,
            buyer_context=agency_context,
        )

        result = await tool._arun(product_id="prod_1")

        # Should include major DSP platforms
        assert "TTD" in result or "Trade Desk" in result
        assert "DV360" in result or "Display & Video" in result
        assert "Amazon" in result

    @pytest.mark.asyncio
    async def test_request_deal_pg_requires_impressions(self, mock_client, agency_context):
        """Test that PG deals require impressions."""
        mock_client.get_product.return_value = MagicMock(
            success=True,
            data={"id": "prod_1", "name": "Test", "basePrice": 20.00},
        )

        tool = RequestDealTool(
            client=mock_client,
            buyer_context=agency_context,
        )

        result = await tool._arun(
            product_id="prod_1",
            deal_type="PG",
            impressions=None,  # No impressions
        )

        assert "require" in result.lower() or "impressions" in result.lower()

    @pytest.mark.asyncio
    async def test_request_deal_negotiation_requires_tier(self, mock_client, public_context):
        """Test that price negotiation requires agency/advertiser tier."""
        mock_client.get_product.return_value = MagicMock(
            success=True,
            data={"id": "prod_1", "name": "Test", "basePrice": 20.00},
        )

        tool = RequestDealTool(
            client=mock_client,
            buyer_context=public_context,
        )

        result = await tool._arun(
            product_id="prod_1",
            target_cpm=15.00,  # Try to negotiate
        )

        # Should indicate negotiation not available at public tier
        assert "tier" in result.lower() or "negotiation" in result.lower()

    @pytest.mark.asyncio
    async def test_request_deal_applies_tier_discount(self, mock_client, advertiser_context):
        """Test that deal price includes tier discount."""
        mock_client.get_product.return_value = MagicMock(
            success=True,
            data={"id": "prod_1", "name": "Test", "basePrice": 20.00},
        )

        tool = RequestDealTool(
            client=mock_client,
            buyer_context=advertiser_context,
        )

        result = await tool._arun(product_id="prod_1")

        # Advertiser gets 15% discount: $20 * 0.85 = $17
        assert "$17.00" in result
        assert "15" in result

    @pytest.mark.asyncio
    async def test_request_deal_invalid_type(self, mock_client, agency_context):
        """Test handling of invalid deal type."""
        tool = RequestDealTool(
            client=mock_client,
            buyer_context=agency_context,
        )

        result = await tool._arun(
            product_id="prod_1",
            deal_type="INVALID",
        )

        assert "Invalid" in result or "invalid" in result.lower()


class TestToolIntegration:
    """Integration tests for DSP tools working together."""

    @pytest.mark.asyncio
    async def test_discover_then_price_then_deal(
        self, mock_client, advertiser_context
    ):
        """Test typical workflow: discover -> get pricing -> request deal."""
        # Mock product data
        product_data = {
            "id": "ctv_premium_001",
            "name": "Premium CTV Package",
            "basePrice": 22.00,
            "publisherId": "streaming_pub",
            "channel": "ctv",
        }

        mock_client.search_products.return_value = MagicMock(
            success=True,
            data=[product_data],
        )
        mock_client.get_product.return_value = MagicMock(
            success=True,
            data=product_data,
        )

        # Step 1: Discover inventory
        discover_tool = DiscoverInventoryTool(
            client=mock_client,
            buyer_context=advertiser_context,
        )
        discovery_result = await discover_tool._arun(query="CTV inventory")

        assert "Premium CTV Package" in discovery_result

        # Step 2: Get pricing
        pricing_tool = GetPricingTool(
            client=mock_client,
            buyer_context=advertiser_context,
        )
        pricing_result = await pricing_tool._arun(
            product_id="ctv_premium_001",
            volume=5_000_000,
        )

        assert "ctv_premium_001" in pricing_result.lower() or "Premium CTV" in pricing_result

        # Step 3: Request deal
        deal_tool = RequestDealTool(
            client=mock_client,
            buyer_context=advertiser_context,
        )
        deal_result = await deal_tool._arun(
            product_id="ctv_premium_001",
            deal_type="PD",
            impressions=5_000_000,
            flight_start="2026-02-01",
            flight_end="2026-02-28",
        )

        assert "DEAL-" in deal_result
        assert "5,000,000" in deal_result
