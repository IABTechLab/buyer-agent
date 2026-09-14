# Authentication

The buyer agent has two distinct authentication concerns:

1. **Inbound authentication** --- protecting the buyer's own API and MCP tools from unauthorized callers (operator keys).
2. **Outbound authentication** --- attaching the correct credentials when the buyer calls seller endpoints.

This page covers both, including the `ApiKeyStore` for managing per-seller credentials and the `AuthMiddleware` that injects keys into outgoing requests.

!!! info "Seller-side reference"
    For full details on how the seller validates keys, issues access tiers, and manages trust levels, see the [Seller Authentication docs](https://iabtechlab.github.io/seller-agent/api/authentication/).

---

## Inbound Authentication (Operator Keys)

Every non-public REST route and every MCP tool over HTTP (except `health_check`) requires an **operator** API key. Anonymous requests receive `401`.

### Bootstrap: First Operator Key

Creating keys via the HTTP API itself requires an existing operator credential. Mint the **first** operator key out-of-band with the CLI (writes directly to SQLite — no network surface):

```bash
ad-buyer create-operator-key --label "Primary operator"
```

Run this with the same `DATABASE_URL` (`.env`) as the server. The full key is printed **once** — store it securely.

```bash
ad-buyer list-operator-keys
ad-buyer list-operator-keys --include-inactive
ad-buyer delete-operator-key --label "Primary operator"
# or
ad-buyer delete-operator-key --key-id key-a1b2c3d4
```

### Authentication Methods

Two methods are accepted on every protected endpoint:

```
Authorization: Bearer <api_key>
```

```
X-Api-Key: <api_key>
```

### Public Paths

These paths never require authentication:

| Path | Purpose |
|------|---------|
| `/health` | Health check |
| `/docs` | Swagger UI |
| `/docs/oauth2-redirect` | Swagger OAuth redirect |
| `/openapi.json` | OpenAPI schema |
| `/redoc` | ReDoc documentation |

### Additional Operator Keys (HTTP)

With an existing operator credential:

```bash
curl -X POST http://localhost:8001/auth/api-keys/operator \
  -H "Authorization: Bearer <operator_api_key>" \
  -H "Content-Type: application/json" \
  -d '{"label": "Ops secondary key", "expires_in_days": 365}'
```

List / get / revoke:

- `GET /auth/api-keys`
- `GET /auth/api-keys/{key_id}`
- `DELETE /auth/api-keys/{key_id}`

There is **no** unauthenticated HTTP bootstrap endpoint. The first key must come from the CLI.

### Storage

Keys are stored as SHA-256 hashes in the `api_keys` SQLite table (schema v6). Plaintext is never persisted. Key format: `abk_live_{token}`.

### Deprecated `API_KEY` env shim

If `API_KEY` is set **and** the `api_keys` table has **never held an operator key**, that env value is accepted as a single synthetic operator credential (compare only). Every shim authentication logs a deprecation warning telling the operator to mint a hashed key.

The shim is gated on *any operator row ever created*, not on active rows:

| DB state | `API_KEY` accepted? |
|----------|--------------------|
| No operator key ever minted | Yes (deprecated) |
| At least one active operator key | No |
| Operator keys minted, then **all revoked or expired** | **No** |

That last row is deliberate. Gating on active keys would let revoking the last hashed key silently downgrade the control plane back to plaintext env auth. If you revoke every key, recover by minting a new one with `ad-buyer create-operator-key` — the shim does not come back.

The shim is removed in the next release. See the [v2.5.0 upgrade guide](../guides/upgrade-v2.5.0.md).

### MCP over HTTP

MCP tools over Streamable HTTP / SSE require the same operator key (`Authorization: Bearer` or `X-Api-Key`). `health_check` remains ungated so probes keep working.

Local stdio MCP access is trusted like the CLI, but only when the process has **not** mounted an MCP HTTP transport. The gate fails closed: in a server process, a tool call that cannot be attributed to an HTTP request is denied rather than treated as trusted stdio, so an MCP SDK change cannot silently un-gate the tools. Both the trusted-local path and every fail-closed denial are logged.

---

## Outbound Authentication

When the buyer calls seller endpoints (quotes, deals, media kits, etc.), it needs to present valid credentials. The seller uses these credentials to determine the buyer's [access tier](https://iabtechlab.github.io/seller-agent/api/authentication/) and unlock tiered pricing, negotiation capabilities, and richer data in responses.

### How Sellers Authenticate Buyers

Sellers accept credentials in two formats on every endpoint:

```
Authorization: Bearer <api_key>
```

```
X-Api-Key: <api_key>
```

Unauthenticated requests receive `public`-tier access only --- price ranges instead of exact prices, no negotiation, and limited data.

### Seller Access Tiers

The tier the buyer receives depends on the identity fields associated with its API key:

| Tier | Identity Required | Pricing Visibility | Negotiation |
|------|-------------------|-------------------|-------------|
| `public` | None (anonymous) | Price ranges only | No |
| `seat` | DSP seat ID | Exact prices, no discounts | Limited |
| `agency` | Agency ID | Tier discounts applied | Standard |
| `advertiser` | Full advertiser identity | Full discounts + volume pricing | Premium |

!!! tip "Maximize your access"
    When creating an API key on the seller, include `seat_id`, `agency_id`, and `advertiser_id` to receive the highest tier. Keys with only a `seat_id` will be limited to seat-tier pricing.

---

## ApiKeyStore (Outbound Seller Credentials)

The `ApiKeyStore` provides file-backed credential storage for **seller** API keys (outbound). It stores one key per seller URL in a JSON file at `~/.ad_buyer/seller_keys.json`. Values are base64-encoded on disk to prevent accidental exposure in casual file reads.

This is distinct from inbound operator keys in SQLite.

!!! warning "MediaKitClient still reads the inbound `API_KEY`"
    The `get_seller_media_kit` and `compare_sellers` MCP tools build `MediaKitClient` with `settings.api_key`. That is the deprecated **inbound** shim, reused as outbound seller auth. Other outbound clients already pick up per-seller keys from `ApiKeyStore` via `AuthMiddleware`. When the shim is removed in v2.6.0, those media-kit calls will go out unauthenticated (public-tier) unless `MediaKitClient` is given a seller credential. Do not rely on `API_KEY` for seller access.

!!! warning "Not encryption"
    Base64 encoding is an obfuscation layer, not encryption. For production deployments, back the store with a secrets manager or encrypted file system.

### Initialization

```python
from ad_buyer.auth.key_store import ApiKeyStore

# Default location: ~/.ad_buyer/seller_keys.json
store = ApiKeyStore()

# Custom location
from pathlib import Path
store = ApiKeyStore(store_path=Path("/etc/ad_buyer/keys.json"))
```

### Store a Key

```python
store.add_key("http://seller.example.com:8000", "sk-abc123secret")
```

### Full ApiKeyStore API

| Method | Signature | Returns | Description |
|--------|-----------|---------|-------------|
| `add_key` | `(seller_url: str, api_key: str)` | `None` | Store or replace a key |
| `get_key` | `(seller_url: str)` | `str \| None` | Retrieve a key |
| `remove_key` | `(seller_url: str)` | `bool` | Remove a key; `True` if it existed |
| `rotate_key` | `(seller_url: str, new_key: str)` | `None` | Replace with a new key |
| `list_sellers` | `()` | `list[str]` | All seller URLs with stored keys |

---

## AuthMiddleware

The `AuthMiddleware` sits between the buyer's HTTP clients and the network. It automatically attaches stored **seller** API keys to outgoing requests and inspects responses for `401` status codes that indicate expired or revoked credentials.

```python
from ad_buyer.auth.key_store import ApiKeyStore
from ad_buyer.auth.middleware import AuthMiddleware

store = ApiKeyStore()
store.add_key("http://seller.example.com:8000", "sk-abc123secret")
middleware = AuthMiddleware(key_store=store, header_type="api_key")
```

!!! note "403 is not re-auth"
    Only HTTP 401 (authentication failure) triggers `needs_reauth`. HTTP 403 (authorization / insufficient permissions) is intentionally excluded.

---

## Related

- [Seller Authentication](https://iabtechlab.github.io/seller-agent/api/authentication/) --- Seller-side key management, access tiers, and trust levels
- [Deals API](deals.md) --- Deal client that uses these auth mechanisms
- [Seller Discovery](seller-discovery.md) --- Discovering sellers to authenticate with
- [Identity Strategy](../guides/identity.md) --- How buyer identity maps to seller tiers
