# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SQLite-backed deal template persistence (v5, Strategic Plan Section 6.3).

Extracted from ``DealStore`` (bead ar-bonx) as part of the EP-2.4 god-class
split.  Operates on the ``deal_templates`` table, created by
``schema.initialize_schema`` during ``DealStore.connect()``.  Instances share
the owning DealStore's SQLite connection and lock.
"""

import logging
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class DealTemplateStore:
    """Store for reusable deal templates.

    Args:
        conn: Active SQLite connection (owned by the composing DealStore).
        lock: Shared lock serializing access to the connection.
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock

    def save_deal_template(
        self,
        *,
        template_id: str | None = None,
        name: str,
        deal_type_pref: str | None = None,
        inventory_types: str | None = None,
        preferred_publishers: str | None = None,
        excluded_publishers: str | None = None,
        targeting_defaults: str | None = None,
        default_price: float | None = None,
        max_cpm: float | None = None,
        min_impressions: int | None = None,
        default_flight_days: int | None = None,
        supply_path_prefs: str | None = None,
        advertiser_id: str | None = None,
        agency_id: str | None = None,
    ) -> str:  # noqa: E501
        """Insert a new deal template. Returns the template ID."""
        if template_id is None:
            template_id = str(uuid.uuid4())
        now = _now_iso()
        with self._lock:
            self._conn.execute(
                """INSERT INTO deal_templates (
                    id, name, deal_type_pref, inventory_types,
                    preferred_publishers, excluded_publishers,
                    targeting_defaults, default_price, max_cpm,
                    min_impressions, default_flight_days,
                    supply_path_prefs, advertiser_id, agency_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    template_id,
                    name,
                    deal_type_pref,
                    inventory_types,
                    preferred_publishers,
                    excluded_publishers,
                    targeting_defaults,
                    default_price,
                    max_cpm,
                    min_impressions,
                    default_flight_days,
                    supply_path_prefs,
                    advertiser_id,
                    agency_id,
                    now,
                    now,
                ),  # noqa: E501
            )
            self._conn.commit()
        logger.info("Saved deal template %s: %s", template_id, name)
        return template_id

    def get_deal_template(self, template_id: str) -> dict[str, Any] | None:
        """Retrieve a deal template by ID."""
        with self._lock:
            cursor = self._conn.execute("SELECT * FROM deal_templates WHERE id = ?", (template_id,))
            row = cursor.fetchone()
        return dict(row) if row else None

    def list_deal_templates(
        self,
        *,
        advertiser_id: str | None = None,
        deal_type_pref: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:  # noqa: E501
        """List deal templates with optional filters."""
        conditions: list[str] = []
        params: list[Any] = []
        if advertiser_id is not None:
            conditions.append("advertiser_id = ?")
            params.append(advertiser_id)
        if deal_type_pref is not None:
            conditions.append("deal_type_pref = ?")
            params.append(deal_type_pref)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        query = f"SELECT * FROM deal_templates {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            cursor = self._conn.execute(query, params)
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def update_deal_template(self, template_id: str, **kwargs: Any) -> bool:
        """Update fields on an existing deal template."""
        if not kwargs:
            return False
        allowed = {
            "name",
            "deal_type_pref",
            "inventory_types",
            "preferred_publishers",
            "excluded_publishers",
            "targeting_defaults",
            "default_price",
            "max_cpm",
            "min_impressions",
            "default_flight_days",
            "supply_path_prefs",
            "advertiser_id",
            "agency_id",
        }  # noqa: E501
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = _now_iso()
        set_clause = ", ".join(f"{col} = ?" for col in updates)
        values = list(updates.values()) + [template_id]
        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE deal_templates SET {set_clause} WHERE id = ?", values
            )  # noqa: E501
            self._conn.commit()
            return cursor.rowcount > 0

    def delete_deal_template(self, template_id: str) -> bool:
        """Delete a deal template by ID."""
        with self._lock:
            cursor = self._conn.execute("DELETE FROM deal_templates WHERE id = ?", (template_id,))
            self._conn.commit()
            return cursor.rowcount > 0
