# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SQLite-backed deal state persistence.

Uses synchronous sqlite3 (not aiosqlite) because CrewAI runs flows in
worker threads that may not have an asyncio event loop.  Thread safety
is provided by check_same_thread=False and a threading.Lock().

``DealStore`` is the composition root ("facade") for deal-lifecycle
persistence.  It owns the SQLite connection and lock and keeps the core
deal CRUD (save/get/list/update-status) inline.  Every other aggregate --
negotiation rounds, booking records, jobs, events, status transitions,
portfolio metadata, deal activations, performance cache, creative assets,
deal templates, and supply path templates -- lives in its own focused
store module under ``ad_buyer.storage`` (EP-2.4 god-class
split).  Those stores share this facade's single connection and lock, so
the public API, table layout, SQL, and thread-safety semantics are
unchanged; callers that use ``deal_store.<method>()`` continue to work
without modification.
"""

import logging
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from ..models.state_machine import (
    BuyerDealStatus,
    DealStateMachine,
)
from .booking_record_store import BookingRecordStore
from .creative_asset_store import CreativeAssetStore
from .deal_activation_store import DealActivationStore
from .deal_event_store import DealEventStore
from .deal_template_store import DealTemplateStore
from .job_store import JobStore
from .negotiation_store import NegotiationStore
from .performance_cache_store import PerformanceCacheStore
from .portfolio_metadata_store import PortfolioMetadataStore
from .schema import initialize_schema
from .status_transition_store import StatusTransitionStore
from .supply_path_template_store import SupplyPathTemplateStore

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class DealStore:
    """SQLite-backed facade for deal state, negotiations, bookings, and jobs.

    Thread-safe via a shared lock. Uses WAL mode for concurrent
    read/write access. All public methods are synchronous.

    Core deal CRUD is implemented inline; the remaining aggregate
    operations are delegated to focused stores (composed in
    :meth:`connect`) that share this instance's connection and lock.

    Args:
        database_url: SQLite connection string (e.g. ``sqlite:///./ad_buyer.db``
            or ``sqlite:///:memory:`` for testing).
    """

    def __init__(self, database_url: str) -> None:
        self._db_path = self._parse_url(database_url)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

        # Composed aggregate stores. Wired in connect() once the shared
        # connection exists; None until then (methods are only usable
        # after connect(), matching the pre-split behavior).
        self._negotiation_store: NegotiationStore | None = None
        self._booking_record_store: BookingRecordStore | None = None
        self._job_store: JobStore | None = None
        self._event_store: DealEventStore | None = None
        self._status_store: StatusTransitionStore | None = None
        self._portfolio_store: PortfolioMetadataStore | None = None
        self._activation_store: DealActivationStore | None = None
        self._performance_store: PerformanceCacheStore | None = None
        self._creative_asset_store: CreativeAssetStore | None = None
        self._deal_template_store: DealTemplateStore | None = None
        self._supply_path_template_store: SupplyPathTemplateStore | None = None

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
        self._wire_stores()

    def disconnect(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._negotiation_store = None
        self._booking_record_store = None
        self._job_store = None
        self._event_store = None
        self._status_store = None
        self._portfolio_store = None
        self._activation_store = None
        self._performance_store = None
        self._creative_asset_store = None
        self._deal_template_store = None
        self._supply_path_template_store = None

    def _wire_stores(self) -> None:
        """Construct the composed aggregate stores over the shared connection.

        Every sub-store receives this facade's live connection and lock,
        so all reads/writes flow through a single connection serialized by
        one lock -- identical to the pre-split monolith.
        """
        conn = self._conn
        lock = self._lock
        self._negotiation_store = NegotiationStore(conn, lock)
        self._booking_record_store = BookingRecordStore(conn, lock)
        self._job_store = JobStore(conn, lock)
        self._event_store = DealEventStore(conn, lock)
        self._status_store = StatusTransitionStore(conn, lock)
        self._portfolio_store = PortfolioMetadataStore(conn, lock)
        self._activation_store = DealActivationStore(conn, lock)
        self._performance_store = PerformanceCacheStore(conn, lock)
        self._creative_asset_store = CreativeAssetStore(conn, lock)
        self._deal_template_store = DealTemplateStore(conn, lock)
        self._supply_path_template_store = SupplyPathTemplateStore(conn, lock)

    # ------------------------------------------------------------------
    # Deals
    # ------------------------------------------------------------------

    # All v2 intrinsic column names on the deals table, used to build
    # dynamic INSERT statements when v2 kwargs are provided.
    _V2_DEAL_COLUMNS = (
        # Counterparty fields
        "display_name",
        "description",
        "buyer_org",
        "buyer_id",
        "seller_org",
        "seller_id",
        "seller_domain",
        "seller_type",
        # Pricing detail fields
        "price_model",
        "bid_floor_cpm",
        "fixed_price_cpm",
        "cpp",
        "guaranteed_grps",
        "currency",
        "fee_transparency",
        # Inventory targeting fields
        "media_type",
        "formats",
        "content_categories",
        "publisher_domains",
        "geo_targets",
        "dayparts",
        "programs",
        "networks",
        "audience_segments",
        "estimated_volume",
        # Lifecycle extensions
        "deprecated_at",
        "deprecated_reason",
        "parent_deal_id",
        # Supply chain fields
        "schain_complete",
        "schain_nodes",
        "sellers_json_url",
        "is_direct",
        "hop_count",
        "inventory_fingerprint",
        # Linear TV fields
        "makegood_provisions",
        "cancellation_window",
        "audience_guarantee",
        "preemption_rights",
        "agency_of_record_status",
    )

    def save_deal(
        self,
        *,
        deal_id: str | None = None,
        seller_url: str,
        product_id: str,
        product_name: str = "",
        deal_type: str = "PD",
        status: str = "draft",
        seller_deal_id: str | None = None,
        price: float | None = None,
        original_price: float | None = None,
        impressions: int | None = None,
        flight_start: str | None = None,
        flight_end: str | None = None,
        buyer_context: str | None = None,
        metadata: str | None = None,
        # v2 counterparty fields
        display_name: str | None = None,
        description: str | None = None,
        buyer_org: str | None = None,
        buyer_id: str | None = None,
        seller_org: str | None = None,
        seller_id: str | None = None,
        seller_domain: str | None = None,
        seller_type: str | None = None,
        # v2 pricing detail fields
        price_model: str | None = None,
        bid_floor_cpm: float | None = None,
        fixed_price_cpm: float | None = None,
        cpp: float | None = None,
        guaranteed_grps: float | None = None,
        currency: str | None = None,
        fee_transparency: float | None = None,
        # v2 inventory targeting fields
        media_type: str | None = None,
        formats: str | None = None,
        content_categories: str | None = None,
        publisher_domains: str | None = None,
        geo_targets: str | None = None,
        dayparts: str | None = None,
        programs: str | None = None,
        networks: str | None = None,
        audience_segments: str | None = None,
        estimated_volume: int | None = None,
        # v2 lifecycle extensions
        deprecated_at: str | None = None,
        deprecated_reason: str | None = None,
        parent_deal_id: str | None = None,
        # v2 supply chain fields
        schain_complete: int | None = None,
        schain_nodes: str | None = None,
        sellers_json_url: str | None = None,
        is_direct: int | None = None,
        hop_count: int | None = None,
        inventory_fingerprint: str | None = None,
        # v2 linear TV fields
        makegood_provisions: str | None = None,
        cancellation_window: str | None = None,
        audience_guarantee: str | None = None,
        preemption_rights: str | None = None,
        agency_of_record_status: str | None = None,
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
            "id",
            "seller_url",
            "seller_deal_id",
            "product_id",
            "product_name",
            "deal_type",
            "status",
            "price",
            "original_price",
            "impressions",
            "flight_start",
            "flight_end",
            "buyer_context",
            "metadata",
            "created_at",
            "updated_at",
        ]
        values: list[Any] = [
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
        ]

        # Collect v2 kwargs into a dict for dynamic column building.
        v2_locals = {
            "display_name": display_name,
            "description": description,
            "buyer_org": buyer_org,
            "buyer_id": buyer_id,
            "seller_org": seller_org,
            "seller_id": seller_id,
            "seller_domain": seller_domain,
            "seller_type": seller_type,
            "price_model": price_model,
            "bid_floor_cpm": bid_floor_cpm,
            "fixed_price_cpm": fixed_price_cpm,
            "cpp": cpp,
            "guaranteed_grps": guaranteed_grps,
            "currency": currency,
            "fee_transparency": fee_transparency,
            "media_type": media_type,
            "formats": formats,
            "content_categories": content_categories,
            "publisher_domains": publisher_domains,
            "geo_targets": geo_targets,
            "dayparts": dayparts,
            "programs": programs,
            "networks": networks,
            "audience_segments": audience_segments,
            "estimated_volume": estimated_volume,
            "deprecated_at": deprecated_at,
            "deprecated_reason": deprecated_reason,
            "parent_deal_id": parent_deal_id,
            "schain_complete": schain_complete,
            "schain_nodes": schain_nodes,
            "sellers_json_url": sellers_json_url,
            "is_direct": is_direct,
            "hop_count": hop_count,
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

    def get_deal(self, deal_id: str) -> dict[str, Any] | None:
        """Retrieve a deal by ID.

        Args:
            deal_id: The deal's primary key.

        Returns:
            Deal as a dict, or None if not found.
        """
        with self._lock:
            cursor = self._conn.execute("SELECT * FROM deals WHERE id = ?", (deal_id,))
            row = cursor.fetchone()
        return dict(row) if row else None

    def list_deals(
        self,
        *,
        status: str | None = None,
        seller_url: str | None = None,
        created_after: str | None = None,
        media_type: str | None = None,
        seller_domain: str | None = None,
        deal_type: str | None = None,
        advertiser_id: str | None = None,
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
            query = f"SELECT d.* FROM deals d {where} ORDER BY d.created_at DESC LIMIT ?"
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
            cursor = self._conn.execute("SELECT status FROM deals WHERE id = ?", (deal_id,))
            row = cursor.fetchone()
            if row is None:
                return False

            old_status = row["status"]

            # Enforce state machine if both statuses are known
            try:
                old_deal_status = BuyerDealStatus(old_status)
                new_deal_status = BuyerDealStatus(new_status)
                # Build a throwaway machine to validate the transition
                sm = DealStateMachine(deal_id, initial_status=old_deal_status)
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
    # Delegated aggregate operations
    #
    # The following methods forward verbatim to the composed aggregate
    # stores wired in connect().  They preserve the historical DealStore
    # public API so existing ``deal_store.<method>()`` call sites and test
    # fixtures keep working unchanged after the god-class split.
    # ------------------------------------------------------------------

    # -- Negotiation rounds ------------------------------------------------

    def save_negotiation_round(self, *args: Any, **kwargs: Any) -> int:
        """Delegates to :meth:`NegotiationStore.save_negotiation_round`."""
        return self._negotiation_store.save_negotiation_round(*args, **kwargs)

    def get_negotiation_history(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Delegates to :meth:`NegotiationStore.get_negotiation_history`."""
        return self._negotiation_store.get_negotiation_history(*args, **kwargs)

    # -- Booking records ---------------------------------------------------

    def save_booking_record(self, *args: Any, **kwargs: Any) -> int:
        """Delegates to :meth:`BookingRecordStore.save_booking_record`."""
        return self._booking_record_store.save_booking_record(*args, **kwargs)

    def get_booking_records(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Delegates to :meth:`BookingRecordStore.get_booking_records`."""
        return self._booking_record_store.get_booking_records(*args, **kwargs)

    # -- Jobs --------------------------------------------------------------

    def save_job(self, *args: Any, **kwargs: Any) -> str:
        """Delegates to :meth:`JobStore.save_job`."""
        return self._job_store.save_job(*args, **kwargs)

    def get_job(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        """Delegates to :meth:`JobStore.get_job`."""
        return self._job_store.get_job(*args, **kwargs)

    def list_jobs(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Delegates to :meth:`JobStore.list_jobs`."""
        return self._job_store.list_jobs(*args, **kwargs)

    # -- Events ------------------------------------------------------------

    def save_event(self, *args: Any, **kwargs: Any) -> str:
        """Delegates to :meth:`DealEventStore.save_event`."""
        return self._event_store.save_event(*args, **kwargs)

    def get_event(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        """Delegates to :meth:`DealEventStore.get_event`."""
        return self._event_store.get_event(*args, **kwargs)

    def list_events(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Delegates to :meth:`DealEventStore.list_events`."""
        return self._event_store.list_events(*args, **kwargs)

    # -- Status transitions ------------------------------------------------

    def record_status_transition(self, *args: Any, **kwargs: Any) -> int:
        """Delegates to :meth:`StatusTransitionStore.record_status_transition`."""
        return self._status_store.record_status_transition(*args, **kwargs)

    def get_status_history(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Delegates to :meth:`StatusTransitionStore.get_status_history`."""
        return self._status_store.get_status_history(*args, **kwargs)

    # -- Portfolio metadata (v2) -------------------------------------------

    def save_portfolio_metadata(self, *args: Any, **kwargs: Any) -> int:
        """Delegates to :meth:`PortfolioMetadataStore.save_portfolio_metadata`."""
        return self._portfolio_store.save_portfolio_metadata(*args, **kwargs)

    def get_portfolio_metadata(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        """Delegates to :meth:`PortfolioMetadataStore.get_portfolio_metadata`."""
        return self._portfolio_store.get_portfolio_metadata(*args, **kwargs)

    def update_portfolio_metadata(self, *args: Any, **kwargs: Any) -> bool:
        """Delegates to :meth:`PortfolioMetadataStore.update_portfolio_metadata`."""
        return self._portfolio_store.update_portfolio_metadata(*args, **kwargs)

    def delete_portfolio_metadata(self, *args: Any, **kwargs: Any) -> bool:
        """Delegates to :meth:`PortfolioMetadataStore.delete_portfolio_metadata`."""
        return self._portfolio_store.delete_portfolio_metadata(*args, **kwargs)

    # -- Deal activations (v2) ---------------------------------------------

    def save_deal_activation(self, *args: Any, **kwargs: Any) -> int:
        """Delegates to :meth:`DealActivationStore.save_deal_activation`."""
        return self._activation_store.save_deal_activation(*args, **kwargs)

    def get_deal_activations(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Delegates to :meth:`DealActivationStore.get_deal_activations`."""
        return self._activation_store.get_deal_activations(*args, **kwargs)

    def update_deal_activation(self, *args: Any, **kwargs: Any) -> bool:
        """Delegates to :meth:`DealActivationStore.update_deal_activation`."""
        return self._activation_store.update_deal_activation(*args, **kwargs)

    def delete_deal_activation(self, *args: Any, **kwargs: Any) -> bool:
        """Delegates to :meth:`DealActivationStore.delete_deal_activation`."""
        return self._activation_store.delete_deal_activation(*args, **kwargs)

    # -- Performance cache (v2) --------------------------------------------

    def save_performance_cache(self, *args: Any, **kwargs: Any) -> int:
        """Delegates to :meth:`PerformanceCacheStore.save_performance_cache`."""
        return self._performance_store.save_performance_cache(*args, **kwargs)

    def get_performance_cache(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        """Delegates to :meth:`PerformanceCacheStore.get_performance_cache`."""
        return self._performance_store.get_performance_cache(*args, **kwargs)

    def update_performance_cache(self, *args: Any, **kwargs: Any) -> bool:
        """Delegates to :meth:`PerformanceCacheStore.update_performance_cache`."""
        return self._performance_store.update_performance_cache(*args, **kwargs)

    def delete_performance_cache(self, *args: Any, **kwargs: Any) -> bool:
        """Delegates to :meth:`PerformanceCacheStore.delete_performance_cache`."""
        return self._performance_store.delete_performance_cache(*args, **kwargs)

    # -- Creative assets (v3) ----------------------------------------------

    def save_creative_asset(self, *args: Any, **kwargs: Any) -> str:
        """Delegates to :meth:`CreativeAssetStore.save_creative_asset`."""
        return self._creative_asset_store.save_creative_asset(*args, **kwargs)

    def get_creative_asset(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        """Delegates to :meth:`CreativeAssetStore.get_creative_asset`."""
        return self._creative_asset_store.get_creative_asset(*args, **kwargs)

    def list_creative_assets(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Delegates to :meth:`CreativeAssetStore.list_creative_assets`."""
        return self._creative_asset_store.list_creative_assets(*args, **kwargs)

    def update_creative_asset(self, *args: Any, **kwargs: Any) -> bool:
        """Delegates to :meth:`CreativeAssetStore.update_creative_asset`."""
        return self._creative_asset_store.update_creative_asset(*args, **kwargs)

    def delete_creative_asset(self, *args: Any, **kwargs: Any) -> bool:
        """Delegates to :meth:`CreativeAssetStore.delete_creative_asset`."""
        return self._creative_asset_store.delete_creative_asset(*args, **kwargs)

    # -- Deal templates (v5) -----------------------------------------------

    def save_deal_template(self, *args: Any, **kwargs: Any) -> str:
        """Delegates to :meth:`DealTemplateStore.save_deal_template`."""
        return self._deal_template_store.save_deal_template(*args, **kwargs)

    def get_deal_template(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        """Delegates to :meth:`DealTemplateStore.get_deal_template`."""
        return self._deal_template_store.get_deal_template(*args, **kwargs)

    def list_deal_templates(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Delegates to :meth:`DealTemplateStore.list_deal_templates`."""
        return self._deal_template_store.list_deal_templates(*args, **kwargs)

    def update_deal_template(self, *args: Any, **kwargs: Any) -> bool:
        """Delegates to :meth:`DealTemplateStore.update_deal_template`."""
        return self._deal_template_store.update_deal_template(*args, **kwargs)

    def delete_deal_template(self, *args: Any, **kwargs: Any) -> bool:
        """Delegates to :meth:`DealTemplateStore.delete_deal_template`."""
        return self._deal_template_store.delete_deal_template(*args, **kwargs)

    # -- Supply path templates (v5) ----------------------------------------

    def save_supply_path_template(self, *args: Any, **kwargs: Any) -> str:
        """Delegates to :meth:`SupplyPathTemplateStore.save_supply_path_template`."""
        return self._supply_path_template_store.save_supply_path_template(*args, **kwargs)

    def get_supply_path_template(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        """Delegates to :meth:`SupplyPathTemplateStore.get_supply_path_template`."""
        return self._supply_path_template_store.get_supply_path_template(*args, **kwargs)

    def list_supply_path_templates(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Delegates to :meth:`SupplyPathTemplateStore.list_supply_path_templates`."""
        return self._supply_path_template_store.list_supply_path_templates(*args, **kwargs)

    def update_supply_path_template(self, *args: Any, **kwargs: Any) -> bool:
        """Delegates to :meth:`SupplyPathTemplateStore.update_supply_path_template`."""
        return self._supply_path_template_store.update_supply_path_template(*args, **kwargs)

    def delete_supply_path_template(self, *args: Any, **kwargs: Any) -> bool:
        """Delegates to :meth:`SupplyPathTemplateStore.delete_supply_path_template`."""
        return self._supply_path_template_store.delete_supply_path_template(*args, **kwargs)

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
            return database_url[len("sqlite:///") :]
        return database_url
