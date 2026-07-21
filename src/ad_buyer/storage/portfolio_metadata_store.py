# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SQLite-backed portfolio metadata persistence (v2 deal library).

Extracted from ``DealStore`` (bead ar-bonx) as part of the EP-2.4 god-class
split.  Operates on the ``portfolio_metadata`` table, created by
``schema.initialize_schema`` during ``DealStore.connect()``.  Instances share
the owning DealStore's SQLite connection and lock.
"""

import sqlite3
import threading
from typing import Any


class PortfolioMetadataStore:
    """Store for extrinsic (portfolio) metadata attached to deals.

    Args:
        conn: Active SQLite connection (owned by the composing DealStore).
        lock: Shared lock serializing access to the connection.
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock

    def save_portfolio_metadata(
        self,
        *,
        deal_id: str,
        import_source: str | None = None,
        import_date: str | None = None,
        tags: str | None = None,
        advertiser_id: str | None = None,
        agency_id: str | None = None,
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
                (deal_id, import_source, import_date, tags, advertiser_id, agency_id),
            )
            self._conn.commit()
            return cursor.lastrowid

    def get_portfolio_metadata(self, deal_id: str) -> dict[str, Any] | None:
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
        allowed = {"import_source", "import_date", "tags", "advertiser_id", "agency_id"}
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
