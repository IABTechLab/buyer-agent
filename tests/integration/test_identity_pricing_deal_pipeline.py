# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Integration tests: identity resolution -> pricing -> deal booking pipeline.

Tests the full chain from identity strategy recommendation through tiered
pricing calculation to deal creation, verifying that module boundaries
correctly propagate identity context.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ad_buyer.clients.unified_client import Protocol, UnifiedClient, UnifiedResult
from ad_buyer.identity.strategy import (
    CampaignGoal,
    DealContext,
    IdentityStrategy,
    SellerRelationship,
)
from ad_buyer.models.buyer_identity import (
    AccessTier,
    BuyerContext,
    BuyerIdentity,
    DealType,
)
from ad_buyer.tools.dsp.get_pricing import GetPricingTool
from ad_buyer.tools.dsp.request_deal import RequestDealTool


class TestIdentityToPricingPipeline:
    """Tests the identity strategy -> pricing tool -> deal tool chain."""

    def test_identity_strategy_determines_tier_then_pricing_applies_discount(
        self,
        advertiser_identity: BuyerIdentity,
    ):
        """Identity strategy recommends tier, pricing tool applies the matching discount."""
        strategy = IdentityStrategy()

        # Strategy recommends a tier based on deal context
        deal_ctx = DealContext(
            deal_value_usd=150_000,
            deal_type=DealType.PREFERRED_DEAL,
            seller_relationship=SellerRelationship.ESTABLISHED,
            campaign_goal=CampaignGoal.AWARENESS,
        )
        recommended_tier = strategy.recommend_tier(deal_ctx)

        # Build a masked identity at the recommended tier
        masked_identity = strategy.build_identity(advertiser_identity, recommended_tier)

        # The masked identity should produce the correct discount
        discount = masked_identity.get_discount_percentage()
        assert discount > 0, "Recommended tier should have a discount"

        # Verify tier/discount alignment
        tier = masked_identity.get_access_tier()
        expected_discounts = {
            AccessTier.PUBLIC: 0.0,
            AccessTier.SEAT: 5.0,
            AccessTier.AGENCY: 10.0,
            AccessTier.ADVERTISER: 15.0,
        }
        assert discount == expected_discounts[tier]

    def test_pg_deal_always_gets_advertiser_tier(
        self,
        advertiser_identity: BuyerIdentity,
    ):
        """PG deals require full identity disclosure (advertiser tier)."""
        strategy = IdentityStrategy()

        # Even with a low-value deal, PG should force advertiser tier
        deal_ctx = DealContext(
            deal_value_usd=5_000,
            deal_type=DealType.PROGRAMMATIC_GUARANTEED,
            seller_relationship=SellerRelationship.UNKNOWN,
            campaign_goal=CampaignGoal.AWARENESS,
        )
        tier = strategy.recommend_tier(deal_ctx)
        assert tier == AccessTier.ADVERTISER

        masked = strategy.build_identity(advertiser_identity, tier)
        assert masked.advertiser_id == advertiser_identity.advertiser_id

    def test_identity_masking_strips_higher_tier_fields(
        self,
        advertiser_identity: BuyerIdentity,
    ):
        """build_identity at SEAT tier should strip agency and advertiser fields."""
        strategy = IdentityStrategy()
        masked = strategy.build_identity(advertiser_identity, AccessTier.SEAT)

        assert masked.seat_id == advertiser_identity.seat_id
        assert masked.agency_id is None
        assert masked.advertiser_id is None
        assert masked.get_access_tier() == AccessTier.SEAT
        assert masked.get_discount_percentage() == 5.0

    def test_savings_estimation_across_tiers(
        self,
    ):
        """estimate_savings should correctly compute incremental savings."""
        strategy = IdentityStrategy()
        base_price = 30.0

        # Upgrading from PUBLIC to ADVERTISER: 15% of $30 = $4.50
        savings = strategy.estimate_savings(base_price, AccessTier.PUBLIC, AccessTier.ADVERTISER)
        assert savings == pytest.approx(4.5)

        # Upgrading from SEAT to AGENCY: 5% of $30 = $1.50
        savings = strategy.estimate_savings(base_price, AccessTier.SEAT, AccessTier.AGENCY)
        assert savings == pytest.approx(1.5)

        # No savings for same tier
        savings = strategy.estimate_savings(base_price, AccessTier.AGENCY, AccessTier.AGENCY)
        assert savings == 0.0

        # No savings for downgrade
        savings = strategy.estimate_savings(base_price, AccessTier.ADVERTISER, AccessTier.SEAT)
        assert savings == 0.0


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


class TestEndToEndIdentityPricingDeal:
    """Full end-to-end: identity strategy -> masked identity -> pricing -> deal."""

    @pytest.mark.asyncio
    async def test_full_pipeline_with_strategy(
        self,
        advertiser_identity: BuyerIdentity,
        sample_products: list[dict[str, Any]],
    ):
        """Walk through the entire identity -> pricing -> deal pipeline."""
        product = sample_products[0]  # CTV at $35

        # Step 1: Identity strategy recommends tier
        strategy = IdentityStrategy()
        deal_ctx = DealContext(
            deal_value_usd=200_000,
            deal_type=DealType.PREFERRED_DEAL,
            seller_relationship=SellerRelationship.TRUSTED,
            campaign_goal=CampaignGoal.PERFORMANCE,
        )
        recommended_tier = strategy.recommend_tier(deal_ctx)
        assert recommended_tier == AccessTier.ADVERTISER

        # Step 2: Build masked identity
        masked = strategy.build_identity(advertiser_identity, recommended_tier)
        assert masked.get_access_tier() == AccessTier.ADVERTISER

        # Step 3: Create buyer context with masked identity
        buyer_ctx = BuyerContext(
            identity=masked,
            is_authenticated=True,
            preferred_deal_types=[DealType.PREFERRED_DEAL],
        )
        assert buyer_ctx.can_negotiate() is True

        # Step 4: Use pricing tool (with mocked client)
        client = UnifiedClient(
            base_url="http://fake-seller.test",
            buyer_identity=masked,
        )
        mock_mcp = AsyncMock()
        mock_mcp.call_tool = AsyncMock(
            return_value=MagicMock(success=True, data=product, error="", raw=None)
        )
        client._mcp_client = mock_mcp

        pricing_tool = GetPricingTool(client=client, buyer_context=buyer_ctx)
        pricing_output = pricing_tool._run(product_id=product["id"], volume=10_000_000)

        # Should contain the discount information
        assert "ADVERTISER" in pricing_output
        assert "15.0%" in pricing_output or "15%" in pricing_output

        # Step 5: Use deal tool
        deal_tool = RequestDealTool(client=client, buyer_context=buyer_ctx)
        deal_output = deal_tool._run(
            product_id=product["id"],
            deal_type="PD",
            impressions=10_000_000,
        )

        assert "DEAL-" in deal_output
        assert "DEAL CREATED SUCCESSFULLY" in deal_output

        await client.close()

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
