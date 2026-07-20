# Storage Layer

The buyer agent persists all state in **SQLite**, accessed through a set of concrete,
domain-specific store classes in `ad_buyer/storage/`. There is no pluggable backend
abstraction — one SQLite database (configured by `DATABASE_URL`, default
`sqlite:///./ad_buyer.db`) holds every table, and each store class owns one slice of it.

The schema is defined centrally in `ad_buyer/storage/schema.py` (17 domain tables plus a
`schema_version` table) and created idempotently on first connect.

## The stores

The [`DealStore`](deal-store.md) is the largest store — a synchronous `sqlite3` layer for
deal lifecycle, negotiation history, and booking records (synchronous by design, for
CrewAI thread safety). Alongside it sit focused domain stores:

| Store (`ad_buyer/storage/`) | Persists |
|---|---|
| `deal_store.py` — `DealStore` | Deals, negotiation rounds, booked lines ([full reference](deal-store.md)) |
| `order_store.py` — `OrderStore` | Buyer-side order records |
| `negotiation_store.py` — `NegotiationStore` | Negotiation state |
| `booking_record_store.py` — `BookingRecordStore` | Booking records |
| `campaign_store.py` — `CampaignStore` | Campaign automation records |
| `pacing_store.py` — `PacingStore` | Budget pacing snapshots |
| `creative_asset_store.py` — `CreativeAssetStore` | Creative assets and validation status |
| `adserver_store.py` — `AdServerStore` | Ad server campaigns and deal-to-line bindings |
| `deal_activation_store.py` — `DealActivationStore` | Cross-platform deal activations |
| `deal_event_store.py` — `DealEventStore` | Persisted deal events |
| `deal_template_store.py` — `DealTemplateStore` | Deal templates |
| `supply_path_template_store.py` — `SupplyPathTemplateStore` | Supply path templates |
| `performance_cache_store.py` — `PerformanceCacheStore` | Cached deal performance data |
| `portfolio_metadata_store.py` — `PortfolioMetadataStore` | Portfolio metadata (import source, tags, advertiser) |
| `status_transition_store.py` — `StatusTransitionStore` | State machine transition audit trail |
| `job_store.py` — `JobStore` | API booking jobs |
| `audience_audit_log.py` | Audience planning audit log |

`ad_buyer/storage/health.py` provides `probe_database()` / `database_accessible()` health
checks against the configured database.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./ad_buyer.db` | SQLite connection string. All stores share it. |

!!! warning "Single writer"
    SQLite serializes writes. Run exactly **one** agent instance against a given database
    file (e.g. `DesiredCount: 1` on ECS). Running multiple instances against the same
    file — including a shared network file system — risks corruption.

## Related

- [Deal Store](deal-store.md) — full schema and API reference for the primary store
- [Configuration](../guides/configuration.md) — environment variables
