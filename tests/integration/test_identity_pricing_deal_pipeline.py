# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Integration tests: pricing -> deal booking pipeline.

Tests the chain from tiered pricing calculation to deal creation, verifying
that module boundaries correctly propagate buyer identity context.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ad_buyer.clients.unified_client import UnifiedClient
from ad_buyer.models.buyer_identity import (
    BuyerContext,
    BuyerIdentity,
)
from ad_buyer.tools.buyer_deals.request_deal import RequestDealTool


class TestPricingToDealPipeline:
    """Tests pricing calculation flowing into deal creation."""

    @pytest.mark.asyncio
    async def test_unified_client_pricing_to_deal_flow(
        self,
        advertiser_identity: BuyerIdentity,
        sample_products: list[dict[str, Any]],
    ):
        """UnifiedClient.get_pricing then request_deal should produce consistent pricing."""
        product = sample_products[0]  # CTV at $35 base

        client = UnifiedClient(
            base_url="http://fake-seller.test",
            buyer_identity=advertiser_identity,
        )

        # Mock the MCP client so no real HTTP is made
        mock_mcp = AsyncMock()
        mock_mcp.call_tool = AsyncMock(
            return_value=MagicMock(
                success=True,
                data=product,
                error="",
                raw=None,
            )
        )
        client._mcp_client = mock_mcp

        # Step 1: get_pricing
        pricing_result = await client.get_pricing(
            product_id=product["id"],
            volume=5_000_000,
            deal_type="PD",
        )
        assert pricing_result.success
        assert "pricing" in pricing_result.data
        pricing = pricing_result.data["pricing"]

        # Advertiser gets 15% discount
        assert pricing["tier"] == "advertiser"
        assert pricing["tier_discount"] == 15.0
        expected_tiered = 35.0 * 0.85  # $29.75
        # Volume discount for 5M: 5%
        expected_final = expected_tiered * 0.95
        assert pricing["tiered_price"] == pytest.approx(expected_final, rel=0.01)

        # Step 2: request_deal with the same product
        deal_result = await client.request_deal(
            product_id=product["id"],
            deal_type="PD",
            impressions=5_000_000,
        )
        assert deal_result.success
        deal = deal_result.data
        assert deal["deal_id"].startswith("DEAL-")
        assert deal["access_tier"] == "advertiser"
        assert deal["discount_applied"] == 15.0

        await client.close()

    @pytest.mark.asyncio
    async def test_public_tier_gets_no_discount(
        self,
        public_identity: BuyerIdentity,
        sample_products: list[dict[str, Any]],
    ):
        """Public-tier buyer should receive base price with no discount."""
        product = sample_products[1]  # Display at $12 base

        client = UnifiedClient(
            base_url="http://fake-seller.test",
            buyer_identity=public_identity,
        )

        mock_mcp = AsyncMock()
        mock_mcp.call_tool = AsyncMock(
            return_value=MagicMock(
                success=True,
                data=product,
                error="",
                raw=None,
            )
        )
        client._mcp_client = mock_mcp

        pricing_result = await client.get_pricing(product_id=product["id"])
        assert pricing_result.success
        pricing = pricing_result.data["pricing"]
        assert pricing["tier"] == "public"
        assert pricing["tier_discount"] == 0.0
        assert pricing["tiered_price"] == 12.0

        await client.close()


class TestEndToEndPricingDeal:
    """End-to-end pricing -> deal behavior driven by buyer identity tier."""

    @pytest.mark.asyncio
    async def test_negotiation_only_for_high_tiers(
        self,
        seat_identity: BuyerIdentity,
        sample_products: list[dict[str, Any]],
    ):
        """Seat-tier buyer should not be able to negotiate (agency+ required)."""
        product = sample_products[0]

        buyer_ctx = BuyerContext(
            identity=seat_identity,
            is_authenticated=True,
        )
        assert buyer_ctx.can_negotiate() is False

        client = UnifiedClient(
            base_url="http://fake-seller.test",
            buyer_identity=seat_identity,
        )
        mock_mcp = AsyncMock()
        mock_mcp.call_tool = AsyncMock(
            return_value=MagicMock(success=True, data=product, error="", raw=None)
        )
        client._mcp_client = mock_mcp

        deal_tool = RequestDealTool(client=client, buyer_context=buyer_ctx)
        result = deal_tool._run(
            product_id=product["id"],
            deal_type="PD",
            target_cpm=25.0,  # Trying to negotiate
        )

        # Should be rejected because seat tier cannot negotiate
        assert "requires Agency or Advertiser tier" in result

        await client.close()
