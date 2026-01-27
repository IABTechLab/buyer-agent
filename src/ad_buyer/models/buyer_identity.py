# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Buyer identity models for tiered pricing access in DSP workflows."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AccessTier(str, Enum):
    """Access tier levels for tiered pricing."""

    PUBLIC = "public"  # No identity - price ranges only
    SEAT = "seat"  # DSP seat ID - 5% discount
    AGENCY = "agency"  # Agency ID - 10% discount
    ADVERTISER = "advertiser"  # Agency + Advertiser - 15% discount


class DealType(str, Enum):
    """Programmatic deal types."""

    PROGRAMMATIC_GUARANTEED = "PG"  # Fixed price, guaranteed impressions
    PREFERRED_DEAL = "PD"  # Fixed price, non-guaranteed first-look
    PRIVATE_AUCTION = "PA"  # Auction with floor price, invited buyers


class BuyerIdentity(BaseModel):
    """Buyer identity for tiered pricing access.

    Identity revelation unlocks progressively better pricing:
    - No identity: Public tier, price ranges only
    - Seat ID: Seat tier, fixed prices with 5% discount
    - Agency ID: Agency tier, 10% discount + premium inventory
    - Advertiser ID: Advertiser tier, 15% discount + volume discounts
    """

    seat_id: Optional[str] = Field(
        default=None,
        description="DSP seat identifier (e.g., 'ttd-seat-123')",
    )
    seat_name: Optional[str] = Field(
        default=None,
        description="DSP platform name (e.g., 'The Trade Desk')",
    )
    agency_id: Optional[str] = Field(
        default=None,
        description="Agency identifier (e.g., 'omnicom-456')",
    )
    agency_name: Optional[str] = Field(
        default=None,
        description="Agency display name (e.g., 'OMD')",
    )
    agency_holding_company: Optional[str] = Field(
        default=None,
        description="Agency holding company (e.g., 'Omnicom', 'WPP', 'Publicis')",
    )
    advertiser_id: Optional[str] = Field(
        default=None,
        description="Advertiser identifier (e.g., 'coca-cola-789')",
    )
    advertiser_name: Optional[str] = Field(
        default=None,
        description="Advertiser display name (e.g., 'Coca-Cola')",
    )
    advertiser_industry: Optional[str] = Field(
        default=None,
        description="Advertiser industry vertical (e.g., 'CPG', 'Auto', 'Finance')",
    )

    def get_access_tier(self) -> AccessTier:
        """Determine access tier based on revealed identity.

        Returns:
            AccessTier based on which identity fields are populated.
        """
        if self.advertiser_id:
            return AccessTier.ADVERTISER
        elif self.agency_id:
            return AccessTier.AGENCY
        elif self.seat_id:
            return AccessTier.SEAT
        return AccessTier.PUBLIC

    def get_discount_percentage(self) -> float:
        """Get the discount percentage for this identity's tier.

        Returns:
            Discount percentage (0-15) based on tier.
        """
        tier = self.get_access_tier()
        discounts = {
            AccessTier.PUBLIC: 0.0,
            AccessTier.SEAT: 5.0,
            AccessTier.AGENCY: 10.0,
            AccessTier.ADVERTISER: 15.0,
        }
        return discounts[tier]

    def to_header_dict(self) -> dict[str, str]:
        """Convert identity to HTTP headers for API calls.

        Returns:
            Dictionary of headers to include in API requests.
        """
        headers = {}
        if self.seat_id:
            headers["X-DSP-Seat-ID"] = self.seat_id
        if self.seat_name:
            headers["X-DSP-Seat-Name"] = self.seat_name
        if self.agency_id:
            headers["X-Agency-ID"] = self.agency_id
        if self.agency_name:
            headers["X-Agency-Name"] = self.agency_name
        if self.agency_holding_company:
            headers["X-Agency-Holding-Company"] = self.agency_holding_company
        if self.advertiser_id:
            headers["X-Advertiser-ID"] = self.advertiser_id
        if self.advertiser_name:
            headers["X-Advertiser-Name"] = self.advertiser_name
        if self.advertiser_industry:
            headers["X-Advertiser-Industry"] = self.advertiser_industry
        return headers

    def to_context_dict(self) -> dict[str, str | None]:
        """Convert identity to context dictionary for tool calls.

        Returns:
            Dictionary with identity context for inclusion in API payloads.
        """
        return {
            "seat_id": self.seat_id,
            "seat_name": self.seat_name,
            "agency_id": self.agency_id,
            "agency_name": self.agency_name,
            "agency_holding_company": self.agency_holding_company,
            "advertiser_id": self.advertiser_id,
            "advertiser_name": self.advertiser_name,
            "advertiser_industry": self.advertiser_industry,
            "access_tier": self.get_access_tier().value,
        }


class BuyerContext(BaseModel):
    """Full buyer context for seller interactions.

    Combines identity with authentication status and additional context
    for making requests to sellers.
    """

    identity: BuyerIdentity = Field(
        default_factory=BuyerIdentity,
        description="Buyer identity for tiered access",
    )
    is_authenticated: bool = Field(
        default=False,
        description="Whether the buyer has been authenticated with the seller",
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Session ID for maintaining state across requests",
    )
    preferred_deal_types: list[DealType] = Field(
        default_factory=lambda: [DealType.PREFERRED_DEAL],
        description="Preferred deal types in order of preference",
    )

    def get_access_tier(self) -> AccessTier:
        """Get access tier from identity.

        Returns:
            AccessTier based on identity.
        """
        return self.identity.get_access_tier()

    def can_negotiate(self) -> bool:
        """Check if buyer can engage in price negotiation.

        Agency and advertiser tiers can negotiate prices.

        Returns:
            True if negotiation is available at this tier.
        """
        tier = self.get_access_tier()
        return tier in (AccessTier.AGENCY, AccessTier.ADVERTISER)

    def can_access_premium_inventory(self) -> bool:
        """Check if buyer has access to premium inventory.

        Agency and advertiser tiers have premium access.

        Returns:
            True if premium inventory is accessible.
        """
        return self.can_negotiate()


class DealRequest(BaseModel):
    """Request for a programmatic deal from a seller."""

    product_id: str = Field(
        ...,
        description="Product ID to request deal for",
    )
    deal_type: DealType = Field(
        default=DealType.PREFERRED_DEAL,
        description="Type of programmatic deal requested",
    )
    impressions: Optional[int] = Field(
        default=None,
        ge=0,
        description="Requested impression volume (required for PG)",
    )
    flight_start: Optional[str] = Field(
        default=None,
        description="Deal start date (YYYY-MM-DD)",
    )
    flight_end: Optional[str] = Field(
        default=None,
        description="Deal end date (YYYY-MM-DD)",
    )
    target_cpm: Optional[float] = Field(
        default=None,
        ge=0,
        description="Target CPM for negotiation (agency/advertiser tier only)",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Additional notes or requirements for the deal",
    )


class DealResponse(BaseModel):
    """Response from seller with deal details."""

    deal_id: str = Field(
        ...,
        description="Unique Deal ID for programmatic activation",
    )
    product_id: str = Field(
        ...,
        description="Product ID the deal is for",
    )
    product_name: str = Field(
        ...,
        description="Product display name",
    )
    deal_type: DealType = Field(
        ...,
        description="Type of deal created",
    )
    price: float = Field(
        ...,
        ge=0,
        description="Final CPM price for the deal",
    )
    original_price: Optional[float] = Field(
        default=None,
        ge=0,
        description="Original price before tier discount",
    )
    discount_applied: Optional[float] = Field(
        default=None,
        ge=0,
        description="Discount percentage applied",
    )
    access_tier: AccessTier = Field(
        ...,
        description="Access tier that determined pricing",
    )
    impressions: Optional[int] = Field(
        default=None,
        ge=0,
        description="Guaranteed impressions (for PG deals)",
    )
    flight_start: Optional[str] = Field(
        default=None,
        description="Deal start date",
    )
    flight_end: Optional[str] = Field(
        default=None,
        description="Deal end date",
    )
    activation_instructions: dict[str, str] = Field(
        default_factory=dict,
        description="Platform-specific activation instructions",
    )
    expires_at: Optional[str] = Field(
        default=None,
        description="When this deal offer expires",
    )

    def get_activation_for_platform(self, platform: str) -> str:
        """Get activation instructions for a specific DSP platform.

        Args:
            platform: DSP platform name (e.g., 'ttd', 'dv360', 'amazon')

        Returns:
            Activation instructions for the platform.
        """
        platform_lower = platform.lower()
        if platform_lower in self.activation_instructions:
            return self.activation_instructions[platform_lower]

        # Default instructions
        return f"Enter Deal ID '{self.deal_id}' in {platform} > Inventory > Private Marketplace"
