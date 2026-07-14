# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SQLite-backed booking record persistence.

Extracted from ``DealStore`` (bead ar-bonx) as part of the EP-2.4 god-class
split.  Operates on the ``booking_records`` table, created by
``schema.initialize_schema`` during ``DealStore.connect()``.  Instances share
the owning DealStore's SQLite connection and lock.
"""

import sqlite3
import threading
from typing import Any


class BookingRecordStore:
    """Store for booked line items associated with deals.

    Args:
        conn: Active SQLite connection (owned by the composing DealStore).
        lock: Shared lock serializing access to the connection.
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock

    def save_booking_record(
        self,
        *,
        deal_id: str,
        order_id: str | None = None,
        line_id: str | None = None,
        channel: str = "",
        impressions: int = 0,
        cost: float = 0.0,
        booking_status: str = "pending",
        metadata: str | None = None,
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
