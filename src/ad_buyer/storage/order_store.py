# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SQLite-backed order state persistence for buyer-side order tracking.

Stores the buyer's local view of order state using an ``order:{id}`` key
format.  Each order is a JSON blob containing order metadata, current
status, and an embedded audit log of state transitions.

This store is intentionally separate from DealStore -- orders represent
the buyer's view of seller-side order state, synced periodically or on
demand from the seller's Order API.

Thread safety is provided by check_same_thread=False and a threading.Lock(),
matching the DealStore pattern.

 (Order Status & Audit API Integration) (Negotiation & Orders MCP Tools)
"""

import json
import logging
import sqlite3
import threading
from typing import Any

logger = logging.getLogger(__name__)


# -- Schema DDL ---------------------------------------------------------------

ORDERS_TABLE = """
CREATE TABLE IF NOT EXISTS orders (
    key         TEXT PRIMARY KEY,
    data        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

ORDERS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);",
]


class OrderStore:
    """SQLite-backed store for buyer-side order state.

    Orders are stored with a key format of ``order:{order_id}`` and the
    full order data is serialized as JSON in the ``data`` column.  The
    ``status`` column is denormalized for efficient filtering.

    Thread-safe via a reentrant lock. Uses WAL mode for concurrent
    read/write access. All public methods are synchronous.

    Args:
        database_url: SQLite connection string (e.g. ``sqlite:///./ad_buyer.db``
            or ``sqlite:///:memory:`` for testing).
    """

    def __init__(self, database_url: str) -> None:
        # Keep the full URL so the connection factory can detect a
        # ``postgresql://`` backend; ``_db_path`` preserves the SQLite path for
        # any legacy reference. SQLite path is byte-identical by default.
        self._database_url = database_url
        self._db_path = self._parse_url(database_url)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_url(url: str) -> str:
        """Extract the file path from a sqlite:/// URL."""
        if url.startswith("sqlite:///"):
            return url[len("sqlite:///") :]
        if url.startswith("sqlite://"):
            path = url[len("sqlite://") :]
            return path if path else ":memory:"
        return url

    def connect(self) -> None:
        """Open the database connection, set pragmas, and create tables."""
        from .connection_factory import (
            apply_sqlite_pragmas,
            is_postgres_url,
            open_connection,
        )

        self._conn = open_connection(self._database_url, check_same_thread=False)
        apply_sqlite_pragmas(self._conn, self._database_url)
        self._create_tables(is_postgres=is_postgres_url(self._database_url))

    def disconnect(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _create_tables(self, is_postgres: bool = False) -> None:
        """Create orders table and indexes if they don't exist.

        On Postgres the inline SQLite DDL is translated via the same
        ``schema_pg._to_postgres`` helper used for ``schema.py`` (SQLite path
        unchanged).
        """
        table_ddl = ORDERS_TABLE
        index_ddls = ORDERS_INDEXES
        if is_postgres:
            from .schema_pg import _to_postgres, _translate_all

            table_ddl = _to_postgres(ORDERS_TABLE)
            index_ddls = _translate_all(ORDERS_INDEXES)
        cursor = self._conn.cursor()
        cursor.execute(table_ddl)
        for idx in index_ddls:
            cursor.execute(idx)
        self._conn.commit()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def _make_key(self, order_id: str) -> str:
        """Build the storage key for an order.

        Args:
            order_id: The order's identifier.

        Returns:
            Storage key in ``order:{order_id}`` format.
        """
        return f"order:{order_id}"

    def set_order(self, order_id: str, data: dict[str, Any]) -> None:
        """Persist an order (insert or update).

        Args:
            order_id: The order identifier.
            data: Full order data dict (must include status).
        """
        key = self._make_key(order_id)
        status = data.get("status", "pending")
        json_data = json.dumps(data)

        with self._lock:
            self._conn.execute(
                """INSERT INTO orders (key, data, status, updated_at)
                   VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                   ON CONFLICT(key) DO UPDATE SET
                       data = excluded.data,
                       status = excluded.status,
                       updated_at = excluded.updated_at""",
                (key, json_data, status),
            )
            self._conn.commit()

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        """Retrieve an order by ID.

        Args:
            order_id: The order identifier.

        Returns:
            Order data dict, or None if not found.
        """
        key = self._make_key(order_id)
        with self._lock:
            cursor = self._conn.execute("SELECT data FROM orders WHERE key = ?", (key,))
            row = cursor.fetchone()
        if row is None:
            return None
        return json.loads(row["data"])

    def list_orders(
        self,
        filters: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """List orders, optionally filtered by status.

        Args:
            filters: Optional dict with ``status`` key for filtering.

        Returns:
            List of order data dicts.
        """
        clauses: list[str] = []
        params: list[Any] = []

        if filters:
            if "status" in filters:
                clauses.append("status = ?")
                params.append(filters["status"])

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        query = f"SELECT data FROM orders {where} ORDER BY created_at DESC"

        with self._lock:
            cursor = self._conn.execute(query, params)
            rows = cursor.fetchall()

        return [json.loads(row["data"]) for row in rows]
