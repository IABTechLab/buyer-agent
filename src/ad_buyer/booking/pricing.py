# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Centralized pricing calculator for deal booking.

Extracts the duplicated pricing logic from:
- unified_client.py (get_pricing, request_deal methods)
- tools/dsp/request_deal.py (_create_deal_response)
- tools/dsp/get_pricing.py (_format_pricing)

Pricing tiers:
    Public:     0% discount
    Seat:       5% discount
    Agency:    10% discount + volume discounts
    Advertiser: 15% discount + volume discounts

Volume discounts (agency/advertiser only):
    5M+ impressions:  5% additional discount
    10M+ impressions: 10% additional discount
"""

from dataclasses import dataclass
from typing import Optional

from ..models.buyer_identity import AccessTier


@dataclass
class PricingResult:
    """Result of a pricing calculation.

    Attributes:
        base_price: Original base price before any discounts.
        tier: The buyer's access tier.
        tier_discount: Tier-based discount percentage applied.
        volume_discount: Volume-based discount percentage applied.
        tiered_price: Price after tier discount (before volume discount).
        final_price: Price after all discounts (tier + volume + negotiation).
        requested_volume: Impression volume used for volume discount calculation.
        deal_type: Deal type requested (if any).
    """

    base_price: float
    tier: AccessTier
    tier_discount: float
    volume_discount: float
    tiered_price: float
    final_price: float
    requested_volume: Optional[int] = None
    deal_type: Optional[str] = None


class PricingCalculator:
    """Calculate tiered and volume-discounted pricing for deals.

    This is the single source of truth for all pricing calculations
    in the ad buyer system. All deal-booking flows should use this
    calculator instead of implementing pricing logic inline.

    Example:
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=20.0,
            tier=AccessTier.AGENCY,
            tier_discount=10.0,
            volume=5_000_000,
        )
        print(result.final_price)  # 17.1
    """

    # Volume discount thresholds (only for agency/advertiser tiers)
    VOLUME_DISCOUNT_THRESHOLDS: list[tuple[int, float]] = [
        (10_000_000, 10.0),  # 10M+ impressions: 10% discount
        (5_000_000, 5.0),    # 5M+ impressions: 5% discount
    ]

    # Tiers eligible for volume discounts
    VOLUME_ELIGIBLE_TIERS: frozenset[AccessTier] = frozenset({
        AccessTier.AGENCY,
        AccessTier.ADVERTISER,
    })

    def calculate(
        self,
        base_price: float,
        tier: AccessTier,
        tier_discount: float,
        volume: Optional[int] = None,
        target_cpm: Optional[float] = None,
        can_negotiate: bool = False,
        negotiation_enabled: bool = False,
        deal_type: Optional[str] = None,
    ) -> PricingResult:
        """Calculate the final price after tier and volume discounts.

        Args:
            base_price: Base CPM price from the product.
            tier: Buyer's access tier (public/seat/agency/advertiser).
            tier_discount: Discount percentage for the tier (0-15).
            volume: Requested impression volume (may unlock volume discounts).
            target_cpm: Buyer's target CPM for negotiation.
            can_negotiate: Whether the buyer is eligible to negotiate.
            negotiation_enabled: Whether the product supports negotiation.
            deal_type: Deal type requested (for informational purposes).

        Returns:
            PricingResult with all pricing details.
        """
        # Step 1: Apply tier discount
        tiered_price = base_price * (1 - tier_discount / 100)

        # Step 2: Calculate volume discount
        volume_discount = self._get_volume_discount(volume, tier)

        # Step 3: Apply volume discount
        if volume_discount > 0:
            final_price = tiered_price * (1 - volume_discount / 100)
        else:
            final_price = tiered_price

        # Step 4: Handle negotiation
        if target_cpm is not None and can_negotiate and negotiation_enabled:
            floor_price = tiered_price * 0.90
            if target_cpm >= floor_price:
                final_price = target_cpm
            else:
                # Counter at floor
                final_price = floor_price

        return PricingResult(
            base_price=base_price,
            tier=tier,
            tier_discount=tier_discount,
            volume_discount=volume_discount,
            tiered_price=tiered_price,
            final_price=final_price,
            requested_volume=volume,
            deal_type=deal_type,
        )

    def _get_volume_discount(
        self,
        volume: Optional[int],
        tier: AccessTier,
    ) -> float:
        """Determine the volume discount percentage.

        Volume discounts are only available for agency and advertiser tiers.

        Args:
            volume: Requested impression volume.
            tier: Buyer's access tier.

        Returns:
            Volume discount percentage (0.0, 5.0, or 10.0).
        """
        if not volume or tier not in self.VOLUME_ELIGIBLE_TIERS:
            return 0.0

        for threshold, discount in self.VOLUME_DISCOUNT_THRESHOLDS:
            if volume >= threshold:
                return discount

        return 0.0
