# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Line item management tools for booking inventory."""

import asyncio
from datetime import datetime
from typing import Any, Optional

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...clients.opendirect_client import OpenDirectClient
from ...models.opendirect import Line, RateType


class CreateLineInput(BaseModel):
    """Input schema for line item creation."""

    account_id: str = Field(..., description="Account ID")
    order_id: str = Field(..., description="Order ID to add line to")
    product_id: str = Field(..., description="Product ID to book")
    line_name: str = Field(..., description="Name for the line item")
    start_date: str = Field(..., description="Line start date (YYYY-MM-DD)")
    end_date: str = Field(..., description="Line end date (YYYY-MM-DD)")
    rate_type: str = Field(
        default="CPM",
        description="Rate type: CPM, CPMV, CPC, CPD, FlatRate",
    )
    rate: float = Field(..., description="Rate/price for the line", gt=0)
    quantity: int = Field(..., description="Target impressions or units", gt=0)
    targeting: Optional[dict[str, Any]] = Field(
        default=None,
        description="Targeting parameters (geo, demographic, etc.)",
    )


class CreateLineTool(BaseTool):
    """Create a line item within an order to book specific inventory."""

    name: str = "create_line_item"
    description: str = """Create a line item within an order to book specific
inventory/product. Line items define the actual media purchase including
targeting, rate, and flight dates.

Args:
    account_id: Account ID
    order_id: Order ID to add line to
    product_id: Product ID to book
    line_name: Name for the line item
    start_date: Line start date (YYYY-MM-DD)
    end_date: Line end date (YYYY-MM-DD)
    rate_type: Rate type (CPM, CPMV, CPC, CPD, FlatRate)
    rate: Rate/price for the line
    quantity: Target impressions or units
    targeting: Targeting parameters (optional)

Returns:
    Line item confirmation with ID and booking status."""

    args_schema: type[BaseModel] = CreateLineInput
    _client: OpenDirectClient

    def __init__(self, client: OpenDirectClient, **kwargs: Any):
        """Initialize with OpenDirect client."""
        super().__init__(**kwargs)
        self._client = client

    def _run(
        self,
        account_id: str,
        order_id: str,
        product_id: str,
        line_name: str,
        start_date: str,
        end_date: str,
        rate_type: str = "CPM",
        rate: float = 0,
        quantity: int = 0,
        targeting: Optional[dict[str, Any]] = None,
    ) -> str:
        """Synchronous wrapper for async line creation."""
        return asyncio.run(
            self._arun(
                account_id=account_id,
                order_id=order_id,
                product_id=product_id,
                line_name=line_name,
                start_date=start_date,
                end_date=end_date,
                rate_type=rate_type,
                rate=rate,
                quantity=quantity,
                targeting=targeting,
            )
        )

    async def _arun(
        self,
        account_id: str,
        order_id: str,
        product_id: str,
        line_name: str,
        start_date: str,
        end_date: str,
        rate_type: str = "CPM",
        rate: float = 0,
        quantity: int = 0,
        targeting: Optional[dict[str, Any]] = None,
    ) -> str:
        """Create a new line item."""
        try:
            # Parse dates
            start_dt = datetime.fromisoformat(start_date)
            end_dt = datetime.fromisoformat(end_date)

            # Parse rate type
            try:
                rt = RateType(rate_type)
            except ValueError:
                return f"Invalid rate type: {rate_type}. Valid options: CPM, CPMV, CPC, CPD, FlatRate"

            # Build line
            line = Line(
                order_id=order_id,
                product_id=product_id,
                name=line_name,
                start_date=start_dt,
                end_date=end_dt,
                rate_type=rt,
                rate=rate,
                quantity=quantity,
                targeting=targeting,
            )

            # Create line
            result = await self._client.create_line(account_id, order_id, line)

            # Calculate estimated cost
            estimated_cost = 0.0
            if result.rate_type == RateType.CPM:
                estimated_cost = (result.quantity / 1000) * result.rate

            return f"""
Line Item Created Successfully!

Line ID: {result.id}
Name: {result.name}
Status: {result.booking_status.value}
Product ID: {product_id}
Order ID: {order_id}
Flight: {start_date} to {end_date}

Pricing:
  Rate Type: {result.rate_type.value}
  Rate: ${result.rate:.2f}
  Quantity: {result.quantity:,}
  Estimated Cost: ${estimated_cost:,.2f}

Next steps:
  - To reserve this inventory, use the reserve_line_item tool
  - To book this line (make it live), use the book_line_item tool
"""

        except ValueError as e:
            return f"Error parsing dates: {e}. Please use YYYY-MM-DD format."
        except Exception as e:
            return f"Error creating line: {e}"


class ReserveLineInput(BaseModel):
    """Input schema for reserving a line."""

    account_id: str = Field(..., description="Account ID")
    order_id: str = Field(..., description="Order ID")
    line_id: str = Field(..., description="Line ID to reserve")


class ReserveLineTool(BaseTool):
    """Reserve inventory for a line item."""

    name: str = "reserve_line_item"
    description: str = """Reserve inventory for a line item, transitioning it from
Draft to Reserved status. This temporarily holds the inventory without final
commitment.

Args:
    account_id: Account ID
    order_id: Order ID
    line_id: Line ID to reserve

Returns:
    Updated line status confirmation."""

    args_schema: type[BaseModel] = ReserveLineInput
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
        """Synchronous wrapper for async reserve."""
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
        """Reserve the line item."""
        try:
            result = await self._client.reserve_line(account_id, order_id, line_id)

            return f"""
Line Item Reserved Successfully!

Line ID: {line_id}
New Status: {result.booking_status.value}

The inventory is now held for this line item.
To confirm the booking, use the book_line_item tool.
"""

        except Exception as e:
            return f"Error reserving line: {e}"


class BookLineInput(BaseModel):
    """Input schema for booking a line."""

    account_id: str = Field(..., description="Account ID")
    order_id: str = Field(..., description="Order ID")
    line_id: str = Field(..., description="Line ID to book")


class BookLineTool(BaseTool):
    """Confirm booking for a line item."""

    name: str = "book_line_item"
    description: str = """Confirm booking for a line item, transitioning it from
Reserved to Booked status. This finalizes the inventory guarantee and
commits the campaign to run.

Args:
    account_id: Account ID
    order_id: Order ID
    line_id: Line ID to book

Returns:
    Booking confirmation with guaranteed impressions and cost."""

    args_schema: type[BaseModel] = BookLineInput
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
        """Synchronous wrapper for async book."""
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
        """Book the line item."""
        try:
            result = await self._client.book_line(account_id, order_id, line_id)

            # Calculate cost
            cost = 0.0
            if result.cost:
                cost = result.cost
            elif result.rate_type == RateType.CPM:
                cost = (result.quantity / 1000) * result.rate

            return f"""
Line Item Booked Successfully!

Line ID: {line_id}
New Status: {result.booking_status.value}
Guaranteed Impressions: {result.quantity:,}
Total Cost: ${cost:,.2f}

The line item is now confirmed and will deliver during the flight dates.
"""

        except Exception as e:
            return f"Error booking line: {e}"
