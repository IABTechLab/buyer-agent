# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SQLite-backed deal performance cache persistence (v2).

Extracted from ``DealStore`` as part of the EP-2.4 god-class
split.  Operates on the ``performance_cache`` table, created by
``schema.initialize_schema`` during ``DealStore.connect()``.  Instances share
the owning DealStore's SQLite connection and lock.
"""

import sqlite3
import threading
from typing import Any


class PerformanceCacheStore:
    """Store for cached deal performance metrics.

    Args:
        conn: Active SQLite connection (owned by the composing DealStore).
        lock: Shared lock serializing access to the connection.
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock

    def save_performance_cache(
        self,
        *,
        deal_id: str,
        impressions_delivered: int | None = None,
        spend_to_date: float | None = None,
        fill_rate: float | None = None,
        win_rate: float | None = None,
        avg_effective_cpm: float | None = None,
        last_delivery_at: str | None = None,
        performance_trend: str | None = None,
        cached_at: str | None = None,
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
                (
                    deal_id,
                    impressions_delivered,
                    spend_to_date,
                    fill_rate,
                    win_rate,
                    avg_effective_cpm,
                    last_delivery_at,
                    performance_trend,
                    cached_at,
                ),
            )
            self._conn.commit()
            return cursor.lastrowid

    def get_performance_cache(self, deal_id: str) -> dict[str, Any] | None:
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
        allowed = {
            "impressions_delivered",
            "spend_to_date",
            "fill_rate",
            "win_rate",
            "avg_effective_cpm",
            "last_delivery_at",
            "performance_trend",
            "cached_at",
        }
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
