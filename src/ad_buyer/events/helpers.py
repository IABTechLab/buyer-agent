# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Helper functions for emitting events from flows.

Thin wrappers that flows call. For most events, if the event bus is not
configured or fails, they log and continue (fail-open).

Audit-class events (AUDIT_EVENT_TYPES in models.py) are fail-closed: if
the bus fails, the event is written to a durable fallback JSONL file
(see audit_fallback.py); if that also fails, the error propagates so the
calling transaction surfaces it instead of silently losing audit trail.

Provides both async (emit_event) and sync (emit_event_sync) variants.
The sync variant is needed because CrewAI flow methods run synchronously
in worker threads that may not have an asyncio event loop.
"""

import asyncio
import logging
from typing import Any

from .audit_fallback import write_audit_fallback
from .models import AUDIT_EVENT_TYPES, Event, EventType

logger = logging.getLogger(__name__)


def _audit_fallback_record(
    event: Event | None,
    event_type: EventType,
    flow_id: str,
    flow_type: str,
    deal_id: str,
    session_id: str,
    payload: dict[str, Any] | None,
    metadata: dict[str, Any],
    error: Exception,
) -> dict[str, Any]:
    """Build the JSONL record for an audit event that failed to publish.

    Uses the constructed Event when available; otherwise reconstructs the
    record from the emit arguments (e.g. when the bus factory itself failed
    before the Event was built).
    """
    if event is not None:
        record = event.model_dump(mode="json")
    else:
        record = {
            "event_type": event_type.value,
            "flow_id": flow_id,
            "flow_type": flow_type,
            "deal_id": deal_id,
            "session_id": session_id,
            "payload": payload or {},
            "metadata": metadata,
        }
    record["emit_error"] = str(error)
    return record


def _handle_emit_failure(
    event: Event | None,
    event_type: EventType,
    flow_id: str,
    flow_type: str,
    deal_id: str,
    session_id: str,
    payload: dict[str, Any] | None,
    metadata: dict[str, Any],
    error: Exception,
) -> None:
    """Shared failure path for emit_event / emit_event_sync.

    Non-audit events: log and swallow (fail-open, unchanged behavior).
    Audit events: write to the durable fallback JSONL; if that also fails,
    re-raise (fail-closed).
    """
    if event_type not in AUDIT_EVENT_TYPES:
        logger.warning("Failed to emit event %s: %s", event_type, error)
        return

    record = _audit_fallback_record(
        event, event_type, flow_id, flow_type, deal_id, session_id, payload, metadata, error
    )
    try:
        write_audit_fallback(record)
    except Exception as fallback_error:
        logger.error(
            "Audit fallback write failed for %s: %s (bus error: %s)",
            event_type,
            fallback_error,
            error,
        )
        raise
    logger.warning(
        "Event bus failed for audit event %s; wrote to fallback log: %s",
        event_type,
        error,
    )


async def emit_event(
    event_type: EventType,
    flow_id: str = "",
    flow_type: str = "",
    deal_id: str = "",
    session_id: str = "",
    payload: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Event | None:
    """Emit an event to the event bus.

    Fail-open for regular events: logs on error and returns None.
    Fail-closed for audit-class events: on bus failure the event is written
    to the fallback JSONL (returns None); if the fallback write also fails,
    the exception propagates.

    Returns the Event if published, None if the bus was unavailable.
    """
    event: Event | None = None
    try:
        from .bus import get_event_bus

        bus = await get_event_bus()
        event = Event(
            event_type=event_type,
            flow_id=flow_id,
            flow_type=flow_type,
            deal_id=deal_id,
            session_id=session_id,
            payload=payload or {},
            metadata=kwargs,
        )
        await bus.publish(event)
        return event
    except Exception as e:  # noqa: BLE001 - fail-open for non-audit; audit types fall back / re-raise
        _handle_emit_failure(
            event, event_type, flow_id, flow_type, deal_id, session_id, payload, kwargs, e
        )
        return None


def emit_event_sync(
    event_type: EventType,
    flow_id: str = "",
    flow_type: str = "",
    deal_id: str = "",
    session_id: str = "",
    payload: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Event | None:
    """Synchronous wrapper around emit_event for use in CrewAI flows.

    CrewAI flow methods run synchronously in worker threads. This helper
    handles the asyncio plumbing so callers don't have to.

    Fail-open for regular events: never raises, returns None on error.
    Fail-closed for audit-class events: on bus failure the event is written
    to the fallback JSONL; if that also fails, the exception propagates.
    """
    event: Event | None = None
    try:
        from .bus import InMemoryEventBus, _event_bus_instance

        # Fast path: if the singleton is an InMemoryEventBus, call
        # publish directly via a new event loop to avoid issues with
        # nested loops in worker threads.
        bus = _event_bus_instance
        if bus is None:
            bus = InMemoryEventBus()
            import ad_buyer.events.bus as bus_mod

            bus_mod._event_bus_instance = bus

        event = Event(
            event_type=event_type,
            flow_id=flow_id,
            flow_type=flow_type,
            deal_id=deal_id,
            session_id=session_id,
            payload=payload or {},
            metadata=kwargs,
        )

        # Run the async publish in a new event loop (safe from worker threads)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Already in an async context -- schedule on the running loop.
                # Fire-and-forget: a publish failure here cannot propagate to
                # the caller, so for audit events attach a callback that
                # writes the fallback record (best effort, logged if it fails).
                future = asyncio.ensure_future(bus.publish(event))
                if event_type in AUDIT_EVENT_TYPES:
                    future.add_done_callback(
                        lambda fut, ev=event: _audit_publish_done(fut, ev)
                    )
            else:
                loop.run_until_complete(bus.publish(event))
        except RuntimeError:
            # No event loop at all -- create one
            asyncio.run(bus.publish(event))

        return event
    except Exception as e:  # noqa: BLE001 - fail-open for non-audit; audit types fall back / re-raise
        _handle_emit_failure(
            event, event_type, flow_id, flow_type, deal_id, session_id, payload, kwargs, e
        )
        return None


def _audit_publish_done(future: "asyncio.Future[None]", event: Event) -> None:
    """Done-callback for fire-and-forget audit publishes from emit_event_sync.

    Cannot fail closed (the caller has already moved on), so on publish
    failure it writes the fallback record and logs critically if even that
    fails.
    """
    exc = future.exception()
    if exc is None:
        return
    record = event.model_dump(mode="json")
    record["emit_error"] = str(exc)
    try:
        write_audit_fallback(record)
        logger.warning(
            "Async publish failed for audit event %s; wrote to fallback log: %s",
            event.event_type,
            exc,
        )
    except Exception as fallback_error:  # noqa: BLE001 - last resort, nothing left to raise into
        logger.critical(
            "AUDIT EVENT LOST: %s (publish error: %s; fallback error: %s)",
            event.event_type,
            exc,
            fallback_error,
        )
