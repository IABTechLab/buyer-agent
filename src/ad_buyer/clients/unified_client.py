# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unified client for IAB agentic-direct server supporting both MCP and A2A protocols."""

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional, Union

from .a2a_client import A2AClient, A2AResponse
from .mcp_client import IABMCPClient, MCPToolResult

if TYPE_CHECKING:
    from ..models.buyer_identity import BuyerIdentity


class Protocol(Enum):
    """Protocol to use for communication with IAB server."""

    MCP = "mcp"  # Direct tool calls via MCP SDK
    A2A = "a2a"  # Natural language via A2A (AI interprets requests)


@dataclass
class UnifiedResult:
    """Unified result from either MCP or A2A client."""

    success: bool = True
    data: Any = None
    error: str = ""
    protocol: Protocol = Protocol.MCP
    raw: Any = None

    @classmethod
    def from_mcp(cls, result: MCPToolResult) -> "UnifiedResult":
        """Create from MCP result."""
        return cls(
            success=result.success,
            data=result.data,
            error=result.error,
            protocol=Protocol.MCP,
            raw=result.raw,
        )

    @classmethod
    def from_a2a(cls, response: A2AResponse) -> "UnifiedResult":
        """Create from A2A response."""
        # A2A returns data in response.data list
        data = response.data[0] if len(response.data) == 1 else response.data
        if not data and response.text:
            data = response.text
        return cls(
            success=response.success,
            data=data,
            error=response.error,
            protocol=Protocol.A2A,
            raw=response.raw,
        )


class UnifiedClient:
    """Unified client that supports both MCP and A2A protocols.

    Use MCP for direct tool control (faster, deterministic).
    Use A2A for natural language requests (more flexible, AI-interpreted).

    Example:
        # MCP mode (default) - direct tool calls
        async with UnifiedClient(protocol=Protocol.MCP) as client:
            result = await client.list_products()

        # A2A mode - natural language
        async with UnifiedClient(protocol=Protocol.A2A) as client:
            result = await client.list_products()

        # Switch protocols on the fly
        async with UnifiedClient(protocol=Protocol.MCP) as client:
            # Use MCP for listing
            products = await client.list_products()

            # Switch to A2A for a complex natural language request
            result = await client.send_natural_language(
                "Find me CTV inventory with household targeting under $30 CPM"
            )
    """

    def __init__(
        self,
        base_url: str = "https://agentic-direct-server-hwgrypmndq-uk.a.run.app",
        protocol: Protocol = Protocol.MCP,
        a2a_agent_type: str = "buyer",
        buyer_identity: "Optional[BuyerIdentity]" = None,
    ):
        """Initialize the unified client.

        Args:
            base_url: Base URL for the IAB server
            protocol: Default protocol to use (MCP or A2A)
            a2a_agent_type: Agent type for A2A ('buyer' or 'seller')
            buyer_identity: Optional BuyerIdentity for tiered pricing access
        """
        self.base_url = base_url
        self.default_protocol = protocol
        self.a2a_agent_type = a2a_agent_type
        self.buyer_identity = buyer_identity

        self._mcp_client: Optional[IABMCPClient] = None
        self._a2a_client: Optional[A2AClient] = None

    async def connect(self, protocol: Protocol = None) -> None:
        """Connect to the server with specified protocol.

        Args:
            protocol: Protocol to connect with (uses default if not specified)
        """
        protocol = protocol or self.default_protocol

        if protocol == Protocol.MCP:
            if not self._mcp_client:
                self._mcp_client = IABMCPClient(base_url=self.base_url)
                await self._mcp_client.connect()
        else:
            if not self._a2a_client:
                self._a2a_client = A2AClient(
                    base_url=self.base_url,
                    agent_type=self.a2a_agent_type,
                )
                # A2A doesn't need explicit connect

    async def connect_both(self) -> None:
        """Connect to both MCP and A2A protocols."""
        await self.connect(Protocol.MCP)
        await self.connect(Protocol.A2A)

    async def close(self) -> None:
        """Close all connections."""
        if self._mcp_client:
            await self._mcp_client.close()
            self._mcp_client = None
        if self._a2a_client:
            await self._a2a_client.close()
            self._a2a_client = None

    async def __aenter__(self) -> "UnifiedClient":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    @property
    def mcp(self) -> Optional[IABMCPClient]:
        """Get the MCP client (if connected)."""
        return self._mcp_client

    @property
    def a2a(self) -> Optional[A2AClient]:
        """Get the A2A client (if connected)."""
        return self._a2a_client

    @property
    def tools(self) -> dict[str, dict]:
        """Get available MCP tools (requires MCP connection)."""
        if self._mcp_client:
            return self._mcp_client.tools
        return {}

    async def _ensure_protocol(self, protocol: Protocol) -> None:
        """Ensure the specified protocol is connected."""
        if protocol == Protocol.MCP and not self._mcp_client:
            await self.connect(Protocol.MCP)
        elif protocol == Protocol.A2A and not self._a2a_client:
            await self.connect(Protocol.A2A)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] = None,
        protocol: Protocol = None,
    ) -> UnifiedResult:
        """Call a tool using the specified protocol.

        Args:
            name: Tool name
            arguments: Tool arguments
            protocol: Protocol to use (default if not specified)

        Returns:
            UnifiedResult with the response
        """
        protocol = protocol or self.default_protocol
        await self._ensure_protocol(protocol)

        if protocol == Protocol.MCP:
            result = await self._mcp_client.call_tool(name, arguments)
            return UnifiedResult.from_mcp(result)
        else:
            # For A2A, construct a natural language request from tool name and args
            message = self._tool_to_natural_language(name, arguments or {})
            response = await self._a2a_client.send_message(message)
            return UnifiedResult.from_a2a(response)

    def _tool_to_natural_language(self, tool_name: str, args: dict) -> str:
        """Convert a tool call to natural language for A2A."""
        # Map common tools to natural language
        tool_mappings = {
            "list_products": "List all available advertising products",
            "list_accounts": "List all accounts",
            "list_orders": "List all orders",
            "list_lines": "List all line items",
            "list_creatives": "List all creatives",
        }

        if tool_name in tool_mappings and not args:
            return tool_mappings[tool_name]

        # For other tools, construct a message
        if tool_name == "create_account":
            return f"Create an account named '{args.get('name')}' of type {args.get('type', 'advertiser')}"
        elif tool_name == "create_order":
            return (
                f"Create an order named '{args.get('name')}' "
                f"for account {args.get('accountId')} "
                f"with budget ${args.get('budget', 0):,.2f}"
            )
        elif tool_name == "create_line":
            return (
                f"Create a line item named '{args.get('name')}' "
                f"for order {args.get('orderId')} "
                f"using product {args.get('productId')} "
                f"with {args.get('quantity', 0):,} impressions"
            )
        elif tool_name == "get_product":
            return f"Get product with ID {args.get('id')}"
        elif tool_name == "get_account":
            return f"Get account with ID {args.get('id')}"
        elif tool_name == "get_order":
            return f"Get order with ID {args.get('id')}"

        # Generic fallback
        args_str = ", ".join(f"{k}={v}" for k, v in args.items())
        return f"Execute {tool_name} with {args_str}" if args_str else f"Execute {tool_name}"

    async def send_natural_language(self, message: str) -> UnifiedResult:
        """Send a natural language request via A2A.

        This always uses A2A regardless of default protocol.

        Args:
            message: Natural language request

        Returns:
            UnifiedResult with the response
        """
        await self._ensure_protocol(Protocol.A2A)
        response = await self._a2a_client.send_message(message)
        return UnifiedResult.from_a2a(response)

    # Convenience methods that use the default protocol

    async def list_products(self, protocol: Protocol = None) -> UnifiedResult:
        """List available advertising products."""
        return await self.call_tool("list_products", protocol=protocol)

    async def get_product(self, product_id: str, protocol: Protocol = None) -> UnifiedResult:
        """Get a specific product by ID."""
        return await self.call_tool("get_product", {"id": product_id}, protocol=protocol)

    async def search_products(
        self,
        query: str = None,
        filters: dict = None,
        protocol: Protocol = None,
    ) -> UnifiedResult:
        """Search for products."""
        args = {}
        if query:
            args["query"] = query
        if filters:
            args["filters"] = filters
        return await self.call_tool("search_products", args, protocol=protocol)

    async def list_accounts(self, protocol: Protocol = None) -> UnifiedResult:
        """List all accounts."""
        return await self.call_tool("list_accounts", protocol=protocol)

    async def create_account(
        self,
        name: str,
        account_type: str = "advertiser",
        status: str = "active",
        protocol: Protocol = None,
    ) -> UnifiedResult:
        """Create a new account."""
        return await self.call_tool(
            "create_account",
            {"name": name, "type": account_type, "status": status},
            protocol=protocol,
        )

    async def get_account(self, account_id: str, protocol: Protocol = None) -> UnifiedResult:
        """Get an account by ID."""
        return await self.call_tool("get_account", {"id": account_id}, protocol=protocol)

    async def list_orders(self, account_id: str = None, protocol: Protocol = None) -> UnifiedResult:
        """List orders."""
        args = {"accountId": account_id} if account_id else {}
        return await self.call_tool("list_orders", args, protocol=protocol)

    async def create_order(
        self,
        account_id: str,
        name: str,
        budget: float,
        start_date: str = None,
        end_date: str = None,
        protocol: Protocol = None,
    ) -> UnifiedResult:
        """Create a new order."""
        args = {"accountId": account_id, "name": name, "budget": budget}
        if start_date:
            args["startDate"] = start_date
        if end_date:
            args["endDate"] = end_date
        return await self.call_tool("create_order", args, protocol=protocol)

    async def get_order(self, order_id: str, protocol: Protocol = None) -> UnifiedResult:
        """Get an order by ID."""
        return await self.call_tool("get_order", {"id": order_id}, protocol=protocol)

    async def list_lines(self, order_id: str = None, protocol: Protocol = None) -> UnifiedResult:
        """List line items."""
        args = {"orderId": order_id} if order_id else {}
        return await self.call_tool("list_lines", args, protocol=protocol)

    async def create_line(
        self,
        order_id: str,
        product_id: str,
        name: str,
        quantity: int,
        start_date: str = None,
        end_date: str = None,
        protocol: Protocol = None,
    ) -> UnifiedResult:
        """Create a new line item."""
        args = {
            "orderId": order_id,
            "productId": product_id,
            "name": name,
            "quantity": quantity,
        }
        if start_date:
            args["startDate"] = start_date
        if end_date:
            args["endDate"] = end_date
        return await self.call_tool("create_line", args, protocol=protocol)

    async def get_line(self, line_id: str, protocol: Protocol = None) -> UnifiedResult:
        """Get a line item by ID."""
        return await self.call_tool("get_line", {"id": line_id}, protocol=protocol)

    async def list_creatives(self, protocol: Protocol = None) -> UnifiedResult:
        """List all creatives."""
        return await self.call_tool("list_creatives", protocol=protocol)

    async def create_creative(
        self,
        name: str,
        creative_type: str,
        url: str = None,
        content: str = None,
        protocol: Protocol = None,
    ) -> UnifiedResult:
        """Create a new creative."""
        args = {"name": name, "type": creative_type}
        if url:
            args["url"] = url
        if content:
            args["content"] = content
        return await self.call_tool("create_creative", args, protocol=protocol)

    async def create_assignment(
        self,
        line_id: str,
        creative_id: str,
        protocol: Protocol = None,
    ) -> UnifiedResult:
        """Assign a creative to a line item."""
        return await self.call_tool(
            "create_assignment",
            {"lineId": line_id, "creativeId": creative_id},
            protocol=protocol,
        )

    # DSP-specific methods for discovery, pricing, and deal management

    def set_buyer_identity(self, identity: "BuyerIdentity") -> None:
        """Set the buyer identity for tiered pricing access.

        Args:
            identity: BuyerIdentity with seat/agency/advertiser info
        """
        self.buyer_identity = identity

    def get_access_tier(self) -> str:
        """Get the current access tier based on buyer identity.

        Returns:
            Access tier name ('public', 'seat', 'agency', 'advertiser')
        """
        if self.buyer_identity:
            return self.buyer_identity.get_access_tier().value
        return "public"

    def _get_identity_context(self) -> dict[str, Any]:
        """Get identity context for API calls.

        Returns:
            Dictionary with identity context to include in requests
        """
        if self.buyer_identity:
            return self.buyer_identity.to_context_dict()
        return {"access_tier": "public"}

    async def discover_inventory(
        self,
        query: str = None,
        channel: str = None,
        max_cpm: float = None,
        min_impressions: int = None,
        targeting: list[str] = None,
        publisher: str = None,
        protocol: Protocol = None,
    ) -> UnifiedResult:
        """Discover available inventory with buyer identity context.

        Queries sellers for available inventory, presenting the buyer's
        identity to unlock tiered pricing and premium inventory access.

        Args:
            query: Natural language query (e.g., 'CTV inventory under $25 CPM')
            channel: Channel filter ('ctv', 'display', 'video', 'mobile')
            max_cpm: Maximum CPM price filter
            min_impressions: Minimum available impressions filter
            targeting: Required targeting capabilities
            publisher: Specific publisher to search
            protocol: Protocol to use for the request

        Returns:
            UnifiedResult with discovered inventory
        """
        # Build filters including identity context
        filters = self._get_identity_context()
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

        args = {"filters": filters}
        if query:
            args["query"] = query

        # Try search_products first, fall back to list_products
        if query:
            return await self.call_tool("search_products", args, protocol=protocol)
        else:
            return await self.call_tool("list_products", protocol=protocol)

    async def get_pricing(
        self,
        product_id: str,
        volume: int = None,
        deal_type: str = None,
        flight_start: str = None,
        flight_end: str = None,
        protocol: Protocol = None,
    ) -> UnifiedResult:
        """Get tier-specific pricing for a product.

        Retrieves pricing from sellers with prices adjusted based
        on the buyer's revealed identity tier.

        Args:
            product_id: Product ID to get pricing for
            volume: Requested impression volume (may unlock volume discounts)
            deal_type: Deal type ('PG', 'PD', 'PA')
            flight_start: Flight start date (YYYY-MM-DD)
            flight_end: Flight end date (YYYY-MM-DD)
            protocol: Protocol to use for the request

        Returns:
            UnifiedResult with pricing information
        """
        # Get product details
        result = await self.get_product(product_id, protocol=protocol)

        if not result.success:
            return result

        # Enhance result with tiered pricing calculation
        if result.data and isinstance(result.data, dict):
            base_price = result.data.get("basePrice", result.data.get("price", 0))
            if isinstance(base_price, (int, float)) and self.buyer_identity:
                discount = self.buyer_identity.get_discount_percentage()
                tiered_price = base_price * (1 - discount / 100)

                # Volume discount for agency/advertiser tiers
                volume_discount = 0
                tier = self.buyer_identity.get_access_tier().value
                if volume and tier in ("agency", "advertiser"):
                    if volume >= 10_000_000:
                        volume_discount = 10.0
                    elif volume >= 5_000_000:
                        volume_discount = 5.0

                if volume_discount > 0:
                    tiered_price = tiered_price * (1 - volume_discount / 100)

                # Add pricing context to result
                result.data["pricing"] = {
                    "base_price": base_price,
                    "tiered_price": round(tiered_price, 2),
                    "tier": tier if self.buyer_identity else "public",
                    "tier_discount": discount if self.buyer_identity else 0,
                    "volume_discount": volume_discount,
                    "requested_volume": volume,
                    "deal_type": deal_type,
                }

        return result

    async def request_deal(
        self,
        product_id: str,
        deal_type: str = "PD",
        impressions: int = None,
        flight_start: str = None,
        flight_end: str = None,
        target_cpm: float = None,
        protocol: Protocol = None,
    ) -> UnifiedResult:
        """Request a Deal ID from seller for programmatic activation.

        Creates a programmatic deal that can be activated in traditional
        DSP platforms (The Trade Desk, DV360, Amazon DSP, etc.).

        Args:
            product_id: Product ID to request deal for
            deal_type: 'PG' (guaranteed), 'PD' (preferred), 'PA' (private auction)
            impressions: Volume (required for PG deals)
            flight_start: Start date (YYYY-MM-DD)
            flight_end: End date (YYYY-MM-DD)
            target_cpm: Target price for negotiation (agency/advertiser only)
            protocol: Protocol to use for the request

        Returns:
            UnifiedResult with Deal ID and activation instructions
        """
        import hashlib
        from datetime import datetime, timedelta

        # Get product details first
        product_result = await self.get_product(product_id, protocol=protocol)

        if not product_result.success:
            return product_result

        product = product_result.data
        if not product:
            return UnifiedResult(
                success=False,
                error=f"Product {product_id} not found",
                protocol=protocol or self.default_protocol,
            )

        # Calculate pricing
        base_price = product.get("basePrice", product.get("price", 20.0))
        if not isinstance(base_price, (int, float)):
            base_price = 20.0

        tier = "public"
        discount = 0.0
        if self.buyer_identity:
            tier = self.buyer_identity.get_access_tier().value
            discount = self.buyer_identity.get_discount_percentage()

        tiered_price = base_price * (1 - discount / 100)

        # Volume discount
        if impressions and tier in ("agency", "advertiser"):
            if impressions >= 10_000_000:
                tiered_price *= 0.90
            elif impressions >= 5_000_000:
                tiered_price *= 0.95

        # Handle negotiation
        final_price = tiered_price
        if target_cpm and tier in ("agency", "advertiser"):
            floor_price = tiered_price * 0.90
            if target_cpm >= floor_price:
                final_price = target_cpm
            else:
                final_price = floor_price

        # Generate Deal ID
        timestamp = datetime.now().strftime("%Y%m%d%H%M")
        identity_seed = ""
        if self.buyer_identity:
            identity_seed = self.buyer_identity.agency_id or self.buyer_identity.seat_id or "public"
        seed = f"{product_id}-{identity_seed}-{timestamp}"
        hash_suffix = hashlib.md5(seed.encode()).hexdigest()[:8].upper()
        deal_id = f"DEAL-{hash_suffix}"

        # Default flight dates
        if not flight_start:
            flight_start = datetime.now().strftime("%Y-%m-%d")
        if not flight_end:
            flight_end = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

        # Build deal response
        deal_data = {
            "deal_id": deal_id,
            "product_id": product_id,
            "product_name": product.get("name", "Unknown Product"),
            "deal_type": deal_type,
            "price": round(final_price, 2),
            "original_price": round(base_price, 2),
            "discount_applied": round(discount, 1),
            "access_tier": tier,
            "impressions": impressions,
            "flight_start": flight_start,
            "flight_end": flight_end,
            "activation_instructions": {
                "ttd": f"The Trade Desk > Inventory > Private Marketplace > Add Deal ID: {deal_id}",
                "dv360": f"Display & Video 360 > Inventory > My Inventory > New > Deal ID: {deal_id}",
                "amazon": f"Amazon DSP > Private Marketplace > Deals > Add Deal: {deal_id}",
                "xandr": f"Xandr > Inventory > Deals > Create Deal with ID: {deal_id}",
                "yahoo": f"Yahoo DSP > Inventory > Private Marketplace > Enter Deal ID: {deal_id}",
            },
            "expires_at": (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d"),
        }

        return UnifiedResult(
            success=True,
            data=deal_data,
            protocol=protocol or self.default_protocol,
        )
