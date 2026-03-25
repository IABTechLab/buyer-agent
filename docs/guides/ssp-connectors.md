# SSP Connector Setup

The buyer agent can import deals directly from three SSPs: PubMatic, Magnite, and Index Exchange. Each connector reads deals targeted to your buyer seat and normalizes them into the deal library.

This guide covers credential setup and running an import for each SSP.

---

## Prerequisites

- The buyer agent must be running (see [Deployment](deployment.md))
- You need a buyer seat ID from each SSP you want to connect
- API credentials from each SSP (see per-SSP sections below)
- Environment variables must be set before starting the agent

Set credentials in your environment or in a `.env` file at the repo root. The agent reads these at startup -- restart if you change them.

---

## Check Which Connectors Are Configured

Before importing, check which connectors are ready:

```
list_ssp_connectors()
```

This returns each connector with `configured: true/false` and the list of required environment variables. Use this to confirm your credentials are picked up correctly.

---

## PubMatic

PubMatic provides a buyer-facing PMP API at `https://api.pubmatic.com`. The connector fetches PMP, Preferred Deal, and PG deals targeted to your seat.

### Credentials

| Variable | Description |
|----------|-------------|
| `PUBMATIC_API_TOKEN` | Bearer token from PubMatic's API portal |
| `PUBMATIC_SEAT_ID` | Your PubMatic buyer seat ID |

Get these from your PubMatic account manager or the PubMatic API portal.

### Setup

```bash
export PUBMATIC_API_TOKEN="your-bearer-token"
export PUBMATIC_SEAT_ID="your-seat-id"
```

### Test Connectivity

```
test_ssp_connection(ssp_name="pubmatic")
```

Returns `connected: true` if credentials are valid. If you get `configured: false`, one or both env vars is missing. If you get `connected: false` with `configured: true`, the token is invalid or expired.

### Run an Import

```
import_deals_ssp(ssp_name="pubmatic")
```

The connector fetches all deal statuses and types (PG, PMP, Preferred) using pagination. Each deal is normalized and saved to the deal library with `import_source: "PUBMATIC"`. Duplicate deals (matched by seller deal ID) are skipped.

---

## Magnite

Magnite operates two platforms with different API endpoints:

- **Magnite Streaming** (`api.tremorhub.com`) -- CTV/OTT inventory (Roku, Fire TV, Samsung TV Plus)
- **Magnite DV+** (`api.rubiconproject.com`) -- display and video

Both platforms use session-based authentication: the connector POSTs your credentials to a login endpoint, then uses the session cookie to fetch deals.

### Credentials

| Variable | Description |
|----------|-------------|
| `MAGNITE_ACCESS_KEY` | API access key (credential username) |
| `MAGNITE_SECRET_KEY` | API secret key (credential password) |
| `MAGNITE_SEAT_ID` | Your Magnite buyer seat ID |
| `MAGNITE_PLATFORM` | `streaming` (default) or `dv_plus` |

`MAGNITE_PLATFORM` is optional. If unset, it defaults to `streaming` (CTV). Set it to `dv_plus` to import from Magnite's display/video platform instead.

### Setup

```bash
export MAGNITE_ACCESS_KEY="your-access-key"
export MAGNITE_SECRET_KEY="your-secret-key"
export MAGNITE_SEAT_ID="your-seat-id"
export MAGNITE_PLATFORM="streaming"   # or "dv_plus"
```

### Test Connectivity

```
test_ssp_connection(ssp_name="magnite")
```

The connection test attempts a login only -- it does not fetch deals. If login succeeds, credentials are valid.

### Run an Import

```
import_deals_ssp(ssp_name="magnite")
```

The connector authenticates, fetches all deals targeted to your seat, and normalizes them. Deals are saved with `import_source: "MAGNITE"`.

!!! note "Platform selection at import time"
    The `MAGNITE_PLATFORM` env var selects the platform for the entire connector instance. To import from both platforms, run two imports with the variable set differently each time.

---

## Index Exchange

Index Exchange uses a simple API key header (`X-API-Key`). Deal creation in IX is publisher-side only -- this connector discovers deals that publishers have already targeted to your seat.

### Credentials

| Variable | Description |
|----------|-------------|
| `IX_API_KEY` | Your Index Exchange API key |
| `IX_SEAT_ID` | Your IX buyer seat/member ID |

Get these from your Index Exchange account team.

### Setup

```bash
export IX_API_KEY="your-api-key"
export IX_SEAT_ID="your-seat-id"
```

### Test Connectivity

```
test_ssp_connection(ssp_name="index_exchange")
```

Makes a minimal API call (1 result) to verify the key is valid.

### Run an Import

```
import_deals_ssp(ssp_name="index_exchange")
```

Deals are saved with `import_source: "INDEX_EXCHANGE"`. The connector handles pagination automatically and deduplicates by seller deal ID.

---

## What Gets Imported

All three connectors normalize deals into the same deal library schema:

| Field | Description |
|-------|-------------|
| `display_name` | Deal name from the SSP |
| `seller_deal_id` | Deal ID as it appears in OpenRTB bid requests |
| `seller_org` | SSP name (e.g. "PubMatic") |
| `deal_type` | Normalized to `PG`, `PD`, `PA`, etc. |
| `status` | `active`, `paused`, or `imported` (for pending deals) |
| `fixed_price_cpm` | Fixed CPM price (PG and PD) |
| `bid_floor_cpm` | Bid floor (auction deals) |
| `flight_start` / `flight_end` | ISO date strings |
| `media_type` | `CTV`, `DIGITAL`, `AUDIO`, etc. |
| `impressions` | Contracted volume (PG) |

After importing, use `list_deals(status="imported")` to review new deals before activating them.

---

## Troubleshooting

### "connector is not configured"

One or more required environment variables is missing or empty. Run `list_ssp_connectors()` to see which variables are needed. Verify they are set in the environment where the agent is running -- not just your terminal.

### Authentication errors (401 / 403)

- **PubMatic**: The bearer token is invalid or expired. Regenerate it in the PubMatic API portal.
- **Magnite**: The access key or secret key is wrong. Check for leading/trailing whitespace.
- **Index Exchange**: The API key is invalid. Contact your IX account team.

### Rate limit errors (429)

The SSP is throttling requests. Wait a few minutes before retrying. For Magnite, the API response may include a `Retry-After` header value (logged at warning level).

### Deals show up as `imported` instead of `active`

This is expected. Deals with `status: "pending"` in the SSP API land as `imported` in the deal library. Review them and update their status to `active` when you are ready to traffic them.

### Zero deals returned

- Confirm your seat ID is correct -- the SSP filters deals by seat.
- Ask your SSP account team whether any deals have been targeted to your seat.
- Try `test_ssp_connection` first to rule out auth issues before a full import.

---

## Related

- [Deal Library](deal-library.md) -- Managing imported deals, templates, and portfolio views
- [Deals API](../api/deals.md) -- REST API reference
- [SSP Connectors Architecture](../architecture/ssp-connectors.md) -- How the connectors work internally
