# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Availability check tool for inventory pricing."""

import asyncio
from datetime import datetime
from typing import Any, Optional

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...clients.opendirect_client import OpenDirectClient
from ...models.opendirect import AvailsRequest


class AvailsCheckInput(BaseModel):
    """Input schema for availability check tool."""

    product_id: str = Field(
        ...,
        description="Product ID to check availability for",
    )
    start_date: str = Field(
        ...,
        description="Campaign start date (YYYY-MM-DD)",
    )
    end_date: str = Field(
        ...,
        description="Campaign end date (YYYY-MM-DD)",
    )
    impressions: Optional[int] = Field(
        default=None,
        description="Desired impression volume",
    )
    budget: Optional[float] = Field(
        default=None,
        description="Total budget in USD",
    )


class AvailsCheckTool(BaseTool):
    """Check real-time availability and pricing for a specific advertising product."""

    name: str = "check_inventory_availability"
    description: str = """Check real-time availability and pricing for a specific
advertising product. Returns available impressions, guaranteed delivery estimates,
and final pricing based on flight dates and volume.

Args:
    product_id: Product ID to check availability for
    start_date: Campaign start date (YYYY-MM-DD)
    end_date: Campaign end date (YYYY-MM-DD)
    impressions: Desired impression volume (optional)
    budget: Total budget in USD (optional)

Returns:
    Availability details including impressions, pricing, and delivery confidence."""

    args_schema: type[BaseModel] = AvailsCheckInput
    _client: OpenDirectClient

    def __init__(self, client: OpenDirectClient, **kwargs: Any):
        """Initialize with OpenDirect client."""
        super().__init__(**kwargs)
        self._client = client

    def _run(
        self,
        product_id: str,
        start_date: str,
        end_date: str,
        impressions: Optional[int] = None,
        budget: Optional[float] = None,
    ) -> str:
        """Synchronous wrapper for async avails check."""
        return asyncio.run(
            self._arun(
                product_id=product_id,
                start_date=start_date,
                end_date=end_date,
                impressions=impressions,
                budget=budget,
            )
        )

    async def _arun(
        self,
        product_id: str,
        start_date: str,
        end_date: str,
        impressions: Optional[int] = None,
        budget: Optional[float] = None,
    ) -> str:
        """Check availability for the specified product."""
        try:
            # Parse dates
            start_dt = datetime.fromisoformat(start_date)
            end_dt = datetime.fromisoformat(end_date)

            # Build request
            request = AvailsRequest(
                product_id=product_id,
                start_date=start_dt,
                end_date=end_dt,
                requested_impressions=impressions,
                budget=budget,
            )

            # Execute check
            avails = await self._client.check_avails(request)

            return self._format_results(product_id, start_date, end_date, avails)

        except ValueError as e:
            return f"Error parsing dates: {e}. Please use YYYY-MM-DD format."
        except Exception as e:
            return f"Error checking availability: {e}"

    def _format_results(
        self,
        product_id: str,
        start_date: str,
        end_date: str,
        avails: Any,
    ) -> str:
        """Format availability results as readable text."""
        targeting_str = "N/A"
        if avails.available_targeting:
            targeting_str = ", ".join(avails.available_targeting)

        guaranteed_str = (
            f"{avails.guaranteed_impressions:,}"
            if avails.guaranteed_impressions
            else "N/A"
        )

        confidence_str = (
            f"{avails.delivery_confidence:.1f}%"
            if avails.delivery_confidence is not None
            else "N/A"
        )

        return f"""
Availability Check Results for Product {product_id}

Flight Dates: {start_date} to {end_date}

Inventory:
  Available Impressions: {avails.available_impressions:,}
  Guaranteed Impressions: {guaranteed_str}
  Delivery Confidence: {confidence_str}

Pricing:
  Estimated CPM: ${avails.estimated_cpm:.2f}
  Total Cost: ${avails.total_cost:,.2f}

Targeting Available: {targeting_str}

Recommendation: {"Good to book" if avails.delivery_confidence and avails.delivery_confidence >= 80 else "Consider alternatives or reduce volume"}
"""
