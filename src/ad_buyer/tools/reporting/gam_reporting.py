# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""GAM reporting tool — pulls delivery data from Google Ad Manager REST API."""

import asyncio

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...clients.gam_reporting_client import GAMReportingClient
from ...config.settings import settings


class GAMReportInput(BaseModel):
    order_ids: list[str] = Field(..., description="GAM order IDs (ORD-xxxx from booked_lines)")
    date_range: str = Field(
        default="LAST_30_DAYS",
        description="LAST_7_DAYS | LAST_30_DAYS | LAST_90_DAYS",
    )


class GAMReportingTool(BaseTool):
    """Pull real delivery reports from Google Ad Manager REST API."""

    name: str = "get_gam_campaign_report"
    description: str = """Retrieve real delivery data from Google Ad Manager for booked orders.
Returns order status, line item delivery, and a submitted report job.

Args:
    order_ids: List of GAM order IDs (ORD-xxxx from booked_lines)
    date_range: Reporting window (LAST_7_DAYS, LAST_30_DAYS, LAST_90_DAYS)

Returns: Delivery report with order status, line items, and report job reference."""

    args_schema: type[BaseModel] = GAMReportInput

    def _run(self, order_ids: list[str], date_range: str = "LAST_30_DAYS") -> str:
        if not settings.gam_enabled:
            return "GAM reporting disabled. Set GAM_ENABLED=true in .env"
        if not settings.gam_network_code or not settings.gam_json_key_path:
            return (
                "GAM not configured. Set GAM_NETWORK_CODE and GAM_JSON_KEY_PATH in .env. "
                "See ActionPlanGAMImplement.md for service account setup steps."
            )
        try:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self._arun(order_ids, date_range))
            finally:
                loop.close()
        except Exception as e:
            return f"GAM reporting error: {e}"

    async def _arun(self, order_ids: list[str], date_range: str) -> str:
        async with GAMReportingClient() as client:
            data = await client.get_delivery_report(order_ids, date_range_type=date_range)
        return self._format(data)

    def _format(self, data: dict) -> str:
        orders = data.get("orders", [])
        if not orders:
            return "No delivery data returned from GAM."

        output = f"GAM Delivery Report — {len(orders)} order(s)\n{'='*50}\n\n"
        for order in orders:
            if order.get("error"):
                output += f"Order {order['order_id']}: Error — {order['error']}\n---\n"
                continue

            line_items = order.get("line_items", [])
            output += f"""Order: {order.get('order_name', order['order_id'])}
  Order ID:    {order['order_id']}
  Status:      {order.get('status', 'UNKNOWN')}
  Line Items:  {len(line_items)}
"""
            for li in line_items:
                goal = li.get("impressions_goal", -1)
                goal_str = f"{goal:,}" if isinstance(goal, int) and goal > 0 else "Unlimited"
                output += f"    [{li['status']}] {li['name']} — goal: {goal_str} impressions\n"

            if data.get("report_job"):
                output += "  Report:      Job submitted — view full metrics in GAM UI\n"
            output += "  ---\n"

        return output
