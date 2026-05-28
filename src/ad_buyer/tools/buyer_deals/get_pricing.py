# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tiered pricing tool for buyer deal workflows."""

from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...async_utils import run_async
from ...booking.pricing import PricingCalculator
from ...clients.unified_client import UnifiedClient
from ...models.buyer_identity import AccessTier, BuyerContext


class GetPricingInput(BaseModel):
    """Input schema for pricing tool."""

    product_id: str = Field(
        ...,
        description="Product ID to get pricing for",
    )
    volume: int | None = Field(
        default=None,
        description="Requested impression volume (may unlock volume discounts)",
        ge=0,
    )
    deal_type: str | None = Field(
        default=None,
        description="Deal type: 'PG' (Programmatic Guaranteed), 'PD' (Preferred Deal), 'PA' (Private Auction)",
    )
    flight_start: str | None = Field(
        default=None,
        description="Flight start date (YYYY-MM-DD)",
    )
    flight_end: str | None = Field(
        default=None,
        description="Flight end date (YYYY-MM-DD)",
    )


class GetPricingTool(BaseTool):
    """Get tier-specific pricing for a product based on buyer identity.

    This tool retrieves pricing from sellers, with prices adjusted based
    on the buyer's revealed identity tier:

    | Tier       | Discount | Access Level                |
    |------------|----------|------------------------------|
    | Public     | 0%       | Price ranges, standard catalog |
    | Seat       | 5%       | Fixed prices                 |
    | Agency     | 10%      | Premium inventory, negotiation |
    | Advertiser | 15%      | Volume discounts, full negotiation |
    """

    name: str = "get_pricing"
    description: str = """Get tier-specific pricing for an advertising product.
Pricing is based on revealed buyer identity (seat, agency, advertiser).

Args:
    product_id: Product ID to get pricing for
    volume: Requested impressions (may unlock volume discounts)
    deal_type: Deal type ('PG', 'PD', 'PA')
    flight_start: Start date (YYYY-MM-DD)
    flight_end: End date (YYYY-MM-DD)

Returns:
    Detailed pricing information including tier discounts and deal options."""

    args_schema: type[BaseModel] = GetPricingInput
    _client: UnifiedClient
    _buyer_context: BuyerContext

    def __init__(
        self,
        client: UnifiedClient,
        buyer_context: BuyerContext,
        **kwargs: Any,
    ):
        """Initialize with unified client and buyer context.

        Args:
            client: UnifiedClient for seller communication
            buyer_context: BuyerContext with identity for tiered access
        """
        super().__init__(**kwargs)
        self._client = client
        self._buyer_context = buyer_context

    def _run(
        self,
        product_id: str,
        volume: int | None = None,
        deal_type: str | None = None,
        flight_start: str | None = None,
        flight_end: str | None = None,
    ) -> str:
        """Synchronous wrapper for async pricing."""
        return run_async(
            self._arun(
                product_id=product_id,
                volume=volume,
                deal_type=deal_type,
                flight_start=flight_start,
                flight_end=flight_end,
            )
        )

    async def _arun(
        self,
        product_id: str,
        volume: int | None = None,
        deal_type: str | None = None,
        flight_start: str | None = None,
        flight_end: str | None = None,
    ) -> str:
        """Get tier-specific pricing for the product."""
        try:
            # Get product details
            result = await self._client.get_product(product_id)

            if not result.success:
                return f"Error getting pricing: {result.error}"

            product = result.data
            if not product:
                return f"Product {product_id} not found."

            return self._format_pricing(product, volume, deal_type, flight_start, flight_end)

        except (OSError, ValueError, RuntimeError) as e:
            return f"Error getting pricing: {e}"

    def _format_pricing(
        self,
        product: dict,
        volume: int | None,
        deal_type: str | None,
        flight_start: str | None,
        flight_end: str | None,
    ) -> str:
        """Format pricing response with tier calculations.

        Uses the centralized PricingCalculator from ad_buyer.booking
        to avoid duplicated pricing logic.
        """
        tier = self._buyer_context.identity.get_access_tier()
        discount = self._buyer_context.identity.get_discount_percentage()

        # Extract product info
        product_id = product.get("id", "Unknown")
        name = product.get("name", "Unknown Product")
        base_price = product.get("basePrice", product.get("price", 0))
        publisher = product.get("publisherId", product.get("publisher", "Unknown"))
        rate_type = product.get("rateType", "CPM")

        # Calculate pricing using centralized calculator
        if not isinstance(base_price, (int, float)):
            base_price = 0

        calculator = PricingCalculator()
        pricing = calculator.calculate(
            base_price=base_price,
            tier=tier,
            tier_discount=discount,
            volume=volume,
            deal_type=deal_type,
        )

        tiered_price = pricing.tiered_price
        volume_discount = pricing.volume_discount
        final_price = pricing.final_price

        # Build output
        output_lines = [
            f"Pricing for: {name}",
            f"Product ID: {product_id}",
            f"Publisher: {publisher}",
            "=" * 50,
            "",
            "Your Access Tier",
            "-" * 20,
            f"Tier: {tier.value.upper()}",
            f"Tier Discount: {discount}%",
        ]

        if volume_discount > 0:
            output_lines.append(f"Volume Discount: {volume_discount}%")

        output_lines.extend(
            [
                "",
                "Pricing Breakdown",
                "-" * 20,
                f"Base {rate_type}: ${base_price:.2f}"
                if isinstance(base_price, (int, float))
                else f"Base {rate_type}: {base_price}",
            ]
        )

        if discount > 0:
            output_lines.append(f"After Tier Discount: ${tiered_price:.2f}")

        if volume_discount > 0:
            output_lines.append(f"After Volume Discount: ${final_price:.2f}")

        output_lines.extend(
            [
                "",
                f"Final {rate_type}: ${final_price:.2f}",
            ]
        )

        # Cost projection if volume provided
        if volume:
            total_cost = (final_price / 1000) * volume
            output_lines.extend(
                [
                    "",
                    "Cost Projection",
                    "-" * 20,
                    f"Impressions: {volume:,}",
                    f"Estimated Cost: ${total_cost:,.2f}",
                ]
            )

        # Deal type information
        output_lines.extend(
            [
                "",
                "Available Deal Types",
                "-" * 20,
            ]
        )

        deal_options = self._get_deal_options(tier, final_price, deal_type)
        for deal_opt in deal_options:
            output_lines.append(deal_opt)

        # Negotiation availability -- only when BOTH buyer can negotiate
        # AND the seller's package has negotiation_enabled=True (ar-9xi)
        package_negotiation_enabled = product.get("negotiation_enabled", False)
        if self._buyer_context.can_negotiate() and package_negotiation_enabled:
            output_lines.extend(
                [
                    "",
                    "Negotiation",
                    "-" * 20,
                    "Price negotiation is available at your tier.",
                    "Contact seller or use request_deal tool with target_cpm parameter.",
                ]
            )

        return "\n".join(output_lines)

    def _get_deal_options(
        self,
        tier: AccessTier,
        price: float,
        requested_type: str | None,
    ) -> list[str]:
        """Get available deal options based on tier."""
        options = []

        # PG - available to all tiers
        pg_available = "✓" if tier != AccessTier.PUBLIC else "○"
        options.append(
            f"{pg_available} Programmatic Guaranteed (PG): ${price:.2f} CPM, guaranteed delivery"
        )

        # PD - available to all authenticated tiers
        pd_available = "✓" if tier != AccessTier.PUBLIC else "○"
        options.append(f"{pd_available} Preferred Deal (PD): ${price:.2f} CPM, first-look access")

        # PA - available to all
        options.append(f"✓ Private Auction (PA): Floor ${price:.2f} CPM, auction-based")

        if tier == AccessTier.PUBLIC:
            options.append("")
            options.append("Note: Authenticate with seat/agency ID to unlock fixed-price deals")

        return options
