# Upgrading to v2.5.0

v2.5.0 closes the buyer agent's control plane. Two changes are **breaking**:

1. **Keyless mode is gone.** Running with no credential no longer leaves the API open — protected routes now answer `401`.
2. **Default ports moved.** `SELLER_ENDPOINTS` examples and the buyer's own listen port were swapped so the two agents no longer collide.

Read both sections before upgrading a running deployment.

---

## 1. Operator keys replace the shared `API_KEY`

### What changed

| Before | After |
|--------|-------|
| A single plaintext `API_KEY` env var, compared by middleware | Hashed, per-key operator credentials in the `api_keys` SQLite table (schema v6) |
| Empty `API_KEY` disabled auth entirely (open API) | No keyless mode — protected routes answer `401` |
| Any key holder could do anything | Keys carry a role; the control plane requires `operator` |
| n/a | Keys are labelled, listable, revocable, and optionally expiring |

The migration from schema v5 to v6 is additive and idempotent (`CREATE TABLE IF NOT EXISTS api_keys`); it runs automatically on first connect. No existing rows are rewritten.

### Upgrade steps

1. Deploy the new version.
2. Mint the first operator key **on the host that owns the database**, using the same `DATABASE_URL` as the server:

```bash
uv run ad-buyer create-operator-key --label "Primary operator"
```

The full key is printed exactly once. Store it in your secret manager.

3. Give the key to every client that calls the buyer:

```bash
curl -H "Authorization: Bearer $BUYER_OPERATOR_KEY" http://localhost:8001/bookings
# X-Api-Key: $BUYER_OPERATOR_KEY works too
```

4. Remove `API_KEY` from your environment once step 3 is done.

There is **no** unauthenticated HTTP bootstrap endpoint — by design. Additional keys come from `POST /auth/api-keys/operator` and require an existing operator credential.

### What is now gated

- Every REST route except `/health`, `/docs`, `/redoc`, `/openapi.json`, and `/docs/oauth2-redirect`.
- Every MCP tool over HTTP except `health_check`.
- Local stdio MCP stays trusted (like the CLI), but only in a process that has not mounted an MCP HTTP transport. In a server process, a tool call that cannot be attributed to an HTTP request is **denied**, not trusted.

`tests/unit/test_route_auth_sweep.py` walks the whole route table and asserts anonymous callers get `401`, so a newly added route must either be gated or be added to the public allowlist deliberately.

### The `API_KEY` shim, and when it stops working

For one release, `API_KEY` still authenticates — but only while the `api_keys` table has **never held an operator key**:

| DB state | `API_KEY` accepted? |
|----------|--------------------|
| No operator key ever minted | Yes (logs a deprecation warning on every use) |
| At least one active operator key | No |
| Operator keys minted, then all revoked or expired | **No** |

The last row matters: revoking every hashed key does **not** reopen plaintext env auth. If you lock yourself out, mint a new key with the CLI — the shim will not come back.

**The shim is removed in the next release (v2.6.0).** Before then:

- Replace `API_KEY` with a minted operator key everywhere.
- `MediaKitClient` (used by `get_seller_media_kit` / `compare_sellers`) still sends `settings.api_key` as outbound seller auth. That reuses the inbound shim; per-seller keys in `ApiKeyStore` already cover other outbound clients. When the shim is removed those media-kit calls go out unauthenticated unless `MediaKitClient` is given a seller credential. See [Authentication](../api/authentication.md).

### Rollback

v2.5.0 only adds a table. Rolling back to v2.4.x leaves the `api_keys` rows in place and unused, and the old `API_KEY` middleware resumes working. Rolling forward again re-enables the gate; the previously minted keys still validate.

---

## 2. Port changes

The seller agent listens on **8000** by default. The buyer's docs, demo script, and examples disagreed with that and with each other, so ports were normalized:

| What | Before | After | Why |
|------|--------|-------|-----|
| Buyer's own listen port (`run_server` default, README examples, `run-demo.sh`) | `8000` | `8001` | `8000` collides with a default seller on the same host. The Dockerfile, docker-compose, CI, and the quickstart already used `8001`. |
| `SELLER_ENDPOINTS` examples (`.env.example`, quickstart, configuration guide, `run-demo.sh`) | `8001` | `8000` | This is the setting that points at a seller agent, and sellers default to `8000`. |

Unchanged:

- `IAB_SERVER_URL` — addresses the IAB agentic-direct server, not a seller agent.
- `OPENDIRECT_BASE_URL` — addresses a separate OpenDirect endpoint.

### Upgrade steps

- If you relied on the buyer serving `:8000`, either pass `--port 8000` to uvicorn or update your reverse proxy, health checks, and MCP client URLs to `:8001`.
- If you set `SELLER_ENDPOINTS` explicitly, nothing changes — only the documented defaults and examples moved.
- `run-demo.sh` now starts the seller on `8000` and the buyer on `8001`; override with `SELLER_PORT` / `BUYER_PORT`.

---

## 3. Demo and smoke tests

`run-demo.sh` now mints an operator key at startup and prints the `curl` commands with it, because the documented follow-on requests would otherwise all `401`.

The live MCP smoke tests need a key too:

```bash
export BUYER_OPERATOR_KEY="$(uv run ad-buyer create-operator-key --label smoke --quiet)"
uv run pytest tests/smoke -m smoke
```

Without `BUYER_OPERATOR_KEY` they skip rather than assert anonymous access, which a correctly gated server refuses.
