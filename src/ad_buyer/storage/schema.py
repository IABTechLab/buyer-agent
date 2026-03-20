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

Uses a schema_version table for forward-compatible migrations.
"""

import logging
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)

# Current schema version
SCHEMA_VERSION = 3

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

# -- v3: Template tables (DealJockey Section 6.3, 6.4) ---------------------

DEAL_TEMPLATE_TABLE = """
CREATE TABLE IF NOT EXISTS deal_templates (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    deal_type_pref      TEXT,
    inventory_types     TEXT,
    preferred_publishers TEXT,
    excluded_publishers TEXT,
    targeting_defaults  TEXT,
    max_cpm             REAL,
    min_impressions     INTEGER,
    default_flight_days INTEGER,
    supply_path_prefs   TEXT,
    advertiser_id       TEXT,
    agency_id           TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

DEAL_TEMPLATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_deal_templates_name ON deal_templates(name);",
    "CREATE INDEX IF NOT EXISTS idx_deal_templates_advertiser_id ON deal_templates(advertiser_id);",
    "CREATE INDEX IF NOT EXISTS idx_deal_templates_deal_type_pref ON deal_templates(deal_type_pref);",
]

SUPPLY_PATH_TEMPLATE_TABLE = """
CREATE TABLE IF NOT EXISTS supply_path_templates (
    id                      TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    scoring_weights         TEXT,
    max_reseller_hops       INTEGER,
    require_sellers_json    INTEGER DEFAULT 0,
    preferred_ssps          TEXT,
    blocked_ssps            TEXT,
    preferred_curators      TEXT,
    rules                   TEXT,
    created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

SUPPLY_PATH_TEMPLATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_supply_path_templates_name ON supply_path_templates(name);",
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
        # v3 template tables
        DEAL_TEMPLATE_TABLE,
        SUPPLY_PATH_TEMPLATE_TABLE,
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
        # v3 template indexes
        DEAL_TEMPLATE_INDEXES,
        SUPPLY_PATH_TEMPLATE_INDEXES,
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


def migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Migrate schema from v2 to v3 (deal and supply path templates).

    Creates ``deal_templates`` and ``supply_path_templates`` tables for
    DealJockey template CRUD (Strategic Plan Sections 6.3 and 6.4).

    This migration is idempotent: CREATE TABLE IF NOT EXISTS is a no-op
    for existing tables.

    Args:
        conn: Active SQLite connection.
    """
    cursor = conn.cursor()

    # Create template tables
    cursor.execute(DEAL_TEMPLATE_TABLE)
    cursor.execute(SUPPLY_PATH_TEMPLATE_TABLE)

    # Create indexes
    for idx in DEAL_TEMPLATE_INDEXES:
        cursor.execute(idx)
    for idx in SUPPLY_PATH_TEMPLATE_INDEXES:
        cursor.execute(idx)

    conn.commit()
    logger.info("Migration v2 -> v3 complete: template tables created")


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
    migrations: dict[int, callable] = {
        2: migrate_v1_to_v2,
        3: migrate_v2_to_v3,
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
