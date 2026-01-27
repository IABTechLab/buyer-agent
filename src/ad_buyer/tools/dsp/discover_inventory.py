# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Inventory discovery tool for DSP workflows."""

import asyncio
from typing import Any, Optional

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...clients.unified_client import Protocol, UnifiedClient
from ...models.buyer_identity import BuyerContext


class DiscoverInventoryInput(BaseModel):
    """Input schema for inventory discovery tool."""

    query: Optional[str] = Field(
        default=None,
        description="Natural language query for inventory (e.g., 'CTV inventory under $25 CPM')",
    )
    channel: Optional[str] = Field(
        default=None,
        description="Channel filter (e.g., 'ctv', 'display', 'video', 'mobile')",
    )
    max_cpm: Optional[float] = Field(
        default=None,
        description="Maximum CPM price filter",
        ge=0,
    )
    min_impressions: Optional[int] = Field(
        default=None,
        description="Minimum available impressions filter",
        ge=0,
    )
    targeting: Optional[list[str]] = Field(
        default=None,
        description="Required targeting capabilities (e.g., ['household', 'geo', 'demographic'])",
    )
    publisher: Optional[str] = Field(
        default=None,
        description="Specific publisher to search",
    )


class DiscoverInventoryTool(BaseTool):
    """Discover available advertising inventory from sellers with identity-based access.

    This tool queries sellers for available inventory, presenting the buyer's
    identity context to unlock tiered pricing and premium inventory access.

    Access tiers:
    - Public: Price ranges only, limited catalog
    - Seat: Fixed prices with 5% discount
    - Agency: 10% discount, premium inventory access
    - Advertiser: 15% discount, full negotiation capability
    """

    name: str = "discover_inventory"
    description: str = """Discover available advertising inventory from sellers.
Presents buyer identity to unlock tiered pricing and premium access.

Args:
    query: Natural language query (e.g., 'CTV inventory under $25 CPM')
    channel: Channel filter ('ctv', 'display', 'video', 'mobile')
    max_cpm: Maximum CPM price
    min_impressions: Minimum available impressions
    targeting: Required targeting capabilities
    publisher: Specific publisher to search

Returns:
    List of available products with pricing based on buyer's access tier."""

    args_schema: type[BaseModel] = DiscoverInventoryInput
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
        query: Optional[str] = None,
        channel: Optional[str] = None,
        max_cpm: Optional[float] = None,
        min_impressions: Optional[int] = None,
        targeting: Optional[list[str]] = None,
        publisher: Optional[str] = None,
    ) -> str:
        """Synchronous wrapper for async discovery."""
        return asyncio.run(
            self._arun(
                query=query,
                channel=channel,
                max_cpm=max_cpm,
                min_impressions=min_impressions,
                targeting=targeting,
                publisher=publisher,
            )
        )

    async def _arun(
        self,
        query: Optional[str] = None,
        channel: Optional[str] = None,
        max_cpm: Optional[float] = None,
        min_impressions: Optional[int] = None,
        targeting: Optional[list[str]] = None,
        publisher: Optional[str] = None,
    ) -> str:
        """Discover inventory with buyer identity context."""
        try:
            # Build filters
            filters = {}
            if channel:
                filters["channel"] = channel
            if max_cpm is not None:
                filters["maxPrice"] = max_cpm
            if min_impressions is not None:
                filters["minImpressions"] = min_impressions
            if targeting:
                filters["targeting"] = targeting
            if publisher:
                filters["publisher"] = publisher

            # Add identity context to filters
            identity_context = self._buyer_context.identity.to_context_dict()
            filters["buyer_context"] = identity_context

            # Execute search
            if query:
                result = await self._client.search_products(
                    query=query,
                    filters=filters if filters else None,
                )
            else:
                result = await self._client.list_products()

            if not result.success:
                return f"Error discovering inventory: {result.error}"

            return self._format_results(result.data, identity_context)

        except Exception as e:
            return f"Error discovering inventory: {e}"

    def _format_results(
        self,
        products: Any,
        identity_context: dict,
    ) -> str:
        """Format discovery results with tier information."""
        if not products:
            return "No inventory found matching your criteria."

        tier = identity_context.get("access_tier", "public")
        discount = self._buyer_context.identity.get_discount_percentage()

        output_lines = [
            f"Inventory Discovery Results",
            f"Access Tier: {tier.upper()} ({discount}% discount)",
            "-" * 50,
            "",
        ]

        # Handle both list and dict formats
        product_list = products if isinstance(products, list) else [products]

        for i, product in enumerate(product_list, 1):
            if isinstance(product, dict):
                product_id = product.get("id", "Unknown")
                name = product.get("name", "Unknown Product")
                publisher = product.get("publisherId", product.get("publisher", "Unknown"))
                base_price = product.get("basePrice", product.get("price", 0))
                channel = product.get("channel", product.get("deliveryType", "N/A"))
                impressions = product.get("availableImpressions", product.get("available_impressions", "N/A"))
                targeting = product.get("targeting", product.get("availableTargeting", []))

                # Calculate tiered price
                if isinstance(base_price, (int, float)) and discount > 0:
                    tiered_price = base_price * (1 - discount / 100)
                    price_display = f"${tiered_price:.2f} (was ${base_price:.2f})"
                else:
                    price_display = f"${base_price:.2f}" if isinstance(base_price, (int, float)) else str(base_price)

                output_lines.extend([
                    f"{i}. {name}",
                    f"   Product ID: {product_id}",
                    f"   Publisher: {publisher}",
                    f"   Channel: {channel}",
                    f"   CPM: {price_display}",
                    f"   Available: {impressions:,}" if isinstance(impressions, int) else f"   Available: {impressions}",
                    f"   Targeting: {', '.join(targeting) if targeting else 'Standard'}",
                    "",
                ])
            else:
                output_lines.append(f"{i}. {product}")
                output_lines.append("")

        output_lines.append("-" * 50)
        output_lines.append(f"Total products found: {len(product_list)}")

        if self._buyer_context.can_access_premium_inventory():
            output_lines.append("Premium inventory access: ENABLED")
        if self._buyer_context.can_negotiate():
            output_lines.append("Price negotiation: AVAILABLE")

        return "\n".join(output_lines)
