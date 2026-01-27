# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Stats retrieval tool for performance reporting."""

import asyncio
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...clients.opendirect_client import OpenDirectClient


class GetStatsInput(BaseModel):
    """Input schema for stats retrieval."""

    account_id: str = Field(..., description="Account ID")
    order_id: str = Field(..., description="Order ID")
    line_id: str = Field(..., description="Line ID to get stats for")


class GetStatsTool(BaseTool):
    """Retrieve performance statistics for a line item."""

    name: str = "get_line_statistics"
    description: str = """Retrieve performance statistics for a line item
including impressions delivered, spend, completion rates, and pacing data.

Args:
    account_id: Account ID
    order_id: Order ID
    line_id: Line ID to get stats for

Returns:
    Performance statistics with delivery metrics, spend, and performance indicators."""

    args_schema: type[BaseModel] = GetStatsInput
    _client: OpenDirectClient

    def __init__(self, client: OpenDirectClient, **kwargs: Any):
        """Initialize with OpenDirect client."""
        super().__init__(**kwargs)
        self._client = client

    def _run(
        self,
        account_id: str,
        order_id: str,
        line_id: str,
    ) -> str:
        """Synchronous wrapper for async stats retrieval."""
        return asyncio.run(
            self._arun(
                account_id=account_id,
                order_id=order_id,
                line_id=line_id,
            )
        )

    async def _arun(
        self,
        account_id: str,
        order_id: str,
        line_id: str,
    ) -> str:
        """Get statistics for the line item."""
        try:
            stats = await self._client.get_line_stats(account_id, order_id, line_id)

            # Format optional fields
            vcr_str = f"{stats.vcr:.1f}%" if stats.vcr is not None else "N/A"
            viewability_str = (
                f"{stats.viewability:.1f}%" if stats.viewability is not None else "N/A"
            )
            ctr_str = f"{stats.ctr:.3f}%" if stats.ctr is not None else "N/A"
            pacing_str = stats.pacing_status or "N/A"
            last_updated_str = (
                stats.last_updated.isoformat() if stats.last_updated else "N/A"
            )

            # Determine pacing health
            pacing_health = "On track"
            if stats.delivery_rate < stats.budget_utilization * 0.8:
                pacing_health = "Under-delivering"
            elif stats.delivery_rate > stats.budget_utilization * 1.2:
                pacing_health = "Over-delivering"

            return f"""
Performance Statistics for Line {line_id}

Delivery Metrics:
  Impressions Delivered: {stats.impressions_delivered:,}
  Target Impressions: {stats.target_impressions:,}
  Delivery Rate: {stats.delivery_rate:.1f}%
  Pacing Status: {pacing_str}
  Pacing Health: {pacing_health}

Spend:
  Amount Spent: ${stats.amount_spent:,.2f}
  Budget: ${stats.budget:,.2f}
  Budget Utilization: {stats.budget_utilization:.1f}%

Performance:
  Effective CPM: ${stats.effective_cpm:.2f}
  Video Completion Rate: {vcr_str}
  Viewability: {viewability_str}
  Click-Through Rate: {ctr_str}

Last Updated: {last_updated_str}

Analysis:
  {"Campaign is performing well." if stats.delivery_rate >= 80 else "Campaign may need optimization."}
  {"Budget pacing is on track." if abs(stats.delivery_rate - stats.budget_utilization) < 10 else "Consider adjusting pacing."}
"""

        except Exception as e:
            return f"Error retrieving stats: {e}"
