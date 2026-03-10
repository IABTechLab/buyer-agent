# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SQLite-backed deal state persistence.

Uses synchronous sqlite3 (not aiosqlite) because CrewAI runs flows in
worker threads that may not have an asyncio event loop.  Thread safety
is provided by check_same_thread=False and a threading.Lock().

The DealStore is the single persistence layer for deal lifecycle state,
negotiation history, booking records, job tracking, and status transitions.
"""

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from .schema import initialize_schema

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class DealStore:
    """SQLite-backed store for deal state, negotiations, bookings, and jobs.

    Thread-safe via a reentrant lock. Uses WAL mode for concurrent
    read/write access. All public methods are synchronous.

    Args:
        database_url: SQLite connection string (e.g. ``sqlite:///./ad_buyer.db``
            or ``sqlite:///:memory:`` for testing).
    """

    def __init__(self, database_url: str) -> None:
        self._db_path = self._parse_url(database_url)
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open the database connection, set pragmas, and initialize schema."""
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row  # dict-like row access
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        initialize_schema(self._conn)

    def disconnect(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Deals
    # ------------------------------------------------------------------

    def save_deal(
        self,
        *,
        deal_id: Optional[str] = None,
        seller_url: str,
        product_id: str,
        product_name: str = "",
        deal_type: str = "PD",
        status: str = "draft",
        seller_deal_id: Optional[str] = None,
        price: Optional[float] = None,
        original_price: Optional[float] = None,
        impressions: Optional[int] = None,
        flight_start: Optional[str] = None,
        flight_end: Optional[str] = None,
        buyer_context: Optional[str] = None,
        metadata: Optional[str] = None,
    ) -> str:
        """Insert a new deal.

        Args:
            deal_id: Optional UUID. Generated if not provided.
            seller_url: Seller endpoint URL.
            product_id: Product being dealt on.
            product_name: Human-readable product name.
            deal_type: PG, PD, or PA.
            status: Initial status (default ``draft``).
            seller_deal_id: Seller-assigned deal ID (may be None initially).
            price: Current/final CPM.
            original_price: Pre-discount price.
            impressions: Contracted impressions.
            flight_start: ISO date string.
            flight_end: ISO date string.
            buyer_context: JSON-serialized BuyerContext.
            metadata: JSON string for extensible fields.

        Returns:
            The deal ID (generated or provided).
        """
        if deal_id is None:
            deal_id = str(uuid.uuid4())
        now = _now_iso()

        with self._lock:
            self._conn.execute(
                """INSERT INTO deals
                   (id, seller_url, seller_deal_id, product_id, product_name,
                    deal_type, status, price, original_price, impressions,
                    flight_start, flight_end, buyer_context, metadata,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    deal_id,
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

        # Record initial status transition
        self.record_status_transition(
            entity_type="deal",
            entity_id=deal_id,
            from_status=None,
            to_status=status,
            triggered_by="system",
            notes="Deal created",
        )

        return deal_id

    def get_deal(self, deal_id: str) -> Optional[dict[str, Any]]:
        """Retrieve a deal by ID.

        Args:
            deal_id: The deal's primary key.

        Returns:
            Deal as a dict, or None if not found.
        """
        with self._lock:
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
            created_after: ISO timestamp lower bound.
            limit: Maximum rows to return.

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

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        query = f"SELECT * FROM deals {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            cursor = self._conn.execute(query, params)
            rows = cursor.fetchall()
        return [dict(r) for r in rows]

    def update_deal_status(
        self,
        deal_id: str,
        new_status: str,
        *,
        triggered_by: str = "system",
        notes: str = "",
    ) -> bool:
        """Update a deal's status and log the transition.

        Args:
            deal_id: The deal to update.
            new_status: Target status value.
            triggered_by: Who/what triggered the change.
            notes: Optional note for the audit log.

        Returns:
            True if the deal was found and updated.
        """
        now = _now_iso()

        with self._lock:
            # Get current status
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
            self._conn.commit()

        # Record the transition (outside lock to avoid deadlock with
        # record_status_transition's own lock acquisition)
        self.record_status_transition(
            entity_type="deal",
            entity_id=deal_id,
            from_status=old_status,
            to_status=new_status,
            triggered_by=triggered_by,
            notes=notes,
        )

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
        """Record a negotiation round.

        Args:
            deal_id: FK to deals.
            proposal_id: Seller's proposal ID.
            round_number: Sequential round number.
            buyer_price: Buyer's offered price.
            seller_price: Seller's asking price.
            action: counter, accept, reject, final_offer.
            rationale: Explanation for the action.

        Returns:
            The auto-generated row ID.
        """
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
        """Get all negotiation rounds for a deal, ordered by round number.

        Args:
            deal_id: The deal to query.

        Returns:
            List of round dicts.
        """
        with self._lock:
            cursor = self._conn.execute(
                """SELECT * FROM negotiation_rounds
                   WHERE deal_id = ?
                   ORDER BY round_number ASC""",
                (deal_id,),
            )
            rows = cursor.fetchall()
        return [dict(r) for r in rows]

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
        """Record a booked line item.

        Args:
            deal_id: FK to deals.
            order_id: OpenDirect order ID.
            line_id: OpenDirect line ID.
            channel: Channel name.
            impressions: Contracted impressions.
            cost: Line cost.
            booking_status: Initial booking status.
            metadata: JSON string for extensible fields.

        Returns:
            The auto-generated row ID.
        """
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO booking_records
                   (deal_id, order_id, line_id, channel, impressions, cost,
                    booking_status, metadata)
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
        """Get all booking records for a deal.

        Args:
            deal_id: The deal to query.

        Returns:
            List of booking record dicts.
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM booking_records WHERE deal_id = ?",
                (deal_id,),
            )
            rows = cursor.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    def save_job(
        self,
        *,
        job_id: str,
        status: str = "pending",
        progress: float = 0.0,
        brief: Optional[str] = None,
        auto_approve: bool = False,
        budget_allocs: Optional[str] = None,
        recommendations: Optional[str] = None,
        booked_lines: Optional[str] = None,
        errors: Optional[str] = None,
    ) -> str:
        """Insert or update a job record (upsert).

        Args:
            job_id: Unique job identifier.
            status: Job status.
            progress: Progress 0.0-1.0.
            brief: JSON campaign brief.
            auto_approve: Whether to auto-approve.
            budget_allocs: JSON budget allocations.
            recommendations: JSON recommendation list.
            booked_lines: JSON booked lines list.
            errors: JSON error list.

        Returns:
            The job ID.
        """
        now = _now_iso()

        with self._lock:
            self._conn.execute(
                """INSERT INTO jobs
                   (id, status, progress, brief, auto_approve,
                    budget_allocs, recommendations, booked_lines, errors,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       status = excluded.status,
                       progress = excluded.progress,
                       brief = excluded.brief,
                       auto_approve = excluded.auto_approve,
                       budget_allocs = excluded.budget_allocs,
                       recommendations = excluded.recommendations,
                       booked_lines = excluded.booked_lines,
                       errors = excluded.errors,
                       updated_at = excluded.updated_at""",
                (
                    job_id,
                    status,
                    progress,
                    brief or "{}",
                    1 if auto_approve else 0,
                    budget_allocs or "{}",
                    recommendations or "[]",
                    booked_lines or "[]",
                    errors or "[]",
                    now,
                    now,
                ),
            )
            self._conn.commit()
        return job_id

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        """Retrieve a job by ID.

        Args:
            job_id: The job's primary key.

        Returns:
            Job as a dict, or None if not found.
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            )
            row = cursor.fetchone()
        if row is None:
            return None

        result = dict(row)
        # Deserialize JSON fields for API compatibility
        for field in ("brief", "budget_allocs", "recommendations", "booked_lines", "errors"):
            val = result.get(field)
            if isinstance(val, str):
                try:
                    result[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
        # Convert auto_approve int to bool
        result["auto_approve"] = bool(result.get("auto_approve", 0))
        return result

    def list_jobs(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List jobs with optional status filter.

        Args:
            status: Filter by job status.
            limit: Maximum rows to return.

        Returns:
            List of job dicts ordered by created_at descending.
        """
        if status is not None:
            query = "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?"
            params: tuple = (status, limit)
        else:
            query = "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?"
            params = (limit,)

        with self._lock:
            cursor = self._conn.execute(query, params)
            rows = cursor.fetchall()

        results = []
        for row in rows:
            r = dict(row)
            # Deserialize JSON fields
            for field in ("brief", "budget_allocs", "recommendations", "booked_lines", "errors"):
                val = r.get(field)
                if isinstance(val, str):
                    try:
                        r[field] = json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        pass
            r["auto_approve"] = bool(r.get("auto_approve", 0))
            results.append(r)
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
        """Log a status change to the audit table.

        Args:
            entity_type: ``deal`` or ``booking``.
            entity_id: The entity's primary key.
            from_status: Previous status (None for creation).
            to_status: New status.
            triggered_by: system, seller_push, user, agent.
            notes: Free-text note.

        Returns:
            The auto-generated row ID.
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
        self,
        entity_type: str,
        entity_id: str,
    ) -> list[dict[str, Any]]:
        """Get status transition history for an entity.

        Args:
            entity_type: ``deal`` or ``booking``.
            entity_id: The entity's primary key.

        Returns:
            List of transition dicts ordered by created_at ascending.
        """
        with self._lock:
            cursor = self._conn.execute(
                """SELECT * FROM status_transitions
                   WHERE entity_type = ? AND entity_id = ?
                   ORDER BY created_at ASC""",
                (entity_type, entity_id),
            )
            rows = cursor.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_url(database_url: str) -> str:
        """Extract the file path from a sqlite:/// URL.

        Handles:
        - ``sqlite:///./ad_buyer.db`` -> ``./ad_buyer.db``
        - ``sqlite:///:memory:`` -> ``:memory:``
        - ``sqlite:///path/to/db`` -> ``path/to/db``
        - Plain paths pass through as-is.

        Args:
            database_url: SQLite connection string.

        Returns:
            Filesystem path or ``:memory:``.
        """
        if database_url.startswith("sqlite:///"):
            return database_url[len("sqlite:///"):]
        return database_url
