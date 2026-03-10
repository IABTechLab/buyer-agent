# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Comprehensive tests for identity, authentication, and session modules.

Covers edge cases, boundary conditions, and uncovered paths in:
- ad_buyer.models.buyer_identity (BuyerIdentity, BuyerContext, DealRequest, DealResponse)
- ad_buyer.identity.strategy (IdentityStrategy, DealContext, tier logic)
- ad_buyer.auth.key_store (ApiKeyStore persistence, encoding, corruption)
- ad_buyer.auth.middleware (AuthMiddleware header attachment, response handling)
- ad_buyer.sessions (SessionRecord, SessionStore, SessionManager edge cases)
"""

import base64
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ad_buyer.auth.key_store import ApiKeyStore
from ad_buyer.auth.middleware import AuthMiddleware, AuthResponse
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
    DealRequest,
    DealResponse,
    DealType,
)
from ad_buyer.sessions.session_manager import SessionManager
from ad_buyer.sessions.session_store import SessionRecord, SessionStore


# =============================================================================
# BuyerIdentity model — additional edge cases
# =============================================================================


class TestBuyerIdentityEdgeCases:
    """Additional edge case tests for BuyerIdentity model."""

    def test_to_header_dict_empty_identity(self):
        """Empty identity should produce empty headers dict."""
        identity = BuyerIdentity()
        headers = identity.to_header_dict()
        assert headers == {}

    def test_to_context_dict_full_identity(self):
        """Full identity context dict should include all fields and computed tier."""
        identity = BuyerIdentity(
            seat_id="ttd-seat-123",
            seat_name="The Trade Desk",
            agency_id="omnicom-456",
            agency_name="OMD",
            agency_holding_company="Omnicom",
            advertiser_id="coca-cola-789",
            advertiser_name="Coca-Cola",
            advertiser_industry="CPG",
        )
        context = identity.to_context_dict()
        assert context["seat_id"] == "ttd-seat-123"
        assert context["seat_name"] == "The Trade Desk"
        assert context["agency_id"] == "omnicom-456"
        assert context["agency_name"] == "OMD"
        assert context["agency_holding_company"] == "Omnicom"
        assert context["advertiser_id"] == "coca-cola-789"
        assert context["advertiser_name"] == "Coca-Cola"
        assert context["advertiser_industry"] == "CPG"
        assert context["access_tier"] == "advertiser"

    def test_to_context_dict_empty_identity(self):
        """Empty identity context dict should have all None fields and public tier."""
        identity = BuyerIdentity()
        context = identity.to_context_dict()
        assert context["seat_id"] is None
        assert context["agency_id"] is None
        assert context["advertiser_id"] is None
        assert context["access_tier"] == "public"

    def test_to_context_dict_seat_tier(self):
        """Seat-tier identity context dict should show seat tier."""
        identity = BuyerIdentity(seat_id="seat-1", seat_name="DSP One")
        context = identity.to_context_dict()
        assert context["access_tier"] == "seat"
        assert context["seat_id"] == "seat-1"
        assert context["agency_id"] is None

    def test_to_header_dict_seat_and_name_only(self):
        """Seat-only identity should only include seat headers."""
        identity = BuyerIdentity(seat_id="seat-1", seat_name="DSP One")
        headers = identity.to_header_dict()
        assert len(headers) == 2
        assert headers["X-DSP-Seat-ID"] == "seat-1"
        assert headers["X-DSP-Seat-Name"] == "DSP One"

    def test_agency_id_without_seat_id_is_agency_tier(self):
        """Agency ID without seat ID should still be agency tier."""
        identity = BuyerIdentity(agency_id="agency-1", agency_name="Agency One")
        assert identity.get_access_tier() == AccessTier.AGENCY
        assert identity.get_discount_percentage() == 10.0

    def test_header_dict_agency_only(self):
        """Agency-only identity headers should include agency fields."""
        identity = BuyerIdentity(
            agency_id="agency-1",
            agency_name="Agency One",
            agency_holding_company="HoldCo",
        )
        headers = identity.to_header_dict()
        assert "X-Agency-ID" in headers
        assert "X-Agency-Name" in headers
        assert "X-Agency-Holding-Company" in headers
        assert "X-DSP-Seat-ID" not in headers

    def test_advertiser_tier_discount_is_highest(self):
        """Advertiser tier should always give 15% — the maximum discount."""
        identity = BuyerIdentity(advertiser_id="adv-1")
        assert identity.get_discount_percentage() == 15.0

    def test_identity_model_serialization_roundtrip(self):
        """Identity should survive JSON serialization and deserialization."""
        identity = BuyerIdentity(
            seat_id="s1",
            seat_name="Seat",
            agency_id="a1",
            agency_name="Agency",
            agency_holding_company="HC",
            advertiser_id="ad1",
            advertiser_name="Adv",
            advertiser_industry="Tech",
        )
        data = identity.model_dump()
        restored = BuyerIdentity.model_validate(data)
        assert restored == identity
        assert restored.get_access_tier() == identity.get_access_tier()


# =============================================================================
# BuyerContext model — additional edge cases
# =============================================================================


class TestBuyerContextEdgeCases:
    """Additional edge case tests for BuyerContext."""

    def test_public_tier_cannot_negotiate(self):
        """Public tier should not have negotiation rights."""
        context = BuyerContext()
        assert context.get_access_tier() == AccessTier.PUBLIC
        assert not context.can_negotiate()

    def test_unauthenticated_context_still_reports_tier(self):
        """Unauthenticated context with agency identity still reports agency tier."""
        identity = BuyerIdentity(agency_id="a1")
        context = BuyerContext(identity=identity, is_authenticated=False)
        assert context.get_access_tier() == AccessTier.AGENCY
        # can_negotiate depends on tier, not auth status
        assert context.can_negotiate() is True

    def test_session_id_stored(self):
        """Session ID should be stored and retrievable."""
        context = BuyerContext(session_id="sess-123")
        assert context.session_id == "sess-123"

    def test_premium_inventory_access_matches_negotiate(self):
        """Premium inventory access should match negotiation access."""
        for tier_fields in [
            {},  # PUBLIC
            {"seat_id": "s1"},  # SEAT
            {"agency_id": "a1"},  # AGENCY
            {"advertiser_id": "ad1"},  # ADVERTISER
        ]:
            identity = BuyerIdentity(**tier_fields)
            ctx = BuyerContext(identity=identity)
            assert ctx.can_access_premium_inventory() == ctx.can_negotiate()

    def test_empty_preferred_deal_types_list(self):
        """Empty preferred deal types list should be allowed."""
        context = BuyerContext(preferred_deal_types=[])
        assert context.preferred_deal_types == []


# =============================================================================
# DealRequest — additional validation tests
# =============================================================================


class TestDealRequestEdgeCases:
    """Additional edge case tests for DealRequest."""

    def test_zero_impressions_allowed(self):
        """Zero impressions should be valid (ge=0 constraint)."""
        request = DealRequest(product_id="p1", impressions=0)
        assert request.impressions == 0

    def test_negative_impressions_rejected(self):
        """Negative impressions should be rejected by validation."""
        with pytest.raises(ValueError):
            DealRequest(product_id="p1", impressions=-1)

    def test_zero_target_cpm_allowed(self):
        """Zero target CPM should be valid."""
        request = DealRequest(product_id="p1", target_cpm=0.0)
        assert request.target_cpm == 0.0

    def test_negative_target_cpm_rejected(self):
        """Negative target CPM should be rejected."""
        with pytest.raises(ValueError):
            DealRequest(product_id="p1", target_cpm=-1.0)

    def test_all_deal_types_accepted(self):
        """All deal type enum values should be accepted."""
        for dt in DealType:
            request = DealRequest(product_id="p1", deal_type=dt)
            assert request.deal_type == dt


# =============================================================================
# DealResponse — additional tests
# =============================================================================


class TestDealResponseEdgeCases:
    """Additional edge case tests for DealResponse."""

    def test_activation_instructions_case_insensitive_lookup(self):
        """Platform lookup should be case-insensitive."""
        response = DealResponse(
            deal_id="D1",
            product_id="P1",
            product_name="Product",
            deal_type=DealType.PREFERRED_DEAL,
            price=10.0,
            access_tier=AccessTier.SEAT,
            activation_instructions={"dv360": "Use DV360 UI"},
        )
        assert response.get_activation_for_platform("DV360") == "Use DV360 UI"
        assert response.get_activation_for_platform("dv360") == "Use DV360 UI"

    def test_activation_default_includes_deal_id_and_platform(self):
        """Default activation instructions should reference deal ID and platform."""
        response = DealResponse(
            deal_id="DEAL-XYZ",
            product_id="P1",
            product_name="Product",
            deal_type=DealType.PREFERRED_DEAL,
            price=10.0,
            access_tier=AccessTier.SEAT,
        )
        instructions = response.get_activation_for_platform("SomeDSP")
        assert "DEAL-XYZ" in instructions
        assert "SomeDSP" in instructions

    def test_zero_price_allowed(self):
        """Zero price should be valid (make-good deals, etc.)."""
        response = DealResponse(
            deal_id="D1",
            product_id="P1",
            product_name="Free Product",
            deal_type=DealType.PREFERRED_DEAL,
            price=0.0,
            access_tier=AccessTier.PUBLIC,
        )
        assert response.price == 0.0

    def test_all_optional_fields_none(self):
        """Response with only required fields should work."""
        response = DealResponse(
            deal_id="D1",
            product_id="P1",
            product_name="Min Product",
            deal_type=DealType.PREFERRED_DEAL,
            price=5.0,
            access_tier=AccessTier.SEAT,
        )
        assert response.original_price is None
        assert response.discount_applied is None
        assert response.impressions is None
        assert response.flight_start is None
        assert response.flight_end is None
        assert response.expires_at is None
        assert response.activation_instructions == {}


# =============================================================================
# IdentityStrategy — additional coverage for tier logic and boundaries
# =============================================================================


class TestIdentityStrategyBoundaryValues:
    """Test tier recommendation at exact boundary values."""

    def test_exactly_at_high_value_threshold(self):
        """Deal at exactly the high-value threshold should use advertiser tier."""
        strategy = IdentityStrategy(high_value_threshold_usd=100_000.0)
        ctx = DealContext(deal_value_usd=100_000.0)
        tier = strategy.recommend_tier(ctx)
        assert tier == AccessTier.ADVERTISER

    def test_just_below_high_value_threshold(self):
        """Deal just below high-value threshold should not use advertiser tier."""
        strategy = IdentityStrategy(high_value_threshold_usd=100_000.0)
        ctx = DealContext(
            deal_value_usd=99_999.99,
            seller_relationship=SellerRelationship.UNKNOWN,
            campaign_goal=CampaignGoal.AWARENESS,
        )
        tier = strategy.recommend_tier(ctx)
        # Should be AGENCY (above mid threshold) but not ADVERTISER
        assert tier != AccessTier.ADVERTISER

    def test_exactly_at_mid_value_threshold(self):
        """Deal at exactly the mid-value threshold should use agency tier."""
        strategy = IdentityStrategy(mid_value_threshold_usd=25_000.0)
        ctx = DealContext(
            deal_value_usd=25_000.0,
            seller_relationship=SellerRelationship.UNKNOWN,
            campaign_goal=CampaignGoal.AWARENESS,
        )
        tier = strategy.recommend_tier(ctx)
        assert tier == AccessTier.AGENCY

    def test_just_below_mid_value_threshold(self):
        """Deal just below mid-value threshold should use seat tier."""
        strategy = IdentityStrategy(mid_value_threshold_usd=25_000.0)
        ctx = DealContext(
            deal_value_usd=24_999.99,
            seller_relationship=SellerRelationship.UNKNOWN,
            campaign_goal=CampaignGoal.AWARENESS,
        )
        tier = strategy.recommend_tier(ctx)
        assert tier == AccessTier.SEAT

    def test_custom_mid_value_threshold(self):
        """Custom mid-value threshold should be respected."""
        strategy = IdentityStrategy(mid_value_threshold_usd=50_000.0)
        ctx = DealContext(
            deal_value_usd=40_000.0,
            seller_relationship=SellerRelationship.UNKNOWN,
            campaign_goal=CampaignGoal.AWARENESS,
        )
        # 40k is below 50k mid threshold, so should be SEAT
        tier = strategy.recommend_tier(ctx)
        assert tier == AccessTier.SEAT


class TestIdentityStrategyRelationshipModifier:
    """Test relationship-based tier upgrades."""

    def test_established_seller_upgrades_tier(self):
        """Established seller relationship should upgrade tier by one level."""
        strategy = IdentityStrategy()
        ctx = DealContext(
            deal_value_usd=5_000.0,
            seller_relationship=SellerRelationship.ESTABLISHED,
            campaign_goal=CampaignGoal.AWARENESS,
        )
        tier = strategy.recommend_tier(ctx)
        # Base would be SEAT, established upgrades by 1 to AGENCY
        assert tier == AccessTier.AGENCY

    def test_new_seller_no_upgrade(self):
        """New seller relationship should not upgrade tier."""
        strategy = IdentityStrategy()
        ctx = DealContext(
            deal_value_usd=5_000.0,
            seller_relationship=SellerRelationship.NEW,
            campaign_goal=CampaignGoal.AWARENESS,
        )
        tier = strategy.recommend_tier(ctx)
        assert tier == AccessTier.SEAT

    def test_unknown_seller_no_upgrade(self):
        """Unknown seller relationship should not upgrade tier."""
        strategy = IdentityStrategy()
        ctx = DealContext(
            deal_value_usd=5_000.0,
            seller_relationship=SellerRelationship.UNKNOWN,
            campaign_goal=CampaignGoal.AWARENESS,
        )
        tier = strategy.recommend_tier(ctx)
        assert tier == AccessTier.SEAT


class TestIdentityStrategyUpgradeCapping:
    """Test that tier upgrades cap at ADVERTISER."""

    def test_upgrade_from_advertiser_stays_at_advertiser(self):
        """Upgrading from ADVERTISER should stay at ADVERTISER (cap)."""
        strategy = IdentityStrategy()
        # High value + trusted + performance = multiple upgrade signals
        ctx = DealContext(
            deal_value_usd=200_000.0,
            seller_relationship=SellerRelationship.TRUSTED,
            campaign_goal=CampaignGoal.PERFORMANCE,
        )
        tier = strategy.recommend_tier(ctx)
        assert tier == AccessTier.ADVERTISER

    def test_multiple_upgrades_cap_at_advertiser(self):
        """Multiple upgrade signals should not go beyond ADVERTISER."""
        strategy = IdentityStrategy()
        # Mid-value (AGENCY base) + trusted (+1) + performance (+1)
        ctx = DealContext(
            deal_value_usd=30_000.0,
            seller_relationship=SellerRelationship.TRUSTED,
            campaign_goal=CampaignGoal.PERFORMANCE,
        )
        tier = strategy.recommend_tier(ctx)
        assert tier == AccessTier.ADVERTISER

    def test_upgrade_tier_internal_method(self):
        """Internal _upgrade_tier should cap at ADVERTISER."""
        strategy = IdentityStrategy()
        # Upgrade from ADVERTISER by 5 levels — should stay at ADVERTISER
        result = strategy._upgrade_tier(AccessTier.ADVERTISER, 5)
        assert result == AccessTier.ADVERTISER

    def test_upgrade_tier_from_public(self):
        """Internal _upgrade_tier from PUBLIC by 1 should give SEAT."""
        strategy = IdentityStrategy()
        result = strategy._upgrade_tier(AccessTier.PUBLIC, 1)
        assert result == AccessTier.SEAT

    def test_upgrade_tier_by_zero(self):
        """Internal _upgrade_tier by 0 should return same tier."""
        strategy = IdentityStrategy()
        result = strategy._upgrade_tier(AccessTier.AGENCY, 0)
        assert result == AccessTier.AGENCY


class TestIdentityStrategyDealTypeConstraint:
    """Test deal type constraints."""

    def test_private_auction_passes_through(self):
        """Private auction should not add extra constraints beyond PG rule."""
        strategy = IdentityStrategy()
        ctx = DealContext(
            deal_type=DealType.PRIVATE_AUCTION,
            deal_value_usd=200_000.0,
        )
        tier = strategy.recommend_tier(ctx)
        # High value -> ADVERTISER, no extra constraints for PA
        assert tier == AccessTier.ADVERTISER

    def test_preferred_deal_passes_through(self):
        """Preferred deal should not add extra constraints beyond PG rule."""
        strategy = IdentityStrategy()
        ctx = DealContext(
            deal_type=DealType.PREFERRED_DEAL,
            deal_value_usd=200_000.0,
        )
        tier = strategy.recommend_tier(ctx)
        assert tier == AccessTier.ADVERTISER

    def test_pg_overrides_all_signals(self):
        """PG deal should return ADVERTISER regardless of other signals."""
        strategy = IdentityStrategy()
        # Very low value, unknown seller, awareness goal — but PG
        ctx = DealContext(
            deal_type=DealType.PROGRAMMATIC_GUARANTEED,
            deal_value_usd=0.0,
            seller_relationship=SellerRelationship.UNKNOWN,
            campaign_goal=CampaignGoal.AWARENESS,
        )
        assert strategy.recommend_tier(ctx) == AccessTier.ADVERTISER


class TestIdentityStrategyBuildIdentityPartial:
    """Test build_identity with partial source identities."""

    def test_build_agency_from_seat_identity(self):
        """Building agency tier from seat identity yields seat-level data only."""
        strategy = IdentityStrategy()
        seat_only = BuyerIdentity(seat_id="s1", seat_name="Seat One")
        masked = strategy.build_identity(seat_only, AccessTier.AGENCY)
        assert masked.seat_id == "s1"
        assert masked.seat_name == "Seat One"
        assert masked.agency_id is None
        # Actual tier is SEAT since agency fields are absent
        assert masked.get_access_tier() == AccessTier.SEAT

    def test_build_public_from_empty(self):
        """Building public tier from empty identity yields empty identity."""
        strategy = IdentityStrategy()
        empty = BuyerIdentity()
        masked = strategy.build_identity(empty, AccessTier.PUBLIC)
        assert masked.get_access_tier() == AccessTier.PUBLIC
        assert masked == BuyerIdentity()

    def test_build_preserves_holding_company_at_agency_tier(self):
        """Agency tier build should preserve holding company field."""
        strategy = IdentityStrategy()
        full = BuyerIdentity(
            seat_id="s1",
            seat_name="Seat",
            agency_id="a1",
            agency_name="Agency",
            agency_holding_company="HoldCo",
            advertiser_id="ad1",
            advertiser_name="Adv",
            advertiser_industry="Tech",
        )
        masked = strategy.build_identity(full, AccessTier.AGENCY)
        assert masked.agency_holding_company == "HoldCo"
        assert masked.advertiser_id is None


class TestEstimateSavingsEdgeCases:
    """Additional savings estimation tests."""

    def test_savings_from_agency_to_advertiser(self):
        """Upgrading agency to advertiser should give 5% incremental."""
        strategy = IdentityStrategy()
        savings = strategy.estimate_savings(100.0, AccessTier.AGENCY, AccessTier.ADVERTISER)
        assert savings == pytest.approx(5.0)

    def test_savings_large_base_price(self):
        """Large base prices should compute correctly."""
        strategy = IdentityStrategy()
        savings = strategy.estimate_savings(
            1_000_000.0, AccessTier.PUBLIC, AccessTier.ADVERTISER
        )
        assert savings == pytest.approx(150_000.0)

    def test_savings_very_small_base_price(self):
        """Very small base prices should still compute."""
        strategy = IdentityStrategy()
        savings = strategy.estimate_savings(
            0.01, AccessTier.PUBLIC, AccessTier.ADVERTISER
        )
        assert savings == pytest.approx(0.0015)


# =============================================================================
# SellerRelationship and CampaignGoal enum tests
# =============================================================================


class TestEnumValues:
    """Test enum string values for serialization consistency."""

    def test_seller_relationship_values(self):
        """SellerRelationship enum values should match spec."""
        assert SellerRelationship.UNKNOWN.value == "unknown"
        assert SellerRelationship.NEW.value == "new"
        assert SellerRelationship.ESTABLISHED.value == "established"
        assert SellerRelationship.TRUSTED.value == "trusted"

    def test_campaign_goal_values(self):
        """CampaignGoal enum values should match spec."""
        assert CampaignGoal.AWARENESS.value == "awareness"
        assert CampaignGoal.PERFORMANCE.value == "performance"

    def test_seller_relationship_from_string(self):
        """SellerRelationship should be constructible from strings."""
        assert SellerRelationship("unknown") == SellerRelationship.UNKNOWN
        assert SellerRelationship("trusted") == SellerRelationship.TRUSTED

    def test_campaign_goal_from_string(self):
        """CampaignGoal should be constructible from strings."""
        assert CampaignGoal("awareness") == CampaignGoal.AWARENESS
        assert CampaignGoal("performance") == CampaignGoal.PERFORMANCE


# =============================================================================
# ApiKeyStore — edge cases for persistence, encoding, special characters
# =============================================================================


class TestApiKeyStoreEdgeCases:
    """Additional edge case tests for ApiKeyStore."""

    def test_special_characters_in_key(self, tmp_path: Path):
        """API keys with special characters should round-trip correctly."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        key = "sk_live_abc123!@#$%^&*()_+-=[]{}|;':\",./<>?"
        store.add_key("https://seller.example.com", key)

        # Reload and verify
        store2 = ApiKeyStore(store_path=tmp_path / "keys.json")
        assert store2.get_key("https://seller.example.com") == key

    def test_unicode_in_key(self, tmp_path: Path):
        """API keys with unicode characters should round-trip correctly."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        key = "key_with_unicode_\u00e9\u00e8\u00ea"
        store.add_key("https://seller.example.com", key)

        store2 = ApiKeyStore(store_path=tmp_path / "keys.json")
        assert store2.get_key("https://seller.example.com") == key

    def test_empty_key_string(self, tmp_path: Path):
        """Empty string as API key should be stored and retrieved."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        store.add_key("https://seller.example.com", "")
        assert store.get_key("https://seller.example.com") == ""

    def test_multiple_trailing_slashes_normalized(self, tmp_path: Path):
        """Multiple trailing slashes should be normalized."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        store.add_key("https://seller.example.com///", "key1")
        assert store.get_key("https://seller.example.com") == "key1"

    def test_parent_directory_created_automatically(self, tmp_path: Path):
        """Store should create parent directories when saving."""
        deep_path = tmp_path / "deep" / "nested" / "dir" / "keys.json"
        store = ApiKeyStore(store_path=deep_path)
        store.add_key("https://seller.example.com", "key1")
        assert deep_path.exists()

    def test_corrupted_base64_in_store(self, tmp_path: Path):
        """Corrupted base64 values should be handled gracefully."""
        store_path = tmp_path / "keys.json"
        # Write invalid base64 data
        store_path.write_text(json.dumps({
            "https://seller.example.com": "not-valid-base64!!!"
        }))
        store = ApiKeyStore(store_path=store_path)
        # Should start empty due to decode error
        assert store.list_sellers() == []

    def test_many_keys_stored(self, tmp_path: Path):
        """Store should handle many keys efficiently."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        for i in range(100):
            store.add_key(f"https://seller{i}.example.com", f"key_{i}")

        assert len(store.list_sellers()) == 100
        assert store.get_key("https://seller50.example.com") == "key_50"

    def test_rotate_key_updates_persisted_value(self, tmp_path: Path):
        """Key rotation should persist the new value to disk."""
        store_path = tmp_path / "keys.json"
        store = ApiKeyStore(store_path=store_path)
        store.add_key("https://seller.example.com", "old_key")
        store.rotate_key("https://seller.example.com", "new_key")

        # Reload from disk
        store2 = ApiKeyStore(store_path=store_path)
        assert store2.get_key("https://seller.example.com") == "new_key"

    def test_remove_key_persists_deletion(self, tmp_path: Path):
        """Key removal should persist to disk."""
        store_path = tmp_path / "keys.json"
        store = ApiKeyStore(store_path=store_path)
        store.add_key("https://seller.example.com", "key1")
        store.remove_key("https://seller.example.com")

        # Reload from disk
        store2 = ApiKeyStore(store_path=store_path)
        assert store2.get_key("https://seller.example.com") is None
        assert store2.list_sellers() == []

    def test_store_file_not_readable(self, tmp_path: Path):
        """Unreadable store file should be handled gracefully."""
        store_path = tmp_path / "keys.json"
        store_path.write_text("{}")  # Valid but...
        store_path.chmod(0o000)
        try:
            store = ApiKeyStore(store_path=store_path)
            # Should start empty due to read error
            assert store.list_sellers() == []
        finally:
            store_path.chmod(0o644)


# =============================================================================
# AuthMiddleware — edge cases for URL extraction, header types, status codes
# =============================================================================


class TestAuthMiddlewareEdgeCases:
    """Additional edge case tests for AuthMiddleware."""

    def test_extract_base_url_with_port(self, tmp_path: Path):
        """Base URL extraction should preserve the port number."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        store.add_key("https://seller.example.com:8443", "key1")
        middleware = AuthMiddleware(key_store=store)

        request = httpx.Request("GET", "https://seller.example.com:8443/api/v1/products")
        modified = middleware.add_auth(request)
        assert modified.headers.get("X-Api-Key") == "key1"

    def test_extract_base_url_with_path_and_query(self, tmp_path: Path):
        """Base URL extraction should strip path and query params."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        store.add_key("https://seller.example.com", "key1")
        middleware = AuthMiddleware(key_store=store)

        request = httpx.Request(
            "GET", "https://seller.example.com/api/products?limit=10&offset=0"
        )
        modified = middleware.add_auth(request)
        assert modified.headers.get("X-Api-Key") == "key1"

    def test_handle_response_500(self, tmp_path: Path):
        """500 response should not trigger reauth."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        middleware = AuthMiddleware(key_store=store)

        response = httpx.Response(
            status_code=500,
            request=httpx.Request("GET", "https://seller.example.com/api/products"),
        )
        result = middleware.handle_response(response)
        assert result.needs_reauth is False
        assert result.status_code == 500

    def test_handle_response_302(self, tmp_path: Path):
        """302 redirect response should not trigger reauth."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        middleware = AuthMiddleware(key_store=store)

        response = httpx.Response(
            status_code=302,
            request=httpx.Request("GET", "https://seller.example.com/api/products"),
        )
        result = middleware.handle_response(response)
        assert result.needs_reauth is False
        assert result.status_code == 302

    def test_handle_response_401_captures_seller_url(self, tmp_path: Path):
        """401 response should capture the correct seller URL."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        middleware = AuthMiddleware(key_store=store)

        response = httpx.Response(
            status_code=401,
            request=httpx.Request("GET", "https://premium.seller.com:9000/v2/deals"),
        )
        result = middleware.handle_response(response)
        assert result.needs_reauth is True
        assert result.seller_url == "https://premium.seller.com:9000"
        assert result.status_code == 401

    def test_add_auth_preserves_request_method(self, tmp_path: Path):
        """Auth header injection should preserve the original HTTP method."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        store.add_key("https://seller.example.com", "key1")
        middleware = AuthMiddleware(key_store=store)

        for method in ["GET", "POST", "PUT", "DELETE", "PATCH"]:
            request = httpx.Request(method, "https://seller.example.com/api")
            modified = middleware.add_auth(request)
            assert modified.method == method

    def test_add_auth_preserves_request_body(self, tmp_path: Path):
        """Auth header injection should preserve the request body."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        store.add_key("https://seller.example.com", "key1")
        middleware = AuthMiddleware(key_store=store)

        body = b'{"product_id": "p1"}'
        request = httpx.Request("POST", "https://seller.example.com/api", content=body)
        modified = middleware.add_auth(request)
        assert modified.content == body

    def test_bearer_and_api_key_are_mutually_exclusive(self, tmp_path: Path):
        """Bearer mode should not add X-Api-Key, and vice versa."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        store.add_key("https://seller.example.com", "key1")

        # API key mode
        mw_api = AuthMiddleware(key_store=store, header_type="api_key")
        req = httpx.Request("GET", "https://seller.example.com/api")
        modified = mw_api.add_auth(req)
        assert "X-Api-Key" in modified.headers
        assert "Authorization" not in modified.headers

        # Bearer mode
        mw_bearer = AuthMiddleware(key_store=store, header_type="bearer")
        req2 = httpx.Request("GET", "https://seller.example.com/api")
        modified2 = mw_bearer.add_auth(req2)
        assert "Authorization" in modified2.headers
        assert modified2.headers.get("X-Api-Key") is None


# =============================================================================
# AuthResponse dataclass tests
# =============================================================================


class TestAuthResponse:
    """Tests for AuthResponse dataclass defaults and construction."""

    def test_default_values(self):
        """Default AuthResponse should indicate no reauth needed."""
        resp = AuthResponse()
        assert resp.needs_reauth is False
        assert resp.seller_url == ""
        assert resp.status_code == 0

    def test_custom_values(self):
        """AuthResponse should store custom values."""
        resp = AuthResponse(
            needs_reauth=True,
            seller_url="https://seller.example.com",
            status_code=401,
        )
        assert resp.needs_reauth is True
        assert resp.seller_url == "https://seller.example.com"
        assert resp.status_code == 401


# =============================================================================
# SessionRecord — edge cases
# =============================================================================


class TestSessionRecordEdgeCases:
    """Additional edge case tests for SessionRecord."""

    def test_is_expired_with_timezone_naive_expires(self):
        """Session with timezone-naive expires_at should handle correctly."""
        # This covers line 39 in session_store.py
        naive_future = (datetime.now(timezone.utc) + timedelta(days=1)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        record = SessionRecord(
            session_id="s1",
            seller_url="http://seller.example.com",
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=naive_future,
        )
        assert record.is_expired() is False

    def test_is_expired_with_timezone_naive_past(self):
        """Expired session with timezone-naive expires_at should be detected."""
        naive_past = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        record = SessionRecord(
            session_id="s1",
            seller_url="http://seller.example.com",
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=naive_past,
        )
        assert record.is_expired() is True

    def test_from_dict_with_extra_fields(self):
        """from_dict should ignore extra fields gracefully."""
        data = {
            "session_id": "s1",
            "seller_url": "http://seller.example.com",
            "created_at": "2026-01-01T00:00:00+00:00",
            "expires_at": "2026-01-08T00:00:00+00:00",
        }
        record = SessionRecord.from_dict(data)
        assert record.session_id == "s1"

    def test_from_dict_missing_field_raises(self):
        """from_dict with missing required field should raise."""
        data = {
            "session_id": "s1",
            # Missing seller_url
            "created_at": "2026-01-01T00:00:00+00:00",
            "expires_at": "2026-01-08T00:00:00+00:00",
        }
        with pytest.raises(KeyError):
            SessionRecord.from_dict(data)


# =============================================================================
# SessionStore — edge cases (cover lines 96-97)
# =============================================================================


class TestSessionStoreEdgeCases:
    """Additional edge case tests for SessionStore."""

    def test_corrupted_json_file_starts_empty(self, tmp_path):
        """Corrupted JSON store file should start with empty sessions."""
        store_path = str(tmp_path / "sessions.json")
        with open(store_path, "w") as f:
            f.write("{invalid json")
        store = SessionStore(store_path)
        assert store.list_sessions() == {}

    def test_missing_key_in_store_data(self, tmp_path):
        """Store data with missing record keys should start empty."""
        # This covers line 96-97 (KeyError in _load)
        store_path = str(tmp_path / "sessions.json")
        with open(store_path, "w") as f:
            # Valid JSON but invalid session record structure (missing session_id)
            json.dump({
                "http://seller.example.com": {
                    "seller_url": "http://seller.example.com",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "expires_at": "2026-01-08T00:00:00+00:00",
                    # Missing "session_id" key
                }
            }, f)
        store = SessionStore(store_path)
        assert store.list_sessions() == {}

    def test_save_creates_directory(self, tmp_path):
        """SessionStore should create directories when saving."""
        store_path = str(tmp_path / "subdir" / "sessions.json")
        store = SessionStore(store_path)
        record = SessionRecord(
            session_id="s1",
            seller_url="http://seller.example.com",
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        )
        store.save(record)
        assert os.path.exists(store_path)

    def test_replace_existing_session(self, tmp_path):
        """Saving a session for an existing seller should replace it."""
        store_path = str(tmp_path / "sessions.json")
        store = SessionStore(store_path)

        old = SessionRecord(
            session_id="old",
            seller_url="http://seller.example.com",
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        )
        store.save(old)

        new = SessionRecord(
            session_id="new",
            seller_url="http://seller.example.com",
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        )
        store.save(new)

        assert store.get("http://seller.example.com").session_id == "new"
        assert len(store.list_sessions()) == 1

    def test_cleanup_expired_no_expired(self, tmp_path):
        """Cleanup with no expired sessions should return 0."""
        store_path = str(tmp_path / "sessions.json")
        store = SessionStore(store_path)
        record = SessionRecord(
            session_id="active",
            seller_url="http://seller.example.com",
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        )
        store.save(record)
        assert store.cleanup_expired() == 0

    def test_cleanup_expired_all_expired(self, tmp_path):
        """Cleanup when all sessions are expired should remove all."""
        store_path = str(tmp_path / "sessions.json")
        store = SessionStore(store_path)
        for i in range(3):
            record = SessionRecord(
                session_id=f"expired-{i}",
                seller_url=f"http://seller{i}.example.com",
                created_at=(datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
                expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            )
            store.save(record)
        assert store.cleanup_expired() == 3
        assert store.list_sessions() == {}

    def test_cleanup_expired_on_empty_store(self, tmp_path):
        """Cleanup on empty store should return 0."""
        store_path = str(tmp_path / "sessions.json")
        store = SessionStore(store_path)
        assert store.cleanup_expired() == 0


# =============================================================================
# SessionManager — edge cases for error handling (covers lines 182, 219, 263-264)
# =============================================================================


class TestSessionManagerEdgeCases:
    """Edge case tests for SessionManager."""

    @pytest.fixture
    def store_path(self, tmp_path):
        return str(tmp_path / "sessions.json")

    @pytest.fixture
    def manager(self, store_path):
        return SessionManager(store_path=store_path)

    @pytest.mark.asyncio
    async def test_send_message_retry_fails(self, manager):
        """If retry after 404 also fails, RuntimeError should be raised."""
        # Covers line 182 — RuntimeError on failed retry
        record = SessionRecord(
            session_id="sess-stale",
            seller_url="http://seller.example.com",
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        )
        manager.store.save(record)

        mock_404 = MagicMock()
        mock_404.status_code = 404
        mock_404.json.return_value = {"error": "Session not found"}

        mock_create = MagicMock()
        mock_create.status_code = 201
        mock_create.json.return_value = {
            "session_id": "sess-new",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        }

        # Retry fails with 500
        mock_retry_fail = MagicMock()
        mock_retry_fail.status_code = 500
        mock_retry_fail.text = "Internal Server Error"

        with patch("ad_buyer.sessions.session_manager.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(
                side_effect=[mock_404, mock_create, mock_retry_fail]
            )

            with pytest.raises(RuntimeError, match="Failed to send message"):
                await manager.send_message(
                    seller_url="http://seller.example.com",
                    session_id="sess-stale",
                    message={"type": "query"},
                    buyer_identity={"seat_id": "s1"},
                )

    @pytest.mark.asyncio
    async def test_create_session_with_client_failure(self, manager):
        """_create_session_with_client should raise RuntimeError on non-200/201."""
        # Covers line 219 — RuntimeError from internal helper
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(RuntimeError, match="Failed to create session"):
            await manager._create_session_with_client(
                mock_client,
                seller_url="http://seller.example.com",
                buyer_identity={"seat_id": "s1"},
            )

    @pytest.mark.asyncio
    async def test_close_session_network_error(self, manager):
        """Close session should handle network errors gracefully."""
        # Covers lines 263-264 — except block in close_session
        record = SessionRecord(
            session_id="sess-close",
            seller_url="http://seller.example.com",
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        )
        manager.store.save(record)

        with patch("ad_buyer.sessions.session_manager.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

            # Should not raise — network errors are caught
            await manager.close_session(
                seller_url="http://seller.example.com",
                session_id="sess-close",
            )

        # Session should still be removed from local store
        assert manager.store.get("http://seller.example.com") is None

    @pytest.mark.asyncio
    async def test_send_message_non_retry_error(self, manager):
        """Non-404 error on send_message should raise immediately (no retry)."""
        record = SessionRecord(
            session_id="sess-1",
            seller_url="http://seller.example.com",
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        )
        manager.store.save(record)

        mock_500 = MagicMock()
        mock_500.status_code = 500
        mock_500.text = "Server Error"

        with patch("ad_buyer.sessions.session_manager.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_500)

            with pytest.raises(RuntimeError, match="Failed to send message"):
                await manager.send_message(
                    seller_url="http://seller.example.com",
                    session_id="sess-1",
                    message={"type": "query"},
                )

    @pytest.mark.asyncio
    async def test_create_session_accepts_200(self, manager):
        """create_session should accept 200 status code (not only 201)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "session_id": "sess-200",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        }

        with patch("ad_buyer.sessions.session_manager.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)

            session_id = await manager.create_session(
                seller_url="http://seller.example.com",
                buyer_identity={"seat_id": "s1"},
            )

        assert session_id == "sess-200"

    @pytest.mark.asyncio
    async def test_create_session_without_buyer_identity(self, manager):
        """create_session with no buyer identity should send empty payload."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "session_id": "sess-no-id",
        }

        with patch("ad_buyer.sessions.session_manager.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)

            session_id = await manager.create_session(
                seller_url="http://seller.example.com",
            )

        assert session_id == "sess-no-id"
        # Verify the post was called with empty dict (no buyer_identity key)
        call_args = mock_client.post.call_args
        assert call_args[1]["json"] == {}

    @pytest.mark.asyncio
    async def test_create_session_without_timestamps_in_response(self, manager):
        """create_session should use defaults if seller omits timestamps."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "session_id": "sess-no-ts",
            # No created_at or expires_at
        }

        with patch("ad_buyer.sessions.session_manager.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)

            session_id = await manager.create_session(
                seller_url="http://seller.example.com",
                buyer_identity={"seat_id": "s1"},
            )

        assert session_id == "sess-no-ts"
        # Session should be stored with generated timestamps
        stored = manager.store.get("http://seller.example.com")
        assert stored is not None
        assert stored.created_at is not None
        assert stored.expires_at is not None

    def test_custom_timeout(self, tmp_path):
        """SessionManager should accept custom timeout."""
        store_path = str(tmp_path / "sessions.json")
        manager = SessionManager(store_path=store_path, timeout=60.0)
        assert manager._timeout == 60.0

    def test_list_active_sessions_empty(self, manager):
        """list_active_sessions on empty store should return empty dict."""
        assert manager.list_active_sessions() == {}


# =============================================================================
# Integration-style: strategy + identity model interaction
# =============================================================================


class TestStrategyIdentityIntegration:
    """Tests that exercise the strategy + identity model together."""

    def test_full_workflow_pg_deal(self):
        """Full PG deal workflow: recommend tier, build identity, estimate savings."""
        strategy = IdentityStrategy()
        full = BuyerIdentity(
            seat_id="s1",
            seat_name="DSP",
            agency_id="a1",
            agency_name="Agency",
            agency_holding_company="HC",
            advertiser_id="ad1",
            advertiser_name="Advertiser",
            advertiser_industry="Tech",
        )

        ctx = DealContext(
            deal_type=DealType.PROGRAMMATIC_GUARANTEED,
            deal_value_usd=500_000.0,
            seller_relationship=SellerRelationship.TRUSTED,
            campaign_goal=CampaignGoal.PERFORMANCE,
        )

        # Recommend tier
        tier = strategy.recommend_tier(ctx)
        assert tier == AccessTier.ADVERTISER

        # Build identity at recommended tier
        masked = strategy.build_identity(full, tier)
        assert masked.get_access_tier() == AccessTier.ADVERTISER
        assert masked.advertiser_id == "ad1"

        # Estimate savings
        savings = strategy.estimate_savings(20.0, AccessTier.PUBLIC, tier)
        assert savings == pytest.approx(3.0)

    def test_full_workflow_conservative_deal(self):
        """Conservative deal: low value, unknown seller, awareness campaign."""
        strategy = IdentityStrategy()
        full = BuyerIdentity(
            seat_id="s1",
            seat_name="DSP",
            agency_id="a1",
            agency_name="Agency",
            agency_holding_company="HC",
            advertiser_id="ad1",
            advertiser_name="Advertiser",
            advertiser_industry="Tech",
        )

        ctx = DealContext(
            deal_type=DealType.PRIVATE_AUCTION,
            deal_value_usd=1_000.0,
            seller_relationship=SellerRelationship.UNKNOWN,
            campaign_goal=CampaignGoal.AWARENESS,
        )

        tier = strategy.recommend_tier(ctx)
        assert tier == AccessTier.SEAT

        masked = strategy.build_identity(full, tier)
        assert masked.get_access_tier() == AccessTier.SEAT
        assert masked.agency_id is None
        assert masked.advertiser_id is None

        # Headers should only reveal seat info
        headers = masked.to_header_dict()
        assert "X-DSP-Seat-ID" in headers
        assert "X-Agency-ID" not in headers
        assert "X-Advertiser-ID" not in headers

    def test_strategy_with_insufficient_identity(self):
        """When strategy recommends high tier but identity lacks fields."""
        strategy = IdentityStrategy()
        seat_only = BuyerIdentity(seat_id="s1", seat_name="DSP")

        ctx = DealContext(
            deal_type=DealType.PROGRAMMATIC_GUARANTEED,
            deal_value_usd=500_000.0,
        )

        # Strategy recommends ADVERTISER
        tier = strategy.recommend_tier(ctx)
        assert tier == AccessTier.ADVERTISER

        # But identity only has seat data
        masked = strategy.build_identity(seat_only, tier)
        # Actual tier reflects available data, not requested tier
        assert masked.get_access_tier() == AccessTier.SEAT
        assert masked.advertiser_id is None

    def test_all_deal_type_relationship_goal_combos_produce_valid_tier(self):
        """Every combination of deal type, relationship, and goal produces valid tier."""
        strategy = IdentityStrategy()
        for deal_type in DealType:
            for relationship in SellerRelationship:
                for goal in CampaignGoal:
                    for value in [0.0, 1_000.0, 25_000.0, 100_000.0, 500_000.0]:
                        ctx = DealContext(
                            deal_type=deal_type,
                            deal_value_usd=value,
                            seller_relationship=relationship,
                            campaign_goal=goal,
                        )
                        tier = strategy.recommend_tier(ctx)
                        assert isinstance(tier, AccessTier)
                        assert tier in list(AccessTier)
