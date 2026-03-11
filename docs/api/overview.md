# API Overview

The Ad Buyer Agent API is a FastAPI application running on port 8001 by default. All endpoints return JSON.

Base URL: `http://localhost:8001`

## Why So Few Endpoints?

The buyer agent exposes only 7 REST endpoints. This is intentional -- the buyer is primarily a **client** that consumes seller APIs, not a server with a large surface area of its own.

A seller agent publishes inventory, manages accounts, processes orders, and handles creative assignments -- operations that demand dozens of endpoints. The buyer agent's job is different: it discovers sellers, browses their catalogs, negotiates pricing, and books deals. Most of that work happens through outbound calls to seller systems via [MCP](mcp-client.md), [A2A](a2a-client.md), or [REST/OpenDirect](../integration/opendirect.md).

The buyer's own REST API exists for two purposes:

1. **Workflow orchestration** -- the `/bookings` endpoints let operators (or upstream systems) submit a campaign brief and track the automated booking workflow.
2. **Catalog proxy** -- the `/products/search` endpoint lets callers search seller inventory through the buyer without establishing a direct seller connection.

!!! info "Where does the real work happen?"
    The buyer's complexity lives in its **client libraries**, not its server endpoints. See [Protocol Overview](protocols.md) for how the buyer communicates with sellers, and the sections below for the specific seller endpoints consumed.

## Buyer Endpoints

| Method | Path | Tag | Summary |
|--------|------|-----|---------|
| `GET` | `/health` | Health | Service health check |
| `POST` | `/bookings` | Bookings | Start a new booking workflow |
| `GET` | `/bookings/{job_id}` | Bookings | Get booking workflow status |
| `POST` | `/bookings/{job_id}/approve` | Bookings | Approve specific recommendations |
| `POST` | `/bookings/{job_id}/approve-all` | Bookings | Approve all recommendations |
| `GET` | `/bookings` | Bookings | List all booking jobs |
| `POST` | `/products/search` | Products | Search seller product catalog |

### Tags

- **Health** -- service health and readiness
- **Bookings** -- campaign booking workflow lifecycle
- **Products** -- seller inventory product search

---

## Seller Endpoints Consumed

The buyer agent acts as a client to seller systems. The table below lists the seller-side endpoints the buyer calls, grouped by domain.

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

## Interactive Documentation

When the server is running, Swagger UI is available at `/docs` and ReDoc at `/redoc`. The raw OpenAPI schema is at `/openapi.json`.

## Related Pages

- [Authentication](authentication.md)
- [Protocol Overview](protocols.md)
- [Bookings API](bookings.md)
- [Products API](products.md)
- [Deals API Client](deals.md)
- [MCP Client](mcp-client.md)
- [A2A Client](a2a-client.md)
- [Seller Agent Docs](https://iabtechlab.github.io/seller-agent/)
