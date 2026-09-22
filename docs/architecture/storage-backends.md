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

## AgentCore deployment: SQLite in-memory by default, durable Postgres opt-in

When the buyer runs as an AgentCore runtime, `infra/aws/agentcore/deploy.sh`
defaults to `STORAGE_TYPE=sqlite` with an **in-memory** `DATABASE_URL`
(`sqlite:///:memory:`), in PUBLIC network mode. State written by the stores
above therefore does **not** survive a container recycle in that default
deployment. This is the right default for same-account/dev and for the agentic
planning/booking path, where the buyer's own records (deals/orders/negotiations)
are re-derivable from the seller of record and the campaign plan.

### Durable Postgres (opt-in): `--storage postgres`

For enterprise/durable deployments the buyer now has a real backend seam and a
`--storage postgres` path (mirrors the seller's group-6 work):

- **Aurora Serverless v2** (`aurora-postgresql` 16.9, `db.serverless`) deployed
  beside Redis in `infra/aws/cloudformation/storage.yaml`, with
  `ServerlessV2ScalingConfiguration.MinCapacity: 0` (**scale-to-zero**, bounded
  max) so it costs nothing when idle.
- **RDS-managed credentials** — `ManageMasterUserPassword: true` puts the DB
  password in an auto-rotating Secrets Manager secret. No plaintext password is
  ever in CFN, env, or logs; the runtime reads the secret **by ARN** at startup
  (`storage/db_secret.py`) and assembles the connection string in-process.
- **CUSTOMER_VPC mode** — `network-agentcore.yaml` + `main-agentcore.yaml`
  provide the VPC interface endpoints (`bedrock-agentcore`/`bedrock-runtime`,
  `sts`, `secretsmanager`, ECR, Logs) and the **443 self-ingress** on the
  runtime SG required for a private-subnet runtime to reach Aurora and those
  endpoints (same reachability lesson as the seller).
- **The backend seam** — `storage/connection_factory.py` selects on
  `STORAGE_TYPE`; `storage/pg_connection.py` adapts the sqlite3 API to
  psycopg, and `storage/schema_pg.py` derives Postgres DDL from the shared
  schema. The 6 connection-owning stores route through the factory; the injected
  sub-stores follow for free. `STORAGE_TYPE=hybrid` on the deployed runtime
  puts KV state in Aurora with Redis alongside.

The entrypoint uses `setdefault` semantics so a deploy-supplied `hybrid`/Postgres
value wins while the dev default stays SQLite. As with the seller, product data
is independent of the KV backend.

If you need durable buyer state and do not want the Aurora path, you can still
run the buyer on ECS with a persistent `DATABASE_URL` (single writer — see the
warning above).

## Related

- [Deal Store](deal-store.md) — full schema and API reference for the primary store
- [Configuration](../guides/configuration.md) — environment variables
