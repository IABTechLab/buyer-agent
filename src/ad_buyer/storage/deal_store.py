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

from ..models.state_machine import (
    BuyerDealStatus,
    DealStateMachine,
    InvalidTransitionError,
)
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

    # All v2 intrinsic column names on the deals table, used to build
    # dynamic INSERT statements when v2 kwargs are provided.
    _V2_DEAL_COLUMNS = (
        # Counterparty fields
        "display_name", "description", "buyer_org", "buyer_id",
        "seller_org", "seller_id", "seller_domain", "seller_type",
        # Pricing detail fields
        "price_model", "bid_floor_cpm", "fixed_price_cpm",
        "cpp", "guaranteed_grps", "currency", "fee_transparency",
        # Inventory targeting fields
        "media_type", "formats", "content_categories",
        "publisher_domains", "geo_targets", "dayparts",
        "programs", "networks", "audience_segments", "estimated_volume",
        # Lifecycle extensions
        "deprecated_at", "deprecated_reason", "parent_deal_id",
        # Supply chain fields
        "schain_complete", "schain_nodes", "sellers_json_url",
        "is_direct", "hop_count", "inventory_fingerprint",
        # Linear TV fields
        "makegood_provisions", "cancellation_window",
        "audience_guarantee", "preemption_rights",
        "agency_of_record_status",
    )

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
        # v2 counterparty fields
        display_name: Optional[str] = None,
        description: Optional[str] = None,
        buyer_org: Optional[str] = None,
        buyer_id: Optional[str] = None,
        seller_org: Optional[str] = None,
        seller_id: Optional[str] = None,
        seller_domain: Optional[str] = None,
        seller_type: Optional[str] = None,
        # v2 pricing detail fields
        price_model: Optional[str] = None,
        bid_floor_cpm: Optional[float] = None,
        fixed_price_cpm: Optional[float] = None,
        cpp: Optional[float] = None,
        guaranteed_grps: Optional[float] = None,
        currency: Optional[str] = None,
        fee_transparency: Optional[float] = None,
        # v2 inventory targeting fields
        media_type: Optional[str] = None,
        formats: Optional[str] = None,
        content_categories: Optional[str] = None,
        publisher_domains: Optional[str] = None,
        geo_targets: Optional[str] = None,
        dayparts: Optional[str] = None,
        programs: Optional[str] = None,
        networks: Optional[str] = None,
        audience_segments: Optional[str] = None,
        estimated_volume: Optional[int] = None,
        # v2 lifecycle extensions
        deprecated_at: Optional[str] = None,
        deprecated_reason: Optional[str] = None,
        parent_deal_id: Optional[str] = None,
        # v2 supply chain fields
        schain_complete: Optional[int] = None,
        schain_nodes: Optional[str] = None,
        sellers_json_url: Optional[str] = None,
        is_direct: Optional[int] = None,
        hop_count: Optional[int] = None,
        inventory_fingerprint: Optional[str] = None,
        # v2 linear TV fields
        makegood_provisions: Optional[str] = None,
        cancellation_window: Optional[str] = None,
        audience_guarantee: Optional[str] = None,
        preemption_rights: Optional[str] = None,
        agency_of_record_status: Optional[str] = None,
    ) -> str:
        """Insert a new deal.

        Accepts all v1 fields plus optional v2 intrinsic fields for the
        deal library (counterparty, pricing detail, inventory targeting,
        lifecycle, supply chain, and linear TV fields).  Backward
        compatible: callers using only v1 fields continue to work
        unchanged.

        Args:
            deal_id: Optional UUID. Generated if not provided.
            seller_url: Seller endpoint URL.
            product_id: Product being dealt on.
            product_name: Human-readable product name.
            deal_type: PG, PD, PA, OPEN_AUCTION, UPFRONT, or SCATTER.
            status: Initial status (default ``draft``).
            seller_deal_id: Seller-assigned deal ID (may be None initially).
            price: Current/final CPM.
            original_price: Pre-discount price.
            impressions: Contracted impressions.
            flight_start: ISO date string.
            flight_end: ISO date string.
            buyer_context: JSON-serialized BuyerContext.
            metadata: JSON string for extensible fields.
            display_name: Human-readable deal name (v2).
            description: Deal description (v2).
            buyer_org: Buyer organization name (v2).
            buyer_id: Buyer seat ID (v2).
            seller_org: Seller organization name (v2).
            seller_id: Seller account ID (v2).
            seller_domain: Seller domain, e.g. ``espn.com`` (v2).
            seller_type: PUBLISHER, SSP, DSP, or INTERMEDIARY (v2).
            price_model: CPM, CPP, FLAT, or HYBRID (v2).
            bid_floor_cpm: Minimum CPM for auction deals (v2).
            fixed_price_cpm: Fixed CPM for PG/PD deals (v2).
            cpp: Cost Per Point for linear TV (v2).
            guaranteed_grps: Guaranteed GRPs for linear TV (v2).
            currency: ISO 4217 currency code (v2).
            fee_transparency: Estimated intermediary fees (v2).
            media_type: DIGITAL, CTV, LINEAR_TV, AUDIO, or DOOH (v2).
            formats: JSON array of format strings (v2).
            content_categories: JSON array of IAB category IDs (v2).
            publisher_domains: JSON array of publisher domains (v2).
            geo_targets: JSON array of geo targets (v2).
            dayparts: JSON array of daypart strings (v2).
            programs: JSON array of program names (v2).
            networks: JSON array of network names (v2).
            audience_segments: JSON array of audience segment IDs (v2).
            estimated_volume: Estimated daily/weekly impressions (v2).
            deprecated_at: ISO timestamp when deprecated (v2).
            deprecated_reason: Why the deal was deprecated (v2).
            parent_deal_id: ID of deal this was cloned/migrated from (v2).
            schain_complete: 1 if full supply chain is known (v2).
            schain_nodes: JSON array of schain nodes (v2).
            sellers_json_url: URL to seller's sellers.json (v2).
            is_direct: 1 if direct relationship (v2).
            hop_count: Number of intermediaries (v2).
            inventory_fingerprint: Canonical inventory identifier (v2).
            makegood_provisions: Makegood terms for linear TV (v2).
            cancellation_window: Cancellation terms for linear TV (v2).
            audience_guarantee: Audience guarantee for linear TV (v2).
            preemption_rights: Preemption terms for linear TV (v2).
            agency_of_record_status: Agency of record for linear TV (v2).

        Returns:
            The deal ID (generated or provided).
        """
        if deal_id is None:
            deal_id = str(uuid.uuid4())
        now = _now_iso()

        # Build column list and values dynamically to include v2 fields
        # when provided.  Start with the v1 columns that are always present.
        columns = [
            "id", "seller_url", "seller_deal_id", "product_id",
            "product_name", "deal_type", "status", "price",
            "original_price", "impressions", "flight_start", "flight_end",
            "buyer_context", "metadata", "created_at", "updated_at",
        ]
        values: list[Any] = [
            deal_id, seller_url, seller_deal_id, product_id,
            product_name, deal_type, status, price,
            original_price, impressions, flight_start, flight_end,
            buyer_context, metadata or "{}", now, now,
        ]

        # Collect v2 kwargs into a dict for dynamic column building.
        v2_locals = {
            "display_name": display_name, "description": description,
            "buyer_org": buyer_org, "buyer_id": buyer_id,
            "seller_org": seller_org, "seller_id": seller_id,
            "seller_domain": seller_domain, "seller_type": seller_type,
            "price_model": price_model, "bid_floor_cpm": bid_floor_cpm,
            "fixed_price_cpm": fixed_price_cpm, "cpp": cpp,
            "guaranteed_grps": guaranteed_grps, "currency": currency,
            "fee_transparency": fee_transparency,
            "media_type": media_type, "formats": formats,
            "content_categories": content_categories,
            "publisher_domains": publisher_domains,
            "geo_targets": geo_targets, "dayparts": dayparts,
            "programs": programs, "networks": networks,
            "audience_segments": audience_segments,
            "estimated_volume": estimated_volume,
            "deprecated_at": deprecated_at,
            "deprecated_reason": deprecated_reason,
            "parent_deal_id": parent_deal_id,
            "schain_complete": schain_complete,
            "schain_nodes": schain_nodes,
            "sellers_json_url": sellers_json_url,
            "is_direct": is_direct, "hop_count": hop_count,
            "inventory_fingerprint": inventory_fingerprint,
            "makegood_provisions": makegood_provisions,
            "cancellation_window": cancellation_window,
            "audience_guarantee": audience_guarantee,
            "preemption_rights": preemption_rights,
            "agency_of_record_status": agency_of_record_status,
        }

        for col in self._V2_DEAL_COLUMNS:
            val = v2_locals.get(col)
            if val is not None:
                columns.append(col)
                values.append(val)

        placeholders = ", ".join("?" for _ in columns)
        col_names = ", ".join(columns)

        with self._lock:
            self._conn.execute(
                f"INSERT INTO deals ({col_names}) VALUES ({placeholders})",
                values,
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
        media_type: Optional[str] = None,
        seller_domain: Optional[str] = None,
        deal_type: Optional[str] = None,
        advertiser_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List deals with optional filters.

        Supports v1 filters (status, seller_url, created_after) and v2
        filters (media_type, seller_domain, deal_type, advertiser_id).
        The advertiser_id filter performs a JOIN to portfolio_metadata.

        Args:
            status: Filter by deal status.
            seller_url: Filter by seller URL.
            created_after: ISO timestamp lower bound.
            media_type: Filter by media type (v2).
            seller_domain: Filter by seller domain (v2).
            deal_type: Filter by deal type (v2).
            advertiser_id: Filter by advertiser ID via portfolio_metadata (v2).
            limit: Maximum rows to return.

        Returns:
            List of deal dicts ordered by created_at descending.
        """
        clauses: list[str] = []
        params: list[Any] = []
        needs_join = False

        if status is not None:
            clauses.append("d.status = ?")
            params.append(status)
        if seller_url is not None:
            clauses.append("d.seller_url = ?")
            params.append(seller_url)
        if created_after is not None:
            clauses.append("d.created_at > ?")
            params.append(created_after)
        if media_type is not None:
            clauses.append("d.media_type = ?")
            params.append(media_type)
        if seller_domain is not None:
            clauses.append("d.seller_domain = ?")
            params.append(seller_domain)
        if deal_type is not None:
            clauses.append("d.deal_type = ?")
            params.append(deal_type)
        if advertiser_id is not None:
            clauses.append("pm.advertiser_id = ?")
            params.append(advertiser_id)
            needs_join = True

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        if needs_join:
            query = (
                f"SELECT d.* FROM deals d "
                f"JOIN portfolio_metadata pm ON pm.deal_id = d.id "
                f"{where} ORDER BY d.created_at DESC LIMIT ?"
            )
        else:
            query = (
                f"SELECT d.* FROM deals d "
                f"{where} ORDER BY d.created_at DESC LIMIT ?"
            )
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

        When both the current status and new_status are valid
        BuyerDealStatus values, the state machine enforces that only
        allowed transitions are executed.  If the current status is not
        a recognized BuyerDealStatus (e.g. a legacy value), the update
        proceeds without validation for backward compatibility.

        Args:
            deal_id: The deal to update.
            new_status: Target status value.
            triggered_by: Who/what triggered the change.
            notes: Optional note for the audit log.

        Returns:
            True if the deal was found and updated, False if the deal
            was not found or the transition was rejected by the state
            machine.
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

            # Enforce state machine if both statuses are known
            try:
                old_deal_status = BuyerDealStatus(old_status)
                new_deal_status = BuyerDealStatus(new_status)
                # Build a throwaway machine to validate the transition
                sm = DealStateMachine(
                    deal_id, initial_status=old_deal_status
                )
                if not sm.can_transition(new_deal_status):
                    logger.warning(
                        "Rejected transition for deal %s: %s -> %s",
                        deal_id,
                        old_status,
                        new_status,
                    )
                    return False
            except ValueError:
                # One or both statuses are not BuyerDealStatus members;
                # skip validation for backward compatibility.
                pass

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
    # Events
    # ------------------------------------------------------------------

    def save_event(
        self,
        *,
        event_id: Optional[str] = None,
        event_type: str,
        flow_id: str = "",
        flow_type: str = "",
        deal_id: str = "",
        session_id: str = "",
        payload: Optional[str] = None,
        metadata: Optional[str] = None,
    ) -> str:
        """Persist an event to the events table.

        Args:
            event_id: Optional UUID. Generated if not provided.
            event_type: Event type string (e.g. "deal.booked").
            flow_id: Flow that produced this event.
            flow_type: Type of flow (e.g. "deal_booking").
            deal_id: Associated deal ID.
            session_id: Associated session ID.
            payload: JSON-serialized payload.
            metadata: JSON-serialized metadata.

        Returns:
            The event ID (generated or provided).
        """
        if event_id is None:
            event_id = str(uuid.uuid4())

        with self._lock:
            self._conn.execute(
                """INSERT INTO events
                   (id, event_type, flow_id, flow_type, deal_id,
                    session_id, payload, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    event_type,
                    flow_id,
                    flow_type,
                    deal_id,
                    session_id,
                    payload or "{}",
                    metadata or "{}",
                ),
            )
            self._conn.commit()

        return event_id

    def get_event(self, event_id: str) -> Optional[dict[str, Any]]:
        """Retrieve an event by ID.

        Args:
            event_id: The event's primary key.

        Returns:
            Event as a dict, or None if not found.
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM events WHERE id = ?", (event_id,)
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def list_events(
        self,
        *,
        event_type: Optional[str] = None,
        flow_id: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List events with optional filters.

        Args:
            event_type: Filter by event type.
            flow_id: Filter by flow ID.
            session_id: Filter by session ID.
            limit: Maximum rows to return.

        Returns:
            List of event dicts ordered by created_at descending.
        """
        clauses: list[str] = []
        params: list[Any] = []

        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        if flow_id is not None:
            clauses.append("flow_id = ?")
            params.append(flow_id)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        query = f"SELECT * FROM events {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            cursor = self._conn.execute(query, params)
            rows = cursor.fetchall()
        return [dict(r) for r in rows]

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
    # Portfolio Metadata (v2)
    # ------------------------------------------------------------------

    def save_portfolio_metadata(
        self,
        *,
        deal_id: str,
        import_source: Optional[str] = None,
        import_date: Optional[str] = None,
        tags: Optional[str] = None,
        advertiser_id: Optional[str] = None,
        agency_id: Optional[str] = None,
    ) -> int:
        """Insert a portfolio metadata record for a deal.

        Args:
            deal_id: FK to deals.
            import_source: How the deal was imported (CSV, MANUAL, TTD_API, etc.).
            import_date: ISO date when the deal was imported.
            tags: JSON array of user-defined tags.
            advertiser_id: Advertiser this deal belongs to.
            agency_id: Agency managing this deal.

        Returns:
            The auto-generated row ID.
        """
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO portfolio_metadata
                   (deal_id, import_source, import_date, tags,
                    advertiser_id, agency_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (deal_id, import_source, import_date, tags,
                 advertiser_id, agency_id),
            )
            self._conn.commit()
            return cursor.lastrowid

    def get_portfolio_metadata(self, deal_id: str) -> Optional[dict[str, Any]]:
        """Get portfolio metadata for a deal.

        Args:
            deal_id: The deal to query.

        Returns:
            Metadata as a dict, or None if not found.
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM portfolio_metadata WHERE deal_id = ?",
                (deal_id,),
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def update_portfolio_metadata(self, deal_id: str, **kwargs: Any) -> bool:
        """Update specific fields on a deal's portfolio metadata.

        Args:
            deal_id: The deal whose metadata to update.
            **kwargs: Column-value pairs to update. Only known columns
                (import_source, import_date, tags, advertiser_id,
                agency_id) are accepted.

        Returns:
            True if a row was updated, False if no metadata exists for
            the deal or no valid kwargs were provided.
        """
        allowed = {"import_source", "import_date", "tags",
                    "advertiser_id", "agency_id"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False

        set_clause = ", ".join(f"{col} = ?" for col in updates)
        values = list(updates.values())
        values.append(deal_id)

        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE portfolio_metadata SET {set_clause} WHERE deal_id = ?",
                values,
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def delete_portfolio_metadata(self, deal_id: str) -> bool:
        """Delete portfolio metadata for a deal.

        Args:
            deal_id: The deal whose metadata to delete.

        Returns:
            True if a row was deleted, False if no metadata existed.
        """
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM portfolio_metadata WHERE deal_id = ?",
                (deal_id,),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Deal Activations (v2)
    # ------------------------------------------------------------------

    def save_deal_activation(
        self,
        *,
        deal_id: str,
        platform: str,
        platform_deal_id: Optional[str] = None,
        activation_status: Optional[str] = None,
        last_sync_at: Optional[str] = None,
    ) -> int:
        """Insert a deal activation record.

        Args:
            deal_id: FK to deals.
            platform: Platform name (TTD, DV360, XANDR, AMAZON_DSP, DIRECT).
            platform_deal_id: Deal ID on the platform.
            activation_status: ACTIVE, PAUSED, PENDING, or ERROR.
            last_sync_at: ISO timestamp of last sync.

        Returns:
            The auto-generated row ID.
        """
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO deal_activations
                   (deal_id, platform, platform_deal_id,
                    activation_status, last_sync_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (deal_id, platform, platform_deal_id,
                 activation_status, last_sync_at),
            )
            self._conn.commit()
            return cursor.lastrowid

    def get_deal_activations(self, deal_id: str) -> list[dict[str, Any]]:
        """Get all activations for a deal.

        Args:
            deal_id: The deal to query.

        Returns:
            List of activation dicts.
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM deal_activations WHERE deal_id = ?",
                (deal_id,),
            )
            rows = cursor.fetchall()
        return [dict(r) for r in rows]

    def update_deal_activation(self, activation_id: int, **kwargs: Any) -> bool:
        """Update specific fields on a deal activation.

        Args:
            activation_id: The activation row ID to update.
            **kwargs: Column-value pairs to update. Only known columns
                (platform, platform_deal_id, activation_status,
                last_sync_at) are accepted.

        Returns:
            True if a row was updated, False if the activation was not
            found or no valid kwargs were provided.
        """
        allowed = {"platform", "platform_deal_id", "activation_status",
                    "last_sync_at"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False

        set_clause = ", ".join(f"{col} = ?" for col in updates)
        values = list(updates.values())
        values.append(activation_id)

        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE deal_activations SET {set_clause} WHERE id = ?",
                values,
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def delete_deal_activation(self, activation_id: int) -> bool:
        """Delete a deal activation by ID.

        Args:
            activation_id: The activation row ID to delete.

        Returns:
            True if a row was deleted, False if not found.
        """
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM deal_activations WHERE id = ?",
                (activation_id,),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Performance Cache (v2)
    # ------------------------------------------------------------------

    def save_performance_cache(
        self,
        *,
        deal_id: str,
        impressions_delivered: Optional[int] = None,
        spend_to_date: Optional[float] = None,
        fill_rate: Optional[float] = None,
        win_rate: Optional[float] = None,
        avg_effective_cpm: Optional[float] = None,
        last_delivery_at: Optional[str] = None,
        performance_trend: Optional[str] = None,
        cached_at: Optional[str] = None,
    ) -> int:
        """Insert a performance cache entry for a deal.

        Args:
            deal_id: FK to deals.
            impressions_delivered: Total impressions delivered.
            spend_to_date: Total spend.
            fill_rate: Fill rate (0.0-1.0).
            win_rate: Win rate (0.0-1.0).
            avg_effective_cpm: Average effective CPM.
            last_delivery_at: ISO timestamp of last delivery.
            performance_trend: IMPROVING, STABLE, DECLINING, or NO_DATA.
            cached_at: ISO timestamp when this cache entry was created.

        Returns:
            The auto-generated row ID.
        """
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO performance_cache
                   (deal_id, impressions_delivered, spend_to_date,
                    fill_rate, win_rate, avg_effective_cpm,
                    last_delivery_at, performance_trend, cached_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (deal_id, impressions_delivered, spend_to_date,
                 fill_rate, win_rate, avg_effective_cpm,
                 last_delivery_at, performance_trend, cached_at),
            )
            self._conn.commit()
            return cursor.lastrowid

    def get_performance_cache(self, deal_id: str) -> Optional[dict[str, Any]]:
        """Get the latest performance cache entry for a deal.

        Args:
            deal_id: The deal to query.

        Returns:
            Performance data as a dict, or None if not found.
        """
        with self._lock:
            cursor = self._conn.execute(
                """SELECT * FROM performance_cache
                   WHERE deal_id = ?
                   ORDER BY id DESC LIMIT 1""",
                (deal_id,),
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def update_performance_cache(self, deal_id: str, **kwargs: Any) -> bool:
        """Update the latest performance cache entry for a deal.

        Updates the most recently inserted cache row for the given
        deal_id.  Functions as an upsert-style update by deal_id.

        Args:
            deal_id: The deal whose cache to update.
            **kwargs: Column-value pairs to update. Only known columns
                (impressions_delivered, spend_to_date, fill_rate,
                win_rate, avg_effective_cpm, last_delivery_at,
                performance_trend, cached_at) are accepted.

        Returns:
            True if a row was updated, False if no cache exists for
            the deal or no valid kwargs were provided.
        """
        allowed = {"impressions_delivered", "spend_to_date", "fill_rate",
                    "win_rate", "avg_effective_cpm", "last_delivery_at",
                    "performance_trend", "cached_at"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False

        set_clause = ", ".join(f"{col} = ?" for col in updates)
        values = list(updates.values())
        values.append(deal_id)

        with self._lock:
            # Update the most recent cache entry for this deal
            cursor = self._conn.execute(
                f"""UPDATE performance_cache SET {set_clause}
                    WHERE id = (
                        SELECT id FROM performance_cache
                        WHERE deal_id = ?
                        ORDER BY id DESC LIMIT 1
                    )""",
                values,
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def delete_performance_cache(self, deal_id: str) -> bool:
        """Delete all performance cache entries for a deal.

        Args:
            deal_id: The deal whose cache to delete.

        Returns:
            True if any rows were deleted, False if none existed.
        """
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM performance_cache WHERE deal_id = ?",
                (deal_id,),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Creative Assets (v3 — Campaign Automation)
    # ------------------------------------------------------------------

    def save_creative_asset(
        self,
        *,
        asset_id: Optional[str] = None,
        campaign_id: str,
        asset_name: str,
        asset_type: str,
        format_spec: Optional[dict] = None,
        source_url: Optional[str] = None,
        validation_status: str = "pending",
        validation_errors: Optional[list] = None,
    ) -> str:
        """Insert a new creative asset.

        Args:
            asset_id: Optional UUID. Generated if not provided.
            campaign_id: ID of the campaign this asset belongs to.
            asset_name: Human-readable name for the creative.
            asset_type: Type of creative (display, video, audio, interactive, native).
            format_spec: Format-specific metadata dict (varies by asset_type).
            source_url: URL where the creative file is hosted.
            validation_status: IAB spec validation status (pending, valid, invalid).
            validation_errors: List of validation error/warning messages.

        Returns:
            The asset ID (generated or provided).
        """
        if asset_id is None:
            asset_id = str(uuid.uuid4())
        now = _now_iso()

        format_spec_json = json.dumps(format_spec) if format_spec is not None else "{}"
        errors_json = json.dumps(validation_errors) if validation_errors is not None else "[]"

        with self._lock:
            self._conn.execute(
                """INSERT INTO creative_assets
                   (asset_id, campaign_id, asset_name, asset_type,
                    format_spec, source_url, validation_status,
                    validation_errors, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    asset_id, campaign_id, asset_name, asset_type,
                    format_spec_json, source_url, validation_status,
                    errors_json, now, now,
                ),
            )
            self._conn.commit()

        return asset_id

    def get_creative_asset(self, asset_id: str) -> Optional[dict[str, Any]]:
        """Retrieve a creative asset by ID.

        JSON fields (format_spec, validation_errors) are automatically
        deserialized.

        Args:
            asset_id: The asset's primary key.

        Returns:
            Asset as a dict with deserialized JSON fields, or None if not found.
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM creative_assets WHERE asset_id = ?",
                (asset_id,),
            )
            row = cursor.fetchone()

        if row is None:
            return None

        result = dict(row)
        # Deserialize JSON fields
        for field in ("format_spec", "validation_errors"):
            val = result.get(field)
            if isinstance(val, str):
                try:
                    result[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
        return result

    def list_creative_assets(
        self,
        *,
        campaign_id: Optional[str] = None,
        asset_type: Optional[str] = None,
        validation_status: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List creative assets with optional filters.

        Args:
            campaign_id: Filter by campaign ID.
            asset_type: Filter by asset type (display, video, etc.).
            validation_status: Filter by validation status (pending, valid, invalid).
            limit: Maximum rows to return.

        Returns:
            List of asset dicts ordered by created_at descending.
        """
        clauses: list[str] = []
        params: list[Any] = []

        if campaign_id is not None:
            clauses.append("campaign_id = ?")
            params.append(campaign_id)
        if asset_type is not None:
            clauses.append("asset_type = ?")
            params.append(asset_type)
        if validation_status is not None:
            clauses.append("validation_status = ?")
            params.append(validation_status)

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        query = f"SELECT * FROM creative_assets {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            cursor = self._conn.execute(query, params)
            rows = cursor.fetchall()

        results = []
        for row in rows:
            r = dict(row)
            # Deserialize JSON fields
            for field in ("format_spec", "validation_errors"):
                val = r.get(field)
                if isinstance(val, str):
                    try:
                        r[field] = json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        pass
            results.append(r)
        return results

    def update_creative_asset(self, asset_id: str, **kwargs: Any) -> bool:
        """Update specific fields on a creative asset.

        Automatically serializes format_spec (dict) and validation_errors
        (list) to JSON before writing.  Bumps ``updated_at``.

        Args:
            asset_id: The asset to update.
            **kwargs: Column-value pairs to update. Accepted columns:
                asset_name, asset_type, format_spec, source_url,
                validation_status, validation_errors, campaign_id.

        Returns:
            True if a row was updated, False if the asset was not found
            or no valid kwargs were provided.
        """
        allowed = {
            "asset_name", "asset_type", "format_spec", "source_url",
            "validation_status", "validation_errors", "campaign_id",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False

        # Serialize JSON fields
        if "format_spec" in updates and isinstance(updates["format_spec"], dict):
            updates["format_spec"] = json.dumps(updates["format_spec"])
        if "validation_errors" in updates and isinstance(updates["validation_errors"], list):
            updates["validation_errors"] = json.dumps(updates["validation_errors"])

        # Always bump updated_at
        updates["updated_at"] = _now_iso()

        set_clause = ", ".join(f"{col} = ?" for col in updates)
        values = list(updates.values())
        values.append(asset_id)

        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE creative_assets SET {set_clause} WHERE asset_id = ?",
                values,
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def delete_creative_asset(self, asset_id: str) -> bool:
        """Delete a creative asset by ID.

        Args:
            asset_id: The asset to delete.

        Returns:
            True if a row was deleted, False if not found.
        """
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM creative_assets WHERE asset_id = ?",
                (asset_id,),
            )
            self._conn.commit()
            return cursor.rowcount > 0

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
