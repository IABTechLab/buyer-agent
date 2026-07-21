# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SQLite-backed booking job persistence.

Extracted from ``DealStore`` as part of the EP-2.4 god-class
split.  Operates on the ``jobs`` table, created by
``schema.initialize_schema`` during ``DealStore.connect()``.  Instances share
the owning DealStore's SQLite connection and lock.
"""

import json
import sqlite3
import threading
from datetime import UTC, datetime
from typing import Any


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class JobStore:
    """Store for API-initiated booking jobs.

    Args:
        conn: Active SQLite connection (owned by the composing DealStore).
        lock: Shared lock serializing access to the connection.
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock

    def save_job(
        self,
        *,
        job_id: str,
        status: str = "pending",
        progress: float = 0.0,
        brief: str | None = None,
        auto_approve: bool = False,
        budget_allocs: str | None = None,
        recommendations: str | None = None,
        booked_lines: str | None = None,
        errors: str | None = None,
    ) -> str:
        """Insert or update a job record (upsert).

        Args:
            job_id: Unique job identifier.
            status: Job status.
            progress: Progress 0.0-1.0.
            brief: JSON campaign brief.
            auto_approve: Whether to auto-approve.
            budget_allocs: JSON budget allocations.
            recommendations: JSON recommendation list.
            booked_lines: JSON booked lines list.
            errors: JSON error list.

        Returns:
            The job ID.
        """
        now = _now_iso()

        with self._lock:
            self._conn.execute(
                """INSERT INTO jobs
                   (id, status, progress, brief, auto_approve,
                    budget_allocs, recommendations, booked_lines, errors,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       status = excluded.status,
                       progress = excluded.progress,
                       brief = excluded.brief,
                       auto_approve = excluded.auto_approve,
                       budget_allocs = excluded.budget_allocs,
                       recommendations = excluded.recommendations,
                       booked_lines = excluded.booked_lines,
                       errors = excluded.errors,
                       updated_at = excluded.updated_at""",
                (
                    job_id,
                    status,
                    progress,
                    brief or "{}",
                    1 if auto_approve else 0,
                    budget_allocs or "{}",
                    recommendations or "[]",
                    booked_lines or "[]",
                    errors or "[]",
                    now,
                    now,
                ),
            )
            self._conn.commit()
        return job_id

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Retrieve a job by ID.

        Args:
            job_id: The job's primary key.

        Returns:
            Job as a dict, or None if not found.
        """
        with self._lock:
            cursor = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = cursor.fetchone()
        if row is None:
            return None

        result = dict(row)
        # Deserialize JSON fields for API compatibility
        for field in ("brief", "budget_allocs", "recommendations", "booked_lines", "errors"):
            val = result.get(field)
            if isinstance(val, str):
                try:
                    result[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
        # Convert auto_approve int to bool
        result["auto_approve"] = bool(result.get("auto_approve", 0))
        return result

    def list_jobs(
        self,
        *,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List jobs with optional status filter.

        Args:
            status: Filter by job status.
            limit: Maximum rows to return.

        Returns:
            List of job dicts ordered by created_at descending.
        """
        if status is not None:
            query = "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?"
            params: tuple = (status, limit)
        else:
            query = "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?"
            params = (limit,)

        with self._lock:
            cursor = self._conn.execute(query, params)
            rows = cursor.fetchall()

        results = []
        for row in rows:
            r = dict(row)
            # Deserialize JSON fields
            for field in ("brief", "budget_allocs", "recommendations", "booked_lines", "errors"):
                val = r.get(field)
                if isinstance(val, str):
                    try:
                        r[field] = json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        pass
            r["auto_approve"] = bool(r.get("auto_approve", 0))
            results.append(r)
        return results
