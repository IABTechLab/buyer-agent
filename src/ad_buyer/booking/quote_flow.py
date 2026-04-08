# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Quote-then-book flow module for deal creation.

Encapsulates the quote-then-book workflow:
1. Get product details from seller
2. Calculate tiered pricing
3. Build deal data with Deal ID and activation instructions

This module is used by both DealLibrary and the campaign
channel specialists for creating deals.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from ..models.buyer_identity import BuyerContext
from .deal_id import generate_deal_id
from .pricing import PricingCalculator, PricingResult


class QuoteFlowClient:
    """Client for the quote-then-book deal creation flow.

    Provides pricing calculation and deal data construction
    using the centralized PricingCalculator and deal ID generator.

    Example:
        client = QuoteFlowClient(
            buyer_context=buyer_context,
            seller_base_url="http://localhost:5000",
        )
        pricing = client.get_pricing(product, volume=5_000_000)
        deal_data = client.build_deal_data(product, deal_type="PD")
    """

    def __init__(
        self,
        buyer_context: BuyerContext,
        seller_base_url: str,
    ) -> None:
        """Initialize the quote flow client.

        Args:
            buyer_context: Buyer context with identity for tiered access.
            seller_base_url: Base URL of the seller's API.
        """
        self._buyer_context = buyer_context
        self._seller_base_url = seller_base_url
        self._pricing_calculator = PricingCalculator()

    def get_pricing(
        self,
        product: dict[str, Any],
        volume: int | None = None,
        target_cpm: float | None = None,
        deal_type: str | None = None,
    ) -> PricingResult | None:
        """Calculate pricing for a product based on buyer context.

        Args:
            product: Product data dict (must contain 'basePrice' or 'price').
            volume: Requested impression volume.
            target_cpm: Target CPM for negotiation.
            deal_type: Deal type requested.

        Returns:
            PricingResult with all pricing details, or None if no
            valid pricing is available from the seller.
        """
        base_price = product.get("basePrice", product.get("price"))
        if not isinstance(base_price, (int, float)):
            return None

        tier = self._buyer_context.identity.get_access_tier()
        tier_discount = self._buyer_context.identity.get_discount_percentage()
        can_negotiate = self._buyer_context.can_negotiate()
        negotiation_enabled = product.get("negotiation_enabled", False)

        return self._pricing_calculator.calculate(
            base_price=base_price,
            tier=tier,
            tier_discount=tier_discount,
            volume=volume,
            target_cpm=target_cpm,
            can_negotiate=can_negotiate,
            negotiation_enabled=negotiation_enabled,
            deal_type=deal_type,
        )

    def build_deal_data(
        self,
        product: dict[str, Any],
        deal_type: str = "PD",
        impressions: int | None = None,
        flight_start: str | None = None,
        flight_end: str | None = None,
        target_cpm: float | None = None,
    ) -> dict[str, Any] | None:
        """Build deal data dict for deal creation.

        Calculates pricing and generates a Deal ID, returning
        a complete deal data dict suitable for persistence or
        API responses.

        Args:
            product: Product data dict.
            deal_type: Deal type ('PG', 'PD', 'PA').
            impressions: Requested impression volume.
            flight_start: Flight start date (YYYY-MM-DD).
            flight_end: Flight end date (YYYY-MM-DD).
            target_cpm: Target CPM for negotiation.

        Returns:
            Dict with deal details including deal_id, pricing,
            and activation instructions. Returns None if the
            product has no valid pricing available.
        """
        # Calculate pricing
        pricing = self.get_pricing(
            product,
            volume=impressions,
            target_cpm=target_cpm,
            deal_type=deal_type,
        )

        if pricing is None:
            return None

        # Generate deal ID
        identity = self._buyer_context.identity
        identity_seed = identity.agency_id or identity.seat_id or "public"
        deal_id = generate_deal_id(
            product_id=product.get("id", "unknown"),
            identity_seed=identity_seed,
        )

        # Default flight dates
        now = datetime.now(timezone.utc)
        if not flight_start:
            flight_start = now.strftime("%Y-%m-%d")
        if not flight_end:
            flight_end = (now + timedelta(days=30)).strftime("%Y-%m-%d")

        # Activation instructions
        activation_instructions = {
            "ttd": f"The Trade Desk > Inventory > Private Marketplace > Add Deal ID: {deal_id}",
            "dv360": f"Display & Video 360 > Inventory > My Inventory > New > Deal ID: {deal_id}",
            "amazon": f"Amazon DSP > Private Marketplace > Deals > Add Deal: {deal_id}",
            "xandr": f"Xandr > Inventory > Deals > Create Deal with ID: {deal_id}",
            "yahoo": f"Yahoo DSP > Inventory > Private Marketplace > Enter Deal ID: {deal_id}",
        }

        return {
            "deal_id": deal_id,
            "product_id": product.get("id", "unknown"),
            "product_name": product.get("name", "Unknown Product"),
            "deal_type": deal_type,
            "price": round(pricing.final_price, 2),
            "original_price": round(pricing.base_price, 2),
            "discount_applied": round(pricing.tier_discount, 1),
            "access_tier": pricing.tier.value,
            "impressions": impressions,
            "flight_start": flight_start,
            "flight_end": flight_end,
            "activation_instructions": activation_instructions,
            "expires_at": (now + timedelta(days=7)).strftime("%Y-%m-%d"),
        }
