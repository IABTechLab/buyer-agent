# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SQLite-backed ad server integration record persistence.

Uses synchronous sqlite3 (not aiosqlite) following the same thread-safety
pattern as DealStore: check_same_thread=False with a threading.Lock().

bead: buyer-uoz (Ad server integration record storage)
"""

import json
import logging
import sqlite3
import threading
from datetime import UTC, datetime

from ..models.campaign import (
    AdServerBinding,
    AdServerCampaign,
    AdServerCampaignStatus,
    AdServerDelivery,
    AdServerType,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# Schema DDL for ad server integration tables
AD_SERVER_CAMPAIGN_TABLE = """
CREATE TABLE IF NOT EXISTS ad_server_campaigns (
    id                      TEXT PRIMARY KEY,
    campaign_id             TEXT NOT NULL,
    ad_server               TEXT NOT NULL,
    ad_server_campaign_id   TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'PENDING',
    bindings                TEXT DEFAULT '[]',
    delivery                TEXT,
    created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

AD_SERVER_CAMPAIGN_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_adserver_campaign_id ON ad_server_campaigns(campaign_id);",
    "CREATE INDEX IF NOT EXISTS idx_adserver_ad_server ON ad_server_campaigns(ad_server);",
    "CREATE INDEX IF NOT EXISTS idx_adserver_status ON ad_server_campaigns(status);",
    "CREATE INDEX IF NOT EXISTS idx_adserver_campaign_server ON ad_server_campaigns(campaign_id, ad_server);",
]


class AdServerStore:
    """SQLite-backed store for ad server integration records.

    Thread-safe via a reentrant lock. Uses WAL mode for concurrent
    read/write access. All public methods are synchronous.

    Args:
        database_url: SQLite connection string (e.g. ``sqlite:///./ad_buyer.db``
            or ``sqlite:///:memory:`` for testing).
    """

    def __init__(self, database_url: str) -> None:
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
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._create_tables()

    def disconnect(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _create_tables(self) -> None:
        """Create ad server campaign tables and indexes if not present."""
        cursor = self._conn.cursor()
        cursor.execute(AD_SERVER_CAMPAIGN_TABLE)
        for idx in AD_SERVER_CAMPAIGN_INDEXES:
            cursor.execute(idx)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _dt_to_iso(dt: datetime) -> str:
        """Convert a datetime to ISO 8601 string."""
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    @staticmethod
    def _iso_to_dt(s: str) -> datetime:
        """Parse an ISO 8601 string to a UTC datetime."""
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=UTC)
            except ValueError:
                continue
        raise ValueError(f"Cannot parse datetime: {s}")

    def _serialize_bindings(self, bindings: list[AdServerBinding]) -> str:
        """Serialize bindings list to JSON for storage."""
        data = []
        for b in bindings:
            d = b.model_dump()
            # Convert datetime to ISO string for JSON
            d["last_sync_at"] = self._dt_to_iso(b.last_sync_at)
            data.append(d)
        return json.dumps(data)

    def _deserialize_bindings(self, raw: str) -> list[AdServerBinding]:
        """Deserialize bindings JSON back to model list."""
        data = json.loads(raw)
        result = []
        for d in data:
            if "last_sync_at" in d and isinstance(d["last_sync_at"], str):
                d["last_sync_at"] = self._iso_to_dt(d["last_sync_at"])
            result.append(AdServerBinding(**d))
        return result

    def _serialize_delivery(self, delivery: AdServerDelivery | None) -> str | None:
        """Serialize delivery data to JSON."""
        if delivery is None:
            return None
        d = delivery.model_dump()
        d["last_report_at"] = self._dt_to_iso(delivery.last_report_at)
        return json.dumps(d)

    def _deserialize_delivery(self, raw: str | None) -> AdServerDelivery | None:
        """Deserialize delivery JSON back to model."""
        if raw is None:
            return None
        d = json.loads(raw)
        if "last_report_at" in d and isinstance(d["last_report_at"], str):
            d["last_report_at"] = self._iso_to_dt(d["last_report_at"])
        return AdServerDelivery(**d)

    def _row_to_campaign(self, row: sqlite3.Row) -> AdServerCampaign:
        """Convert a database row to an AdServerCampaign model."""
        return AdServerCampaign(
            id=row["id"],
            campaign_id=row["campaign_id"],
            ad_server=AdServerType(row["ad_server"]),
            ad_server_campaign_id=row["ad_server_campaign_id"],
            status=AdServerCampaignStatus(row["status"]),
            bindings=self._deserialize_bindings(row["bindings"]),
            delivery=self._deserialize_delivery(row["delivery"]),
            created_at=self._iso_to_dt(row["created_at"]),
        )

    # ------------------------------------------------------------------
    # CRUD Operations
    # ------------------------------------------------------------------

    def save_ad_server_campaign(self, record: AdServerCampaign) -> str:
        """Persist an ad server integration record.

        Args:
            record: The AdServerCampaign model to save.

        Returns:
            The record ID.
        """
        now = _now_iso()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO ad_server_campaigns (
                    id, campaign_id, ad_server, ad_server_campaign_id,
                    status, bindings, delivery, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.campaign_id,
                    record.ad_server.value,
                    record.ad_server_campaign_id,
                    record.status.value,
                    self._serialize_bindings(record.bindings),
                    self._serialize_delivery(record.delivery),
                    self._dt_to_iso(record.created_at),
                    now,
                ),
            )
            self._conn.commit()
        return record.id

    def get_ad_server_campaign(self, record_id: str) -> AdServerCampaign | None:
        """Retrieve an ad server integration record by its ID.

        Args:
            record_id: The record UUID.

        Returns:
            AdServerCampaign if found, None otherwise.
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM ad_server_campaigns WHERE id = ?",
                (record_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_campaign(row)

    def list_ad_server_campaigns(
        self,
        campaign_id: str | None = None,
        ad_server: AdServerType | None = None,
        status: AdServerCampaignStatus | None = None,
    ) -> list[AdServerCampaign]:
        """List ad server integration records with optional filters.

        Args:
            campaign_id: Filter by campaign ID.
            ad_server: Filter by ad server type.
            status: Filter by integration status.

        Returns:
            List of matching AdServerCampaign models.
        """
        conditions = []
        params: list = []

        if campaign_id is not None:
            conditions.append("campaign_id = ?")
            params.append(campaign_id)

        if ad_server is not None:
            conditions.append("ad_server = ?")
            params.append(ad_server.value)

        if status is not None:
            conditions.append("status = ?")
            params.append(status.value)

        query = "SELECT * FROM ad_server_campaigns"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at ASC"

        with self._lock:
            cursor = self._conn.execute(query, params)
            rows = cursor.fetchall()

        return [self._row_to_campaign(row) for row in rows]

    def update_ad_server_campaign(
        self,
        record_id: str,
        *,
        status: AdServerCampaignStatus | None = None,
        ad_server_campaign_id: str | None = None,
        bindings: list[AdServerBinding] | None = None,
        delivery: AdServerDelivery | None = None,
    ) -> None:
        """Update fields on an existing ad server integration record.

        Only the fields provided (non-None) will be updated.

        Args:
            record_id: The record UUID to update.
            status: New status.
            ad_server_campaign_id: New external campaign ID.
            bindings: New bindings list (replaces existing).
            delivery: New delivery data (replaces existing).

        Raises:
            ValueError: If the record does not exist.
        """
        updates = []
        params: list = []

        if status is not None:
            updates.append("status = ?")
            params.append(status.value)

        if ad_server_campaign_id is not None:
            updates.append("ad_server_campaign_id = ?")
            params.append(ad_server_campaign_id)

        if bindings is not None:
            updates.append("bindings = ?")
            params.append(self._serialize_bindings(bindings))

        if delivery is not None:
            updates.append("delivery = ?")
            params.append(self._serialize_delivery(delivery))

        if not updates:
            return

        updates.append("updated_at = ?")
        params.append(_now_iso())
        params.append(record_id)

        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE ad_server_campaigns SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            self._conn.commit()
            if cursor.rowcount == 0:
                raise ValueError(f"Ad server campaign record '{record_id}' not found")
