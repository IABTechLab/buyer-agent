# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Database schema definitions and migration runner for deal state persistence.

Defines 5 relational tables for the deal lifecycle:
- deals: Central deal tracking
- negotiation_rounds: Per-round audit trail
- booking_records: Booked line items
- jobs: API-initiated booking jobs (replaces in-memory dict)
- status_transitions: Append-only audit log

Uses a schema_version table for forward-compatible migrations.
"""

import logging
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)

# Current schema version
SCHEMA_VERSION = 1

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
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

DEALS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_deals_status ON deals(status);",
    "CREATE INDEX IF NOT EXISTS idx_deals_seller_url ON deals(seller_url);",
    "CREATE INDEX IF NOT EXISTS idx_deals_seller_deal_id ON deals(seller_deal_id);",
    "CREATE INDEX IF NOT EXISTS idx_deals_created_at ON deals(created_at);",
    "CREATE INDEX IF NOT EXISTS idx_deals_status_created ON deals(status, created_at);",
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
        STATUS_TRANSITIONS_TABLE,
    ]:
        cursor.execute(ddl)

    # Indexes
    for index_list in [
        DEALS_INDEXES,
        NEGOTIATION_ROUNDS_INDEXES,
        BOOKING_RECORDS_INDEXES,
        JOBS_INDEXES,
        STATUS_TRANSITIONS_INDEXES,
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
    # Add entries here as the schema evolves:
    #   2: migrate_v1_to_v2,
    #   3: migrate_v2_to_v3,
    migrations: dict[int, callable] = {}

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
