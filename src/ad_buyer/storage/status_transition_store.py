# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SQLite-backed status transition audit log.

Extracted from ``DealStore`` (bead ar-bonx) as part of the EP-2.4 god-class
split.  Operates on the ``status_transitions`` table, created by
``schema.initialize_schema`` during ``DealStore.connect()``.  Instances share
the owning DealStore's SQLite connection and lock.
"""

import sqlite3
import threading
from typing import Any


class StatusTransitionStore:
    """Append-only store for entity status transitions.

    Args:
        conn: Active SQLite connection (owned by the composing DealStore).
        lock: Shared lock serializing access to the connection.
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock

    def record_status_transition(
        self,
        *,
        entity_type: str,
        entity_id: str,
        from_status: str | None,
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
