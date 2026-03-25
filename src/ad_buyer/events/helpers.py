# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Helper functions for emitting events from flows.

Thin wrappers that flows call. If the event bus is not configured
or fails, they log and continue (fail-open).

Provides both async (emit_event) and sync (emit_event_sync) variants.
The sync variant is needed because CrewAI flow methods run synchronously
in worker threads that may not have an asyncio event loop.
"""

import asyncio
import logging
from typing import Any

from .models import Event, EventType

logger = logging.getLogger(__name__)


async def emit_event(
    event_type: EventType,
    flow_id: str = "",
    flow_type: str = "",
    deal_id: str = "",
    session_id: str = "",
    payload: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Event | None:
    """Emit an event to the event bus. Fail-open: logs on error.

    Returns the Event if published, None if the bus was unavailable.
    """
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
    except Exception as e:  # noqa: BLE001 - event emission is fail-open by design
        logger.warning("Failed to emit event %s: %s", event_type, e)
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

    Fail-open: never raises, returns None on error.
    """
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
                # Already in an async context -- schedule on the running loop
                asyncio.ensure_future(bus.publish(event))
            else:
                loop.run_until_complete(bus.publish(event))
        except RuntimeError:
            # No event loop at all -- create one
            asyncio.run(bus.publish(event))

        return event
    except Exception as e:  # noqa: BLE001 - event emission is fail-open by design
        logger.warning("Failed to emit event (sync) %s: %s", event_type, e)
        return None
