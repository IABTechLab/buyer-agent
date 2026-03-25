# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Event bus implementations for the buyer system.

Provides abstract EventBus interface with InMemoryEventBus backend
for development and testing. Pure stdlib + Pydantic, no external deps.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable

from .models import Event

logger = logging.getLogger(__name__)

Subscriber = Callable[[Event], None]


class EventBus(ABC):
    """Abstract event bus interface."""

    @abstractmethod
    async def publish(self, event: Event) -> None:
        """Publish an event."""

    @abstractmethod
    async def subscribe(self, event_type: str, callback: Subscriber) -> None:
        """Subscribe to events of a given type.

        Args:
            event_type: EventType value or "*" for all events.
            callback: Function called when matching event arrives.
        """

    @abstractmethod
    async def get_event(self, event_id: str) -> Event | None:
        """Retrieve a persisted event by ID."""

    @abstractmethod
    async def list_events(
        self,
        flow_id: str | None = None,
        event_type: str | None = None,
        session_id: str | None = None,
        limit: int = 50,
    ) -> list[Event]:
        """List persisted events, optionally filtered."""


class InMemoryEventBus(EventBus):
    """In-memory event bus for development and testing.

    Events stored in a list. Subscribers called synchronously.
    No persistence across restarts.
    """

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._subscribers: dict[str, list[Subscriber]] = {}

    async def publish(self, event: Event) -> None:
        self._events.append(event)
        logger.info("Event published: %s (id=%s)", event.event_type, event.event_id)
        for cb in self._subscribers.get(event.event_type.value, []):
            try:
                cb(event)
            except Exception as e:  # noqa: BLE001 - subscriber isolation: one failure must not block others
                logger.error("Subscriber error for %s: %s", event.event_type, e)
        for cb in self._subscribers.get("*", []):
            try:
                cb(event)
            except Exception as e:  # noqa: BLE001 - subscriber isolation: one failure must not block others
                logger.error("Subscriber error (wildcard): %s", e)

    async def subscribe(self, event_type: str, callback: Subscriber) -> None:
        self._subscribers.setdefault(event_type, []).append(callback)

    async def get_event(self, event_id: str) -> Event | None:
        for ev in self._events:
            if ev.event_id == event_id:
                return ev
        return None

    async def list_events(
        self,
        flow_id: str | None = None,
        event_type: str | None = None,
        session_id: str | None = None,
        limit: int = 50,
    ) -> list[Event]:
        results = self._events
        if flow_id:
            results = [e for e in results if e.flow_id == flow_id]
        if event_type:
            results = [e for e in results if e.event_type.value == event_type]
        if session_id:
            results = [e for e in results if e.session_id == session_id]
        return results[-limit:]


# ---------------------------------------------------------------------------
# Factory / singleton
# ---------------------------------------------------------------------------

_event_bus_instance: EventBus | None = None


async def get_event_bus() -> EventBus:
    """Get or create the global event bus instance.

    Returns an InMemoryEventBus by default. The buyer system does not
    currently have a shared storage backend like the seller, so we
    always use the in-memory implementation.
    """
    global _event_bus_instance

    if _event_bus_instance is not None:
        return _event_bus_instance

    _event_bus_instance = InMemoryEventBus()
    return _event_bus_instance


async def close_event_bus() -> None:
    """Reset the global event bus instance."""
    global _event_bus_instance
    _event_bus_instance = None
