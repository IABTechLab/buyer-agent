# A2A Client (Conversational Protocol)

The buyer agent uses the **Agent-to-Agent (A2A) protocol** for conversational discovery and negotiation with seller agents. A2A sends natural language messages over JSON-RPC 2.0; the seller's AI interprets the request and executes the appropriate tools.

## A2AClient Class

The `A2AClient` connects to a seller's A2A endpoint and sends natural language messages.

- **Endpoint**: `{base_url}/a2a/{agent_type}/jsonrpc`
- **Agent card**: `{base_url}/a2a/{agent_type}/.well-known/agent-card.json`
- **Agent types**: `"buyer"` or `"seller"` -- determines which agent persona handles the request
- **Protocol**: JSON-RPC 2.0 with `message/send` method
- **Transport**: Standard HTTP POST with `Content-Type: application/json`

No explicit connect step is needed. The client sends requests immediately.

## Convenience Methods

The `A2AClient` provides typed convenience methods that translate structured calls into natural language messages:

| Method | Description |
|--------|-------------|
| `send_message(message, context_id)` | Send any natural language request |
| `get_agent_card()` | Fetch the seller's agent capabilities |
| `list_products()` | "List all available advertising products" |
| `search_products(criteria)` | "Search for advertising products: {criteria}" |
| `create_account(name, advertiser_id)` | Create an account via natural language |
| `create_order(account_id, name, budget, start_date, end_date)` | Create an order via natural language |
| `create_line(order_id, product_id, name, quantity, start_date, end_date)` | Create a line item via natural language |
| `book_line(line_id)` | "Book line item {line_id}" |
| `check_availability(product_id, quantity, start_date, end_date)` | Check product availability |

All convenience methods call `send_message()` internally with a formatted prompt.

## Multi-Turn Conversations

A2A supports multi-turn conversations via `contextId`. The seller maintains conversation state across requests within the same context:

```python
from ad_buyer.clients.a2a_client import A2AClient

client = A2AClient(base_url="http://seller:8001")
response = await client.send_message(
    "What premium video inventory do you have for Q2 with household targeting?"
)
# Follow-up in same context
response = await client.send_message(
    "Can you give me pricing for the CTV package at agency tier?",
    context_id=response.context_id
)
```

The client also tracks context automatically. If you omit `context_id`, the client reuses the most recent context from the last response:

```python
# First message sets the context
response = await client.send_message("Show me your CTV products")
# This automatically continues in the same context
response = await client.send_message("What targeting options are available?")
```

## Response Structure

All A2A responses are parsed into an `A2AResponse` object:

| Field | Type | Description |
|-------|------|-------------|
| `text` | `str` | Natural language response text |
| `data` | `list[dict]` | Structured data parts (product listings, pricing, etc.) |
| `task_id` | `str` | Server-assigned task identifier |
| `context_id` | `str` | Conversation context for multi-turn |
| `success` | `bool` | Whether the request succeeded |
| `error` | `str` | Error message if failed |
| `raw` | `dict` | Full JSON-RPC response |

## Example: Discovery and Negotiation

```python
from ad_buyer.clients.a2a_client import A2AClient

async with A2AClient(base_url="http://seller:8001") as client:
    # Discover inventory with complex criteria
    response = await client.send_message(
        "What premium video inventory do you have for Q2 "
        "with household targeting capabilities under $30 CPM?"
    )
    print(response.text)

    # Negotiate in the same conversation
    response = await client.send_message(
        "Can you give me pricing for the CTV package at agency tier?",
        context_id=response.context_id,
    )

    # Check availability
    response = await client.check_availability(
        product_id="premium-ctv",
        quantity=5_000_000,
        start_date="2026-04-01",
        end_date="2026-06-30",
    )
```

## When to Use A2A

| Scenario | A2A | MCP |
|----------|-----|-----|
| Exploratory discovery queries | Preferred | -- |
| Complex negotiations with context | Preferred | -- |
| Ambiguous or open-ended requests | Preferred | -- |
| Automated booking workflows | -- | Preferred |
| Deterministic, repeatable results | -- | Preferred |

Use A2A when the request benefits from natural language interpretation -- for example, asking "What CTV inventory do you have under $25 with household targeting?" rather than constructing exact filter parameters.

## Related

- [MCP Client](mcp-client.md) -- structured tool execution protocol
- [Protocol Overview](protocols.md) -- comparison of all three protocols
- [Seller A2A Documentation](https://iabtechlab.github.io/seller-agent/api/a2a/)
