# Deal Library

The deal library is the buyer's managed collection of deals available for activation. It extends the core [DealStore](deal-store.md) persistence layer with import pipelines, validation, templates, and portfolio analytics.

!!! info "Relationship to DealStore"
    The `DealStore` is the SQLite layer --- tables, schema, and CRUD methods. The deal library is the business layer built on top of it: import pipelines, templates, event tracking, and the MCP tools that surface everything to AI assistants. They share the same database; the library does not add a separate store.

---

## Import Methods

Deals enter the library through three paths.

### CSV Import

The `import_deals_csv` MCP tool accepts a CSV string. Columns are auto-detected from the header row (case-insensitive). Required columns: `seller_deal_id` (or `deal_id`), `display_name` (or `name`), `seller_url`, `deal_type`. All other columns are optional.

The import pipeline:

1. Parse and validate each row via `_parse_row()` / `_resolve_columns()` in `tools/deal_import.py`
2. Deduplicate by `seller_deal_id` within the batch
3. Persist each valid deal via `store.save_deal(**deal_data)`
4. Record portfolio metadata with `import_source="CSV"` and `import_date=<today>`

Failed rows are captured in an `errors` list; they do not block valid rows from being saved.

### Manual Entry

The `create_deal_manual` MCP tool accepts individual deal fields. Input is validated through a `ManualDealEntry` Pydantic model (`tools/deal_library/deal_entry.py`) before any write occurs. The model enforces:

- `display_name` and `seller_url` are required
- `deal_type` is one of `PG`, `PD`, `PA`, `OPEN_AUCTION`, `UPFRONT`, `SCATTER`
- `media_type` is one of `DIGITAL`, `CTV`, `LINEAR_TV`, `AUDIO`, `DOOH`
- `status` is one of `draft`, `active`, `paused`

Portfolio metadata is saved with `import_source="MANUAL"`.

### SSP Connectors

The `sync_ssp_deals` and `import_ssp_deals` tools trigger a connector fetch. Each connector normalizes SSP deal objects into `DealStore.save_deal()` kwargs and returns an `SSPFetchResult`. Portfolio metadata is saved with `import_source=<SSP_TAG>` (e.g., `"PUBMATIC"`, `"MAGNITE"`, `"INDEX_EXCHANGE"`).

See [SSP Connectors](ssp-connectors.md) for the full connector architecture.

---

## Deal Lifecycle in the Library

Deals in the library follow a status progression distinct from the deal negotiation lifecycle. Library-managed deals use these statuses:

| Status | Meaning |
|--------|---------|
| `imported` | Freshly imported from SSP or CSV; not yet reviewed |
| `draft` | Manually created; under review |
| `active` | Deal is available for activation and targeting |
| `paused` | Temporarily inactive; not included in targeting |
| `booked` | Deal has been activated in a campaign |
| `expired` | Flight end date passed or deal no longer available |

Transitions are recorded in the `status_transitions` audit table. The `update_deal_status()` method (in DealStore) validates transitions via the `DealStateMachine` before writing.

---

## Templates

The library provides two template types backed by dedicated DealStore tables.

### Deal Templates

Deal templates (`deal_templates` table) encode an agency's preferred terms for common deal types --- default CPM, price model, flight duration, targeting defaults, and advertiser scoping.

A template is created once (via `create_template` MCP tool) and instantiated into a real deal via `instantiate_from_template`. Overrides can be applied at instantiation time:

```
Template: "Sports PG Default"
  deal_type_pref: PG
  default_price: 22.0
  default_flight_days: 30
  advertiser_id: adv-espn

Instantiation:
  overrides: {"price": 25.0, "seller_url": "https://sports.seller.example.com"}
  → new deal with price=25.0, all other fields from template
```

### Supply Path Templates

Supply path templates (`supply_path_templates` table) codify SPO (supply path optimization) routing preferences. They record scoring weights, preferred SSPs, blocked SSPs, and maximum reseller hop counts. Supply path templates are used by the DSP deal flow to evaluate and rank inventory sources.

---

## Portfolio Metadata

Every deal in the library has an associated `portfolio_metadata` row that records:

| Field | Description |
|-------|-------------|
| `import_source` | Where the deal came from: `CSV`, `MANUAL`, `PUBMATIC`, `MAGNITE`, `INDEX_EXCHANGE` |
| `import_date` | Date the deal entered the library (ISO 8601) |
| `advertiser_id` | Advertiser scoping for multi-advertiser portfolios |
| `agency_id` | Agency identifier |
| `tags` | JSON array of user-defined tags |

The `inspect_deal` MCP tool returns the full deal record including its portfolio metadata.

---

## MCP Tools Surface

The deal library is surfaced entirely through the MCP server's Deal Library tool category. AI assistants interact with the library through these tools:

| Tool | Operation |
|------|-----------|
| `list_deals` | Filter and list deals by status, type, media, or seller |
| `search_deals` | Free-text search across name, description, seller fields |
| `inspect_deal` | Full deal detail including metadata and activations |
| `import_deals_csv` | Bulk CSV import |
| `create_deal_manual` | Single deal manual entry |
| `get_portfolio_summary` | Aggregate counts, value, top sellers, expiring deals |
| `list_templates` | List deal and supply path templates |
| `create_template` | Create a new template |
| `instantiate_from_template` | Create a deal from a template |
| `sync_ssp_deals` | Trigger SSP connector fetch |

---

## Related

- [Deal Store](deal-store.md) --- SQLite schema, CRUD API, and status lifecycle
- [SSP Connectors](ssp-connectors.md) --- Per-SSP import connector architecture
- [MCP Server](mcp-server.md) --- How MCP tools are registered and served
- [Buyer Guide: Deal Library](../guides/deal-library.md) --- Operator guide for using the deal library
