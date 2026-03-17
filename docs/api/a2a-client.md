# A2A Client (Conversational Protocol)

The buyer agent uses the **Agent-to-Agent (A2A) protocol** for conversational discovery and negotiation with seller agents. A2A sends natural language messages over JSON-RPC 2.0; the seller's AI interprets the request and executes the appropriate tools.

## When to Use A2A

| Scenario | A2A | MCP |
|----------|-----|-----|
| Exploratory discovery queries | Preferred | -- |
| Complex negotiations with context | Preferred | -- |
| Ambiguous or open-ended requests | Preferred | -- |
| Automated booking workflows | -- | Preferred |
| Deterministic, repeatable results | -- | Preferred |

Use A2A when the request benefits from natural language interpretation --- for example, asking "What CTV inventory do you have under $25 with household targeting?" rather than constructing exact filter parameters. For structured, deterministic operations, see the [MCP Client](mcp-client.md).

## A2AClient Class

The `A2AClient` connects to a seller's A2A endpoint and sends natural language messages.

- **Endpoint**: `{base_url}/a2a/{agent_type}/jsonrpc`
- **Agent card**: `{base_url}/a2a/{agent_type}/.well-known/agent-card.json`
- **Agent types**: `"buyer"` or `"seller"` --- determines which agent persona handles the request
- **Protocol**: JSON-RPC 2.0 with `message/send` method
- **Transport**: Standard HTTP POST with `Content-Type: application/json`

No explicit connect step is needed. The client sends requests immediately.

## JSON-RPC Request Structure

Every A2A request is a JSON-RPC 2.0 envelope containing a natural language message. The buyer constructs this envelope automatically; understanding the wire format is useful for debugging and integration testing.

### Envelope Fields

| Field | Type | Description |
|-------|------|-------------|
| `jsonrpc` | `str` | Always `"2.0"` |
| `method` | `str` | Always `"message/send"` |
| `params` | `object` | Contains `message` and optional `contextId` |
| `id` | `str` | Client-generated UUID for request correlation |

### `params.message` Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `messageId` | `str` | yes | Client-generated UUID identifying this message |
| `role` | `str` | yes | Always `"user"` for buyer-initiated messages |
| `parts` | `list[object]` | yes | Message content parts (see below) |

### Message Parts

Each entry in the `parts` array has a `kind` field that determines its structure:

| Kind | Fields | Description |
|------|--------|-------------|
| `text` | `kind`, `text` | Natural language text content |
| `data` | `kind`, `data` | Structured data (JSON object) |

The buyer currently sends only `text` parts. The seller may return both `text` and `data` parts in its response.

### Wire Format Example

```json
{
  "jsonrpc": "2.0",
  "method": "message/send",
  "params": {
    "message": {
      "messageId": "a1b2c3d4-5678-90ab-cdef-1234567890ab",
      "role": "user",
      "parts": [
        {"kind": "text", "text": "List all available advertising products"}
      ]
    },
    "contextId": "ctx-previous-response-id"
  },
  "id": "f9e8d7c6-5432-10ab-cdef-fedcba987654"
}
```

The `contextId` field is omitted on the first message in a conversation and included on subsequent messages to continue the same context.

## Request Formats for Key Operations

Each convenience method generates a specific natural language prompt. Here is the request format for each operation:

### `send_message(message, context_id)`

Sends an arbitrary natural language message. This is the base method all other convenience methods call.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `message` | `str` | yes | Natural language request text |
| `context_id` | `str` | no | Context ID from a previous response for multi-turn |

### `get_agent_card()`

Fetches the seller's agent card via `GET` (not JSON-RPC). No parameters.

**Endpoint:** `GET {base_url}/a2a/{agent_type}/.well-known/agent-card.json`

### `list_products()`

No parameters. Sends: `"List all available advertising products"`

### `search_products(criteria)`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `criteria` | `str` | yes | Natural language search criteria |

Sends: `"Search for advertising products: {criteria}"`

### `create_account(name, advertiser_id)`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | `str` | yes | Account name |
| `advertiser_id` | `str` | yes | Advertiser organization ID |

Sends: `"Create an account named '{name}' for advertiser {advertiser_id}"`

### `create_order(account_id, name, budget, start_date, end_date)`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `account_id` | `str` | yes | Account ID |
| `name` | `str` | yes | Order name |
| `budget` | `float` | yes | Budget in USD |
| `start_date` | `str` | yes | Start date (YYYY-MM-DD) |
| `end_date` | `str` | yes | End date (YYYY-MM-DD) |

Sends: `"Create an order named '{name}' for account {account_id} with budget ${budget:,.2f} from {start_date} to {end_date}"`

### `create_line(order_id, product_id, name, quantity, start_date, end_date)`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `order_id` | `str` | yes | Order ID |
| `product_id` | `str` | yes | Product ID |
| `name` | `str` | yes | Line item name |
| `quantity` | `int` | yes | Impressions to book |
| `start_date` | `str` | yes | Start date (YYYY-MM-DD) |
| `end_date` | `str` | yes | End date (YYYY-MM-DD) |

Sends: `"Create a line item named '{name}' for order {order_id} using product {product_id} with {quantity:,} impressions from {start_date} to {end_date}"`

### `book_line(line_id)`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `line_id` | `str` | yes | Line item ID to book |

Sends: `"Book line item {line_id}"`

### `check_availability(product_id, quantity, start_date, end_date)`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `product_id` | `str` | yes | Product ID |
| `quantity` | `int` | yes | Requested impressions |
| `start_date` | `str` | no | Start date (YYYY-MM-DD) |
| `end_date` | `str` | no | End date (YYYY-MM-DD) |

Sends: `"Check availability for product {product_id} with {quantity:,} impressions"` (appends date range if both dates provided)

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

## Error Handling

The client raises `A2AError` when the seller returns a JSON-RPC error response. HTTP-level errors (connection refused, timeouts) raise standard `httpx` exceptions.

```python
from ad_buyer.clients.a2a_client import A2AClient, A2AError
import httpx

try:
    response = await client.send_message("List products")
except A2AError as e:
    # Seller returned a JSON-RPC error
    print(f"A2A error: {e}")
except httpx.ConnectError:
    # Seller agent is unreachable
    print("Cannot connect to seller")
except httpx.TimeoutException:
    # Request timed out (default: 60s)
    print("Request timed out")
except httpx.HTTPStatusError as e:
    # Seller returned non-2xx HTTP status
    print(f"HTTP {e.response.status_code}")
```

### Error Scenarios

| Scenario | Exception | Description |
|----------|-----------|-------------|
| JSON-RPC error in response | `A2AError` | Seller processed the request but returned an error (e.g., invalid tool, internal failure) |
| Seller unreachable | `httpx.ConnectError` | Cannot establish TCP connection to the seller endpoint |
| Request timeout | `httpx.TimeoutException` | No response within the configured timeout (default: 60s) |
| HTTP 4xx/5xx | `httpx.HTTPStatusError` | Seller returned an HTTP error before JSON-RPC processing |
| Invalid agent card | `httpx.HTTPStatusError` | Agent card endpoint returned non-2xx status |
| Malformed response | `KeyError` / `TypeError` | Seller returned JSON that does not match expected A2A structure |

!!! note "No automatic retries"
    Unlike the `DealsClient`, the A2A client does not retry failed requests automatically. Callers should implement their own retry logic for transient failures (connection errors, timeouts).

## Related

- [MCP Client](mcp-client.md) --- structured tool execution protocol
- [Protocol Overview](protocols.md) --- comparison of all three protocols
- [Seller A2A Documentation](https://iabtechlab.github.io/seller-agent/api/a2a/)
