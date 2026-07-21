# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SQLite-backed deal activation persistence (v2 cross-platform activations).

Extracted from ``DealStore`` (bead ar-bonx) as part of the EP-2.4 god-class
split.  Operates on the ``deal_activations`` table, created by
``schema.initialize_schema`` during ``DealStore.connect()``.  Instances share
the owning DealStore's SQLite connection and lock.
"""

import sqlite3
import threading
from typing import Any


class DealActivationStore:
    """Store for cross-platform deal activation records.

    Args:
        conn: Active SQLite connection (owned by the composing DealStore).
        lock: Shared lock serializing access to the connection.
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock

    def save_deal_activation(
        self,
        *,
        deal_id: str,
        platform: str,
        platform_deal_id: str | None = None,
        activation_status: str | None = None,
        last_sync_at: str | None = None,
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
                (deal_id, platform, platform_deal_id, activation_status, last_sync_at),
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
        allowed = {"platform", "platform_deal_id", "activation_status", "last_sync_at"}
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
