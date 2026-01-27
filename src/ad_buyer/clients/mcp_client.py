# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""MCP client for IAB agentic-direct server using Streamable HTTP transport."""

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


@dataclass
class MCPToolResult:
    """Result from an MCP tool call."""

    success: bool = True
    data: Any = None
    error: str = ""
    raw: Any = None


class IABMCPClient:
    """MCP client for direct tool calls to IAB agentic-direct server.

    Uses the official MCP SDK with Streamable HTTP transport for proper
    session management and tool execution.
    """

    def __init__(
        self,
        base_url: str = "https://agentic-direct-server-hwgrypmndq-uk.a.run.app",
    ):
        """Initialize the MCP client.

        Args:
            base_url: Base URL for the MCP server
        """
        self.base_url = base_url.rstrip("/")
        self.mcp_url = f"{self.base_url}/mcp/sse"
        self._tools: dict[str, dict] = {}
        self._session: Optional[ClientSession] = None
        self._client_ctx = None
        self._read_stream = None
        self._write_stream = None
        self._get_session_id = None

    async def connect(self) -> None:
        """Connect to the MCP server and initialize session."""
        # Create streamable HTTP client
        self._client_ctx = streamablehttp_client(self.mcp_url)
        streams = await self._client_ctx.__aenter__()
        self._read_stream, self._write_stream, self._get_session_id = streams

        # Create and initialize session
        self._session = ClientSession(self._read_stream, self._write_stream)
        await self._session.__aenter__()
        init_result = await self._session.initialize()

        # Cache available tools
        tools_result = await self._session.list_tools()
        for tool in tools_result.tools:
            self._tools[tool.name] = {
                "name": tool.name,
                "description": tool.description or "",
                "schema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
            }

        server_name = init_result.serverInfo.name if init_result.serverInfo else "unknown"
        print(f"Connected to MCP server: {server_name}")
        print(f"Available tools: {len(self._tools)}")

    async def close(self) -> None:
        """Close the MCP session and connection."""
        if self._session:
            await self._session.__aexit__(None, None, None)
            self._session = None
        if self._client_ctx:
            await self._client_ctx.__aexit__(None, None, None)
            self._client_ctx = None

    async def __aenter__(self) -> "IABMCPClient":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    @property
    def tools(self) -> dict[str, dict]:
        """Get available tools."""
        return self._tools

    @property
    def session_id(self) -> Optional[str]:
        """Get the current session ID."""
        if self._get_session_id:
            return self._get_session_id()
        return None

    async def call_tool(self, name: str, arguments: dict[str, Any] = None) -> MCPToolResult:
        """Call an MCP tool directly.

        Args:
            name: Tool name (e.g., 'list_products', 'create_account')
            arguments: Tool arguments as a dict

        Returns:
            MCPToolResult with the response data
        """
        if not self._session:
            raise MCPClientError("Not connected. Call connect() first.")

        try:
            result = await self._session.call_tool(name, arguments or {})

            # Parse the result content
            data = None
            text_parts = []

            for content in result.content:
                if content.type == "text":
                    text_parts.append(content.text)
                    # Try to parse as JSON
                    try:
                        data = json.loads(content.text)
                    except json.JSONDecodeError:
                        pass

            return MCPToolResult(
                success=not result.isError if hasattr(result, "isError") else True,
                data=data if data is not None else "\n".join(text_parts),
                raw=result,
            )

        except Exception as e:
            return MCPToolResult(
                success=False,
                error=str(e),
            )

    # Convenience methods for common operations

    async def list_products(self) -> MCPToolResult:
        """List available advertising products."""
        return await self.call_tool("list_products")

    async def get_product(self, product_id: str) -> MCPToolResult:
        """Get a specific product by ID."""
        return await self.call_tool("get_product", {"id": product_id})

    async def search_products(self, query: str = None, filters: dict = None) -> MCPToolResult:
        """Search for products."""
        args = {}
        if query:
            args["query"] = query
        if filters:
            args["filters"] = filters
        return await self.call_tool("search_products", args)

    async def list_accounts(self) -> MCPToolResult:
        """List all accounts."""
        return await self.call_tool("list_accounts")

    async def create_account(
        self,
        name: str,
        account_type: str = "advertiser",
        status: str = "active",
    ) -> MCPToolResult:
        """Create a new account.

        Args:
            name: Account name
            account_type: Type of account (advertiser, agency)
            status: Account status
        """
        return await self.call_tool(
            "create_account",
            {
                "name": name,
                "type": account_type,
                "status": status,
            },
        )

    async def get_account(self, account_id: str) -> MCPToolResult:
        """Get an account by ID."""
        return await self.call_tool("get_account", {"id": account_id})

    async def list_orders(self, account_id: str = None) -> MCPToolResult:
        """List orders, optionally filtered by account."""
        args = {}
        if account_id:
            args["accountId"] = account_id
        return await self.call_tool("list_orders", args)

    async def create_order(
        self,
        account_id: str,
        name: str,
        budget: float,
        start_date: str = None,
        end_date: str = None,
    ) -> MCPToolResult:
        """Create a new order.

        Args:
            account_id: Account ID
            name: Order name
            budget: Budget in USD
            start_date: Start date (ISO format)
            end_date: End date (ISO format)
        """
        args = {
            "accountId": account_id,
            "name": name,
            "budget": budget,
        }
        if start_date:
            args["startDate"] = start_date
        if end_date:
            args["endDate"] = end_date
        return await self.call_tool("create_order", args)

    async def get_order(self, order_id: str) -> MCPToolResult:
        """Get an order by ID."""
        return await self.call_tool("get_order", {"id": order_id})

    async def list_lines(self, order_id: str = None) -> MCPToolResult:
        """List line items, optionally filtered by order."""
        args = {}
        if order_id:
            args["orderId"] = order_id
        return await self.call_tool("list_lines", args)

    async def create_line(
        self,
        order_id: str,
        product_id: str,
        name: str,
        quantity: int,
        start_date: str = None,
        end_date: str = None,
    ) -> MCPToolResult:
        """Create a new line item.

        Args:
            order_id: Order ID
            product_id: Product ID to book
            name: Line item name
            quantity: Impressions to book
            start_date: Start date (ISO format)
            end_date: End date (ISO format)
        """
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
        return await self.call_tool("create_line", args)

    async def get_line(self, line_id: str) -> MCPToolResult:
        """Get a line item by ID."""
        return await self.call_tool("get_line", {"id": line_id})

    async def update_line(self, line_id: str, updates: dict[str, Any]) -> MCPToolResult:
        """Update a line item.

        Args:
            line_id: Line ID to update
            updates: Fields to update
        """
        args = {"id": line_id, **updates}
        return await self.call_tool("update_line", args)

    async def list_creatives(self) -> MCPToolResult:
        """List all creatives."""
        return await self.call_tool("list_creatives")

    async def create_creative(
        self,
        name: str,
        creative_type: str,
        url: str = None,
        content: str = None,
    ) -> MCPToolResult:
        """Create a new creative.

        Args:
            name: Creative name
            creative_type: Type (banner, video, native)
            url: URL for hosted creative
            content: Inline creative content
        """
        args = {
            "name": name,
            "type": creative_type,
        }
        if url:
            args["url"] = url
        if content:
            args["content"] = content
        return await self.call_tool("create_creative", args)

    async def create_assignment(
        self,
        line_id: str,
        creative_id: str,
    ) -> MCPToolResult:
        """Assign a creative to a line item.

        Args:
            line_id: Line item ID
            creative_id: Creative ID
        """
        return await self.call_tool(
            "create_assignment",
            {
                "lineId": line_id,
                "creativeId": creative_id,
            },
        )


class MCPClientError(Exception):
    """Error from MCP client."""

    pass
