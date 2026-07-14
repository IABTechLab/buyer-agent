# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SQLite-backed supply path template persistence (v5, Strategic Plan Section 6.4).

Extracted from ``DealStore`` (bead ar-bonx) as part of the EP-2.4 god-class
split.  Operates on the ``supply_path_templates`` table, created by
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


class SupplyPathTemplateStore:
    """Store for reusable supply path (curation) templates.

    Args:
        conn: Active SQLite connection (owned by the composing DealStore).
        lock: Shared lock serializing access to the connection.
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock

    def save_supply_path_template(
        self,
        *,
        template_id: str | None = None,
        name: str,
        scoring_weights: str | None = None,
        max_reseller_hops: int | None = None,
        require_sellers_json: int | None = None,
        preferred_ssps: str | None = None,
        blocked_ssps: str | None = None,
        preferred_curators: str | None = None,
        rules: str | None = None,
    ) -> str:  # noqa: E501
        """Insert a new supply path template. Returns the template ID."""
        if template_id is None:
            template_id = str(uuid.uuid4())
        now = _now_iso()
        with self._lock:
            self._conn.execute(
                """INSERT INTO supply_path_templates (
                    id, name, scoring_weights, max_reseller_hops,
                    require_sellers_json, preferred_ssps, blocked_ssps,
                    preferred_curators, rules, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    template_id,
                    name,
                    scoring_weights,
                    max_reseller_hops,
                    require_sellers_json,
                    preferred_ssps,
                    blocked_ssps,
                    preferred_curators,
                    rules,
                    now,
                    now,
                ),  # noqa: E501
            )
            self._conn.commit()
        logger.info("Saved supply path template %s: %s", template_id, name)
        return template_id

    def get_supply_path_template(self, template_id: str) -> dict[str, Any] | None:
        """Retrieve a supply path template by ID."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM supply_path_templates WHERE id = ?", (template_id,)
            )  # noqa: E501
            row = cursor.fetchone()
        return dict(row) if row else None

    def list_supply_path_templates(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """List supply path templates."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM supply_path_templates ORDER BY created_at DESC LIMIT ?", (limit,)
            )  # noqa: E501
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def update_supply_path_template(self, template_id: str, **kwargs: Any) -> bool:
        """Update fields on an existing supply path template."""
        if not kwargs:
            return False
        allowed = {
            "name",
            "scoring_weights",
            "max_reseller_hops",
            "require_sellers_json",
            "preferred_ssps",
            "blocked_ssps",
            "preferred_curators",
            "rules",
        }  # noqa: E501
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = _now_iso()
        set_clause = ", ".join(f"{col} = ?" for col in updates)
        values = list(updates.values()) + [template_id]
        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE supply_path_templates SET {set_clause} WHERE id = ?", values
            )  # noqa: E501
            self._conn.commit()
            return cursor.rowcount > 0

    def delete_supply_path_template(self, template_id: str) -> bool:
        """Delete a supply path template by ID."""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM supply_path_templates WHERE id = ?", (template_id,)
            )  # noqa: E501
            self._conn.commit()
            return cursor.rowcount > 0
