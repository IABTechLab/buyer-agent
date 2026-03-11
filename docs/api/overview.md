# API Overview

The Ad Buyer Agent API exposes **7 endpoints** across **3 tags**. All endpoints are served from a single FastAPI application.

**Base URL:** `http://localhost:8001`
**OpenAPI docs:** `http://localhost:8001/docs`

The buyer agent is primarily a **client** that consumes seller APIs, not a server with a large surface area of its own. Most of its work happens through outbound calls to seller systems via [MCP](mcp-client.md), [A2A](a2a-client.md), or [REST/OpenDirect](../integration/opendirect.md). The buyer's own REST API handles workflow orchestration and catalog proxying.

---

## Health

| Method | Path | Summary |
|--------|------|---------|
| `GET` | `/health` | Service health check |

## Bookings

| Method | Path | Summary |
|--------|------|---------|
| `POST` | `/bookings` | Start a new booking workflow |
| `GET` | `/bookings` | List all booking jobs |
| `GET` | `/bookings/{job_id}` | Get booking workflow status |
| `POST` | `/bookings/{job_id}/approve` | Approve specific recommendations |
| `POST` | `/bookings/{job_id}/approve-all` | Approve all recommendations |

## Products

| Method | Path | Summary |
|--------|------|---------|
| `POST` | `/products/search` | Search seller product catalog |

---

## Seller Endpoints Consumed

The buyer agent acts as a client to seller systems. The tables below list the seller-side endpoints the buyer calls, grouped by domain.

### Quotes & Deals (REST)

The [`DealsClient`](deals.md) communicates with the seller's quote-then-book REST API:

| Method | Seller Endpoint | Purpose | Buyer Client Method |
|--------|----------------|---------|---------------------|
| `POST` | `/api/v1/quotes` | Request non-binding price quote | `DealsClient.request_quote()` |
| `GET` | `/api/v1/quotes/{id}` | Retrieve a quote | `DealsClient.get_quote()` |
| `POST` | `/api/v1/deals` | Book a deal from a quote | `DealsClient.book_deal()` |
| `GET` | `/api/v1/deals/{id}` | Retrieve deal status | `DealsClient.get_deal()` |
| `POST` | `/api/v1/deals/{id}/makegoods` | Request makegood (linear TV) | `DealsClient.request_makegood()` |
| `POST` | `/api/v1/deals/{id}/cancel` | Request cancellation (linear TV) | `DealsClient.request_cancellation()` |

### OpenDirect (REST)

The [`OpenDirectClient`](../integration/opendirect.md) implements the IAB OpenDirect 2.1 resource model:

| Method | Seller Endpoint | Purpose |
|--------|----------------|---------|
| `GET` | `/products` | List products |
| `GET` | `/products/{id}` | Get product detail |
| `POST` | `/products/search` | Search products with filters |
| `POST` | `/products/avails` | Check inventory availability |
| `POST/GET` | `/accounts` | Create / list accounts |
| `GET` | `/accounts/{id}` | Get account detail |
| `POST/GET` | `/accounts/{id}/orders` | Create / list orders |
| `GET` | `/accounts/{id}/orders/{id}` | Get order detail |
| `POST/GET` | `/accounts/{id}/orders/{id}/lines` | Create / list line items |
| `PUT/PATCH` | `/accounts/{id}/orders/{id}/lines/{id}` | Update / book line items |
| `GET` | `/accounts/{id}/orders/{id}/lines/{id}/stats` | Line item delivery stats |
| `POST/GET` | `/accounts/{id}/creatives` | Create / list creatives |

### MCP Tools (Seller)

Via [MCP](mcp-client.md), the buyer calls seller-side tools directly. These map to the same underlying operations as the OpenDirect endpoints:

| Tool | Purpose |
|------|---------|
| `list_products` / `get_product` / `search_products` | Product catalog |
| `list_accounts` / `create_account` / `get_account` | Account management |
| `list_orders` / `create_order` / `get_order` | Order management |
| `list_lines` / `create_line` / `get_line` / `update_line` | Line item management |
| `list_creatives` / `create_creative` / `create_assignment` | Creative management |

### A2A (Conversational)

Via [A2A](a2a-client.md), the buyer sends natural language requests to the seller's AI agent. The same operations are available but expressed conversationally (e.g., *"Find me CTV inventory with household targeting under $30 CPM"*).

!!! tip "Seller documentation"
    For full details on the seller-side endpoints and tools listed above, see the [Seller Agent API Reference](https://iabtechlab.github.io/seller-agent/api/overview/).

---

## Related

- [Authentication](authentication.md)
- [Protocol Overview](protocols.md)
- [Bookings API](bookings.md)
- [Products API](products.md)
- [Deals API Client](deals.md)
- [MCP Client](mcp-client.md)
- [A2A Client](a2a-client.md)
- [Seller Agent Docs](https://iabtechlab.github.io/seller-agent/)
