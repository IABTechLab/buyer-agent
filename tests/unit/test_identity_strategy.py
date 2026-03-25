# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for tiered identity presentation strategy."""

import pytest

from ad_buyer.identity.strategy import (
    CampaignGoal,
    DealContext,
    IdentityStrategy,
    SellerRelationship,
)
from ad_buyer.models.buyer_identity import (
    AccessTier,
    BuyerIdentity,
    DealType,
)

# --- Fixtures ---


def _full_identity() -> BuyerIdentity:
    """Create a fully-populated buyer identity (advertiser tier)."""
    return BuyerIdentity(
        seat_id="ttd-seat-123",
        seat_name="The Trade Desk",
        agency_id="omnicom-456",
        agency_name="OMD",
        agency_holding_company="Omnicom",
        advertiser_id="coca-cola-789",
        advertiser_name="Coca-Cola",
        advertiser_industry="CPG",
    )


def _agency_identity() -> BuyerIdentity:
    """Create an agency-tier buyer identity."""
    return BuyerIdentity(
        seat_id="ttd-seat-123",
        seat_name="The Trade Desk",
        agency_id="omnicom-456",
        agency_name="OMD",
        agency_holding_company="Omnicom",
    )


def _seat_identity() -> BuyerIdentity:
    """Create a seat-tier buyer identity."""
    return BuyerIdentity(
        seat_id="ttd-seat-123",
        seat_name="The Trade Desk",
    )


# --- Tests for DealContext ---


class TestDealContext:
    """Tests for DealContext model."""

    def test_deal_context_defaults(self):
        """DealContext should have sensible defaults."""
        ctx = DealContext()
        assert ctx.deal_value_usd == 0.0
        assert ctx.deal_type == DealType.PREFERRED_DEAL
        assert ctx.seller_relationship == SellerRelationship.UNKNOWN
        assert ctx.campaign_goal == CampaignGoal.AWARENESS

    def test_deal_context_custom_values(self):
        """DealContext should accept custom values."""
        ctx = DealContext(
            deal_value_usd=500_000.0,
            deal_type=DealType.PROGRAMMATIC_GUARANTEED,
            seller_relationship=SellerRelationship.TRUSTED,
            campaign_goal=CampaignGoal.PERFORMANCE,
        )
        assert ctx.deal_value_usd == 500_000.0
        assert ctx.deal_type == DealType.PROGRAMMATIC_GUARANTEED
        assert ctx.seller_relationship == SellerRelationship.TRUSTED
        assert ctx.campaign_goal == CampaignGoal.PERFORMANCE

    def test_deal_context_rejects_negative_value(self):
        """DealContext should reject negative deal values."""
        with pytest.raises(ValueError):
            DealContext(deal_value_usd=-100.0)


# --- Tests for recommend_tier ---


class TestRecommendTier:
    """Tests for IdentityStrategy.recommend_tier."""

    def test_pg_deal_recommends_advertiser(self):
        """PG deals need advertiser-level identity for guaranteed inventory."""
        strategy = IdentityStrategy()
        ctx = DealContext(
            deal_type=DealType.PROGRAMMATIC_GUARANTEED,
            deal_value_usd=100_000.0,
        )
        assert strategy.recommend_tier(ctx) == AccessTier.ADVERTISER

    def test_high_value_pd_recommends_advertiser(self):
        """High-value PD deals should reveal advertiser for max discount."""
        strategy = IdentityStrategy()
        ctx = DealContext(
            deal_type=DealType.PREFERRED_DEAL,
            deal_value_usd=200_000.0,
        )
        assert strategy.recommend_tier(ctx) == AccessTier.ADVERTISER

    def test_low_value_pd_recommends_seat(self):
        """Low-value PD deals with unknown sellers default to seat tier."""
        strategy = IdentityStrategy()
        ctx = DealContext(
            deal_type=DealType.PREFERRED_DEAL,
            deal_value_usd=5_000.0,
            seller_relationship=SellerRelationship.UNKNOWN,
        )
        assert strategy.recommend_tier(ctx) == AccessTier.SEAT

    def test_mid_value_pd_recommends_agency(self):
        """Mid-value PD deals should recommend agency tier."""
        strategy = IdentityStrategy()
        ctx = DealContext(
            deal_type=DealType.PREFERRED_DEAL,
            deal_value_usd=30_000.0,
            seller_relationship=SellerRelationship.UNKNOWN,
        )
        assert strategy.recommend_tier(ctx) == AccessTier.AGENCY

    def test_private_auction_unknown_seller_recommends_seat(self):
        """Private auctions with unknown sellers should reveal minimal identity."""
        strategy = IdentityStrategy()
        ctx = DealContext(
            deal_type=DealType.PRIVATE_AUCTION,
            deal_value_usd=10_000.0,
            seller_relationship=SellerRelationship.UNKNOWN,
        )
        assert strategy.recommend_tier(ctx) == AccessTier.SEAT

    def test_trusted_seller_upgrades_tier(self):
        """Trusted sellers should receive higher identity tier."""
        strategy = IdentityStrategy()
        ctx = DealContext(
            deal_type=DealType.PRIVATE_AUCTION,
            deal_value_usd=10_000.0,
            seller_relationship=SellerRelationship.TRUSTED,
        )
        # Trusted seller should upgrade from seat to at least agency
        tier = strategy.recommend_tier(ctx)
        assert tier in (AccessTier.AGENCY, AccessTier.ADVERTISER)

    def test_performance_goal_prefers_higher_tier(self):
        """Performance campaigns benefit from higher tiers (better targeting)."""
        strategy = IdentityStrategy()
        ctx_awareness = DealContext(
            deal_type=DealType.PREFERRED_DEAL,
            deal_value_usd=20_000.0,
            campaign_goal=CampaignGoal.AWARENESS,
        )
        ctx_performance = DealContext(
            deal_type=DealType.PREFERRED_DEAL,
            deal_value_usd=20_000.0,
            campaign_goal=CampaignGoal.PERFORMANCE,
        )
        tier_awareness = strategy.recommend_tier(ctx_awareness)
        tier_performance = strategy.recommend_tier(ctx_performance)

        tier_order = [AccessTier.PUBLIC, AccessTier.SEAT, AccessTier.AGENCY, AccessTier.ADVERTISER]
        assert tier_order.index(tier_performance) >= tier_order.index(tier_awareness)

    def test_zero_value_deal_defaults_to_seat(self):
        """Zero-value deal with no other signals defaults to seat tier."""
        strategy = IdentityStrategy()
        ctx = DealContext(deal_value_usd=0.0)
        tier = strategy.recommend_tier(ctx)
        assert tier in (AccessTier.PUBLIC, AccessTier.SEAT)

    def test_custom_high_value_threshold(self):
        """Custom high-value threshold should be respected."""
        strategy = IdentityStrategy(high_value_threshold_usd=1_000_000.0)
        ctx = DealContext(
            deal_type=DealType.PREFERRED_DEAL,
            deal_value_usd=500_000.0,
        )
        # 500k is below the 1M threshold, so should not recommend advertiser
        tier = strategy.recommend_tier(ctx)
        assert tier != AccessTier.ADVERTISER


# --- Tests for build_identity ---


class TestBuildIdentity:
    """Tests for IdentityStrategy.build_identity."""

    def test_mask_to_seat_strips_agency_and_advertiser(self):
        """Masking to seat tier should strip agency and advertiser fields."""
        strategy = IdentityStrategy()
        full = _full_identity()
        masked = strategy.build_identity(full, AccessTier.SEAT)

        assert masked.seat_id == "ttd-seat-123"
        assert masked.seat_name == "The Trade Desk"
        assert masked.agency_id is None
        assert masked.agency_name is None
        assert masked.agency_holding_company is None
        assert masked.advertiser_id is None
        assert masked.advertiser_name is None
        assert masked.advertiser_industry is None
        assert masked.get_access_tier() == AccessTier.SEAT

    def test_mask_to_agency_strips_advertiser(self):
        """Masking to agency tier should strip advertiser fields."""
        strategy = IdentityStrategy()
        full = _full_identity()
        masked = strategy.build_identity(full, AccessTier.AGENCY)

        assert masked.seat_id == "ttd-seat-123"
        assert masked.agency_id == "omnicom-456"
        assert masked.agency_name == "OMD"
        assert masked.advertiser_id is None
        assert masked.advertiser_name is None
        assert masked.get_access_tier() == AccessTier.AGENCY

    def test_mask_to_advertiser_keeps_everything(self):
        """Masking to advertiser tier should keep all fields."""
        strategy = IdentityStrategy()
        full = _full_identity()
        masked = strategy.build_identity(full, AccessTier.ADVERTISER)

        assert masked.seat_id == "ttd-seat-123"
        assert masked.agency_id == "omnicom-456"
        assert masked.advertiser_id == "coca-cola-789"
        assert masked.get_access_tier() == AccessTier.ADVERTISER

    def test_mask_to_public_strips_everything(self):
        """Masking to public tier should strip all identity fields."""
        strategy = IdentityStrategy()
        full = _full_identity()
        masked = strategy.build_identity(full, AccessTier.PUBLIC)

        assert masked.seat_id is None
        assert masked.agency_id is None
        assert masked.advertiser_id is None
        assert masked.get_access_tier() == AccessTier.PUBLIC

    def test_mask_does_not_mutate_original(self):
        """Masking should not modify the original identity."""
        strategy = IdentityStrategy()
        full = _full_identity()
        _ = strategy.build_identity(full, AccessTier.SEAT)

        # Original should be unchanged
        assert full.advertiser_id == "coca-cola-789"
        assert full.agency_id == "omnicom-456"

    def test_mask_identity_without_requested_fields(self):
        """Masking a seat identity to advertiser returns what's available."""
        strategy = IdentityStrategy()
        seat = _seat_identity()
        masked = strategy.build_identity(seat, AccessTier.ADVERTISER)

        # Can't reveal what you don't have
        assert masked.seat_id == "ttd-seat-123"
        assert masked.agency_id is None
        assert masked.advertiser_id is None
        # The tier reflects what's actually present, not what was requested
        assert masked.get_access_tier() == AccessTier.SEAT

    def test_mask_empty_identity_to_any_tier(self):
        """Masking an empty identity returns empty regardless of target tier."""
        strategy = IdentityStrategy()
        empty = BuyerIdentity()
        masked = strategy.build_identity(empty, AccessTier.ADVERTISER)
        assert masked.get_access_tier() == AccessTier.PUBLIC


# --- Tests for estimate_savings ---


class TestEstimateSavings:
    """Tests for IdentityStrategy.estimate_savings."""

    def test_savings_from_public_to_advertiser(self):
        """Upgrading from public to advertiser should give 15% savings."""
        strategy = IdentityStrategy()
        base_price = 20.0
        savings = strategy.estimate_savings(base_price, AccessTier.PUBLIC, AccessTier.ADVERTISER)
        assert savings == pytest.approx(3.0)  # 15% of $20

    def test_savings_from_seat_to_agency(self):
        """Upgrading from seat to agency should give 5% incremental savings."""
        strategy = IdentityStrategy()
        base_price = 20.0
        savings = strategy.estimate_savings(base_price, AccessTier.SEAT, AccessTier.AGENCY)
        assert savings == pytest.approx(1.0)  # (10% - 5%) of $20

    def test_savings_same_tier_is_zero(self):
        """No savings when source and target tier are the same."""
        strategy = IdentityStrategy()
        savings = strategy.estimate_savings(20.0, AccessTier.AGENCY, AccessTier.AGENCY)
        assert savings == 0.0

    def test_savings_downgrade_is_zero(self):
        """Downgrading tiers should return zero savings (no negative savings)."""
        strategy = IdentityStrategy()
        savings = strategy.estimate_savings(20.0, AccessTier.ADVERTISER, AccessTier.PUBLIC)
        assert savings == 0.0

    def test_savings_from_public_to_seat(self):
        """Upgrading from public to seat should give 5% savings."""
        strategy = IdentityStrategy()
        savings = strategy.estimate_savings(100.0, AccessTier.PUBLIC, AccessTier.SEAT)
        assert savings == pytest.approx(5.0)

    def test_savings_zero_price(self):
        """Zero base price should return zero savings."""
        strategy = IdentityStrategy()
        savings = strategy.estimate_savings(0.0, AccessTier.PUBLIC, AccessTier.ADVERTISER)
        assert savings == 0.0


# --- Tests for edge cases ---


class TestEdgeCases:
    """Tests for edge cases in identity strategy."""

    def test_strategy_with_none_identity_fields(self):
        """Strategy should handle identity with all None fields."""
        strategy = IdentityStrategy()
        identity = BuyerIdentity()
        masked = strategy.build_identity(identity, AccessTier.SEAT)
        assert masked.get_access_tier() == AccessTier.PUBLIC

    def test_recommend_tier_returns_valid_access_tier(self):
        """recommend_tier should always return a valid AccessTier."""
        strategy = IdentityStrategy()
        for deal_type in DealType:
            for relationship in SellerRelationship:
                for goal in CampaignGoal:
                    ctx = DealContext(
                        deal_type=deal_type,
                        deal_value_usd=50_000.0,
                        seller_relationship=relationship,
                        campaign_goal=goal,
                    )
                    tier = strategy.recommend_tier(ctx)
                    assert isinstance(tier, AccessTier)
