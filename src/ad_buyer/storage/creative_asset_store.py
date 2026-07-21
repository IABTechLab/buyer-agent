# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SQLite-backed creative asset persistence (v3 campaign automation).

Extracted from ``DealStore`` as part of the EP-2.4 god-class
split.  Operates on the ``creative_assets`` table, created by
``schema.initialize_schema`` during ``DealStore.connect()``.  Instances share
the owning DealStore's SQLite connection and lock.
"""

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from typing import Any


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class CreativeAssetStore:
    """Store for campaign creative assets.

    Args:
        conn: Active SQLite connection (owned by the composing DealStore).
        lock: Shared lock serializing access to the connection.
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock

    def save_creative_asset(
        self,
        *,
        asset_id: str | None = None,
        campaign_id: str,
        asset_name: str,
        asset_type: str,
        format_spec: dict | None = None,
        source_url: str | None = None,
        validation_status: str = "pending",
        validation_errors: list | None = None,
    ) -> str:
        """Insert a new creative asset.

        Args:
            asset_id: Optional UUID. Generated if not provided.
            campaign_id: ID of the campaign this asset belongs to.
            asset_name: Human-readable name for the creative.
            asset_type: Type of creative (display, video, audio, interactive, native).
            format_spec: Format-specific metadata dict (varies by asset_type).
            source_url: URL where the creative file is hosted.
            validation_status: IAB spec validation status (pending, valid, invalid).
            validation_errors: List of validation error/warning messages.

        Returns:
            The asset ID (generated or provided).
        """
        if asset_id is None:
            asset_id = str(uuid.uuid4())
        now = _now_iso()

        format_spec_json = json.dumps(format_spec) if format_spec is not None else "{}"
        errors_json = json.dumps(validation_errors) if validation_errors is not None else "[]"

        with self._lock:
            self._conn.execute(
                """INSERT INTO creative_assets
                   (asset_id, campaign_id, asset_name, asset_type,
                    format_spec, source_url, validation_status,
                    validation_errors, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    asset_id,
                    campaign_id,
                    asset_name,
                    asset_type,
                    format_spec_json,
                    source_url,
                    validation_status,
                    errors_json,
                    now,
                    now,
                ),
            )
            self._conn.commit()

        return asset_id

    def get_creative_asset(self, asset_id: str) -> dict[str, Any] | None:
        """Retrieve a creative asset by ID.

        JSON fields (format_spec, validation_errors) are automatically
        deserialized.

        Args:
            asset_id: The asset's primary key.

        Returns:
            Asset as a dict with deserialized JSON fields, or None if not found.
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM creative_assets WHERE asset_id = ?",
                (asset_id,),
            )
            row = cursor.fetchone()

        if row is None:
            return None

        result = dict(row)
        # Deserialize JSON fields
        for field in ("format_spec", "validation_errors"):
            val = result.get(field)
            if isinstance(val, str):
                try:
                    result[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
        return result

    def list_creative_assets(
        self,
        *,
        campaign_id: str | None = None,
        asset_type: str | None = None,
        validation_status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List creative assets with optional filters.

        Args:
            campaign_id: Filter by campaign ID.
            asset_type: Filter by asset type (display, video, etc.).
            validation_status: Filter by validation status (pending, valid, invalid).
            limit: Maximum rows to return.

        Returns:
            List of asset dicts ordered by created_at descending.
        """
        clauses: list[str] = []
        params: list[Any] = []

        if campaign_id is not None:
            clauses.append("campaign_id = ?")
            params.append(campaign_id)
        if asset_type is not None:
            clauses.append("asset_type = ?")
            params.append(asset_type)
        if validation_status is not None:
            clauses.append("validation_status = ?")
            params.append(validation_status)

        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        query = f"SELECT * FROM creative_assets {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            cursor = self._conn.execute(query, params)
            rows = cursor.fetchall()

        results = []
        for row in rows:
            r = dict(row)
            # Deserialize JSON fields
            for field in ("format_spec", "validation_errors"):
                val = r.get(field)
                if isinstance(val, str):
                    try:
                        r[field] = json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        pass
            results.append(r)
        return results

    def update_creative_asset(self, asset_id: str, **kwargs: Any) -> bool:
        """Update specific fields on a creative asset.

        Automatically serializes format_spec (dict) and validation_errors
        (list) to JSON before writing.  Bumps ``updated_at``.

        Args:
            asset_id: The asset to update.
            **kwargs: Column-value pairs to update. Accepted columns:
                asset_name, asset_type, format_spec, source_url,
                validation_status, validation_errors, campaign_id.

        Returns:
            True if a row was updated, False if the asset was not found
            or no valid kwargs were provided.
        """
        allowed = {
            "asset_name",
            "asset_type",
            "format_spec",
            "source_url",
            "validation_status",
            "validation_errors",
            "campaign_id",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False

        # Serialize JSON fields
        if "format_spec" in updates and isinstance(updates["format_spec"], dict):
            updates["format_spec"] = json.dumps(updates["format_spec"])
        if "validation_errors" in updates and isinstance(updates["validation_errors"], list):
            updates["validation_errors"] = json.dumps(updates["validation_errors"])

        # Always bump updated_at
        updates["updated_at"] = _now_iso()

        set_clause = ", ".join(f"{col} = ?" for col in updates)
        values = list(updates.values())
        values.append(asset_id)

        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE creative_assets SET {set_clause} WHERE asset_id = ?",
                values,
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def delete_creative_asset(self, asset_id: str) -> bool:
        """Delete a creative asset by ID.

        Args:
            asset_id: The asset to delete.

        Returns:
            True if a row was deleted, False if not found.
        """
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM creative_assets WHERE asset_id = ?",
                (asset_id,),
            )
            self._conn.commit()
            return cursor.rowcount > 0
