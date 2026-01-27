# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""HTTP client for IAB OpenDirect 2.1 API."""

from typing import Any, Optional

import httpx

from ..models.opendirect import (
    Account,
    AvailsRequest,
    AvailsResponse,
    Creative,
    Line,
    LineStats,
    Order,
    Product,
)


class OpenDirectClient:
    """Async HTTP client for OpenDirect API v2.1."""

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        oauth_token: Optional[str] = None,
        timeout: float = 30.0,
    ):
        """Initialize the client.

        Args:
            base_url: Base URL for the OpenDirect API
            api_key: Optional API key for authentication
            oauth_token: Optional OAuth bearer token
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._build_headers(api_key, oauth_token),
            timeout=timeout,
        )

    def _build_headers(
        self, api_key: Optional[str], oauth_token: Optional[str]
    ) -> dict[str, str]:
        """Build request headers."""
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if oauth_token:
            headers["Authorization"] = f"Bearer {oauth_token}"
        elif api_key:
            headers["X-API-Key"] = api_key
        return headers

    # -------------------------------------------------------------------------
    # Products
    # -------------------------------------------------------------------------

    async def list_products(
        self, skip: int = 0, top: int = 50, **filters: Any
    ) -> list[Product]:
        """List available products with pagination.

        Args:
            skip: Number of items to skip
            top: Maximum number of items to return
            **filters: Additional filter parameters

        Returns:
            List of Product objects
        """
        params = {"$skip": skip, "$top": top, **filters}
        response = await self._client.get("/products", params=params)
        response.raise_for_status()
        data = response.json()
        products = data.get("products", data) if isinstance(data, dict) else data
        return [Product.model_validate(p) for p in products]

    async def get_product(self, product_id: str) -> Product:
        """Get a single product by ID.

        Args:
            product_id: The product ID

        Returns:
            Product object
        """
        response = await self._client.get(f"/products/{product_id}")
        response.raise_for_status()
        return Product.model_validate(response.json())

    async def search_products(self, filters: dict[str, Any]) -> list[Product]:
        """Search products with filters.

        Args:
            filters: Search filter parameters (channel, format, pricing, etc.)

        Returns:
            List of matching Product objects
        """
        response = await self._client.post("/products/search", json=filters)
        response.raise_for_status()
        data = response.json()
        products = data.get("products", data) if isinstance(data, dict) else data
        return [Product.model_validate(p) for p in products]

    async def check_avails(self, request: AvailsRequest) -> AvailsResponse:
        """Check availability and pricing for a product.

        Args:
            request: Availability check request parameters

        Returns:
            AvailsResponse with availability and pricing info
        """
        response = await self._client.post(
            "/products/avails", json=request.model_dump(by_alias=True, exclude_none=True)
        )
        response.raise_for_status()
        return AvailsResponse.model_validate(response.json())

    # -------------------------------------------------------------------------
    # Accounts
    # -------------------------------------------------------------------------

    async def create_account(self, account: Account) -> Account:
        """Create a new account.

        Args:
            account: Account data to create

        Returns:
            Created Account with ID
        """
        response = await self._client.post(
            "/accounts", json=account.model_dump(by_alias=True, exclude_none=True)
        )
        response.raise_for_status()
        return Account.model_validate(response.json())

    async def get_account(self, account_id: str) -> Account:
        """Get an account by ID.

        Args:
            account_id: The account ID

        Returns:
            Account object
        """
        response = await self._client.get(f"/accounts/{account_id}")
        response.raise_for_status()
        return Account.model_validate(response.json())

    async def list_accounts(self, skip: int = 0, top: int = 50) -> list[Account]:
        """List accounts with pagination.

        Args:
            skip: Number of items to skip
            top: Maximum number of items to return

        Returns:
            List of Account objects
        """
        params = {"$skip": skip, "$top": top}
        response = await self._client.get("/accounts", params=params)
        response.raise_for_status()
        data = response.json()
        accounts = data.get("accounts", data) if isinstance(data, dict) else data
        return [Account.model_validate(a) for a in accounts]

    # -------------------------------------------------------------------------
    # Orders
    # -------------------------------------------------------------------------

    async def create_order(self, account_id: str, order: Order) -> Order:
        """Create a new order under an account.

        Args:
            account_id: The account ID
            order: Order data to create

        Returns:
            Created Order with ID
        """
        response = await self._client.post(
            f"/accounts/{account_id}/orders",
            json=order.model_dump(by_alias=True, exclude_none=True),
        )
        response.raise_for_status()
        return Order.model_validate(response.json())

    async def get_order(self, account_id: str, order_id: str) -> Order:
        """Get an order by ID.

        Args:
            account_id: The account ID
            order_id: The order ID

        Returns:
            Order object
        """
        response = await self._client.get(f"/accounts/{account_id}/orders/{order_id}")
        response.raise_for_status()
        return Order.model_validate(response.json())

    async def list_orders(
        self, account_id: str, skip: int = 0, top: int = 50
    ) -> list[Order]:
        """List orders for an account.

        Args:
            account_id: The account ID
            skip: Number of items to skip
            top: Maximum number of items to return

        Returns:
            List of Order objects
        """
        params = {"$skip": skip, "$top": top}
        response = await self._client.get(
            f"/accounts/{account_id}/orders", params=params
        )
        response.raise_for_status()
        data = response.json()
        orders = data.get("orders", data) if isinstance(data, dict) else data
        return [Order.model_validate(o) for o in orders]

    async def update_order(self, account_id: str, order_id: str, order: Order) -> Order:
        """Update an existing order.

        Args:
            account_id: The account ID
            order_id: The order ID
            order: Updated order data

        Returns:
            Updated Order object
        """
        response = await self._client.patch(
            f"/accounts/{account_id}/orders/{order_id}",
            json=order.model_dump(by_alias=True, exclude_none=True),
        )
        response.raise_for_status()
        return Order.model_validate(response.json())

    # -------------------------------------------------------------------------
    # Lines
    # -------------------------------------------------------------------------

    async def create_line(
        self, account_id: str, order_id: str, line: Line
    ) -> Line:
        """Create a new line item under an order.

        Args:
            account_id: The account ID
            order_id: The order ID
            line: Line data to create

        Returns:
            Created Line with ID
        """
        response = await self._client.post(
            f"/accounts/{account_id}/orders/{order_id}/lines",
            json=line.model_dump(by_alias=True, exclude_none=True),
        )
        response.raise_for_status()
        return Line.model_validate(response.json())

    async def get_line(self, account_id: str, order_id: str, line_id: str) -> Line:
        """Get a line item by ID.

        Args:
            account_id: The account ID
            order_id: The order ID
            line_id: The line ID

        Returns:
            Line object
        """
        response = await self._client.get(
            f"/accounts/{account_id}/orders/{order_id}/lines/{line_id}"
        )
        response.raise_for_status()
        return Line.model_validate(response.json())

    async def list_lines(
        self, account_id: str, order_id: str, skip: int = 0, top: int = 50
    ) -> list[Line]:
        """List line items for an order.

        Args:
            account_id: The account ID
            order_id: The order ID
            skip: Number of items to skip
            top: Maximum number of items to return

        Returns:
            List of Line objects
        """
        params = {"$skip": skip, "$top": top}
        response = await self._client.get(
            f"/accounts/{account_id}/orders/{order_id}/lines", params=params
        )
        response.raise_for_status()
        data = response.json()
        lines = data.get("lines", data) if isinstance(data, dict) else data
        return [Line.model_validate(ln) for ln in lines]

    async def reserve_line(
        self, account_id: str, order_id: str, line_id: str
    ) -> Line:
        """Reserve inventory for a line item.

        Args:
            account_id: The account ID
            order_id: The order ID
            line_id: The line ID

        Returns:
            Updated Line with Reserved status
        """
        response = await self._client.patch(
            f"/accounts/{account_id}/orders/{order_id}/lines/{line_id}",
            params={"action": "reserve"},
        )
        response.raise_for_status()
        return Line.model_validate(response.json())

    async def book_line(self, account_id: str, order_id: str, line_id: str) -> Line:
        """Confirm booking for a line item.

        Args:
            account_id: The account ID
            order_id: The order ID
            line_id: The line ID

        Returns:
            Updated Line with Booked status
        """
        response = await self._client.patch(
            f"/accounts/{account_id}/orders/{order_id}/lines/{line_id}",
            params={"action": "book"},
        )
        response.raise_for_status()
        return Line.model_validate(response.json())

    async def cancel_line(
        self, account_id: str, order_id: str, line_id: str
    ) -> Line:
        """Cancel a line item.

        Args:
            account_id: The account ID
            order_id: The order ID
            line_id: The line ID

        Returns:
            Updated Line with Cancelled status
        """
        response = await self._client.patch(
            f"/accounts/{account_id}/orders/{order_id}/lines/{line_id}",
            params={"action": "cancel"},
        )
        response.raise_for_status()
        return Line.model_validate(response.json())

    async def get_line_stats(
        self, account_id: str, order_id: str, line_id: str
    ) -> LineStats:
        """Get performance statistics for a line item.

        Args:
            account_id: The account ID
            order_id: The order ID
            line_id: The line ID

        Returns:
            LineStats with delivery and performance metrics
        """
        response = await self._client.get(
            f"/accounts/{account_id}/orders/{order_id}/lines/{line_id}/stats"
        )
        response.raise_for_status()
        return LineStats.model_validate(response.json())

    # -------------------------------------------------------------------------
    # Creatives
    # -------------------------------------------------------------------------

    async def create_creative(self, account_id: str, creative: Creative) -> Creative:
        """Create a new creative.

        Args:
            account_id: The account ID
            creative: Creative data to create

        Returns:
            Created Creative with ID
        """
        response = await self._client.post(
            f"/accounts/{account_id}/creatives",
            json=creative.model_dump(by_alias=True, exclude_none=True),
        )
        response.raise_for_status()
        return Creative.model_validate(response.json())

    async def get_creative(self, account_id: str, creative_id: str) -> Creative:
        """Get a creative by ID.

        Args:
            account_id: The account ID
            creative_id: The creative ID

        Returns:
            Creative object
        """
        response = await self._client.get(
            f"/accounts/{account_id}/creatives/{creative_id}"
        )
        response.raise_for_status()
        return Creative.model_validate(response.json())

    async def list_creatives(
        self, account_id: str, skip: int = 0, top: int = 50
    ) -> list[Creative]:
        """List creatives for an account.

        Args:
            account_id: The account ID
            skip: Number of items to skip
            top: Maximum number of items to return

        Returns:
            List of Creative objects
        """
        params = {"$skip": skip, "$top": top}
        response = await self._client.get(
            f"/accounts/{account_id}/creatives", params=params
        )
        response.raise_for_status()
        data = response.json()
        creatives = data.get("creatives", data) if isinstance(data, dict) else data
        return [Creative.model_validate(c) for c in creatives]

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> "OpenDirectClient":
        """Async context manager entry."""
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Async context manager exit."""
        await self.close()
