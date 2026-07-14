# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Lightweight database connectivity probes for the storage layer.

The interface layer (MCP tools, HTTP API) must not open raw sqlite3
connections itself.  These helpers keep the raw-sqlite health check in
the storage package where low-level database access belongs, so callers
can ask "is the database reachable?" without importing ``sqlite3``.
"""

from __future__ import annotations

import sqlite3


def _sqlite_path(database_url: str) -> str:
    """Strip a ``sqlite:///`` prefix to get a connectable path.

    Mirrors ``DealStore._parse_url`` for the subset used by the probe:
    ``sqlite:///:memory:`` -> ``:memory:`` and ``sqlite:///path`` ->
    ``path``.  Plain paths pass through unchanged.
    """
    if database_url.startswith("sqlite:///"):
        return database_url[len("sqlite:///") :]
    return database_url


def probe_database(database_url: str) -> tuple[bool, str | None]:
    """Attempt a lightweight connection + ``SELECT 1`` against the database.

    Args:
        database_url: SQLite connection string (or plain path).

    Returns:
        ``(True, None)`` when the database is reachable, otherwise
        ``(False, error_message)``.
    """
    db_path = _sqlite_path(database_url)
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("SELECT 1")
        conn.close()
        return True, None
    except (sqlite3.Error, OSError) as exc:
        return False, str(exc)


def database_accessible(database_url: str) -> bool:
    """Return whether the database is reachable (boolean form of probe)."""
    ok, _ = probe_database(database_url)
    return ok
