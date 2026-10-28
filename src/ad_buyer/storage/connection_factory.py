# Donated to IAB Tech Lab

"""Connection factory — the single seam the buyer stores route through.

Scope 2 / Req 12.6. By default returns a plain ``sqlite3`` connection (the SQLite
path is byte-for-byte unchanged). When ``database_url`` is a PostgreSQL URL, it
returns the :class:`PgConnection` adapter instead. The stores' connection-open
sites change from ``sqlite3.connect(path, …)`` to ``open_connection(url, …)`` —
one line each — and otherwise keep their existing query code.

Schema creation is likewise dialect-routed: ``initialize_schema_for`` calls the
SQLite ``schema.initialize_schema`` or the ``schema_pg.initialize_schema_pg``.
"""

from __future__ import annotations

import sqlite3


def is_postgres_url(database_url: str | None) -> bool:
    return bool(database_url) and database_url.startswith(("postgresql://", "postgres://"))


def _sqlite_path(database_url: str) -> str:
    """Extract the sqlite file path (matches the stores' historical parsing)."""
    if database_url.startswith("sqlite:///"):
        return database_url[len("sqlite:///") :]
    return database_url


def open_connection(database_url: str, *, check_same_thread: bool = False):
    """Open a connection for ``database_url``.

    - ``postgresql://…`` / ``postgres://…`` → :class:`PgConnection` (psycopg
      wrapped to present the sqlite3 API the stores use).
    - anything else → ``sqlite3.connect`` (default, unchanged behavior).

    ``check_same_thread`` is accepted for call-site parity with the previous
    ``sqlite3.connect`` calls; it is a no-op for the Postgres adapter.
    """
    if is_postgres_url(database_url):
        from .pg_connection import PgConnection

        # psycopg accepts the libpq URL directly (strip a SQLAlchemy +driver
        # suffix if one slipped through, e.g. postgresql+asyncpg://).
        dsn = database_url.replace("+asyncpg", "").replace("+psycopg", "")
        return PgConnection(dsn)

    conn = sqlite3.connect(_sqlite_path(database_url), check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    return conn


def apply_sqlite_pragmas(conn, database_url: str) -> None:
    """Apply the WAL/foreign-key/busy-timeout pragmas — SQLite only, no-op on PG.

    Call sites previously ran these unconditionally; routing them here keeps the
    Postgres adapter clean (Postgres has no PRAGMA).
    """
    if is_postgres_url(database_url):
        return
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")


def initialize_schema_for(conn, database_url: str) -> None:
    """Create tables using the dialect-appropriate DDL."""
    if is_postgres_url(database_url):
        from .schema_pg import initialize_schema_pg

        initialize_schema_pg(conn)
    else:
        from .schema import initialize_schema

        initialize_schema(conn)
