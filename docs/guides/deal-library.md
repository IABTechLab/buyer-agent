# Deal Library

The deal library is your portfolio of deals -- imports from SSPs, manually entered deals, and deals booked directly with sellers. This guide covers how to add, search, and manage deals using the MCP tools or the REST API.

## Overview

The deal library stores:

- **Booked deals** -- deals you've negotiated and booked with sellers
- **Imported deals** -- deals pulled from SSP connectors (PubMatic, Magnite, Index Exchange)
- **CSV imports** -- bulk deal uploads from spreadsheets or other systems
- **Manual entries** -- deals created directly from known deal IDs or structured data
- **Templates** -- reusable deal configurations you can instantiate for new campaigns

Each deal in the library tracks its origin (`import_source`), status, pricing, flight dates, and targeting. A deal can move from `draft` or `imported` to `active` once it is activated in your DSP.

---

## Adding Deals

### From a CSV File

Use `import_deals_csv` to load deals from a CSV. The CSV must have a `display_name` and `seller_url` column at minimum; all other columns are optional.

Minimum required columns:

| Column | Description |
|--------|-------------|
| `display_name` | Human-readable deal name |
| `seller_url` | Seller API endpoint or domain |

Optional columns include: `deal_type`, `status`, `seller_deal_id`, `seller_org`, `price`, `fixed_price_cpm`, `bid_floor_cpm`, `media_type`, `impressions`, `flight_start`, `flight_end`, `currency`, `advertiser_id`, `tags`.

Valid values:

- `deal_type`: `PG`, `PD`, `PA`, `OPEN_AUCTION`, `UPFRONT`, `SCATTER`
- `media_type`: `DIGITAL`, `CTV`, `LINEAR_TV`, `AUDIO`, `DOOH`
- `status`: `draft`, `active`, `paused` (defaults to `draft`)

The tool returns a summary with `total_rows`, `successful`, `failed`, `skipped`, and `deal_ids`.

### Manual Entry

Use `create_deal_manual` to add a single deal from structured data. Only `display_name` and `seller_url` are required; all other fields are optional.

Via MCP (Claude Desktop or API client):

```
create_deal_manual(
  display_name="ESPN Sports PMP",
  seller_url="https://espn-seller.example.com",
  deal_type="PD",
  seller_deal_id="ESPN-PMP-2026-001",
  price=22.50,
  media_type="DIGITAL",
  flight_start="2026-07-01",
  flight_end="2026-09-30",
  advertiser_id="coca-cola",
  tags=["sports", "premium"]
)
```

The tool validates your input and returns a confirmation with the new deal ID. It does not activate the deal in any DSP -- that step is separate.

### SSP Sync

Use `import_deals_ssp` to pull deals from a connected SSP. See [SSP Connector Setup](ssp-connectors.md) for credential setup first.

```
import_deals_ssp(ssp_name="pubmatic")
```

Valid values for `ssp_name`: `pubmatic`, `magnite`, `index_exchange`.

The import fetches all deals targeted to your buyer seat, normalizes them to the deal library schema, deduplicates by seller deal ID, and saves them with `import_source` set to the SSP name. It returns the same summary structure as a CSV import.

---

## Searching and Inspecting Deals

### List with Filters

`list_deals` returns a paginated list with optional filters:

```
list_deals(status="active", media_type="CTV", limit=25)
```

Available filters:

| Filter | Values |
|--------|--------|
| `status` | `draft`, `active`, `paused`, `imported` |
| `deal_type` | `PG`, `PD`, `PA`, `OPEN_AUCTION`, `UPFRONT`, `SCATTER` |
| `media_type` | `DIGITAL`, `CTV`, `LINEAR_TV`, `AUDIO`, `DOOH` |
| `seller_domain` | Any domain string, e.g. `espn.com` |
| `limit` | Max results (default 50) |

### Free-Text Search

`search_deals` does a case-insensitive match across `display_name`, `description`, `seller_org`, and `seller_domain`:

```
search_deals(query="Roku")
```

Results include which field matched each deal.

### Inspect a Single Deal

`inspect_deal` returns the full deal record including pricing, targeting, flight dates, portfolio metadata (import source, tags, advertiser), cross-platform activations, and any cached performance data:

```
inspect_deal(deal_id="deal-abc123")
```

### Portfolio Summary

`get_portfolio_summary` aggregates the entire portfolio into counts by status, media type, and deal type, plus top sellers and deals expiring within 30 days:

```
get_portfolio_summary()
```

Optionally pass `top_sellers_count` (default 5) and `expiring_within_days` (default 30) to customize the summary.

---

## Using Templates

Deal templates let you define a standard set of terms -- deal type, max CPM, preferred inventory types, targeting defaults -- that you can reuse across campaigns.

### Create a Template

```
manage_deal_template(
  action="create",
  params_json='{
    "name": "Sports PG Standard",
    "deal_type_pref": "PG",
    "max_cpm": 40.0,
    "inventory_types": ["CTV", "DIGITAL"],
    "preferred_publishers": ["espn.com", "nfl.com"],
    "default_flight_days": 90,
    "advertiser_id": "coca-cola"
  }'
)
```

A template can be agency-wide (no `advertiser_id`) or scoped to a specific advertiser.

### List and Read Templates

```
manage_deal_template(action="list", params_json='{}')
manage_deal_template(action="read", params_json='{"template_id": "tmpl-001"}')
```

### Update or Delete

```
manage_deal_template(
  action="update",
  params_json='{"template_id": "tmpl-001", "max_cpm": 45.0}'
)

manage_deal_template(
  action="delete",
  params_json='{"template_id": "tmpl-001"}'
)
```

Templates are not deals -- they don't appear in `list_deals`. Use them as starting points when creating deals manually or when the buyer agent is building a campaign plan.

---

## Deal Lifecycle

A deal in the library moves through these statuses:

| Status | Meaning |
|--------|---------|
| `draft` | Manually entered or CSV-imported, not yet reviewed |
| `imported` | Pulled from an SSP, not yet activated |
| `active` | Deal is live and should be trafficked |
| `paused` | Temporarily inactive (SSP-inactive deals land here) |

To update a deal's status, use `create_deal_manual` with the `status` field, or update it via the REST API at `PATCH /api/deals/{deal_id}`.

---

## Using via the REST API

All deal library operations are also available over HTTP if you're integrating programmatically rather than through an MCP client.

| Operation | Endpoint |
|-----------|----------|
| List deals | `GET /api/deals` |
| Inspect deal | `GET /api/deals/{deal_id}` |
| Create manual | `POST /api/deals` |
| Import CSV | `POST /api/deals/import/csv` |
| Import SSP | `POST /api/deals/import/ssp/{ssp_name}` |
| Portfolio summary | `GET /api/deals/summary` |
| List templates | `GET /api/deal-templates` |
| Create template | `POST /api/deal-templates` |

See [Deals API](../api/deals.md) for the full request/response schemas.

---

## Related

- [SSP Connector Setup](ssp-connectors.md) -- Credential setup for PubMatic, Magnite, Index Exchange
- [Deals API](../api/deals.md) -- REST API reference
- [Deal Store Architecture](../architecture/deal-store.md) -- How the deal library stores data
- [Deal Booking](deal-booking.md) -- Booking new deals with sellers
