# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SQLite-backed event persistence for the deal event bus.

Extracted from ``DealStore`` (bead ar-bonx) as part of the EP-2.4 god-class
split.  Operates on the ``events`` table, created by
``schema.initialize_schema`` during ``DealStore.connect()``.  Instances share
the owning DealStore's SQLite connection and lock.
"""

import sqlite3
import threading
import uuid
from typing import Any


class DealEventStore:
    """Store for persisted event-bus events.

    Args:
        conn: Active SQLite connection (owned by the composing DealStore).
        lock: Shared lock serializing access to the connection.
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock

    def save_event(
        self,
        *,
        event_id: str | None = None,
        event_type: str,
        flow_id: str = "",
        flow_type: str = "",
        deal_id: str = "",
        session_id: str = "",
        payload: str | None = None,
        metadata: str | None = None,
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

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        """Retrieve an event by ID.

        Args:
            event_id: The event's primary key.

        Returns:
            Event as a dict, or None if not found.
        """
        with self._lock:
            cursor = self._conn.execute("SELECT * FROM events WHERE id = ?", (event_id,))
            row = cursor.fetchone()
        return dict(row) if row else None

    def list_events(
        self,
        *,
        event_type: str | None = None,
        flow_id: str | None = None,
        session_id: str | None = None,
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
