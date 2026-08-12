# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SQLite-backed persistence for inbound operator API keys.

Uses the same database URL as DealStore. Schema is created via
``schema.initialize_schema`` (v6 ``api_keys`` table) so CLI bootstrap
and the HTTP server share one store.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime
from typing import Any, Optional

from ..models.api_key import ApiKeyRecord, ApiKeyRole
from .schema import initialize_schema

logger = logging.getLogger(__name__)


def _parse_url(url: str) -> str:
    """Extract the file path from a sqlite:/// URL."""
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///") :]
    if url.startswith("sqlite://"):
        path = url[len("sqlite://") :]
        return path if path else ":memory:"
    return url


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    # Accept both "...Z" and plain ISO from datetime.isoformat()
    text = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _fmt_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


class OperatorApiKeyStore:
    """SQLite store for hashed inbound operator API keys.

    Thread-safe via a lock. All public methods are synchronous.
    Distinct from :class:`ad_buyer.auth.key_store.ApiKeyStore`, which
    stores outbound seller credentials on disk.

    Args:
        database_url: SQLite connection string (same as DealStore).
    """

    def __init__(self, database_url: str) -> None:
        self._db_path = _parse_url(database_url)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        """Open the database connection and ensure schema is current."""
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        initialize_schema(self._conn)

    def disconnect(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError(
                "OperatorApiKeyStore is not connected; call connect() first"
            )
        return self._conn

    def insert(self, record: ApiKeyRecord) -> None:
        """Insert a new API key record."""
        conn = self._require_conn()
        with self._lock:
            conn.execute(
                """
                INSERT INTO api_keys (
                    key_id, key_hash, key_prefix_hint, role, label,
                    created_at, expires_at, revoked, revoked_at,
                    last_used_at, use_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.key_id,
                    record.key_hash,
                    record.key_prefix_hint,
                    record.role.value,
                    record.label,
                    _fmt_dt(record.created_at),
                    _fmt_dt(record.expires_at),
                    1 if record.revoked else 0,
                    _fmt_dt(record.revoked_at),
                    _fmt_dt(record.last_used_at),
                    record.use_count,
                ),
            )
            conn.commit()

    def get_by_hash(self, key_hash: str) -> Optional[ApiKeyRecord]:
        """Look up a key by SHA-256 hash."""
        conn = self._require_conn()
        with self._lock:
            row = conn.execute(
                "SELECT * FROM api_keys WHERE key_hash = ?",
                (key_hash,),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def get_by_id(self, key_id: str) -> Optional[ApiKeyRecord]:
        """Look up a key by key_id."""
        conn = self._require_conn()
        with self._lock:
            row = conn.execute(
                "SELECT * FROM api_keys WHERE key_id = ?",
                (key_id,),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def list_all(self) -> list[ApiKeyRecord]:
        """Return all key records."""
        conn = self._require_conn()
        with self._lock:
            rows = conn.execute(
                "SELECT * FROM api_keys ORDER BY created_at ASC"
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def update(self, record: ApiKeyRecord) -> None:
        """Update a key record in place (by key_id)."""
        conn = self._require_conn()
        with self._lock:
            conn.execute(
                """
                UPDATE api_keys SET
                    key_hash = ?,
                    key_prefix_hint = ?,
                    role = ?,
                    label = ?,
                    created_at = ?,
                    expires_at = ?,
                    revoked = ?,
                    revoked_at = ?,
                    last_used_at = ?,
                    use_count = ?
                WHERE key_id = ?
                """,
                (
                    record.key_hash,
                    record.key_prefix_hint,
                    record.role.value,
                    record.label,
                    _fmt_dt(record.created_at),
                    _fmt_dt(record.expires_at),
                    1 if record.revoked else 0,
                    _fmt_dt(record.revoked_at),
                    _fmt_dt(record.last_used_at),
                    record.use_count,
                    record.key_id,
                ),
            )
            conn.commit()

    def count_active_operator_keys(self) -> int:
        """Count non-revoked operator keys (expiry checked in service)."""
        conn = self._require_conn()
        with self._lock:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM api_keys
                WHERE role = ? AND revoked = 0
                """,
                (ApiKeyRole.OPERATOR.value,),
            ).fetchone()
        return int(row["n"]) if row else 0

    @staticmethod
    def _row_to_record(row: Any) -> ApiKeyRecord:
        return ApiKeyRecord(
            key_id=row["key_id"],
            key_hash=row["key_hash"],
            key_prefix_hint=row["key_prefix_hint"],
            role=ApiKeyRole(row["role"]),
            label=row["label"] or "",
            created_at=_parse_dt(row["created_at"]) or datetime.utcnow(),
            expires_at=_parse_dt(row["expires_at"]),
            revoked=bool(row["revoked"]),
            revoked_at=_parse_dt(row["revoked_at"]),
            last_used_at=_parse_dt(row["last_used_at"]),
            use_count=int(row["use_count"] or 0),
        )
