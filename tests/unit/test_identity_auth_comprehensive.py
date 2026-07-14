# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Comprehensive tests for the buyer-identity models and authentication modules.

Covers edge cases, boundary conditions, and uncovered paths in:
- ad_buyer.models.buyer_identity (BuyerIdentity, BuyerContext, DealRequest, DealResponse)
- ad_buyer.auth.key_store (ApiKeyStore persistence, encoding, corruption)
- ad_buyer.auth.middleware (AuthMiddleware header attachment, response handling)
"""

import json
from pathlib import Path

import httpx
import pytest

from ad_buyer.auth.key_store import ApiKeyStore
from ad_buyer.auth.middleware import AuthMiddleware, AuthResponse
from ad_buyer.models.buyer_identity import (
    AccessTier,
    BuyerContext,
    BuyerIdentity,
    DealRequest,
    DealResponse,
    DealType,
)

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
        store_path.write_text(json.dumps({"https://seller.example.com": "not-valid-base64!!!"}))
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

        request = httpx.Request("GET", "https://seller.example.com/api/products?limit=10&offset=0")
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
