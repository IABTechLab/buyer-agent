# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SQLite-backed campaign state persistence.

Provides CRUD operations for the campaign automation data model:
- campaigns: Core campaign records with status, brief, budget, flight dates
- pacing_snapshots: Periodic pacing data points per campaign
- creative_assets: Creative files and metadata per campaign
- ad_server_campaigns: Ad server integration records (Innovid/Flashtalking)
- campaign_events: Lifecycle events emitted during state transitions

Integrates with CampaignAutomationStateMachine (buyer-0u9) to validate
all status transitions before persisting them.  Lifecycle convenience
methods (create_campaign, start_planning, start_booking, mark_ready,
activate_campaign, pause_campaign, resume_campaign, complete_campaign,
cancel_campaign) combine validation, DB update, and event emission.

Thread safety is provided by check_same_thread=False and a threading.Lock(),
matching the DealStore pattern.
"""

import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from ..models.state_machine import (
    CampaignAutomationStateMachine,
    CampaignStatus,
    InvalidTransitionError,
)
from .schema import initialize_schema

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class CampaignStore:
    """SQLite-backed store for campaigns, pacing, creative assets, and ad server bindings.

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
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        initialize_schema(self._conn)
        self._create_campaign_events_table()

    def disconnect(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @staticmethod
    def _parse_url(url: str) -> str:
        """Extract the database path from a SQLite URL.

        Supports ``sqlite:///path`` and ``sqlite:///:memory:`` formats.
        """
        if url.startswith("sqlite:///"):
            return url[len("sqlite:///"):]
        return url

    def _create_campaign_events_table(self) -> None:
        """Create the campaign_events table if it doesn't exist."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS campaign_events (
                event_id        TEXT PRIMARY KEY,
                campaign_id     TEXT NOT NULL,
                event_type      TEXT NOT NULL,
                timestamp       TEXT NOT NULL,
                from_status     TEXT,
                to_status       TEXT,
                payload         TEXT DEFAULT '{}',
                FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_campaign_events_campaign_id "
            "ON campaign_events(campaign_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_campaign_events_timestamp "
            "ON campaign_events(timestamp)"
        )
        self._conn.commit()

    def _emit_event(
        self,
        campaign_id: str,
        event_type: str,
        from_status: Optional[str] = None,
        to_status: Optional[str] = None,
        payload: Optional[str] = None,
    ) -> str:
        """Record a campaign lifecycle event.

        Args:
            campaign_id: The campaign this event belongs to.
            event_type: Event type string (e.g. ``campaign.created``).
            from_status: Previous status (for transitions).
            to_status: New status (for transitions).
            payload: Optional JSON payload string.

        Returns:
            The generated event_id.
        """
        event_id = str(uuid.uuid4())
        now = _now_iso()

        with self._lock:
            self._conn.execute(
                """INSERT INTO campaign_events
                   (event_id, campaign_id, event_type, timestamp,
                    from_status, to_status, payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (event_id, campaign_id, event_type, now,
                 from_status, to_status, payload or "{}"),
            )
            self._conn.commit()

        return event_id

    # ------------------------------------------------------------------
    # Campaign lifecycle events
    # ------------------------------------------------------------------

    def get_campaign_events(
        self,
        campaign_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Retrieve lifecycle events for a campaign, oldest first.

        Args:
            campaign_id: The campaign to query.
            limit: Maximum number of events to return.

        Returns:
            List of event dicts ordered by timestamp ascending.
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM campaign_events WHERE campaign_id = ? "
                "ORDER BY timestamp ASC LIMIT ?",
                (campaign_id, limit),
            )
            rows = cursor.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # State machine integration
    # ------------------------------------------------------------------

    # Map CampaignStatus transitions to event type strings.
    _TRANSITION_EVENT_MAP: dict[CampaignStatus, str] = {
        CampaignStatus.PLANNING: "campaign.plan_generated",
        CampaignStatus.BOOKING: "campaign.booking_started",
        CampaignStatus.READY: "campaign.ready",
        CampaignStatus.ACTIVE: "campaign.activated",
        CampaignStatus.COMPLETED: "campaign.completed",
        CampaignStatus.CANCELED: "campaign.canceled",
        CampaignStatus.PAUSED: "campaign.paused",
        CampaignStatus.PACING_HOLD: "campaign.pacing_hold",
    }

    def transition_campaign_status(
        self,
        campaign_id: str,
        new_status: CampaignStatus,
    ) -> bool:
        """Validate and execute a campaign status transition.

        Uses the CampaignAutomationStateMachine to validate that the
        transition is legal before updating the database.

        Args:
            campaign_id: The campaign to transition.
            new_status: Target CampaignStatus.

        Returns:
            True if the transition succeeded.

        Raises:
            KeyError: If the campaign does not exist.
            InvalidTransitionError: If the transition is not valid.
        """
        campaign = self.get_campaign(campaign_id)
        if campaign is None:
            raise KeyError(f"Campaign not found: {campaign_id}")

        current_status = CampaignStatus(campaign["status"])

        # Build a transient state machine at the current status to validate.
        sm = CampaignAutomationStateMachine(
            order_id=campaign_id,
            initial_status=current_status,
        )
        # This raises InvalidTransitionError if the transition is illegal.
        sm.transition(new_status)

        # Persist the transition.
        self.update_campaign_status(campaign_id, new_status.value)

        # Emit event.
        event_type = self._TRANSITION_EVENT_MAP.get(new_status)
        if event_type:
            self._emit_event(
                campaign_id=campaign_id,
                event_type=event_type,
                from_status=current_status.value,
                to_status=new_status.value,
            )

        return True

    # ------------------------------------------------------------------
    # Campaign lifecycle convenience methods
    # ------------------------------------------------------------------

    def create_campaign(self, brief: dict[str, Any]) -> str:
        """Create a new campaign in DRAFT status from a brief dict.

        Args:
            brief: Dict with campaign brief fields (advertiser_id,
                campaign_name, total_budget, currency, flight_start,
                flight_end, and optional channels, target_audience,
                target_geo, kpis, brand_safety, approval_config).

        Returns:
            The new campaign_id.
        """
        campaign_id = self.save_campaign(
            advertiser_id=brief["advertiser_id"],
            campaign_name=brief["campaign_name"],
            status=CampaignStatus.DRAFT.value,
            total_budget=brief["total_budget"],
            currency=brief.get("currency", "USD"),
            flight_start=brief["flight_start"],
            flight_end=brief["flight_end"],
            channels=brief.get("channels"),
            target_audience=brief.get("target_audience"),
            target_geo=brief.get("target_geo"),
            kpis=brief.get("kpis"),
            brand_safety=brief.get("brand_safety"),
            approval_config=brief.get("approval_config"),
        )
        self._emit_event(
            campaign_id=campaign_id,
            event_type="campaign.created",
            to_status=CampaignStatus.DRAFT.value,
        )
        return campaign_id

    def start_planning(self, campaign_id: str) -> None:
        """Transition campaign from DRAFT to PLANNING.

        Args:
            campaign_id: Campaign to transition.

        Raises:
            KeyError: If the campaign does not exist.
            InvalidTransitionError: If current status is not DRAFT.
        """
        self.transition_campaign_status(campaign_id, CampaignStatus.PLANNING)

    def start_booking(self, campaign_id: str) -> None:
        """Transition campaign from PLANNING to BOOKING.

        Args:
            campaign_id: Campaign to transition.

        Raises:
            KeyError: If the campaign does not exist.
            InvalidTransitionError: If current status is not PLANNING.
        """
        self.transition_campaign_status(campaign_id, CampaignStatus.BOOKING)

    def mark_ready(self, campaign_id: str) -> None:
        """Transition campaign from BOOKING to READY.

        Args:
            campaign_id: Campaign to transition.

        Raises:
            KeyError: If the campaign does not exist.
            InvalidTransitionError: If current status is not BOOKING.
        """
        self.transition_campaign_status(campaign_id, CampaignStatus.READY)

    def activate_campaign(self, campaign_id: str) -> None:
        """Transition campaign from READY to ACTIVE.

        Args:
            campaign_id: Campaign to transition.

        Raises:
            KeyError: If the campaign does not exist.
            InvalidTransitionError: If current status is not READY.
        """
        self.transition_campaign_status(campaign_id, CampaignStatus.ACTIVE)

    def pause_campaign(self, campaign_id: str) -> None:
        """Transition campaign from ACTIVE to PAUSED.

        Args:
            campaign_id: Campaign to transition.

        Raises:
            KeyError: If the campaign does not exist.
            InvalidTransitionError: If current status is not ACTIVE.
        """
        self.transition_campaign_status(campaign_id, CampaignStatus.PAUSED)

    def resume_campaign(self, campaign_id: str) -> None:
        """Transition campaign from PAUSED to ACTIVE.

        Args:
            campaign_id: Campaign to transition.

        Raises:
            KeyError: If the campaign does not exist.
            InvalidTransitionError: If current status is not PAUSED.
        """
        self.transition_campaign_status(campaign_id, CampaignStatus.ACTIVE)

    def complete_campaign(self, campaign_id: str) -> None:
        """Transition campaign from ACTIVE to COMPLETED.

        Args:
            campaign_id: Campaign to transition.

        Raises:
            KeyError: If the campaign does not exist.
            InvalidTransitionError: If current status is not ACTIVE.
        """
        self.transition_campaign_status(campaign_id, CampaignStatus.COMPLETED)

    def cancel_campaign(self, campaign_id: str) -> None:
        """Transition campaign to CANCELED from any non-terminal state.

        Note: DRAFT -> CANCELED is not a valid transition per the state
        machine. Cancel is available from PLANNING, BOOKING, READY,
        ACTIVE, PAUSED, and PACING_HOLD.

        Args:
            campaign_id: Campaign to cancel.

        Raises:
            KeyError: If the campaign does not exist.
            InvalidTransitionError: If already in a terminal state or DRAFT.
        """
        self.transition_campaign_status(campaign_id, CampaignStatus.CANCELED)

    # ------------------------------------------------------------------
    # Campaigns (low-level CRUD)
    # ------------------------------------------------------------------

    def save_campaign(
        self,
        *,
        campaign_id: Optional[str] = None,
        advertiser_id: str,
        campaign_name: str,
        status: str = "DRAFT",
        total_budget: float,
        currency: str = "USD",
        flight_start: str,
        flight_end: str,
        channels: Optional[str] = None,
        target_audience: Optional[str] = None,
        target_geo: Optional[str] = None,
        kpis: Optional[str] = None,
        brand_safety: Optional[str] = None,
        approval_config: Optional[str] = None,
    ) -> str:
        """Insert a new campaign record.

        Args:
            campaign_id: Optional UUID. Generated if not provided.
            advertiser_id: Which advertiser this campaign is for.
            campaign_name: Human-readable campaign name.
            status: Campaign status (default DRAFT).
            total_budget: Total campaign budget.
            currency: ISO 4217 currency code (default USD).
            flight_start: Campaign start date (ISO string).
            flight_end: Campaign end date (ISO string).
            channels: JSON array of channel allocations.
            target_audience: JSON array of audience segment IDs.
            target_geo: JSON array of geo targets.
            kpis: JSON array of KPI definitions.
            brand_safety: JSON object for brand safety requirements.
            approval_config: JSON object for approval gate configuration.

        Returns:
            The campaign_id (generated or provided).
        """
        if campaign_id is None:
            campaign_id = str(uuid.uuid4())
        now = _now_iso()

        with self._lock:
            self._conn.execute(
                """INSERT INTO campaigns (
                    campaign_id, advertiser_id, campaign_name, status,
                    total_budget, currency, flight_start, flight_end,
                    channels, target_audience, target_geo, kpis,
                    brand_safety, approval_config, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    campaign_id, advertiser_id, campaign_name, status,
                    total_budget, currency, flight_start, flight_end,
                    channels, target_audience, target_geo, kpis,
                    brand_safety, approval_config, now, now,
                ),
            )
            self._conn.commit()

        return campaign_id

    def get_campaign(self, campaign_id: str) -> Optional[dict[str, Any]]:
        """Retrieve a campaign by ID.

        Args:
            campaign_id: The campaign's primary key.

        Returns:
            Campaign as a dict, or None if not found.
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM campaigns WHERE campaign_id = ?",
                (campaign_id,),
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def list_campaigns(
        self,
        *,
        status: Optional[str] = None,
        advertiser_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List campaigns with optional filters.

        Args:
            status: Filter by campaign status.
            advertiser_id: Filter by advertiser ID.
            limit: Maximum rows to return.

        Returns:
            List of campaign dicts ordered by created_at descending.
        """
        clauses: list[str] = []
        params: list[Any] = []

        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if advertiser_id is not None:
            clauses.append("advertiser_id = ?")
            params.append(advertiser_id)

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        query = f"SELECT * FROM campaigns {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            cursor = self._conn.execute(query, params)
            rows = cursor.fetchall()
        return [dict(r) for r in rows]

    def update_campaign_status(
        self,
        campaign_id: str,
        new_status: str,
    ) -> bool:
        """Update a campaign's status.

        Args:
            campaign_id: The campaign to update.
            new_status: Target status value.

        Returns:
            True if the campaign was found and updated, False otherwise.
        """
        now = _now_iso()

        with self._lock:
            cursor = self._conn.execute(
                "SELECT campaign_id FROM campaigns WHERE campaign_id = ?",
                (campaign_id,),
            )
            if cursor.fetchone() is None:
                return False

            self._conn.execute(
                "UPDATE campaigns SET status = ?, updated_at = ? WHERE campaign_id = ?",
                (new_status, now, campaign_id),
            )
            self._conn.commit()

        return True

    def update_campaign(
        self,
        campaign_id: str,
        **kwargs: Any,
    ) -> bool:
        """Update specified fields on a campaign.

        Args:
            campaign_id: The campaign to update.
            **kwargs: Column name/value pairs to update. Only known
                campaign columns are accepted.

        Returns:
            True if the campaign was found and updated, False otherwise.
        """
        allowed = {
            "campaign_name", "status", "total_budget", "currency",
            "flight_start", "flight_end", "channels", "target_audience",
            "target_geo", "kpis", "brand_safety", "approval_config",
            "advertiser_id",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False

        now = _now_iso()
        updates["updated_at"] = now

        set_clause = ", ".join(f"{col} = ?" for col in updates)
        values = list(updates.values()) + [campaign_id]

        with self._lock:
            cursor = self._conn.execute(
                "SELECT campaign_id FROM campaigns WHERE campaign_id = ?",
                (campaign_id,),
            )
            if cursor.fetchone() is None:
                return False

            self._conn.execute(
                f"UPDATE campaigns SET {set_clause} WHERE campaign_id = ?",
                values,
            )
            self._conn.commit()

        return True

    # ------------------------------------------------------------------
    # Pacing Snapshots
    # ------------------------------------------------------------------

    def save_pacing_snapshot(
        self,
        *,
        snapshot_id: Optional[str] = None,
        campaign_id: str,
        timestamp: str,
        channel: Optional[str] = None,
        budget_allocated: Optional[float] = None,
        budget_spent: Optional[float] = None,
        impressions_target: Optional[int] = None,
        impressions_delivered: Optional[int] = None,
        pacing_percentage: Optional[float] = None,
        deviation: Optional[float] = None,
        data_source: Optional[str] = None,
        data_freshness: Optional[str] = None,
    ) -> str:
        """Insert a new pacing snapshot.

        Args:
            snapshot_id: Optional UUID. Generated if not provided.
            campaign_id: FK to campaigns table.
            timestamp: When this snapshot was taken (ISO string).
            channel: Channel this snapshot covers (CTV, DISPLAY, etc.).
            budget_allocated: Budget allocated to this channel/campaign.
            budget_spent: Budget spent to date.
            impressions_target: Target impressions.
            impressions_delivered: Impressions delivered to date.
            pacing_percentage: Spend / expected spend * 100.
            deviation: Pacing deviation percentage.
            data_source: Where the data came from (e.g. dsp_report).
            data_freshness: Timestamp of source data.

        Returns:
            The snapshot_id (generated or provided).
        """
        if snapshot_id is None:
            snapshot_id = str(uuid.uuid4())

        with self._lock:
            self._conn.execute(
                """INSERT INTO pacing_snapshots (
                    snapshot_id, campaign_id, timestamp, channel,
                    budget_allocated, budget_spent,
                    impressions_target, impressions_delivered,
                    pacing_percentage, deviation,
                    data_source, data_freshness
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot_id, campaign_id, timestamp, channel,
                    budget_allocated, budget_spent,
                    impressions_target, impressions_delivered,
                    pacing_percentage, deviation,
                    data_source, data_freshness,
                ),
            )
            self._conn.commit()

        return snapshot_id

    def get_pacing_snapshot(
        self, snapshot_id: str
    ) -> Optional[dict[str, Any]]:
        """Retrieve a pacing snapshot by ID.

        Args:
            snapshot_id: The snapshot's primary key.

        Returns:
            Snapshot as a dict, or None if not found.
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM pacing_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def list_pacing_snapshots(
        self,
        *,
        campaign_id: str,
        channel: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List pacing snapshots for a campaign.

        Args:
            campaign_id: Filter by campaign ID.
            channel: Optional channel filter.
            limit: Maximum rows to return.

        Returns:
            List of snapshot dicts ordered by timestamp descending.
        """
        clauses = ["campaign_id = ?"]
        params: list[Any] = [campaign_id]

        if channel is not None:
            clauses.append("channel = ?")
            params.append(channel)

        where = "WHERE " + " AND ".join(clauses)
        query = f"SELECT * FROM pacing_snapshots {where} ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            cursor = self._conn.execute(query, params)
            rows = cursor.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Creative Assets
    # ------------------------------------------------------------------

    def save_creative_asset(
        self,
        *,
        asset_id: Optional[str] = None,
        campaign_id: str,
        asset_name: str,
        asset_type: str,
        format_spec: Optional[str] = None,
        source_url: Optional[str] = None,
        validation_status: Optional[str] = None,
        validation_errors: Optional[str] = None,
    ) -> str:
        """Insert a new creative asset.

        Args:
            asset_id: Optional UUID. Generated if not provided.
            campaign_id: FK to campaigns table.
            asset_name: Human-readable name.
            asset_type: display, video, audio, interactive, or native.
            format_spec: JSON object with format details (size, duration, etc.).
            source_url: Original creative file URL.
            validation_status: Current validation status.
            validation_errors: JSON array of validation issues.

        Returns:
            The asset_id (generated or provided).
        """
        if asset_id is None:
            asset_id = str(uuid.uuid4())
        now = _now_iso()

        # Build column list dynamically so that omitted optional fields
        # (e.g. validation_status) use the DB DEFAULT value.
        columns = ["asset_id", "campaign_id", "asset_name", "asset_type",
                    "format_spec", "source_url", "created_at"]
        values: list[Any] = [asset_id, campaign_id, asset_name, asset_type,
                             format_spec, source_url, now]

        if validation_status is not None:
            columns.append("validation_status")
            values.append(validation_status)
        if validation_errors is not None:
            columns.append("validation_errors")
            values.append(validation_errors)

        placeholders = ", ".join("?" for _ in columns)
        col_names = ", ".join(columns)

        with self._lock:
            self._conn.execute(
                f"INSERT INTO creative_assets ({col_names}) VALUES ({placeholders})",
                values,
            )
            self._conn.commit()

        return asset_id

    def get_creative_asset(
        self, asset_id: str
    ) -> Optional[dict[str, Any]]:
        """Retrieve a creative asset by ID.

        Args:
            asset_id: The asset's primary key.

        Returns:
            Asset as a dict, or None if not found.
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM creative_assets WHERE asset_id = ?",
                (asset_id,),
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def list_creative_assets(
        self,
        *,
        campaign_id: str,
        asset_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List creative assets for a campaign.

        Args:
            campaign_id: Filter by campaign ID.
            asset_type: Optional asset type filter.
            limit: Maximum rows to return.

        Returns:
            List of asset dicts ordered by created_at descending.
        """
        clauses = ["campaign_id = ?"]
        params: list[Any] = [campaign_id]

        if asset_type is not None:
            clauses.append("asset_type = ?")
            params.append(asset_type)

        where = "WHERE " + " AND ".join(clauses)
        query = f"SELECT * FROM creative_assets {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            cursor = self._conn.execute(query, params)
            rows = cursor.fetchall()
        return [dict(r) for r in rows]

    def update_creative_asset(
        self,
        asset_id: str,
        **kwargs: Any,
    ) -> bool:
        """Update specified fields on a creative asset.

        Args:
            asset_id: The asset to update.
            **kwargs: Column name/value pairs to update.

        Returns:
            True if the asset was found and updated, False otherwise.
        """
        allowed = {
            "asset_name", "asset_type", "format_spec", "source_url",
            "validation_status", "validation_errors",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False

        set_clause = ", ".join(f"{col} = ?" for col in updates)
        values = list(updates.values()) + [asset_id]

        with self._lock:
            cursor = self._conn.execute(
                "SELECT asset_id FROM creative_assets WHERE asset_id = ?",
                (asset_id,),
            )
            if cursor.fetchone() is None:
                return False

            self._conn.execute(
                f"UPDATE creative_assets SET {set_clause} WHERE asset_id = ?",
                values,
            )
            self._conn.commit()

        return True

    # ------------------------------------------------------------------
    # Ad Server Campaigns
    # ------------------------------------------------------------------

    def save_ad_server_campaign(
        self,
        *,
        binding_id: Optional[str] = None,
        campaign_id: str,
        ad_server: str,
        external_campaign_id: Optional[str] = None,
        status: str = "PENDING",
        creative_assignments: Optional[str] = None,
        last_sync_at: Optional[str] = None,
    ) -> str:
        """Insert a new ad server campaign binding.

        Args:
            binding_id: Optional UUID. Generated if not provided.
            campaign_id: FK to campaigns table.
            ad_server: Ad server name (innovid, flashtalking, other).
            external_campaign_id: Campaign ID on the ad server.
            status: Binding status (default PENDING).
            creative_assignments: JSON object mapping assets to lines.
            last_sync_at: Last synchronization timestamp.

        Returns:
            The binding_id (generated or provided).
        """
        if binding_id is None:
            binding_id = str(uuid.uuid4())
        now = _now_iso()

        with self._lock:
            self._conn.execute(
                """INSERT INTO ad_server_campaigns (
                    binding_id, campaign_id, ad_server,
                    external_campaign_id, status,
                    creative_assignments, last_sync_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    binding_id, campaign_id, ad_server,
                    external_campaign_id, status,
                    creative_assignments, last_sync_at, now,
                ),
            )
            self._conn.commit()

        return binding_id

    def get_ad_server_campaign(
        self, binding_id: str
    ) -> Optional[dict[str, Any]]:
        """Retrieve an ad server campaign binding by ID.

        Args:
            binding_id: The binding's primary key.

        Returns:
            Binding as a dict, or None if not found.
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM ad_server_campaigns WHERE binding_id = ?",
                (binding_id,),
            )
            row = cursor.fetchone()
        return dict(row) if row else None

    def list_ad_server_campaigns(
        self,
        *,
        campaign_id: str,
        ad_server: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List ad server campaign bindings for a campaign.

        Args:
            campaign_id: Filter by campaign ID.
            ad_server: Optional ad server filter.
            limit: Maximum rows to return.

        Returns:
            List of binding dicts ordered by created_at descending.
        """
        clauses = ["campaign_id = ?"]
        params: list[Any] = [campaign_id]

        if ad_server is not None:
            clauses.append("ad_server = ?")
            params.append(ad_server)

        where = "WHERE " + " AND ".join(clauses)
        query = f"SELECT * FROM ad_server_campaigns {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            cursor = self._conn.execute(query, params)
            rows = cursor.fetchall()
        return [dict(r) for r in rows]

    def update_ad_server_campaign(
        self,
        binding_id: str,
        **kwargs: Any,
    ) -> bool:
        """Update specified fields on an ad server campaign binding.

        Args:
            binding_id: The binding to update.
            **kwargs: Column name/value pairs to update.

        Returns:
            True if the binding was found and updated, False otherwise.
        """
        allowed = {
            "ad_server", "external_campaign_id", "status",
            "creative_assignments", "last_sync_at",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False

        set_clause = ", ".join(f"{col} = ?" for col in updates)
        values = list(updates.values()) + [binding_id]

        with self._lock:
            cursor = self._conn.execute(
                "SELECT binding_id FROM ad_server_campaigns WHERE binding_id = ?",
                (binding_id,),
            )
            if cursor.fetchone() is None:
                return False

            self._conn.execute(
                f"UPDATE ad_server_campaigns SET {set_clause} WHERE binding_id = ?",
                values,
            )
            self._conn.commit()

        return True
