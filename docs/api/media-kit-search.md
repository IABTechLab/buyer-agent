# Media Kit Search API

Search seller media kits for packages by keyword (e.g. sports, video, premium). Use this when you want to discover packages by topic before booking.

## POST /media-kit/search

Search all configured seller endpoints for packages matching a query. Sellers match the query against package names, descriptions, tags, and content categories.

### Request Body — `MediaKitSearchRequest`

| Field  | Type   | Required | Description                                      |
|--------|--------|----------|--------------------------------------------------|
| `query` | string | yes      | Search keyword (e.g. `sports`, `video`, `premium display`) |

### Response

```json
{
  "query": "sports",
  "packages": [
    {
      "package_id": "pkg-001",
      "name": "Sports Premium Video",
      "description": "...",
      "price_range": "$28-$42 CPM",
      "tags": ["sports", "video"],
      "seller_url": "http://localhost:8001"
    }
  ],
  "total": 1
}
```

### Example: sports packages

With the **buyer** on port **8000** and the **seller** on port **8001**:

```bash
curl -X POST http://localhost:8000/media-kit/search \
  -H "Content-Type: application/json" \
  -d '{"query": "sports"}'
```

### Configuration

The endpoint uses the same seller configuration as the rest of the buyer agent:

- **SELLER_ENDPOINTS** — Comma-separated seller base URLs (e.g. `http://localhost:8001` when the seller is on port 8001).
- **OPENDIRECT_BASE_URL** — If `SELLER_ENDPOINTS` is not set, the media kit base URL is derived by stripping `/api/v2.1` (or `/api/v2`, `/api`) from this URL. Example: seller on 8001 → `http://localhost:8001/api/v2.1` yields media kit base `http://localhost:8001`.

**Typical local setup:** Buyer agent on **localhost:8000**, seller agent on **localhost:8001**. Set `OPENDIRECT_BASE_URL=http://localhost:8001/api/v2.1` or `SELLER_ENDPOINTS=http://localhost:8001` so the buyer connects to the seller.

Ensure the seller exposes a media kit at `GET /media-kit` and `POST /media-kit/search`.
