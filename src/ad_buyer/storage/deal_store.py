# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SQLite-backed persistence for deal state, negotiation history, and job tracking.

Uses synchronous sqlite3 (not aiosqlite) for CrewAI thread compatibility.
All writes are serialized via threading.Lock(). The connection is opened
with check_same_thread=False so any thread can use it.

Typical usage:
    store = DealStore("sqlite:///./ad_buyer.db")
    store.connect()
    store.save_deal(id="deal-1", seller_url="http://seller", product_id="p1")
    deal = store.get_deal("deal-1")
    store.disconnect()
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from . import schema

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class DealStore:
    """SQLite-backed store for deal lifecycle, negotiations, bookings, and jobs.

    Thread-safe: uses a single connection with check_same_thread=False and
    a threading.Lock() to serialize writes. Reads do not take the lock
    because SQLite in WAL mode supports concurrent readers.

    Args:
        database_url: SQLite URL in the format ``sqlite:///path`` or
            ``sqlite:///:memory:`` for in-memory databases.
    """

    def __init__(self, database_url: str) -> None:
        self._db_path = self._parse_url(database_url)
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open the database connection, create tables, and apply migrations."""
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        schema.create_tables(self._conn)
        schema.apply_migrations(self._conn)

    def disconnect(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Deals
    # ------------------------------------------------------------------

    def save_deal(
        self,
        *,
        id: str,
        seller_url: str,
        product_id: str,
        product_name: str = "",
        seller_deal_id: Optional[str] = None,
        deal_type: str = "PD",
        status: str = "draft",
        price: Optional[float] = None,
        original_price: Optional[float] = None,
        impressions: Optional[int] = None,
        flight_start: Optional[str] = None,
        flight_end: Optional[str] = None,
        buyer_context: Optional[str] = None,
        metadata: Optional[str] = None,
    ) -> str:
        """Insert or replace a deal record.

        Returns the deal id.
        """
        now = _now_iso()
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO deals
                   (id, seller_url, seller_deal_id, product_id, product_name,
                    deal_type, status, price, original_price, impressions,
                    flight_start, flight_end, buyer_context, metadata,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    id,
                    seller_url,
                    seller_deal_id,
                    product_id,
                    product_name,
                    deal_type,
                    status,
                    price,
                    original_price,
                    impressions,
                    flight_start,
                    flight_end,
                    buyer_context,
                    metadata or "{}",
                    now,
                    now,
                ),
            )
            self._conn.commit()
        return id

    def get_deal(self, deal_id: str) -> Optional[dict[str, Any]]:
        """Retrieve a single deal by id, or None if not found."""
        cursor = self._conn.execute(
            "SELECT * FROM deals WHERE id = ?", (deal_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def list_deals(
        self,
        *,
        status: Optional[str] = None,
        seller_url: Optional[str] = None,
        created_after: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List deals with optional filters.

        Args:
            status: Filter by deal status.
            seller_url: Filter by seller URL.
            created_after: ISO timestamp — return only deals created after this.
            limit: Maximum number of rows to return.

        Returns:
            List of deal dicts ordered by created_at descending.
        """
        clauses: list[str] = []
        params: list[Any] = []

        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if seller_url is not None:
            clauses.append("seller_url = ?")
            params.append(seller_url)
        if created_after is not None:
            clauses.append("created_at > ?")
            params.append(created_after)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM deals{where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor = self._conn.execute(sql, params)
        return [dict(r) for r in cursor.fetchall()]

    def update_deal_status(
        self,
        deal_id: str,
        new_status: str,
        *,
        triggered_by: str = "system",
        notes: str = "",
    ) -> bool:
        """Update a deal's status and record the transition.

        Writes to both the deals table and the status_transitions table
        atomically within a single lock acquisition.

        Returns True if the deal was found and updated, False otherwise.
        """
        now = _now_iso()
        with self._lock:
            # Read current status
            cursor = self._conn.execute(
                "SELECT status FROM deals WHERE id = ?", (deal_id,)
            )
            row = cursor.fetchone()
            if row is None:
                return False
            old_status = row["status"]

            self._conn.execute(
                "UPDATE deals SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, now, deal_id),
            )
            self._conn.execute(
                """INSERT INTO status_transitions
                   (entity_type, entity_id, from_status, to_status,
                    triggered_by, notes)
                   VALUES ('deal', ?, ?, ?, ?, ?)""",
                (deal_id, old_status, new_status, triggered_by, notes),
            )
            self._conn.commit()
        return True

    # ------------------------------------------------------------------
    # Negotiation Rounds
    # ------------------------------------------------------------------

    def save_negotiation_round(
        self,
        *,
        deal_id: str,
        proposal_id: str,
        round_number: int,
        buyer_price: float,
        seller_price: float,
        action: str,
        rationale: str = "",
    ) -> int:
        """Insert a negotiation round. Returns the auto-generated row id."""
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO negotiation_rounds
                   (deal_id, proposal_id, round_number, buyer_price,
                    seller_price, action, rationale)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    deal_id,
                    proposal_id,
                    round_number,
                    buyer_price,
                    seller_price,
                    action,
                    rationale,
                ),
            )
            self._conn.commit()
            return cursor.lastrowid

    def get_negotiation_history(self, deal_id: str) -> list[dict[str, Any]]:
        """Get all negotiation rounds for a deal, ordered by round number."""
        cursor = self._conn.execute(
            """SELECT * FROM negotiation_rounds
               WHERE deal_id = ?
               ORDER BY round_number""",
            (deal_id,),
        )
        return [dict(r) for r in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Booking Records
    # ------------------------------------------------------------------

    def save_booking_record(
        self,
        *,
        deal_id: str,
        order_id: Optional[str] = None,
        line_id: Optional[str] = None,
        channel: str = "",
        impressions: int = 0,
        cost: float = 0.0,
        booking_status: str = "pending",
        metadata: Optional[str] = None,
    ) -> int:
        """Insert a booking record. Returns the auto-generated row id."""
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO booking_records
                   (deal_id, order_id, line_id, channel, impressions,
                    cost, booking_status, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    deal_id,
                    order_id,
                    line_id,
                    channel,
                    impressions,
                    cost,
                    booking_status,
                    metadata or "{}",
                ),
            )
            self._conn.commit()
            return cursor.lastrowid

    def get_booking_records(self, deal_id: str) -> list[dict[str, Any]]:
        """Get all booking records for a deal."""
        cursor = self._conn.execute(
            "SELECT * FROM booking_records WHERE deal_id = ?",
            (deal_id,),
        )
        return [dict(r) for r in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    def save_job(self, job_id: str, data: dict[str, Any]) -> None:
        """Insert or update a job record.

        The data dict should contain keys matching the jobs table columns:
        status, progress, brief, auto_approve, budget_allocs,
        recommendations, booked_lines, errors.

        JSON-serializable values (brief, budget_allocs, recommendations,
        booked_lines, errors) are automatically serialized.
        """
        now = _now_iso()
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO jobs
                   (id, status, progress, brief, auto_approve,
                    budget_allocs, recommendations, booked_lines, errors,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    data.get("status", "pending"),
                    data.get("progress", 0.0),
                    json.dumps(data.get("brief", {})),
                    1 if data.get("auto_approve") else 0,
                    json.dumps(data.get("budget_allocs", data.get("budget_allocations", {}))),
                    json.dumps(data.get("recommendations", [])),
                    json.dumps(data.get("booked_lines", [])),
                    json.dumps(data.get("errors", [])),
                    data.get("created_at", now),
                    now,
                ),
            )
            self._conn.commit()

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        """Retrieve a job by id, deserializing JSON columns.

        Returns None if not found.
        """
        cursor = self._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None

        d = dict(row)
        # Deserialize JSON columns
        for col in ("brief", "budget_allocs", "recommendations", "booked_lines", "errors"):
            if isinstance(d.get(col), str):
                try:
                    d[col] = json.loads(d[col])
                except (json.JSONDecodeError, TypeError):
                    pass
        # Convert auto_approve back to bool
        d["auto_approve"] = bool(d.get("auto_approve"))
        return d

    def list_jobs(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List jobs, optionally filtered by status.

        Returns dicts with JSON columns deserialized.
        """
        if status is not None:
            cursor = self._conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cursor = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )

        results = []
        for row in cursor.fetchall():
            d = dict(row)
            for col in ("brief", "budget_allocs", "recommendations", "booked_lines", "errors"):
                if isinstance(d.get(col), str):
                    try:
                        d[col] = json.loads(d[col])
                    except (json.JSONDecodeError, TypeError):
                        pass
            d["auto_approve"] = bool(d.get("auto_approve"))
            results.append(d)
        return results

    # ------------------------------------------------------------------
    # Status Transitions
    # ------------------------------------------------------------------

    def record_status_transition(
        self,
        *,
        entity_type: str,
        entity_id: str,
        from_status: Optional[str],
        to_status: str,
        triggered_by: str = "system",
        notes: str = "",
    ) -> int:
        """Record a status transition in the audit log.

        Returns the auto-generated row id.
        """
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO status_transitions
                   (entity_type, entity_id, from_status, to_status,
                    triggered_by, notes)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (entity_type, entity_id, from_status, to_status, triggered_by, notes),
            )
            self._conn.commit()
            return cursor.lastrowid

    def get_status_history(
        self, entity_type: str, entity_id: str
    ) -> list[dict[str, Any]]:
        """Get status transition history for an entity, ordered by time."""
        cursor = self._conn.execute(
            """SELECT * FROM status_transitions
               WHERE entity_type = ? AND entity_id = ?
               ORDER BY created_at""",
            (entity_type, entity_id),
        )
        return [dict(r) for r in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_url(database_url: str) -> str:
        """Extract the file path from a SQLite URL.

        Supports:
            sqlite:///./relative.db  -> ./relative.db
            sqlite:///absolute.db    -> /absolute.db
            sqlite:///:memory:       -> :memory:
            :memory:                 -> :memory:

        Args:
            database_url: A database URL string.

        Returns:
            The filesystem path (or ':memory:').
        """
        if database_url == ":memory:":
            return ":memory:"
        prefix = "sqlite:///"
        if database_url.startswith(prefix):
            path = database_url[len(prefix):]
            # Handle :memory: URI
            if path == ":memory:":
                return ":memory:"
            return path
        # Fallback: treat as raw path
        return database_url
