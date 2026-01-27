# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for buyer identity models."""

import pytest

from ad_buyer.models.buyer_identity import (
    AccessTier,
    BuyerContext,
    BuyerIdentity,
    DealRequest,
    DealResponse,
    DealType,
)


class TestBuyerIdentity:
    """Tests for BuyerIdentity model."""

    def test_empty_identity_is_public_tier(self):
        """Empty identity should be public tier."""
        identity = BuyerIdentity()
        assert identity.get_access_tier() == AccessTier.PUBLIC
        assert identity.get_discount_percentage() == 0.0

    def test_seat_id_only_is_seat_tier(self):
        """Seat ID only should be seat tier with 5% discount."""
        identity = BuyerIdentity(
            seat_id="ttd-seat-123",
            seat_name="The Trade Desk",
        )
        assert identity.get_access_tier() == AccessTier.SEAT
        assert identity.get_discount_percentage() == 5.0

    def test_agency_id_is_agency_tier(self):
        """Agency ID should be agency tier with 10% discount."""
        identity = BuyerIdentity(
            seat_id="ttd-seat-123",
            agency_id="omnicom-456",
            agency_name="OMD",
            agency_holding_company="Omnicom",
        )
        assert identity.get_access_tier() == AccessTier.AGENCY
        assert identity.get_discount_percentage() == 10.0

    def test_advertiser_id_is_advertiser_tier(self):
        """Advertiser ID should be advertiser tier with 15% discount."""
        identity = BuyerIdentity(
            seat_id="ttd-seat-123",
            agency_id="omnicom-456",
            agency_name="OMD",
            advertiser_id="coca-cola-789",
            advertiser_name="Coca-Cola",
            advertiser_industry="CPG",
        )
        assert identity.get_access_tier() == AccessTier.ADVERTISER
        assert identity.get_discount_percentage() == 15.0

    def test_advertiser_without_agency_is_still_advertiser_tier(self):
        """Advertiser ID without agency should still be advertiser tier."""
        identity = BuyerIdentity(
            advertiser_id="coca-cola-789",
            advertiser_name="Coca-Cola",
        )
        assert identity.get_access_tier() == AccessTier.ADVERTISER

    def test_to_header_dict_includes_all_fields(self):
        """to_header_dict should include all non-null identity fields."""
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
        headers = identity.to_header_dict()

        assert headers["X-DSP-Seat-ID"] == "ttd-seat-123"
        assert headers["X-DSP-Seat-Name"] == "The Trade Desk"
        assert headers["X-Agency-ID"] == "omnicom-456"
        assert headers["X-Agency-Name"] == "OMD"
        assert headers["X-Agency-Holding-Company"] == "Omnicom"
        assert headers["X-Advertiser-ID"] == "coca-cola-789"
        assert headers["X-Advertiser-Name"] == "Coca-Cola"
        assert headers["X-Advertiser-Industry"] == "CPG"

    def test_to_header_dict_excludes_null_fields(self):
        """to_header_dict should exclude null fields."""
        identity = BuyerIdentity(seat_id="ttd-seat-123")
        headers = identity.to_header_dict()

        assert "X-DSP-Seat-ID" in headers
        assert "X-Agency-ID" not in headers
        assert "X-Advertiser-ID" not in headers

    def test_to_context_dict_includes_access_tier(self):
        """to_context_dict should include calculated access tier."""
        identity = BuyerIdentity(agency_id="omnicom-456")
        context = identity.to_context_dict()

        assert context["agency_id"] == "omnicom-456"
        assert context["access_tier"] == "agency"


class TestBuyerContext:
    """Tests for BuyerContext model."""

    def test_default_context_is_public(self):
        """Default context should be public tier."""
        context = BuyerContext()
        assert context.get_access_tier() == AccessTier.PUBLIC
        assert not context.is_authenticated
        assert not context.can_negotiate()
        assert not context.can_access_premium_inventory()

    def test_authenticated_context(self):
        """Authenticated context with agency should have negotiation rights."""
        identity = BuyerIdentity(
            agency_id="omnicom-456",
            agency_name="OMD",
        )
        context = BuyerContext(
            identity=identity,
            is_authenticated=True,
        )

        assert context.get_access_tier() == AccessTier.AGENCY
        assert context.is_authenticated
        assert context.can_negotiate()
        assert context.can_access_premium_inventory()

    def test_seat_tier_cannot_negotiate(self):
        """Seat tier should not have negotiation rights."""
        identity = BuyerIdentity(seat_id="ttd-seat-123")
        context = BuyerContext(identity=identity, is_authenticated=True)

        assert context.get_access_tier() == AccessTier.SEAT
        assert not context.can_negotiate()
        assert not context.can_access_premium_inventory()

    def test_advertiser_tier_has_all_access(self):
        """Advertiser tier should have full access."""
        identity = BuyerIdentity(
            agency_id="omnicom-456",
            advertiser_id="coca-cola-789",
        )
        context = BuyerContext(identity=identity, is_authenticated=True)

        assert context.get_access_tier() == AccessTier.ADVERTISER
        assert context.can_negotiate()
        assert context.can_access_premium_inventory()

    def test_default_preferred_deal_types(self):
        """Default preferred deal type should be Preferred Deal."""
        context = BuyerContext()
        assert DealType.PREFERRED_DEAL in context.preferred_deal_types

    def test_custom_preferred_deal_types(self):
        """Custom preferred deal types should be respected."""
        context = BuyerContext(
            preferred_deal_types=[
                DealType.PROGRAMMATIC_GUARANTEED,
                DealType.PRIVATE_AUCTION,
            ]
        )
        assert DealType.PROGRAMMATIC_GUARANTEED in context.preferred_deal_types
        assert DealType.PRIVATE_AUCTION in context.preferred_deal_types
        assert DealType.PREFERRED_DEAL not in context.preferred_deal_types


class TestDealType:
    """Tests for DealType enum."""

    def test_deal_type_values(self):
        """Deal type enum values should match spec."""
        assert DealType.PROGRAMMATIC_GUARANTEED.value == "PG"
        assert DealType.PREFERRED_DEAL.value == "PD"
        assert DealType.PRIVATE_AUCTION.value == "PA"

    def test_deal_type_from_string(self):
        """Deal types should be constructible from strings."""
        assert DealType("PG") == DealType.PROGRAMMATIC_GUARANTEED
        assert DealType("PD") == DealType.PREFERRED_DEAL
        assert DealType("PA") == DealType.PRIVATE_AUCTION


class TestAccessTier:
    """Tests for AccessTier enum."""

    def test_access_tier_values(self):
        """Access tier enum values should match spec."""
        assert AccessTier.PUBLIC.value == "public"
        assert AccessTier.SEAT.value == "seat"
        assert AccessTier.AGENCY.value == "agency"
        assert AccessTier.ADVERTISER.value == "advertiser"


class TestDealRequest:
    """Tests for DealRequest model."""

    def test_deal_request_creation(self):
        """Test creating a DealRequest."""
        request = DealRequest(
            product_id="prod_123",
            deal_type=DealType.PROGRAMMATIC_GUARANTEED,
            impressions=5_000_000,
            flight_start="2026-02-01",
            flight_end="2026-02-28",
            target_cpm=18.50,
            notes="Premium placement requested",
        )

        assert request.product_id == "prod_123"
        assert request.deal_type == DealType.PROGRAMMATIC_GUARANTEED
        assert request.impressions == 5_000_000
        assert request.target_cpm == 18.50

    def test_deal_request_defaults(self):
        """Test DealRequest default values."""
        request = DealRequest(product_id="prod_123")

        assert request.deal_type == DealType.PREFERRED_DEAL
        assert request.impressions is None
        assert request.target_cpm is None


class TestDealResponse:
    """Tests for DealResponse model."""

    def test_deal_response_creation(self):
        """Test creating a DealResponse."""
        response = DealResponse(
            deal_id="DEAL-A1B2C3D4",
            product_id="prod_123",
            product_name="Premium CTV Package",
            deal_type=DealType.PREFERRED_DEAL,
            price=17.00,
            original_price=20.00,
            discount_applied=15.0,
            access_tier=AccessTier.ADVERTISER,
            impressions=5_000_000,
            flight_start="2026-02-01",
            flight_end="2026-02-28",
            activation_instructions={
                "ttd": "Enter Deal ID in TTD > Inventory > PMP",
                "dv360": "Enter Deal ID in DV360 > Inventory > My Inventory",
            },
            expires_at="2026-01-25",
        )

        assert response.deal_id == "DEAL-A1B2C3D4"
        assert response.price == 17.00
        assert response.discount_applied == 15.0

    def test_get_activation_for_platform_known(self):
        """Test getting activation instructions for known platform."""
        response = DealResponse(
            deal_id="DEAL-A1B2C3D4",
            product_id="prod_123",
            product_name="Test Product",
            deal_type=DealType.PREFERRED_DEAL,
            price=17.00,
            access_tier=AccessTier.AGENCY,
            activation_instructions={
                "ttd": "Custom TTD instructions",
            },
        )

        assert response.get_activation_for_platform("TTD") == "Custom TTD instructions"
        assert response.get_activation_for_platform("ttd") == "Custom TTD instructions"

    def test_get_activation_for_platform_unknown(self):
        """Test getting activation instructions for unknown platform."""
        response = DealResponse(
            deal_id="DEAL-A1B2C3D4",
            product_id="prod_123",
            product_name="Test Product",
            deal_type=DealType.PREFERRED_DEAL,
            price=17.00,
            access_tier=AccessTier.AGENCY,
        )

        instructions = response.get_activation_for_platform("NewDSP")
        assert "DEAL-A1B2C3D4" in instructions
        assert "NewDSP" in instructions
