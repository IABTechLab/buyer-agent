# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Product search tool for inventory discovery."""

import asyncio
from typing import Any, Optional

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...clients.opendirect_client import OpenDirectClient


class ProductSearchInput(BaseModel):
    """Input schema for product search tool."""

    channel: Optional[str] = Field(
        default=None,
        description="Channel type: display, video, mobile, ctv, native",
    )
    format: Optional[str] = Field(
        default=None,
        description="Ad format: banner, video, interstitial, rewarded",
    )
    min_price: Optional[float] = Field(
        default=None,
        description="Minimum CPM price in USD",
    )
    max_price: Optional[float] = Field(
        default=None,
        description="Maximum CPM price in USD",
    )
    publisher_ids: Optional[list[str]] = Field(
        default=None,
        description="Specific publisher IDs to search",
    )
    targeting_capabilities: Optional[list[str]] = Field(
        default=None,
        description="Required targeting: geo, demographic, behavioral, contextual",
    )
    delivery_type: Optional[str] = Field(
        default=None,
        description="Delivery type: Exclusive, Guaranteed, PMP",
    )
    limit: int = Field(
        default=10,
        description="Maximum number of results to return",
        ge=1,
        le=50,
    )


class ProductSearchTool(BaseTool):
    """Search for advertising products/inventory across publishers."""

    name: str = "search_advertising_products"
    description: str = """Search for advertising products/inventory across publishers
using OpenDirect API. Returns available placements with pricing, reach estimates,
and targeting capabilities. Use this to discover inventory matching campaign requirements.

Args:
    channel: Channel type (display, video, mobile, ctv, native)
    format: Ad format (banner, video, interstitial, rewarded)
    min_price: Minimum CPM price in USD
    max_price: Maximum CPM price in USD
    publisher_ids: Specific publisher IDs to search
    targeting_capabilities: Required targeting types
    delivery_type: Delivery type (Exclusive, Guaranteed, PMP)
    limit: Maximum results to return (default 10)

Returns:
    Formatted list of matching products with pricing and capabilities."""

    args_schema: type[BaseModel] = ProductSearchInput
    _client: OpenDirectClient

    def __init__(self, client: OpenDirectClient, **kwargs: Any):
        """Initialize with OpenDirect client."""
        super().__init__(**kwargs)
        self._client = client

    def _run(
        self,
        channel: Optional[str] = None,
        format: Optional[str] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        publisher_ids: Optional[list[str]] = None,
        targeting_capabilities: Optional[list[str]] = None,
        delivery_type: Optional[str] = None,
        limit: int = 10,
    ) -> str:
        """Synchronous wrapper for async search."""
        return asyncio.run(
            self._arun(
                channel=channel,
                format=format,
                min_price=min_price,
                max_price=max_price,
                publisher_ids=publisher_ids,
                targeting_capabilities=targeting_capabilities,
                delivery_type=delivery_type,
                limit=limit,
            )
        )

    async def _arun(
        self,
        channel: Optional[str] = None,
        format: Optional[str] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        publisher_ids: Optional[list[str]] = None,
        targeting_capabilities: Optional[list[str]] = None,
        delivery_type: Optional[str] = None,
        limit: int = 10,
    ) -> str:
        """Search for products matching the criteria."""
        # Build search filters
        filters: dict[str, Any] = {}
        if channel:
            filters["channel"] = channel
        if format:
            filters["adFormat"] = format
        if publisher_ids:
            filters["publisherIds"] = publisher_ids
        if targeting_capabilities:
            filters["targeting"] = targeting_capabilities
        if delivery_type:
            filters["deliveryType"] = delivery_type

        # Execute search
        try:
            if filters:
                products = await self._client.search_products(filters)
            else:
                products = await self._client.list_products(top=limit)
        except Exception as e:
            return f"Error searching products: {e}"

        # Filter by price client-side if needed
        if min_price is not None:
            products = [p for p in products if p.base_price >= min_price]
        if max_price is not None:
            products = [p for p in products if p.base_price <= max_price]

        # Limit results
        products = products[:limit]

        return self._format_results(products)

    def _format_results(self, products: list[Any]) -> str:
        """Format product results as readable text."""
        if not products:
            return "No products found matching the search criteria."

        output = f"Found {len(products)} matching products:\n\n"

        for i, p in enumerate(products, 1):
            targeting_str = "N/A"
            if p.targeting:
                capabilities = p.targeting.get("capabilities", [])
                if capabilities:
                    targeting_str = ", ".join(capabilities)

            avail_str = f"{p.available_impressions:,}" if p.available_impressions else "N/A"

            output += f"""
{i}. {p.name}
   Product ID: {p.id}
   Publisher ID: {p.publisher_id}
   Channel: {getattr(p, 'channel', 'N/A')}
   Format: {getattr(p, 'ad_format', 'N/A')}
   Base CPM: ${p.base_price:.2f}
   Rate Type: {p.rate_type.value}
   Delivery Type: {p.delivery_type.value}
   Available Impressions: {avail_str}
   Targeting: {targeting_str}
   ---
"""
        return output
