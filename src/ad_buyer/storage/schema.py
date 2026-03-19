# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Database schema definitions and migration runner for deal state persistence.

Defines relational tables for the deal lifecycle:
- deals: Central deal tracking (extended in v2 with deal library fields)
- negotiation_rounds: Per-round audit trail
- booking_records: Booked line items
- jobs: API-initiated booking jobs (replaces in-memory dict)
- status_transitions: Append-only audit log
- events: Event bus event persistence
- portfolio_metadata: Extrinsic deal metadata (v2, D-4 hybrid approach)
- deal_activations: Cross-platform deal activations (v2)
- performance_cache: Cached deal performance metrics (v2)
- campaigns: Campaign automation records (v4)
- pacing_snapshots: Periodic pacing data points per campaign (v4)
- creative_assets: Creative files and metadata per campaign (v4)
- ad_server_campaigns: Ad server integration records (v4)
- approval_requests: Human approval gate requests (v4, buyer-2qs)

Uses a schema_version table for forward-compatible migrations.
"""

import logging
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)

# Current schema version
# Version registry:
#   v1: Initial schema (deals, negotiation_rounds, booking_records, jobs, etc.)
#   v2: Deal library hybrid approach (portfolio_metadata, deal_activations, etc.)
#   v3: Reserved for deal_templates (ar-fcq)
#   v4: Campaign automation tables (buyer-80o)
SCHEMA_VERSION = 4

# -- Schema version tracking ------------------------------------------------

SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

# -- Core tables ------------------------------------------------------------

DEALS_TABLE = """
CREATE TABLE IF NOT EXISTS deals (
    id              TEXT PRIMARY KEY,
    seller_url      TEXT NOT NULL,
    seller_deal_id  TEXT,
    product_id      TEXT NOT NULL,
    product_name    TEXT NOT NULL DEFAULT '',
    deal_type       TEXT NOT NULL DEFAULT 'PD',
    status          TEXT NOT NULL DEFAULT 'draft',
    price           REAL,
    original_price  REAL,
    impressions     INTEGER,
    flight_start    TEXT,
    flight_end      TEXT,
    buyer_context   TEXT,
    metadata        TEXT DEFAULT '{}',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    -- v2: Counterparty fields (D-4 hybrid intrinsic)
    display_name            TEXT,
    description             TEXT,
    buyer_org               TEXT,
    buyer_id                TEXT,
    seller_org              TEXT,
    seller_id               TEXT,
    seller_domain           TEXT,
    seller_type             TEXT,

    -- v2: Pricing detail fields
    price_model             TEXT,
    bid_floor_cpm           REAL,
    fixed_price_cpm         REAL,
    cpp                     REAL,
    guaranteed_grps         REAL,
    currency                TEXT DEFAULT 'USD',
    fee_transparency        REAL,

    -- v2: Inventory targeting fields
    media_type              TEXT,
    formats                 TEXT,
    content_categories      TEXT,
    publisher_domains       TEXT,
    geo_targets             TEXT,
    dayparts                TEXT,
    programs                TEXT,
    networks                TEXT,
    audience_segments       TEXT,
    estimated_volume        INTEGER,

    -- v2: Lifecycle extensions
    deprecated_at           TEXT,
    deprecated_reason       TEXT,
    parent_deal_id          TEXT,

    -- v2: Supply chain fields
    schain_complete         INTEGER,
    schain_nodes            TEXT,
    sellers_json_url        TEXT,
    is_direct               INTEGER,
    hop_count               INTEGER,
    inventory_fingerprint   TEXT,

    -- v2: Linear TV fields (per Q-6 / L-1)
    makegood_provisions     TEXT,
    cancellation_window     TEXT,
    audience_guarantee      TEXT,
    preemption_rights       TEXT,
    agency_of_record_status TEXT
);
"""

DEALS_INDEXES = [
    # v1 indexes
    "CREATE INDEX IF NOT EXISTS idx_deals_status ON deals(status);",
    "CREATE INDEX IF NOT EXISTS idx_deals_seller_url ON deals(seller_url);",
    "CREATE INDEX IF NOT EXISTS idx_deals_seller_deal_id ON deals(seller_deal_id);",
    "CREATE INDEX IF NOT EXISTS idx_deals_created_at ON deals(created_at);",
    "CREATE INDEX IF NOT EXISTS idx_deals_status_created ON deals(status, created_at);",
    # v2 indexes
    "CREATE INDEX IF NOT EXISTS idx_deals_media_type ON deals(media_type);",
    "CREATE INDEX IF NOT EXISTS idx_deals_deal_type ON deals(deal_type);",
    "CREATE INDEX IF NOT EXISTS idx_deals_seller_domain ON deals(seller_domain);",
    "CREATE INDEX IF NOT EXISTS idx_deals_inventory_fingerprint ON deals(inventory_fingerprint);",
]

NEGOTIATION_ROUNDS_TABLE = """
CREATE TABLE IF NOT EXISTS negotiation_rounds (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id         TEXT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    proposal_id     TEXT NOT NULL,
    round_number    INTEGER NOT NULL,
    buyer_price     REAL NOT NULL,
    seller_price    REAL NOT NULL,
    action          TEXT NOT NULL,
    rationale       TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(deal_id, round_number)
);
"""

NEGOTIATION_ROUNDS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_neg_rounds_deal_id ON negotiation_rounds(deal_id);",
    "CREATE INDEX IF NOT EXISTS idx_neg_rounds_proposal_id ON negotiation_rounds(proposal_id);",
]

BOOKING_RECORDS_TABLE = """
CREATE TABLE IF NOT EXISTS booking_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id         TEXT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    order_id        TEXT,
    line_id         TEXT,
    channel         TEXT NOT NULL DEFAULT '',
    impressions     INTEGER NOT NULL DEFAULT 0,
    cost            REAL NOT NULL DEFAULT 0.0,
    booking_status  TEXT NOT NULL DEFAULT 'pending',
    booked_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    metadata        TEXT DEFAULT '{}',
    UNIQUE(deal_id, line_id)
);
"""

BOOKING_RECORDS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_booking_deal_id ON booking_records(deal_id);",
    "CREATE INDEX IF NOT EXISTS idx_booking_status ON booking_records(booking_status);",
    "CREATE INDEX IF NOT EXISTS idx_booking_order_id ON booking_records(order_id);",
]

JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'pending',
    progress        REAL NOT NULL DEFAULT 0.0,
    brief           TEXT NOT NULL DEFAULT '{}',
    auto_approve    INTEGER NOT NULL DEFAULT 0,
    budget_allocs   TEXT DEFAULT '{}',
    recommendations TEXT DEFAULT '[]',
    booked_lines    TEXT DEFAULT '[]',
    errors          TEXT DEFAULT '[]',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

JOBS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);",
    "CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at);",
]

EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS events (
    id              TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    flow_id         TEXT NOT NULL DEFAULT '',
    flow_type       TEXT NOT NULL DEFAULT '',
    deal_id         TEXT NOT NULL DEFAULT '',
    session_id      TEXT NOT NULL DEFAULT '',
    payload         TEXT DEFAULT '{}',
    metadata        TEXT DEFAULT '{}',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

EVENTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);",
    "CREATE INDEX IF NOT EXISTS idx_events_flow_id ON events(flow_id);",
    "CREATE INDEX IF NOT EXISTS idx_events_deal_id ON events(deal_id);",
    "CREATE INDEX IF NOT EXISTS idx_events_session_id ON events(session_id);",
    "CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);",
]

STATUS_TRANSITIONS_TABLE = """
CREATE TABLE IF NOT EXISTS status_transitions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type     TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    from_status     TEXT,
    to_status       TEXT NOT NULL,
    triggered_by    TEXT DEFAULT 'system',
    notes           TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

STATUS_TRANSITIONS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_transitions_entity ON status_transitions(entity_type, entity_id);",
    "CREATE INDEX IF NOT EXISTS idx_transitions_created ON status_transitions(created_at);",
]

# -- v2: Extrinsic tables (D-4 hybrid approach) ----------------------------

PORTFOLIO_METADATA_TABLE = """
CREATE TABLE IF NOT EXISTS portfolio_metadata (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id         TEXT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    import_source   TEXT,
    import_date     TEXT,
    tags            TEXT,
    advertiser_id   TEXT,
    agency_id       TEXT
);
"""

PORTFOLIO_METADATA_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_portfolio_metadata_deal_id ON portfolio_metadata(deal_id);",
    "CREATE INDEX IF NOT EXISTS idx_portfolio_metadata_advertiser_id ON portfolio_metadata(advertiser_id);",
]

DEAL_ACTIVATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS deal_activations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id             TEXT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    platform            TEXT,
    platform_deal_id    TEXT,
    activation_status   TEXT,
    last_sync_at        TEXT
);
"""

DEAL_ACTIVATIONS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_deal_activations_deal_id ON deal_activations(deal_id);",
    "CREATE INDEX IF NOT EXISTS idx_deal_activations_platform_deal ON deal_activations(platform, deal_id);",
]

PERFORMANCE_CACHE_TABLE = """
CREATE TABLE IF NOT EXISTS performance_cache (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id             TEXT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    impressions_delivered INTEGER,
    spend_to_date       REAL,
    fill_rate           REAL,
    win_rate            REAL,
    avg_effective_cpm   REAL,
    last_delivery_at    TEXT,
    performance_trend   TEXT,
    cached_at           TEXT
);
"""

PERFORMANCE_CACHE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_performance_cache_deal_id ON performance_cache(deal_id);",
]

# -- v4: Campaign Automation tables (buyer-80o) ----------------------------

CAMPAIGNS_TABLE = """
CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id     TEXT PRIMARY KEY,
    advertiser_id   TEXT NOT NULL,
    campaign_name   TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'DRAFT',
    total_budget    REAL NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'USD',
    flight_start    TEXT NOT NULL,
    flight_end      TEXT NOT NULL,
    channels        TEXT DEFAULT '[]',
    target_audience TEXT DEFAULT '[]',
    target_geo      TEXT DEFAULT '[]',
    kpis            TEXT DEFAULT '[]',
    brand_safety    TEXT,
    approval_config TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

CAMPAIGNS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);",
    "CREATE INDEX IF NOT EXISTS idx_campaigns_advertiser_id ON campaigns(advertiser_id);",
    "CREATE INDEX IF NOT EXISTS idx_campaigns_flight_start ON campaigns(flight_start);",
]

PACING_SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS pacing_snapshots (
    snapshot_id         TEXT PRIMARY KEY,
    campaign_id         TEXT NOT NULL,
    timestamp           TEXT NOT NULL,
    total_budget        REAL NOT NULL,
    total_spend         REAL NOT NULL,
    pacing_pct          REAL NOT NULL,
    expected_spend      REAL NOT NULL,
    deviation_pct       REAL NOT NULL,
    channel_snapshots   TEXT DEFAULT '[]',
    deal_snapshots      TEXT DEFAULT '[]',
    recommendations     TEXT DEFAULT '[]',
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

PACING_SNAPSHOTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_pacing_snapshots_campaign_id ON pacing_snapshots(campaign_id);",
    "CREATE INDEX IF NOT EXISTS idx_pacing_snapshots_timestamp ON pacing_snapshots(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_pacing_snapshots_campaign_timestamp ON pacing_snapshots(campaign_id, timestamp);",
]

CREATIVE_ASSETS_TABLE = """
CREATE TABLE IF NOT EXISTS creative_assets (
    asset_id            TEXT PRIMARY KEY,
    campaign_id         TEXT NOT NULL,
    asset_name          TEXT NOT NULL,
    asset_type          TEXT NOT NULL,
    format_spec         TEXT DEFAULT '{}',
    source_url          TEXT,
    validation_status   TEXT,
    validation_errors   TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

CREATIVE_ASSETS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_creative_assets_campaign_id ON creative_assets(campaign_id);",
    "CREATE INDEX IF NOT EXISTS idx_creative_assets_asset_type ON creative_assets(asset_type);",
    "CREATE INDEX IF NOT EXISTS idx_creative_assets_validation_status ON creative_assets(validation_status);",
]

AD_SERVER_CAMPAIGNS_TABLE = """
CREATE TABLE IF NOT EXISTS ad_server_campaigns (
    binding_id              TEXT PRIMARY KEY,
    campaign_id             TEXT NOT NULL,
    ad_server               TEXT NOT NULL,
    external_campaign_id    TEXT,
    status                  TEXT NOT NULL DEFAULT 'PENDING',
    creative_assignments    TEXT DEFAULT '{}',
    last_sync_at            TEXT,
    created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

AD_SERVER_CAMPAIGNS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ad_server_campaigns_campaign_id ON ad_server_campaigns(campaign_id);",
    "CREATE INDEX IF NOT EXISTS idx_ad_server_campaigns_ad_server ON ad_server_campaigns(ad_server);",
]

# -- v4 continued: Approval requests (buyer-2qs) --------------------------

APPROVAL_REQUESTS_TABLE = """
CREATE TABLE IF NOT EXISTS approval_requests (
    approval_request_id TEXT PRIMARY KEY,
    campaign_id         TEXT NOT NULL,
    stage               TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    requested_at        TEXT NOT NULL,
    decided_at          TEXT,
    reviewer            TEXT,
    notes               TEXT,
    context             TEXT DEFAULT '{}',
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
);
"""

APPROVAL_REQUESTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_approval_requests_campaign_id ON approval_requests(campaign_id);",
    "CREATE INDEX IF NOT EXISTS idx_approval_requests_status ON approval_requests(status);",
    "CREATE INDEX IF NOT EXISTS idx_approval_requests_stage ON approval_requests(stage);",
]


def create_tables(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes if they don't already exist.

    Args:
        conn: Active SQLite connection.
    """
    cursor = conn.cursor()

    # Schema version table first
    cursor.execute(SCHEMA_VERSION_TABLE)

    # Core tables
    for ddl in [
        DEALS_TABLE,
        NEGOTIATION_ROUNDS_TABLE,
        BOOKING_RECORDS_TABLE,
        JOBS_TABLE,
        EVENTS_TABLE,
        STATUS_TRANSITIONS_TABLE,
        # v2 extrinsic tables
        PORTFOLIO_METADATA_TABLE,
        DEAL_ACTIVATIONS_TABLE,
        PERFORMANCE_CACHE_TABLE,
        # v4 campaign automation tables
        CAMPAIGNS_TABLE,
        PACING_SNAPSHOTS_TABLE,
        CREATIVE_ASSETS_TABLE,
        AD_SERVER_CAMPAIGNS_TABLE,
        APPROVAL_REQUESTS_TABLE,
    ]:
        cursor.execute(ddl)

    # Indexes
    for index_list in [
        DEALS_INDEXES,
        NEGOTIATION_ROUNDS_INDEXES,
        BOOKING_RECORDS_INDEXES,
        JOBS_INDEXES,
        EVENTS_INDEXES,
        STATUS_TRANSITIONS_INDEXES,
        # v2 extrinsic indexes
        PORTFOLIO_METADATA_INDEXES,
        DEAL_ACTIVATIONS_INDEXES,
        PERFORMANCE_CACHE_INDEXES,
        # v4 campaign automation indexes
        CAMPAIGNS_INDEXES,
        PACING_SNAPSHOTS_INDEXES,
        CREATIVE_ASSETS_INDEXES,
        AD_SERVER_CAMPAIGNS_INDEXES,
        APPROVAL_REQUESTS_INDEXES,
    ]:
        for idx in index_list:
            cursor.execute(idx)

    conn.commit()


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Get the current schema version from the database.

    Args:
        conn: Active SQLite connection.

    Returns:
        Current schema version, or 0 if no version recorded.
    """
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT MAX(version) FROM schema_version")
        row = cursor.fetchone()
        return row[0] if row[0] is not None else 0
    except sqlite3.OperationalError:
        # Table doesn't exist yet
        return 0


def set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    """Record a schema version as applied.

    Args:
        conn: Active SQLite connection.
        version: Version number to record.
    """
    conn.execute(
        "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
        (version,),
    )
    conn.commit()


def migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Migrate schema from v1 to v2 (deal library hybrid approach, D-4).

    Adds intrinsic deal library fields to the existing ``deals`` table via
    ALTER TABLE ADD COLUMN, and creates three new extrinsic tables:
    ``portfolio_metadata``, ``deal_activations``, ``performance_cache``.

    This migration is idempotent: re-running it on a v2 database is safe
    because both ALTER TABLE ADD COLUMN (with IF NOT EXISTS-style error
    handling) and CREATE TABLE IF NOT EXISTS are no-ops for existing objects.

    Args:
        conn: Active SQLite connection.
    """
    cursor = conn.cursor()

    # -- Intrinsic columns on deals table ----------------------------------
    # SQLite does not support ALTER TABLE ADD COLUMN IF NOT EXISTS, so we
    # check existing columns first and only add missing ones.
    cursor.execute("PRAGMA table_info(deals)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    # (column_name, column_def) pairs for all v2 intrinsic columns
    v2_columns = [
        # Counterparty fields
        ("display_name", "TEXT"),
        ("description", "TEXT"),
        ("buyer_org", "TEXT"),
        ("buyer_id", "TEXT"),
        ("seller_org", "TEXT"),
        ("seller_id", "TEXT"),
        ("seller_domain", "TEXT"),
        ("seller_type", "TEXT"),
        # Pricing detail fields
        ("price_model", "TEXT"),
        ("bid_floor_cpm", "REAL"),
        ("fixed_price_cpm", "REAL"),
        ("cpp", "REAL"),
        ("guaranteed_grps", "REAL"),
        ("currency", "TEXT DEFAULT 'USD'"),
        ("fee_transparency", "REAL"),
        # Inventory targeting fields
        ("media_type", "TEXT"),
        ("formats", "TEXT"),
        ("content_categories", "TEXT"),
        ("publisher_domains", "TEXT"),
        ("geo_targets", "TEXT"),
        ("dayparts", "TEXT"),
        ("programs", "TEXT"),
        ("networks", "TEXT"),
        ("audience_segments", "TEXT"),
        ("estimated_volume", "INTEGER"),
        # Lifecycle extensions
        ("deprecated_at", "TEXT"),
        ("deprecated_reason", "TEXT"),
        ("parent_deal_id", "TEXT"),
        # Supply chain fields
        ("schain_complete", "INTEGER"),
        ("schain_nodes", "TEXT"),
        ("sellers_json_url", "TEXT"),
        ("is_direct", "INTEGER"),
        ("hop_count", "INTEGER"),
        ("inventory_fingerprint", "TEXT"),
        # Linear TV fields (per Q-6 / L-1)
        ("makegood_provisions", "TEXT"),
        ("cancellation_window", "TEXT"),
        ("audience_guarantee", "TEXT"),
        ("preemption_rights", "TEXT"),
        ("agency_of_record_status", "TEXT"),
    ]

    for col_name, col_def in v2_columns:
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE deals ADD COLUMN {col_name} {col_def}")

    # -- v2 indexes on deals table -----------------------------------------
    v2_deal_indexes = [
        "CREATE INDEX IF NOT EXISTS idx_deals_media_type ON deals(media_type);",
        "CREATE INDEX IF NOT EXISTS idx_deals_deal_type ON deals(deal_type);",
        "CREATE INDEX IF NOT EXISTS idx_deals_seller_domain ON deals(seller_domain);",
        "CREATE INDEX IF NOT EXISTS idx_deals_inventory_fingerprint ON deals(inventory_fingerprint);",
    ]
    for idx in v2_deal_indexes:
        cursor.execute(idx)

    # -- Extrinsic tables --------------------------------------------------
    cursor.execute(PORTFOLIO_METADATA_TABLE)
    cursor.execute(DEAL_ACTIVATIONS_TABLE)
    cursor.execute(PERFORMANCE_CACHE_TABLE)

    # -- Extrinsic indexes -------------------------------------------------
    for index_list in [
        PORTFOLIO_METADATA_INDEXES,
        DEAL_ACTIVATIONS_INDEXES,
        PERFORMANCE_CACHE_INDEXES,
    ]:
        for idx in index_list:
            cursor.execute(idx)

    conn.commit()
    logger.info("Migration v1 -> v2 complete: deal library hybrid schema applied")


def migrate_v2_to_v4(conn: sqlite3.Connection) -> None:
    """Migrate schema from v2 to v4 (campaign automation tables).

    Creates four new tables for campaign management:
    ``campaigns``, ``pacing_snapshots``, ``creative_assets``,
    ``ad_server_campaigns``.

    This migration is idempotent: all DDL uses CREATE TABLE IF NOT EXISTS
    and CREATE INDEX IF NOT EXISTS, so re-running on a v4 database is safe.

    Note: v3 is reserved for deal_templates (ar-fcq) but may not yet exist
    in the database.  This migration is independent of v3 and works whether
    or not v3 tables are present.

    Args:
        conn: Active SQLite connection.
    """
    cursor = conn.cursor()

    # -- Create campaign automation tables ---------------------------------
    cursor.execute(CAMPAIGNS_TABLE)
    cursor.execute(PACING_SNAPSHOTS_TABLE)
    cursor.execute(CREATIVE_ASSETS_TABLE)
    cursor.execute(AD_SERVER_CAMPAIGNS_TABLE)

    # -- Create indexes ----------------------------------------------------
    for index_list in [
        CAMPAIGNS_INDEXES,
        PACING_SNAPSHOTS_INDEXES,
        CREATIVE_ASSETS_INDEXES,
        AD_SERVER_CAMPAIGNS_INDEXES,
    ]:
        for idx in index_list:
            cursor.execute(idx)

    conn.commit()
    logger.info(
        "Migration v2 -> v4 complete: campaign automation tables created"
    )


def run_migrations(conn: sqlite3.Connection) -> None:
    """Run pending schema migrations.

    Checks current version and applies any migrations needed to reach
    SCHEMA_VERSION. Each migration is a function that takes a connection.

    Args:
        conn: Active SQLite connection.
    """
    current = get_schema_version(conn)

    if current >= SCHEMA_VERSION:
        return

    # Migration registry: version -> migration function
    # Note: v3 is reserved for deal_templates (ar-fcq); when implemented,
    # add migrate_v2_to_v3 here.  v4 (campaign automation) is independent.
    migrations: dict[int, callable] = {
        2: migrate_v1_to_v2,
        4: migrate_v2_to_v4,
    }

    for version in range(current + 1, SCHEMA_VERSION + 1):
        migration_fn = migrations.get(version)
        if migration_fn is not None:
            logger.info("Running migration to schema version %d", version)
            migration_fn(conn)

    # Record current version after all migrations
    set_schema_version(conn, SCHEMA_VERSION)
    logger.info("Schema at version %d", SCHEMA_VERSION)


def initialize_schema(conn: sqlite3.Connection) -> None:
    """Full schema initialization: create tables, run migrations, set version.

    This is the single entry point for schema setup, called by DealStore.connect().

    Args:
        conn: Active SQLite connection.
    """
    create_tables(conn)
    run_migrations(conn)
