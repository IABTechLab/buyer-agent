# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SQLite-backed pacing snapshot persistence.

Uses synchronous sqlite3 (not aiosqlite) following the same thread-safety
pattern as DealStore: check_same_thread=False with a threading.Lock().

bead: buyer-lna (Pacing snapshot storage)
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional

from ..models.campaign import (
    ChannelSnapshot,
    DealSnapshot,
    PacingRecommendation,
    PacingSnapshot,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# Schema DDL for pacing snapshot tables
PACING_SNAPSHOT_TABLE = """
CREATE TABLE IF NOT EXISTS pacing_snapshots (
    snapshot_id         TEXT PRIMARY KEY,
    campaign_id         TEXT NOT NULL,
    timestamp           TEXT NOT NULL,
    total_budget        REAL NOT NULL,
    total_spend         REAL NOT NULL,
    pacing_pct          REAL NOT NULL,
    expected_spend      REAL NOT NULL,
    deviation_pct       REAL NOT NULL,
    channel_snapshots   TEXT DEFAULT '[]',
    deal_snapshots      TEXT DEFAULT '[]',
    recommendations     TEXT DEFAULT '[]',
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

PACING_SNAPSHOT_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_pacing_campaign_id ON pacing_snapshots(campaign_id);",
    "CREATE INDEX IF NOT EXISTS idx_pacing_timestamp ON pacing_snapshots(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_pacing_campaign_timestamp ON pacing_snapshots(campaign_id, timestamp);",
]


class PacingStore:
    """SQLite-backed store for pacing snapshots.

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

    @staticmethod
    def _parse_url(url: str) -> str:
        """Extract the file path from a sqlite:/// URL."""
        if url.startswith("sqlite:///"):
            return url[len("sqlite:///"):]
        if url.startswith("sqlite://"):
            path = url[len("sqlite://"):]
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
        """Create pacing snapshot tables and indexes if not present."""
        cursor = self._conn.cursor()
        cursor.execute(PACING_SNAPSHOT_TABLE)
        for idx in PACING_SNAPSHOT_INDEXES:
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
        # Handle both with and without microseconds
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        raise ValueError(f"Cannot parse datetime: {s}")

    def _row_to_snapshot(self, row: sqlite3.Row) -> PacingSnapshot:
        """Convert a database row to a PacingSnapshot model."""
        channel_data = json.loads(row["channel_snapshots"])
        deal_data = json.loads(row["deal_snapshots"])
        rec_data = json.loads(row["recommendations"])

        return PacingSnapshot(
            snapshot_id=row["snapshot_id"],
            campaign_id=row["campaign_id"],
            timestamp=self._iso_to_dt(row["timestamp"]),
            total_budget=row["total_budget"],
            total_spend=row["total_spend"],
            pacing_pct=row["pacing_pct"],
            expected_spend=row["expected_spend"],
            deviation_pct=row["deviation_pct"],
            channel_snapshots=[ChannelSnapshot(**ch) for ch in channel_data],
            deal_snapshots=[DealSnapshot(**ds) for ds in deal_data],
            recommendations=[PacingRecommendation(**rec) for rec in rec_data],
        )

    # ------------------------------------------------------------------
    # CRUD Operations
    # ------------------------------------------------------------------

    def save_pacing_snapshot(self, snapshot: PacingSnapshot) -> str:
        """Persist a pacing snapshot.

        Args:
            snapshot: The PacingSnapshot model to save.

        Returns:
            The snapshot_id.
        """
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO pacing_snapshots (
                    snapshot_id, campaign_id, timestamp,
                    total_budget, total_spend, pacing_pct,
                    expected_spend, deviation_pct,
                    channel_snapshots, deal_snapshots, recommendations,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.campaign_id,
                    self._dt_to_iso(snapshot.timestamp),
                    snapshot.total_budget,
                    snapshot.total_spend,
                    snapshot.pacing_pct,
                    snapshot.expected_spend,
                    snapshot.deviation_pct,
                    json.dumps(
                        [ch.model_dump() for ch in snapshot.channel_snapshots]
                    ),
                    json.dumps(
                        [ds.model_dump() for ds in snapshot.deal_snapshots]
                    ),
                    json.dumps(
                        [rec.model_dump() for rec in snapshot.recommendations]
                    ),
                    _now_iso(),
                ),
            )
            self._conn.commit()
        return snapshot.snapshot_id

    def get_pacing_snapshot(self, snapshot_id: str) -> Optional[PacingSnapshot]:
        """Retrieve a pacing snapshot by its ID.

        Args:
            snapshot_id: The snapshot UUID.

        Returns:
            PacingSnapshot if found, None otherwise.
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM pacing_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_snapshot(row)

    def list_pacing_snapshots(
        self,
        campaign_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> list[PacingSnapshot]:
        """List pacing snapshots for a campaign, optionally filtered by time.

        Results are ordered by timestamp ascending (chronological).

        Args:
            campaign_id: Campaign to filter by.
            start_time: If provided, only include snapshots at or after this time.
            end_time: If provided, only include snapshots before this time.

        Returns:
            List of PacingSnapshot models, ordered by timestamp.
        """
        query = "SELECT * FROM pacing_snapshots WHERE campaign_id = ?"
        params: list = [campaign_id]

        if start_time is not None:
            query += " AND timestamp >= ?"
            params.append(self._dt_to_iso(start_time))

        if end_time is not None:
            query += " AND timestamp < ?"
            params.append(self._dt_to_iso(end_time))

        query += " ORDER BY timestamp ASC"

        with self._lock:
            cursor = self._conn.execute(query, params)
            rows = cursor.fetchall()

        return [self._row_to_snapshot(row) for row in rows]

    def get_latest_pacing_snapshot(
        self, campaign_id: str
    ) -> Optional[PacingSnapshot]:
        """Get the most recent pacing snapshot for a campaign.

        Args:
            campaign_id: Campaign to look up.

        Returns:
            The most recent PacingSnapshot, or None if no snapshots exist.
        """
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT * FROM pacing_snapshots
                WHERE campaign_id = ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (campaign_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_snapshot(row)
