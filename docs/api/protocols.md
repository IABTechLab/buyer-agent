# Protocol Overview

The buyer agent supports **three protocols** for communicating with seller agents. Each serves a different purpose in the media buying workflow.

## Protocol Comparison

| Feature | MCP | A2A | REST (OpenDirect) |
|---------|-----|-----|-------------------|
| Transport | Streamable HTTP (SSE) | JSON-RPC 2.0 | Standard HTTP |
| Input | Structured tool calls | Natural language | HTTP requests |
| Speed | Fast | Moderate (LLM step) | Fast |
| Determinism | Deterministic | Non-deterministic | Deterministic |
| Tool Discovery | `session.list_tools()` | `agent-card.json` | `/docs` (OpenAPI) |
| Best For | Automated workflows | Discovery, negotiation | Legacy systems |

**MCP** is the default for all CrewAI tool operations. **A2A** is used for exploratory discovery and complex negotiations where natural language interpretation adds value. **REST** (OpenDirect 2.1) supports operator dashboards and legacy integrations.

## When to Use Which

!!! tip "Decision guide"
    **Use MCP** when you know exactly which operation to perform and want fast, predictable results. This covers the vast majority of automated workflows -- listing products, creating orders, booking line items.

    **Use A2A** when the request is ambiguous or benefits from interpretation. Examples: multi-criteria inventory searches expressed in natural language, complex negotiation exchanges where the seller's AI can reason about pricing, or exploratory discovery where you do not know the exact tool to call.

    **Use REST (OpenDirect)** when integrating with systems that do not support MCP or A2A -- for example, existing dashboards, third-party DSP platforms, or legacy ad servers. The buyer's own REST API also uses this path internally to proxy requests from human operators.

Most buyers will use MCP exclusively and only reach for A2A in discovery or negotiation scenarios. The `UnifiedClient` makes switching trivial -- change a single `protocol` parameter.

## UnifiedClient

The `UnifiedClient` wraps both MCP and A2A behind a single interface. CrewAI tools use it internally so they can switch protocols without changing their logic.

### Protocol Selection

```python
from ad_buyer.clients.unified_client import UnifiedClient, Protocol

# MCP mode (default) -- direct tool execution
client = UnifiedClient(base_url="http://seller:8001", protocol=Protocol.MCP)
await client.connect()
result = await client.list_products()  # calls MCP list_products tool

# A2A mode -- natural language interpretation
client = UnifiedClient(base_url="http://seller:8001", protocol=Protocol.A2A)
await client.connect()
result = await client.list_products()  # sends "List all available advertising products"
```

- `Protocol.MCP` (default) -- executes tools directly via the MCP session. Fast and deterministic.
- `Protocol.A2A` -- converts tool calls to natural language, sends them to the seller's AI agent. Slower but handles ambiguity.

### Dual-Protocol Operation

Connect to both protocols simultaneously for workflows that mix structured operations with conversational queries:

```python
async with UnifiedClient(base_url="http://seller:8001") as client:
    await client.connect_both()

    # Use MCP for structured listing
    products = await client.list_products(protocol=Protocol.MCP)

    # Switch to A2A for a complex natural language query
    result = await client.send_natural_language(
        "Find me CTV inventory with household targeting under $30 CPM"
    )
```

### Automatic Tool-to-Language Mapping

When using A2A mode, the `UnifiedClient` automatically converts structured tool calls into natural language. For example:

| Tool Call | A2A Message |
|-----------|-------------|
| `call_tool("list_products")` | "List all available advertising products" |
| `call_tool("create_account", {"name": "Acme"})` | "Create an account named 'Acme' of type advertiser" |
| `call_tool("create_order", {"name": "Q2", "budget": 50000})` | "Create an order named 'Q2' for account ... with budget $50,000.00" |
| `call_tool("get_product", {"id": "ctv-1"})` | "Get product with ID ctv-1" |

This lets the same CrewAI tool code work transparently across both protocols.

## How CrewAI Tools Use the Unified Client

Each CrewAI tool (e.g., `DiscoverInventoryTool`, `RequestDealTool`) receives a `UnifiedClient` at initialization. The tool calls convenience methods like `client.search_products()` or `client.get_product()`, and the `UnifiedClient` routes the request through the configured protocol:

```
CrewAI Tool (_arun)
    |
    v
UnifiedClient.call_tool(name, args, protocol)
    |
    +-- Protocol.MCP --> IABMCPClient.call_tool(name, args) --> Seller MCP Server
    |
    +-- Protocol.A2A --> A2AClient.send_message(natural_language) --> Seller A2A Server
```

The tool does not need to know which protocol is in use.

## Communication Paths

### MCP Path (Automated Workflows)

```
CrewAI Tools --> UnifiedClient --> IABMCPClient --> Seller MCP Server (/mcp/sse) --> Seller Tools
```

Direct tool execution. The buyer specifies the tool name and arguments; the seller executes them and returns structured results.

### A2A Path (Conversational)

```
CrewAI Tools --> UnifiedClient --> A2AClient --> Seller A2A Server (/a2a/*/jsonrpc) --> NL Processing --> Seller Tools
```

Natural language interpretation. The buyer sends a message; the seller's AI selects and executes the appropriate tools, then responds in natural language with structured data.

### REST Path (Operator / Legacy)

```
Human / Dashboard --> REST API --> Buyer Agent --> (MCP or A2A) --> Seller Agent
```

The buyer's own REST API (FastAPI) serves human operators and dashboards. Internally, the buyer still uses MCP or A2A to communicate with sellers.

## Configuration

The buyer agent's protocol behavior is controlled by these settings:

| Setting | Description | Default |
|---------|-------------|---------|
| `iab_server_url` | Base URL for the seller agent | `http://localhost:8000` |
| `seller_endpoints` | Map of seller names to URLs (multi-seller) | `{}` |
| `default_protocol` | Default protocol for UnifiedClient | `Protocol.MCP` |

Set via environment variables or `config/settings.py`.

## Related

- [MCP Client](mcp-client.md) -- detailed MCP client usage
- [A2A Client](a2a-client.md) -- detailed A2A client usage
- [API Overview](overview.md) -- buyer REST API reference
- [Seller MCP Documentation](https://iabtechlab.github.io/seller-agent/api/mcp/)
- [Seller A2A Documentation](https://iabtechlab.github.io/seller-agent/api/a2a/)
