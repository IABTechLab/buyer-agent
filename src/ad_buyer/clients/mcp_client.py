# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""MCP client for IAB agentic-direct server using Streamable HTTP transport."""

import json
from dataclasses import dataclass
from typing import Any

import httpx

# Optional MCP SDK imports - fall back to simple HTTP if not available
try:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    MCP_SDK_AVAILABLE = True
except ImportError:
    MCP_SDK_AVAILABLE = False


def _is_unauthorized(exc: Exception) -> bool:
    """True when an exception looks like an HTTP 401 from the MCP transport.

    The MCP Streamable-HTTP transport surfaces auth failures as httpx errors or
    generic exceptions whose message carries the status; we match either an
    httpx 401 or a "401"/"Unauthorized" marker in the string form so a single
    reactive re-mint can be attempted before giving up.
    """
    resp = getattr(exc, "response", None)
    if resp is not None and getattr(resp, "status_code", None) == 401:
        return True
    text = str(exc).lower()
    return "401" in text or "unauthorized" in text


@dataclass
class MCPToolResult:
    """Result from an MCP tool call."""

    success: bool = True
    data: Any = None
    error: str = ""
    raw: Any = None


class SimpleMCPClient:
    """Simple HTTP-based MCP client for servers using /mcp/call endpoint.

    This is a lightweight alternative to the full MCP SDK, suitable for
    servers that implement a simple REST-based tool calling interface.
    """

    def __init__(self, base_url: str, timeout: float = 30.0):
        """Initialize the simple MCP client.

        Args:
            base_url: Base URL for the MCP server
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)
        self._tools: dict[str, dict] = {}
        self._server_info: dict[str, Any] = {}

    async def connect(self) -> None:
        """Connect to the server and discover available tools."""
        # Get server info
        try:
            response = await self._client.get(f"{self.base_url}/")
            if response.status_code == 200:
                self._server_info = response.json()
        except (httpx.HTTPError, ValueError):
            pass

        # Try /mcp/tools endpoint first (standard for ad seller agents)
        try:
            response = await self._client.get(f"{self.base_url}/mcp/tools")
            if response.status_code == 200:
                data = response.json()
                tools = data.get("tools", data) if isinstance(data, dict) else data
                if isinstance(tools, list):
                    for tool in tools:
                        name = tool.get("name", "")
                        if name:
                            self._tools[name] = tool
        except (httpx.HTTPError, ValueError):
            pass

        # If no tools found, try calling list_tools
        if not self._tools:
            try:
                result = await self.call_tool("list_tools")
                if result.success and isinstance(result.data, list):
                    for tool in result.data:
                        name = tool.get("name", "")
                        if name:
                            self._tools[name] = tool
            except (httpx.HTTPError, ValueError):
                pass

        # Final fallback: assume standard OpenDirect tools
        if not self._tools:
            standard_tools = [
                "list_products",
                "get_product",
                "list_accounts",
                "create_account",
                "list_orders",
                "create_order",
                "list_lines",
                "create_line",
                "get_pricing",
                "book_programmatic_guaranteed",
                "create_pmp_deal",
            ]
            for name in standard_tools:
                self._tools[name] = {"name": name}

        server_name = self._server_info.get("name", self.base_url)
        print(f"Connected to: {server_name}")
        print(f"Available tools: {len(self._tools)}")

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> "SimpleMCPClient":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    @property
    def tools(self) -> dict[str, dict]:
        """Get available tools."""
        return self._tools

    async def call_tool(self, name: str, arguments: dict[str, Any] = None) -> MCPToolResult:
        """Call a tool via the /mcp/call endpoint.

        Args:
            name: Tool name
            arguments: Tool arguments

        Returns:
            MCPToolResult with the response
        """
        try:
            response = await self._client.post(
                f"{self.base_url}/mcp/call",
                json={"name": name, "arguments": arguments or {}},
            )
            response.raise_for_status()
            data = response.json()

            return MCPToolResult(
                success=data.get("success", True),
                data=data.get("result", data),
                error=data.get("error", ""),
                raw=data,
            )
        except httpx.HTTPStatusError as e:
            return MCPToolResult(success=False, error=f"HTTP {e.response.status_code}")
        except (httpx.HTTPError, ValueError) as e:
            return MCPToolResult(success=False, error=str(e))

    # Convenience methods matching IABMCPClient interface

    async def list_products(self) -> MCPToolResult:
        return await self.call_tool("list_products")

    async def get_product(self, product_id: str) -> MCPToolResult:
        return await self.call_tool("get_product", {"id": product_id})

    async def search_products(self, query: str = None, filters: dict = None) -> MCPToolResult:
        args = {}
        if query:
            args["query"] = query
        if filters:
            args["filters"] = filters
        return await self.call_tool("search_products", args)


class IABMCPClient:
    """MCP client for direct tool calls to IAB agentic-direct server.

    Uses the official MCP SDK with Streamable HTTP transport for proper
    session management and tool execution.
    """

    def __init__(
        self,
        base_url: str,
        *,
        auth_token_provider: Any = None,
        token_endpoint: str = "",
        scope: str = "",
    ):
        """Initialize the MCP client.

        Args:
            base_url: Base URL for the MCP server.
            auth_token_provider: Optional
                :class:`ad_buyer.auth.oauth_token_provider.OAuthTokenProvider`.
                When supplied (the AWS Bedrock AgentCore CUSTOM_JWT path, where a
                discovered seller advertises an OAuth issuer), the client mints a
                client_credentials JWT from ``token_endpoint``/``scope`` and
                attaches ``Authorization: Bearer <JWT>`` to the MCP transport,
                re-minting once on a 401. Omitted = no auth header (local dev /
                unauthenticated servers).
            token_endpoint: OAuth2 token endpoint (from the discovered record's
                ``authentication`` object). Required when a provider is given.
            scope: OAuth2 scope to request (e.g. ``seller-agent/invoke``).
        """
        self.base_url = base_url.rstrip("/")
        # Endpoint shapes (verified against the AWS AgentCore MCP docs):
        #  - An AgentCore CUSTOM_JWT runtime is invoked over HTTPS at
        #    https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<ENCODED_ARN>/invocations?qualifier=DEFAULT
        #    (SDK/SigV4 is DISALLOWED for OAuth runtimes — a raw HTTPS request
        #    with a Bearer token is required). That full URL is used AS-IS; the
        #    Streamable-HTTP transport speaks MCP directly on it.
        #  - The legacy IAB agentic-direct server exposes MCP at `<base>/mcp/sse`.
        # Detect the AgentCore invocations URL and do NOT append `/mcp/sse`.
        if "/invocations" in self.base_url or self.base_url.endswith("/invocations"):
            self.mcp_url = self.base_url
        else:
            self.mcp_url = f"{self.base_url}/mcp/sse"
        self._tools: dict[str, dict] = {}
        self._session: ClientSession | None = None
        self._client_ctx = None
        self._read_stream = None
        self._write_stream = None
        self._get_session_id = None
        self._auth_provider = auth_token_provider
        self._token_endpoint = token_endpoint
        self._scope = scope

    def _auth_headers(self, *, force: bool = False) -> dict[str, str]:
        """Bearer header for the AgentCore CUSTOM_JWT path, or {} when no auth.

        ``force`` re-mints (used after a 401) rather than reusing the cached token.
        """
        if self._auth_provider is None:
            return {}
        token = self._auth_provider.get_token(self._token_endpoint, self._scope, force=force)
        return {"Authorization": f"Bearer {token}"}

    async def connect(self) -> None:
        """Connect to the MCP server and initialize session.

        On the authenticated (AgentCore CUSTOM_JWT) path, a 401 during connect
        triggers a single reactive re-mint (the cached token may have expired)
        before failing.
        """
        try:
            await self._connect_once(self._auth_headers())
        except Exception as exc:  # noqa: BLE001 — reactively re-mint on a 401 only
            if self._auth_provider is not None and _is_unauthorized(exc):
                # Drop the cached token and retry once with a fresh JWT.
                self._auth_provider.invalidate(self._token_endpoint, self._scope)
                await self._connect_once(self._auth_headers(force=True))
            else:
                raise

    async def _connect_once(self, headers: dict[str, str]) -> None:
        # Create streamable HTTP client (headers carry the bearer JWT when auth
        # is configured; empty otherwise).
        self._client_ctx = (
            streamablehttp_client(self.mcp_url, headers=headers)
            if headers
            else streamablehttp_client(self.mcp_url)
        )
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
    def session_id(self) -> str | None:
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

        except (OSError, ValueError, RuntimeError) as e:
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
