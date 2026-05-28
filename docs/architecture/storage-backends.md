# Storage Backends

The buyer agent persists state through a pluggable `StorageBackend` abstraction. A factory selects one of three concrete backends at startup based on configuration: **SQLite** (default), **Redis**, or **Hybrid** (PostgreSQL + Redis). All backends implement the same key/value interface plus higher-level domain helpers (deals, campaigns, orders, sessions, conversions, optimization decisions, experiments, supply path scores, quotes, negotiations, model artifacts, pacing snapshots).

This abstraction sits alongside the legacy [`DealStore`](deal-store.md), which uses synchronous `sqlite3` directly for CrewAI thread safety. Over time, callers are expected to migrate from direct DealStore access to the pluggable backend; for now, both coexist.

## Why pluggable?

A single SQLite file works fine for local development and demos, but breaks down for production:

- **Single-writer constraint** — SQLite serializes writes, so horizontal scaling means one ECS task and tight contention under load.
- **No native TTL** — Sessions and caches need expiry; SQLite emulates this with sweep queries.
- **No pub/sub or distributed locks** — Multi-instance deployments need them; Redis provides them natively.

The pluggable factory lets a single agent run on SQLite for development, swap to Redis for ephemeral-heavy workloads, or use a Hybrid mode that puts durable business data on PostgreSQL and short-lived data on Redis — all without code changes.

## Backend selection

Backend choice is driven by `settings.storage_type` (env var `STORAGE_TYPE`), with sensible auto-detection.

| `STORAGE_TYPE` | Required env vars | When to use |
|---|---|---|
| `sqlite` *(default)* | `DATABASE_URL` (defaults to `sqlite:///./ad_buyer.db`) | Local dev, demos, single-task deployments |
| `redis` | `REDIS_URL` | Ephemeral-heavy workloads where durability isn't required |
| `hybrid` | `DATABASE_URL=postgresql+asyncpg://…` **and** `REDIS_URL` | Production multi-instance with durable + cached data |

If `STORAGE_TYPE` is unset and `REDIS_URL` is provided, the factory auto-selects Redis; otherwise it falls back to SQLite. See `ad_buyer/storage/factory.py` for the selection logic.

```python
from ad_buyer.storage.factory import get_storage

# Reads STORAGE_TYPE / DATABASE_URL / REDIS_URL from settings,
# constructs the backend, and connects.
storage = await get_storage()

await storage.set_deal("deal-123", {"status": "active", "spend": 1500})
deal = await storage.get_deal("deal-123")
```

## The three backends

### SQLite (`SQLiteBackend`)

A single key/value table with optional TTL, backed by `aiosqlite`.

- **Schema**: `kv_store(key TEXT PRIMARY KEY, value TEXT, expires_at REAL)` plus an index on `expires_at`.
- **TTL**: Honored on read via `_cleanup_expired()` and per-call expiry checks.
- **Concurrency**: Single writer. Use `DesiredCount: 1` on ECS — multiple tasks will corrupt the file.
- **Best for**: Local development, CI, demos.

### Redis (`RedisBackend`)

Async Redis client (`redis.asyncio`) with namespaced keys (`ad_buyer:` prefix by default).

- **TTL**: Native `EX` / `PEXPIRE` — no sweep needed.
- **Concurrency**: Multi-writer, distributed.
- **Durability**: Configurable via Redis persistence (RDB / AOF). Treat Redis as a cache by default unless you've explicitly hardened it.
- **Best for**: Workloads dominated by sessions, caches, and rate limits.

### Hybrid (`HybridBackend`)

Routes operations to PostgreSQL or Redis based on key prefix.

| Goes to **Redis** (ephemeral) | Goes to **PostgreSQL** (durable) |
|---|---|
| `session:`, `session_index:` | `deal:`, `campaign:`, `order:` |
| `cache:`, `lock:` | `conversion:`, `opt_decision:` |
| `pubsub:`, `rate_limit:` | `experiment:`, `supply_path:` |
| | `quote:`, `negotiation:` |
| | `model:`, `pacing:` |

Routing rules live in `ad_buyer/storage/hybrid_backend.py` (`_REDIS_PREFIXES`). The PostgreSQL backend (`PostgresBackend`) uses a JSONB key/value table via `asyncpg` connection pooling (configurable through `POSTGRES_POOL_MIN` / `POSTGRES_POOL_MAX`, defaults `2` / `10`).

`hybrid` is the recommended production mode: business data survives a Redis flush, while sessions and caches benefit from in-memory speed and native TTL.

## Optional dependencies

Storage drivers are optional extras to keep the base install lean:

```bash
# SQLite only (default — already included)
pip install -e .

# Add Redis
pip install -e ".[redis]"

# Add PostgreSQL
pip install -e ".[postgres]"

# Production (Redis + PostgreSQL)
pip install -e ".[production]"
```

`aiosqlite` ships in the base dependencies. `redis>=5.0.0` and `asyncpg>=0.29.0` are gated behind extras and imported lazily by the factory, so they aren't loaded unless the corresponding backend is selected.

## Configuration reference

| Variable | Default | Description |
|---|---|---|
| `STORAGE_TYPE` | `sqlite` | Backend selector — `sqlite`, `redis`, or `hybrid`. |
| `DATABASE_URL` | `sqlite:///./ad_buyer.db` | Connection string for SQLite or PostgreSQL (use `postgresql+asyncpg://user:pass@host/db` for hybrid). |
| `REDIS_URL` | `None` | Redis connection URL — required for `redis` and `hybrid` modes. |
| `POSTGRES_POOL_MIN` | `2` | Minimum asyncpg connection pool size (hybrid only). |
| `POSTGRES_POOL_MAX` | `10` | Maximum asyncpg connection pool size (hybrid only). |

## Migrating from SQLite to Hybrid

There is no automatic migration — the SQLite key/value table and the PostgreSQL key/value table share a schema, but data is not copied for you. For a clean cutover:

1. Drain in-flight work and stop the agent.
2. Set `STORAGE_TYPE=hybrid`, `DATABASE_URL=postgresql+asyncpg://…`, `REDIS_URL=redis://…`.
3. Restart. The new backend will lazily create the `kv_store` table and indexes on first connect.
4. Re-import durable state (deals, campaigns) from your source of truth if needed.

For zero-downtime migration, run the agent against Hybrid in parallel with SQLite, dual-write at the application layer, then cut reads over once Postgres is hot. This is non-trivial and not currently scripted.

## Implementation files

- `ad_buyer/storage/base.py` — `StorageBackend` ABC and shared domain helpers
- `ad_buyer/storage/factory.py` — `get_storage_backend()` selection logic
- `ad_buyer/storage/sqlite_backend.py` — SQLite implementation (`aiosqlite`)
- `ad_buyer/storage/redis_backend.py` — Redis implementation (`redis.asyncio`)
- `ad_buyer/storage/postgres_backend.py` — PostgreSQL implementation (`asyncpg`)
- `ad_buyer/storage/hybrid_backend.py` — Prefix-based routing between Postgres and Redis
