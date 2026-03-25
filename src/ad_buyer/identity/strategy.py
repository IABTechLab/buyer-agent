# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tiered identity presentation strategy for buyer-seller interactions.

Decides what level of identity to reveal to sellers based on deal context.
Higher identity tiers unlock better pricing but expose more buyer information.
The strategy balances information disclosure against pricing benefits.

Seller's tiered pricing:
    - PUBLIC: 0% discount (no identity)
    - SEAT: 5% discount (DSP seat ID)
    - AGENCY: 10% discount (agency ID)
    - ADVERTISER: 15% discount + volume discounts (full identity)
"""

from enum import Enum

from pydantic import BaseModel, Field

from ad_buyer.models.buyer_identity import AccessTier, BuyerIdentity, DealType


class SellerRelationship(str, Enum):
    """Level of trust/history with a seller."""

    UNKNOWN = "unknown"  # No prior relationship
    NEW = "new"  # Recently established, limited history
    ESTABLISHED = "established"  # Multiple successful deals
    TRUSTED = "trusted"  # Long-term partner, high trust


class CampaignGoal(str, Enum):
    """Campaign optimization goal."""

    AWARENESS = "awareness"  # Brand awareness, broad reach
    PERFORMANCE = "performance"  # Direct response, conversions


# Discount percentages by tier, matching seller pricing
_TIER_DISCOUNTS: dict[AccessTier, float] = {
    AccessTier.PUBLIC: 0.0,
    AccessTier.SEAT: 5.0,
    AccessTier.AGENCY: 10.0,
    AccessTier.ADVERTISER: 15.0,
}

# Tier ordering for comparisons
_TIER_ORDER: list[AccessTier] = [
    AccessTier.PUBLIC,
    AccessTier.SEAT,
    AccessTier.AGENCY,
    AccessTier.ADVERTISER,
]


class DealContext(BaseModel):
    """Context about a deal that influences identity presentation strategy.

    Captures the signals the strategy uses to decide how much identity
    to reveal: deal economics, deal structure, seller trust, and
    campaign objectives.
    """

    deal_value_usd: float = Field(
        default=0.0,
        ge=0.0,
        description="Total deal value in USD. Higher values justify revealing more identity.",
    )
    deal_type: DealType = Field(
        default=DealType.PREFERRED_DEAL,
        description="Type of programmatic deal being negotiated.",
    )
    seller_relationship: SellerRelationship = Field(
        default=SellerRelationship.UNKNOWN,
        description="Level of trust/history with the seller.",
    )
    campaign_goal: CampaignGoal = Field(
        default=CampaignGoal.AWARENESS,
        description="Campaign optimization goal.",
    )


class IdentityStrategy:
    """Decides what identity tier to present to sellers based on deal context.

    The strategy layer sits on top of BuyerIdentity and controls which
    fields are revealed. It does not modify BuyerIdentity itself --
    instead it creates masked copies at the appropriate tier level.

    Args:
        high_value_threshold_usd: Deal value above which advertiser tier
            is recommended for non-PG deals. Defaults to 100,000.
        mid_value_threshold_usd: Deal value above which agency tier is
            preferred over seat tier. Defaults to 25,000.
    """

    def __init__(
        self,
        high_value_threshold_usd: float = 100_000.0,
        mid_value_threshold_usd: float = 25_000.0,
    ) -> None:
        self._high_value_threshold = high_value_threshold_usd
        self._mid_value_threshold = mid_value_threshold_usd

    def recommend_tier(self, deal_context: DealContext) -> AccessTier:
        """Recommend what identity tier to present based on deal context.

        Decision logic:
        1. PG deals always need advertiser tier (guaranteed inventory).
        2. High-value deals benefit from advertiser tier (max discount).
        3. Trusted sellers can receive higher tiers safely.
        4. Performance campaigns benefit from higher tiers (targeting).
        5. Unknown sellers / low-value deals get minimal disclosure.

        Args:
            deal_context: Context about the deal being negotiated.

        Returns:
            Recommended AccessTier to present to the seller.
        """
        # PG deals require full identity for guaranteed inventory
        if deal_context.deal_type == DealType.PROGRAMMATIC_GUARANTEED:
            return AccessTier.ADVERTISER

        # Start with a base tier from deal value
        base_tier = self._tier_from_value(deal_context.deal_value_usd)

        # Apply relationship modifier
        base_tier = self._apply_relationship_modifier(base_tier, deal_context.seller_relationship)

        # Apply campaign goal modifier
        base_tier = self._apply_campaign_goal_modifier(base_tier, deal_context.campaign_goal)

        # Apply deal type constraints
        base_tier = self._apply_deal_type_constraint(base_tier, deal_context.deal_type)

        return base_tier

    def build_identity(
        self, buyer_identity: BuyerIdentity, target_tier: AccessTier
    ) -> BuyerIdentity:
        """Create a masked identity at the target tier level.

        Strips fields above the target tier. If the buyer doesn't have
        the fields for the target tier, returns what's available (the
        actual tier may be lower than requested).

        Does not mutate the original identity.

        Args:
            buyer_identity: Full buyer identity with all available fields.
            target_tier: The tier level to present.

        Returns:
            New BuyerIdentity with fields masked to the target tier.
        """
        if target_tier == AccessTier.PUBLIC:
            return BuyerIdentity()

        if target_tier == AccessTier.SEAT:
            return BuyerIdentity(
                seat_id=buyer_identity.seat_id,
                seat_name=buyer_identity.seat_name,
            )

        if target_tier == AccessTier.AGENCY:
            return BuyerIdentity(
                seat_id=buyer_identity.seat_id,
                seat_name=buyer_identity.seat_name,
                agency_id=buyer_identity.agency_id,
                agency_name=buyer_identity.agency_name,
                agency_holding_company=buyer_identity.agency_holding_company,
            )

        # ADVERTISER: keep everything
        return BuyerIdentity(
            seat_id=buyer_identity.seat_id,
            seat_name=buyer_identity.seat_name,
            agency_id=buyer_identity.agency_id,
            agency_name=buyer_identity.agency_name,
            agency_holding_company=buyer_identity.agency_holding_company,
            advertiser_id=buyer_identity.advertiser_id,
            advertiser_name=buyer_identity.advertiser_name,
            advertiser_industry=buyer_identity.advertiser_industry,
        )

    def estimate_savings(
        self,
        base_price: float,
        current_tier: AccessTier,
        target_tier: AccessTier,
    ) -> float:
        """Estimate savings from upgrading identity tier.

        Calculates the incremental discount gained by moving from the
        current tier to the target tier, applied to the base price.

        Args:
            base_price: Base CPM price before any discounts.
            current_tier: Current identity tier.
            target_tier: Proposed identity tier.

        Returns:
            Estimated savings in the same currency as base_price.
            Returns 0.0 if target_tier is the same or lower than current_tier.
        """
        current_discount = _TIER_DISCOUNTS[current_tier]
        target_discount = _TIER_DISCOUNTS[target_tier]
        incremental = target_discount - current_discount

        if incremental <= 0.0:
            return 0.0

        return base_price * (incremental / 100.0)

    # --- Private helpers ---

    def _tier_from_value(self, deal_value_usd: float) -> AccessTier:
        """Map deal value to a base tier recommendation."""
        if deal_value_usd >= self._high_value_threshold:
            return AccessTier.ADVERTISER
        elif deal_value_usd >= self._mid_value_threshold:
            return AccessTier.AGENCY
        elif deal_value_usd > 0:
            return AccessTier.SEAT
        return AccessTier.SEAT

    def _apply_relationship_modifier(
        self, tier: AccessTier, relationship: SellerRelationship
    ) -> AccessTier:
        """Upgrade tier based on seller trust level."""
        if relationship in (SellerRelationship.TRUSTED, SellerRelationship.ESTABLISHED):
            return self._upgrade_tier(tier, 1)
        return tier

    def _apply_campaign_goal_modifier(self, tier: AccessTier, goal: CampaignGoal) -> AccessTier:
        """Performance campaigns benefit from higher tiers."""
        if goal == CampaignGoal.PERFORMANCE:
            return self._upgrade_tier(tier, 1)
        return tier

    def _apply_deal_type_constraint(self, tier: AccessTier, deal_type: DealType) -> AccessTier:
        """Apply deal-type-specific constraints.

        Private auctions with minimal signals should stay conservative.
        """
        # No additional constraints beyond PG (handled in recommend_tier)
        return tier

    def _upgrade_tier(self, tier: AccessTier, levels: int) -> AccessTier:
        """Upgrade a tier by N levels, capping at ADVERTISER."""
        current_idx = _TIER_ORDER.index(tier)
        new_idx = min(current_idx + levels, len(_TIER_ORDER) - 1)
        return _TIER_ORDER[new_idx]
