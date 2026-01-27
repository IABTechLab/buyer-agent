# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Order management tool for creating advertising orders."""

import asyncio
from datetime import datetime
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...clients.opendirect_client import OpenDirectClient
from ...models.opendirect import Order


class CreateOrderInput(BaseModel):
    """Input schema for order creation."""

    account_id: str = Field(
        ...,
        description="Account ID for the order",
    )
    order_name: str = Field(
        ...,
        description="Name/title for the order",
    )
    brand_id: str = Field(
        ...,
        description="Advertiser brand identifier",
    )
    start_date: str = Field(
        ...,
        description="Order start date (YYYY-MM-DD)",
    )
    end_date: str = Field(
        ...,
        description="Order end date (YYYY-MM-DD)",
    )
    budget: float = Field(
        ...,
        description="Total order budget in USD",
        gt=0,
    )
    currency: str = Field(
        default="USD",
        description="Budget currency (ISO-4217)",
    )
    publisher_id: str | None = Field(
        default=None,
        description="Target publisher ID (optional)",
    )


class CreateOrderTool(BaseTool):
    """Create a new advertising order (IO) in OpenDirect."""

    name: str = "create_advertising_order"
    description: str = """Create a new advertising order (IO) in OpenDirect.
An order is a container for line items and represents the overall campaign
agreement. Returns the order ID for adding line items.

Args:
    account_id: Account ID for the order
    order_name: Name/title for the order
    brand_id: Advertiser brand identifier
    start_date: Order start date (YYYY-MM-DD)
    end_date: Order end date (YYYY-MM-DD)
    budget: Total order budget in USD
    currency: Budget currency (default: USD)
    publisher_id: Target publisher ID (optional)

Returns:
    Order confirmation with ID and status."""

    args_schema: type[BaseModel] = CreateOrderInput
    _client: OpenDirectClient

    def __init__(self, client: OpenDirectClient, **kwargs: Any):
        """Initialize with OpenDirect client."""
        super().__init__(**kwargs)
        self._client = client

    def _run(
        self,
        account_id: str,
        order_name: str,
        brand_id: str,
        start_date: str,
        end_date: str,
        budget: float,
        currency: str = "USD",
        publisher_id: str | None = None,
    ) -> str:
        """Synchronous wrapper for async order creation."""
        return asyncio.run(
            self._arun(
                account_id=account_id,
                order_name=order_name,
                brand_id=brand_id,
                start_date=start_date,
                end_date=end_date,
                budget=budget,
                currency=currency,
                publisher_id=publisher_id,
            )
        )

    async def _arun(
        self,
        account_id: str,
        order_name: str,
        brand_id: str,
        start_date: str,
        end_date: str,
        budget: float,
        currency: str = "USD",
        publisher_id: str | None = None,
    ) -> str:
        """Create a new advertising order."""
        try:
            # Parse dates
            start_dt = datetime.fromisoformat(start_date)
            end_dt = datetime.fromisoformat(end_date)

            # Build order
            order = Order(
                name=order_name,
                account_id=account_id,
                brand_id=brand_id,
                publisher_id=publisher_id,
                start_date=start_dt,
                end_date=end_dt,
                budget=budget,
                currency=currency,
            )

            # Create order
            result = await self._client.create_order(account_id, order)

            return f"""
Order Created Successfully!

Order ID: {result.id}
Name: {result.name}
Status: {result.order_status.value}
Account ID: {account_id}
Flight: {start_date} to {end_date}
Budget: {currency} {budget:,.2f}

Next step: Add line items to this order using the create_line_item tool.
"""

        except ValueError as e:
            return f"Error parsing dates: {e}. Please use YYYY-MM-DD format."
        except Exception as e:
            return f"Error creating order: {e}"
