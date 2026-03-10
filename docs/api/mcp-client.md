# MCP Client (Seller Communication)

The buyer agent uses the **Model Context Protocol (MCP)** as its primary protocol for calling seller agent tools. MCP provides deterministic, structured tool execution over HTTP -- ideal for automated workflows where the buyer knows exactly which operation to perform.

## Client Implementations

The buyer includes two MCP client implementations, selected based on environment and dependency availability.

### IABMCPClient

The full-featured client built on the official MCP SDK. It uses **Streamable HTTP transport** (SSE) for session-based communication with the seller.

- **Transport**: Streamable HTTP at `{base_url}/mcp/sse`
- **Session management**: Creates a `ClientSession` with `initialize()` handshake
- **Tool discovery**: Calls `session.list_tools()` to enumerate seller capabilities
- **Dependency**: Requires `mcp` SDK package (`from mcp import ClientSession`)

Connection flow:

1. Open a Streamable HTTP connection to `/mcp/sse`
2. Initialize the MCP session (protocol handshake)
3. Call `list_tools()` to cache available tools
4. Execute tools via `session.call_tool(name, arguments)`

### SimpleMCPClient

A lightweight HTTP fallback that does not require the MCP SDK. It calls simple REST endpoints exposed by the seller.

- **Tool listing**: `GET /mcp/tools` -- returns available tool definitions
- **Tool execution**: `POST /mcp/call` -- sends `{"name": ..., "arguments": {...}}`
- **Fallback chain**: Tries `/mcp/tools`, then `call_tool("list_tools")`, then assumes standard OpenDirect tools
- **No session**: Each request is independent (no SSE connection)

The `SimpleMCPClient` is used automatically when the `mcp` SDK is not installed.

## Tool Discovery

Both clients discover seller tools on connect. The seller exposes tools following the IAB OpenDirect 2.1 data model. After connecting, inspect `client.tools` to see what the seller offers:

```python
client = IABMCPClient(base_url="http://seller:8001")
await client.connect()

for name, tool in client.tools.items():
    print(f"{name}: {tool.get('description', '')}")
```

## Available Seller Tools

From the buyer's perspective, a standard IAB seller agent exposes these tools:

| Category | Tools |
|----------|-------|
| **Products** | `list_products`, `get_product`, `search_products` |
| **Accounts** | `list_accounts`, `create_account`, `get_account` |
| **Orders** | `list_orders`, `create_order`, `get_order` |
| **Lines** | `list_lines`, `create_line`, `get_line`, `update_line` |
| **Creatives** | `list_creatives`, `create_creative` |
| **Assignments** | `create_assignment` |

Each tool accepts structured arguments (e.g., `{"id": "product-123"}`) and returns JSON results wrapped in an `MCPToolResult`.

## Example Usage

Direct usage via the `UnifiedClient` (recommended for CrewAI tools):

```python
from ad_buyer.clients.unified_client import UnifiedClient, Protocol

client = UnifiedClient(base_url="http://seller:8001")
await client.connect(Protocol.MCP)
products = await client.list_products()
pricing = await client.get_pricing("premium-video", volume=5000000)
deal = await client.request_deal("premium-video", "PG", impressions=5000000)
```

Direct usage via `IABMCPClient`:

```python
from ad_buyer.clients.mcp_client import IABMCPClient

async with IABMCPClient(base_url="http://seller:8001") as client:
    products = await client.list_products()
    account = await client.create_account("Acme Corp", account_type="advertiser")
    order = await client.create_order(
        account_id=account.data["id"],
        name="Q2 Campaign",
        budget=50000.0,
        start_date="2026-04-01",
        end_date="2026-06-30",
    )
    line = await client.create_line(
        order_id=order.data["id"],
        product_id="premium-video",
        name="CTV Flight",
        quantity=5000000,
    )
```

## When to Use MCP

| Scenario | MCP | A2A |
|----------|-----|-----|
| Automated booking workflows | Preferred | -- |
| Structured CRUD operations | Preferred | -- |
| Deterministic, repeatable results | Preferred | -- |
| Exploratory discovery | -- | Preferred |
| Complex multi-turn negotiation | -- | Preferred |

Use MCP when you know the exact tool and arguments. The buyer's CrewAI tools default to MCP for all standard operations.

## Related

- [A2A Client](a2a-client.md) -- conversational protocol for discovery and negotiation
- [Protocol Overview](protocols.md) -- comparison of all three protocols
- [Seller MCP Documentation](https://iabtechlab.github.io/seller-agent/api/mcp/)
