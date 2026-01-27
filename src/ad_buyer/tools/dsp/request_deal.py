# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Deal ID request tool for DSP workflows."""

import asyncio
import hashlib
from datetime import datetime, timedelta
from typing import Any, Optional

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...clients.unified_client import Protocol, UnifiedClient
from ...models.buyer_identity import (
    AccessTier,
    BuyerContext,
    DealRequest,
    DealResponse,
    DealType,
)


class RequestDealInput(BaseModel):
    """Input schema for deal request tool."""

    product_id: str = Field(
        ...,
        description="Product ID to request deal for",
    )
    deal_type: str = Field(
        default="PD",
        description="Deal type: 'PG' (Programmatic Guaranteed), 'PD' (Preferred Deal), 'PA' (Private Auction)",
    )
    impressions: Optional[int] = Field(
        default=None,
        description="Requested impression volume (required for PG deals)",
        ge=0,
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
        description="Target CPM for negotiation (agency/advertiser tier only)",
        ge=0,
    )


class RequestDealTool(BaseTool):
    """Request a Deal ID from a seller for programmatic activation.

    This tool creates programmatic deals that can be activated in traditional
    DSP platforms (The Trade Desk, DV360, Amazon DSP, etc.).

    Deal Types:
    - PG (Programmatic Guaranteed): Fixed price, guaranteed impressions
    - PD (Preferred Deal): Fixed price, non-guaranteed first-look
    - PA (Private Auction): Auction with floor price, invited buyers

    The returned Deal ID can be entered into any DSP platform's
    Private Marketplace section for activation.
    """

    name: str = "request_deal"
    description: str = """Request a Deal ID from seller for programmatic activation.
Returns a Deal ID that can be used in DSP platforms (TTD, DV360, Amazon DSP).

Args:
    product_id: Product ID to request deal for
    deal_type: 'PG' (guaranteed), 'PD' (preferred), or 'PA' (private auction)
    impressions: Volume (required for PG deals)
    flight_start: Start date (YYYY-MM-DD)
    flight_end: End date (YYYY-MM-DD)
    target_cpm: Target price for negotiation (agency/advertiser only)

Returns:
    Deal ID and activation instructions for DSP platforms."""

    args_schema: type[BaseModel] = RequestDealInput
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
        deal_type: str = "PD",
        impressions: Optional[int] = None,
        flight_start: Optional[str] = None,
        flight_end: Optional[str] = None,
        target_cpm: Optional[float] = None,
    ) -> str:
        """Synchronous wrapper for async deal request."""
        return asyncio.run(
            self._arun(
                product_id=product_id,
                deal_type=deal_type,
                impressions=impressions,
                flight_start=flight_start,
                flight_end=flight_end,
                target_cpm=target_cpm,
            )
        )

    async def _arun(
        self,
        product_id: str,
        deal_type: str = "PD",
        impressions: Optional[int] = None,
        flight_start: Optional[str] = None,
        flight_end: Optional[str] = None,
        target_cpm: Optional[float] = None,
    ) -> str:
        """Request a deal ID from the seller."""
        try:
            # Validate deal type
            try:
                deal_type_enum = DealType(deal_type.upper())
            except ValueError:
                return f"Invalid deal type '{deal_type}'. Use 'PG', 'PD', or 'PA'."

            # Validate PG requirements
            if deal_type_enum == DealType.PROGRAMMATIC_GUARANTEED and not impressions:
                return "Programmatic Guaranteed (PG) deals require an impressions volume."

            # Check negotiation eligibility
            tier = self._buyer_context.identity.get_access_tier()
            if target_cpm and not self._buyer_context.can_negotiate():
                return f"Price negotiation requires Agency or Advertiser tier (current: {tier.value})"

            # Get product details first
            product_result = await self._client.get_product(product_id)
            if not product_result.success:
                return f"Error getting product: {product_result.error}"

            product = product_result.data
            if not product:
                return f"Product {product_id} not found."

            # Calculate pricing
            deal_response = self._create_deal_response(
                product=product,
                deal_type=deal_type_enum,
                impressions=impressions,
                flight_start=flight_start,
                flight_end=flight_end,
                target_cpm=target_cpm,
            )

            return self._format_deal_response(deal_response)

        except Exception as e:
            return f"Error requesting deal: {e}"

    def _create_deal_response(
        self,
        product: dict,
        deal_type: DealType,
        impressions: Optional[int],
        flight_start: Optional[str],
        flight_end: Optional[str],
        target_cpm: Optional[float],
    ) -> DealResponse:
        """Create a deal response with calculated pricing."""
        # Get tier and base price
        tier = self._buyer_context.identity.get_access_tier()
        discount = self._buyer_context.identity.get_discount_percentage()
        base_price = product.get("basePrice", product.get("price", 20.0))

        if not isinstance(base_price, (int, float)):
            base_price = 20.0

        # Calculate tiered price
        tiered_price = base_price * (1 - discount / 100)

        # Apply volume discount for agency/advertiser
        if impressions and tier in (AccessTier.AGENCY, AccessTier.ADVERTISER):
            if impressions >= 10_000_000:
                tiered_price *= 0.90  # 10% volume discount
            elif impressions >= 5_000_000:
                tiered_price *= 0.95  # 5% volume discount

        # Handle negotiation
        final_price = tiered_price
        if target_cpm and self._buyer_context.can_negotiate():
            # Simple negotiation: accept if within 10% of floor
            floor_price = tiered_price * 0.90
            if target_cpm >= floor_price:
                final_price = target_cpm
            else:
                # Counter at floor
                final_price = floor_price

        # Generate Deal ID
        deal_id = self._generate_deal_id(product.get("id", "unknown"), tier)

        # Set default flight dates if not provided
        if not flight_start:
            flight_start = datetime.now().strftime("%Y-%m-%d")
        if not flight_end:
            flight_end = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

        # Activation instructions
        activation_instructions = {
            "ttd": f"The Trade Desk > Inventory > Private Marketplace > Add Deal ID: {deal_id}",
            "dv360": f"Display & Video 360 > Inventory > My Inventory > New > Deal ID: {deal_id}",
            "amazon": f"Amazon DSP > Private Marketplace > Deals > Add Deal: {deal_id}",
            "xandr": f"Xandr > Inventory > Deals > Create Deal with ID: {deal_id}",
            "yahoo": f"Yahoo DSP > Inventory > Private Marketplace > Enter Deal ID: {deal_id}",
        }

        return DealResponse(
            deal_id=deal_id,
            product_id=product.get("id", "unknown"),
            product_name=product.get("name", "Unknown Product"),
            deal_type=deal_type,
            price=round(final_price, 2),
            original_price=round(base_price, 2),
            discount_applied=round(discount, 1),
            access_tier=tier,
            impressions=impressions,
            flight_start=flight_start,
            flight_end=flight_end,
            activation_instructions=activation_instructions,
            expires_at=(datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d"),
        )

    def _generate_deal_id(self, product_id: str, tier: AccessTier) -> str:
        """Generate a unique Deal ID."""
        # Create a semi-random but reproducible deal ID
        timestamp = datetime.now().strftime("%Y%m%d%H%M")
        identity = self._buyer_context.identity
        seed = f"{product_id}-{identity.agency_id or identity.seat_id or 'public'}-{timestamp}"
        hash_suffix = hashlib.md5(seed.encode()).hexdigest()[:8].upper()
        return f"DEAL-{hash_suffix}"

    def _format_deal_response(self, deal: DealResponse) -> str:
        """Format deal response for output."""
        deal_type_names = {
            DealType.PROGRAMMATIC_GUARANTEED: "Programmatic Guaranteed (PG)",
            DealType.PREFERRED_DEAL: "Preferred Deal (PD)",
            DealType.PRIVATE_AUCTION: "Private Auction (PA)",
        }

        output_lines = [
            "=" * 60,
            "DEAL CREATED SUCCESSFULLY",
            "=" * 60,
            "",
            f"Deal ID: {deal.deal_id}",
            "",
            "Deal Details",
            "-" * 30,
            f"Product: {deal.product_name}",
            f"Product ID: {deal.product_id}",
            f"Deal Type: {deal_type_names.get(deal.deal_type, deal.deal_type.value)}",
            f"Flight: {deal.flight_start} to {deal.flight_end}",
        ]

        if deal.impressions:
            output_lines.append(f"Impressions: {deal.impressions:,}")

        output_lines.extend([
            "",
            "Pricing",
            "-" * 30,
            f"Original CPM: ${deal.original_price:.2f}",
            f"Your Tier: {deal.access_tier.value.upper()} ({deal.discount_applied}% discount)",
            f"Final CPM: ${deal.price:.2f}",
        ])

        if deal.impressions:
            total_cost = (deal.price / 1000) * deal.impressions
            output_lines.append(f"Estimated Total: ${total_cost:,.2f}")

        output_lines.extend([
            "",
            "Activation Instructions",
            "-" * 30,
        ])

        for platform, instruction in deal.activation_instructions.items():
            output_lines.append(f"• {platform.upper()}: {instruction}")

        output_lines.extend([
            "",
            "-" * 30,
            f"Deal expires: {deal.expires_at}",
            "",
            "Copy the Deal ID above and enter it in your DSP's",
            "Private Marketplace or Inventory section.",
            "=" * 60,
        ])

        return "\n".join(output_lines)
