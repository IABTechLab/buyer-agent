# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Append-only audit trail for audience-plan lifecycle events.

Implements proposal §5.7 + §6 row 13a:

> Audit-trail surface for degradation events -- every plan degradation,
> capability rejection, and snapshot-honor decision lands in a structured
> log keyed by `audience_plan_id`. Required for the §7 silent-degradation
> mitigation.

The log lives in a single SQLite table, `audience_audit_log`, with one row
per event:

    (plan_id TEXT, event_type TEXT, payload_json TEXT, created_at TEXT)

Append-only by contract -- callers only `log_event` (insert) and `get_events`
(read). There is no update or delete path. The table schema lives in
`storage.schema.AUDIENCE_AUDIT_LOG_TABLE` and is created idempotently on
every `initialize_schema` call, so older buyer DBs gain the table on first
boot after this bead lands without an explicit migration.

Event types (per proposal §5.7):
  - "degradation"           -- one event per `degrade_plan_for_seller` call
                               that produced a non-empty log
  - "capability_rejection"  -- seller returned `audience_plan_unsupported`
  - "snapshot_honor"        -- fulfillment honored a frozen snapshot
                               vs. current capabilities (mostly a §11/§16
                               hook on the seller-response path; the helper
                               is here so the buyer side can emit when the
                               seller surfaces snapshot info on the wire)
  - "preflight_cache"       -- capability cache hit/miss (optional, lower
                               priority; helper supports it for §13)

Payload is a free-form JSON dict so we can grow event types without
schema changes. Helpers serialize Pydantic models cleanly via
`model_dump(mode="json")` and accept plain dicts as well.

Bead: ar-q2uh (proposal §5.7 + §6 row 13a).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import UTC, datetime
from typing import Any, Iterable

from .schema import AUDIENCE_AUDIT_LOG_INDEXES, AUDIENCE_AUDIT_LOG_TABLE

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event-type constants
# ---------------------------------------------------------------------------

EVENT_DEGRADATION = "degradation"
EVENT_CAPABILITY_REJECTION = "capability_rejection"
EVENT_SNAPSHOT_HONOR = "snapshot_honor"
EVENT_PREFLIGHT_CACHE = "preflight_cache"

KNOWN_EVENT_TYPES: frozenset[str] = frozenset(
    {
        EVENT_DEGRADATION,
        EVENT_CAPABILITY_REJECTION,
        EVENT_SNAPSHOT_HONOR,
        EVENT_PREFLIGHT_CACHE,
    }
)


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------
#
# The helpers keep their own connection pool so they can be called from any
# code path (orchestration, seller-response handling, future pre-flight) without
# requiring a `DealStore` reference. The connection is opened lazily on first
# use and reused; a write lock serializes inserts because sqlite3 connections
# are not thread-safe across simultaneous writes.

_DEFAULT_DATABASE_URL = "sqlite:///./ad_buyer.db"

_database_url: str = _DEFAULT_DATABASE_URL
_conn: sqlite3.Connection | None = None
_conn_lock = threading.Lock()


def configure(database_url: str) -> None:
    """Override the default database URL used by the helper.

    Tests and alternate configurations call this before the first
    `log_event` / `get_events`. Resets any cached connection so the next
    call re-opens against the new URL. Calling with the same URL is a no-op.

    Args:
        database_url: SQLite connection string (sqlite:///path or :memory:).
    """

    global _database_url, _conn
    with _conn_lock:
        if database_url == _database_url and _conn is not None:
            return
        _database_url = database_url
        if _conn is not None:
            try:
                _conn.close()
            except Exception:  # noqa: BLE001 -- best-effort cleanup
                pass
        _conn = None


def _parse_url(database_url: str) -> str:
    """Extract the file path from a sqlite:/// URL (mirrors DealStore)."""

    if database_url.startswith("sqlite:///"):
        return database_url[len("sqlite:///") :]
    return database_url


def _ensure_table(conn: sqlite3.Connection) -> None:
    """Create `audience_audit_log` if it does not already exist.

    Used on first connection and as the migration safety-net for the test
    that opens a DB created before this bead landed.
    """

    cursor = conn.cursor()
    cursor.execute(AUDIENCE_AUDIT_LOG_TABLE)
    for idx in AUDIENCE_AUDIT_LOG_INDEXES:
        cursor.execute(idx)
    conn.commit()


def _get_conn() -> sqlite3.Connection:
    """Return the shared connection, opening it lazily on first call."""

    global _conn
    if _conn is None:
        with _conn_lock:
            if _conn is None:
                path = _parse_url(_database_url)
                conn = sqlite3.connect(path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                # WAL is shared with the rest of the buyer DB so we don't fight
                # the deal store on the same file.
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                except sqlite3.OperationalError:
                    # `:memory:` and some test backends reject WAL mode --
                    # fail-open, the table still works in journal mode.
                    pass
                conn.execute("PRAGMA busy_timeout=5000")
                _ensure_table(conn)
                _conn = conn
    return _conn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Match the storage-layer ISO format used elsewhere in the buyer."""

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _to_json_safe(value: Any) -> Any:
    """Coerce common types into something `json.dumps` will accept.

    Handles Pydantic v2 models (via `model_dump(mode="json")`), iterables of
    them, and plain dicts/lists/scalars. Anything else falls through to
    `default=str` in `json.dumps` -- the audit log prioritizes "always
    writes something" over schema strictness.
    """

    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: _to_json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_to_json_safe(v) for v in value]
    return value


def log_event(
    plan_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Append a structured audit event to `audience_audit_log`.

    Append-only -- there is no update path. Safe to call from any thread:
    the underlying connection is shared and writes are serialized via the
    SQLite busy-timeout (5s) plus the connection lock.

    The function is fail-open: if the write raises, the error is logged at
    WARN and swallowed. Audit-log failures must NEVER fail the parent flow
    (orchestration, seller response handling). The whole point of the audit
    trail is to be a passive observer.

    Args:
        plan_id: The `audience_plan_id` of the plan this event refers to.
            Required, non-empty.
        event_type: One of the `EVENT_*` constants. Unknown event types are
            accepted (logged at WARN) so callers can experiment with new
            types ahead of constants landing here.
        payload: Free-form structured payload. Pydantic models are
            serialized via `model_dump(mode="json")`; lists/dicts are
            walked recursively. None is stored as `{}`.
    """

    if not plan_id:
        logger.warning(
            "audience_audit_log.log_event called with empty plan_id; skipping"
        )
        return

    if event_type not in KNOWN_EVENT_TYPES:
        # Don't reject -- callers may know about a newer event type than
        # this module. Just surface it at WARN so it shows up in logs.
        logger.warning(
            "audience_audit_log.log_event: unknown event_type=%r (allowed: %s)",
            event_type,
            sorted(KNOWN_EVENT_TYPES),
        )

    safe_payload = _to_json_safe(payload or {})
    try:
        payload_json = json.dumps(safe_payload, default=str, sort_keys=True)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "audience_audit_log.log_event: payload not JSON-serializable for "
            "plan_id=%s event_type=%s (%s); writing string repr",
            plan_id,
            event_type,
            exc,
        )
        payload_json = json.dumps({"_repr": repr(safe_payload)})

    created_at = _now_iso()
    try:
        conn = _get_conn()
        with _conn_lock:
            conn.execute(
                "INSERT INTO audience_audit_log "
                "(plan_id, event_type, payload_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                (plan_id, event_type, payload_json, created_at),
            )
            conn.commit()
    except sqlite3.Error as exc:  # noqa: BLE001 -- audit log is fail-open
        logger.warning(
            "audience_audit_log.log_event: failed to insert "
            "plan_id=%s event_type=%s: %s",
            plan_id,
            event_type,
            exc,
        )


def get_events(plan_id: str) -> list[dict[str, Any]]:
    """Read all events for a plan, oldest first.

    Each row is returned as a dict with keys ``plan_id``, ``event_type``,
    ``payload`` (already JSON-deserialized), and ``created_at``. The raw
    `payload_json` text is intentionally not exposed; callers that need
    it can re-serialize the dict.

    Args:
        plan_id: The `audience_plan_id` to read events for.

    Returns:
        A list of event dicts in `created_at` order, or an empty list when
        the plan has no events. Read failures are logged and return [].
    """

    if not plan_id:
        return []

    try:
        conn = _get_conn()
        cursor = conn.execute(
            "SELECT plan_id, event_type, payload_json, created_at "
            "FROM audience_audit_log "
            "WHERE plan_id = ? "
            "ORDER BY created_at ASC, rowid ASC",
            (plan_id,),
        )
        rows = cursor.fetchall()
    except sqlite3.Error as exc:  # noqa: BLE001 -- read is fail-open too
        logger.warning(
            "audience_audit_log.get_events: failed for plan_id=%s: %s",
            plan_id,
            exc,
        )
        return []

    events: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            # Truly malformed row -- surface raw text rather than dropping.
            payload = {"_raw": row["payload_json"]}
        events.append(
            {
                "plan_id": row["plan_id"],
                "event_type": row["event_type"],
                "payload": payload,
                "created_at": row["created_at"],
            }
        )
    return events


def _all_events() -> list[dict[str, Any]]:
    """Return every event in the table (test/debug helper).

    Not part of the public surface in the proposal. Useful for assertions
    that the table is empty or for end-to-end debugging.
    """

    try:
        conn = _get_conn()
        cursor = conn.execute(
            "SELECT plan_id, event_type, payload_json, created_at "
            "FROM audience_audit_log ORDER BY created_at ASC, rowid ASC"
        )
        rows = cursor.fetchall()
    except sqlite3.Error:
        return []

    events: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            payload = {"_raw": row["payload_json"]}
        events.append(
            {
                "plan_id": row["plan_id"],
                "event_type": row["event_type"],
                "payload": payload,
                "created_at": row["created_at"],
            }
        )
    return events


__all__ = [
    "EVENT_CAPABILITY_REJECTION",
    "EVENT_DEGRADATION",
    "EVENT_PREFLIGHT_CACHE",
    "EVENT_SNAPSHOT_HONOR",
    "KNOWN_EVENT_TYPES",
    "configure",
    "get_events",
    "log_event",
]


# Quiet unused-import linter for `Iterable` -- kept as a hint for future
# bulk-write helpers without bloating the public surface yet.
_ = Iterable
