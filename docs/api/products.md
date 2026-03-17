# Products API

The products endpoint lets you search the seller agent's product catalog directly, outside of a booking workflow. Use this for quick inventory lookups when you already know what channel, format, or price range you need --- for richer browsing with package metadata, tier-based pricing, and cross-seller comparison, use the [Media Kit API](media-kit.md) instead.

## POST /products/search

Search available advertising products from connected seller agents.

### Request Body --- `ProductSearchRequest`

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `channel` | `string` | no | `null` | Filter by channel (e.g. `display`, `video`, `ctv`) |
| `format` | `string` | no | `null` | Filter by ad format (e.g. `banner`, `pre-roll`) |
| `min_price` | `float` | no | `null` | Minimum base price |
| `max_price` | `float` | no | `null` | Maximum base price |
| `limit` | `int` | no | `10` | Number of results (1-50) |

### Response

```json
{
  "results": [ ... ]
}
```

The `results` array contains product data returned by the seller agent's product catalog, filtered according to the search parameters.

### How It Works

The endpoint creates an `OpenDirectClient` using the configured seller connection (`OPENDIRECT_BASE_URL`, `OPENDIRECT_TOKEN`, `OPENDIRECT_API_KEY`) and delegates to the `ProductSearchTool`. The tool calls the seller agent's `/products/search` endpoint (or `/products` with filter parameters) and returns matching products.

### Example

```bash
curl -X POST http://localhost:8001/products/search \
  -H "Content-Type: application/json" \
  -d '{
    "channel": "display",
    "max_price": 15.0,
    "limit": 5
  }'
```

### Connection to Seller Agent

The product search connects to the seller agent's [OpenDirect](https://iabtechlab.com/standards/opendirect/) API. Make sure:

1. The seller agent is running and accessible at the configured `OPENDIRECT_BASE_URL`.
2. Authentication credentials (`OPENDIRECT_TOKEN` or `OPENDIRECT_API_KEY`) are set if the seller requires them.

See [Seller Agent Integration](../integration/seller-agent.md) for details.

---

## Error Handling

The products endpoint returns structured error responses with an HTTP status code and a JSON body.

```json
{
  "error": "seller_unreachable",
  "detail": "Cannot connect to seller agent at http://seller:8001"
}
```

### Error Codes

| HTTP Status | `error` | When |
|------------|---------|------|
| `422` | `validation_error` | Invalid search parameters (e.g., `min_price` > `max_price`, `limit` out of 1-50 range) |
| `502` | `seller_unreachable` | Seller agent is not running or not accessible at the configured `OPENDIRECT_BASE_URL` |
| `502` | `seller_error` | Seller agent returned a non-2xx response to the product search request |
| `401` | `auth_error` | Seller rejected the configured `OPENDIRECT_TOKEN` or `OPENDIRECT_API_KEY` |
| `504` | `seller_timeout` | Seller agent did not respond within the request timeout |
| `500` | `internal_error` | Unexpected error during product search execution |

!!! tip "Empty results are not errors"
    If no products match the search criteria, the endpoint returns `200` with an empty `results` array --- not a `404`.

!!! tip "Negotiate Before Booking"
    After finding products through search, eligible buyer tiers (Agency and Advertiser) can negotiate pricing with the seller before placing bookings. See the [Negotiation Guide](../guides/negotiation.md) for details.
