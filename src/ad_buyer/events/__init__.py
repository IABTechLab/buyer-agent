# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Event bus for buyer workflow observability and control."""

from .audit_fallback import get_audit_fallback_path, write_audit_fallback
from .bus import EventBus, InMemoryEventBus, close_event_bus, get_event_bus
from .helpers import emit_event, emit_event_sync
from .models import AUDIT_EVENT_TYPES, Event, EventType

__all__ = [
    "AUDIT_EVENT_TYPES",
    "Event",
    "EventType",
    "EventBus",
    "InMemoryEventBus",
    "get_event_bus",
    "close_event_bus",
    "emit_event",
    "emit_event_sync",
    "get_audit_fallback_path",
    "write_audit_fallback",
]
