# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for buyer event bus: models, bus, helpers, and API endpoints."""

import asyncio
from datetime import datetime
from unittest.mock import patch

import pytest

from ad_buyer.events.bus import EventBus

# ---------------------------------------------------------------------------
# EventType enum tests
# ---------------------------------------------------------------------------


class TestEventType:
    """Tests for buyer-specific EventType enum."""

    def test_event_type_values_exist(self):
        """All buyer event types must be defined."""
        from ad_buyer.events.models import EventType

        expected = [
            "quote.requested",
            "quote.received",
            "deal.booked",
            "deal.cancelled",
            "campaign.created",
            "budget.allocated",
            "booking.submitted",
            "inventory.discovered",
            "negotiation.started",
            "negotiation.round",
            "negotiation.concluded",
            "session.created",
            "session.closed",
        ]
        actual_values = [e.value for e in EventType]
        for val in expected:
            assert val in actual_values, f"Missing EventType value: {val}"

    def test_event_type_is_string_enum(self):
        """EventType should be a str Enum for JSON serialization."""
        from ad_buyer.events.models import EventType

        assert isinstance(EventType.QUOTE_REQUESTED, str)
        assert EventType.QUOTE_REQUESTED == "quote.requested"


# ---------------------------------------------------------------------------
# Event model tests
# ---------------------------------------------------------------------------


class TestEventModel:
    """Tests for the Event Pydantic model."""

    def test_event_creation_defaults(self):
        """Event should auto-generate id and timestamp."""
        from ad_buyer.events.models import Event, EventType

        event = Event(event_type=EventType.DEAL_BOOKED)
        assert event.event_id  # non-empty
        assert isinstance(event.timestamp, datetime)
        assert event.flow_id == ""
        assert event.payload == {}

    def test_event_creation_with_values(self):
        """Event can be created with all fields."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.QUOTE_REQUESTED,
            flow_id="flow-1",
            flow_type="deal_booking",
            deal_id="deal-42",
            session_id="sess-7",
            payload={"amount": 1000},
            metadata={"source": "test"},
        )
        assert event.event_type == EventType.QUOTE_REQUESTED
        assert event.flow_id == "flow-1"
        assert event.deal_id == "deal-42"
        assert event.payload["amount"] == 1000

    def test_event_serialization(self):
        """Event should serialize to dict for storage."""
        from ad_buyer.events.models import Event, EventType

        event = Event(event_type=EventType.CAMPAIGN_CREATED, payload={"name": "Q1"})
        data = event.model_dump(mode="json")
        assert data["event_type"] == "campaign.created"
        assert isinstance(data["timestamp"], str)


# ---------------------------------------------------------------------------
# InMemoryEventBus tests
# ---------------------------------------------------------------------------


class TestInMemoryEventBus:
    """Tests for the InMemoryEventBus implementation."""

    @pytest.fixture
    def bus(self):
        from ad_buyer.events.bus import InMemoryEventBus

        return InMemoryEventBus()

    def test_publish_stores_event(self, bus):
        """Published event should be stored."""
        from ad_buyer.events.models import Event, EventType

        event = Event(event_type=EventType.DEAL_BOOKED)
        asyncio.get_event_loop().run_until_complete(bus.publish(event))
        assert len(bus._events) == 1
        assert bus._events[0].event_id == event.event_id

    def test_subscribe_receives_event(self, bus):
        """Subscriber should be called on matching event type."""
        from ad_buyer.events.models import Event, EventType

        received = []
        callback = lambda e: received.append(e)

        asyncio.get_event_loop().run_until_complete(bus.subscribe("deal.booked", callback))

        event = Event(event_type=EventType.DEAL_BOOKED)
        asyncio.get_event_loop().run_until_complete(bus.publish(event))

        assert len(received) == 1
        assert received[0].event_id == event.event_id

    def test_wildcard_subscriber(self, bus):
        """Wildcard subscriber should receive all events."""
        from ad_buyer.events.models import Event, EventType

        received = []
        asyncio.get_event_loop().run_until_complete(
            bus.subscribe("*", lambda e: received.append(e))
        )

        e1 = Event(event_type=EventType.DEAL_BOOKED)
        e2 = Event(event_type=EventType.CAMPAIGN_CREATED)
        asyncio.get_event_loop().run_until_complete(bus.publish(e1))
        asyncio.get_event_loop().run_until_complete(bus.publish(e2))

        assert len(received) == 2

    def test_subscriber_error_does_not_break_bus(self, bus):
        """Subscriber error should be caught; bus continues."""
        from ad_buyer.events.models import Event, EventType

        def bad_callback(e):
            raise RuntimeError("boom")

        asyncio.get_event_loop().run_until_complete(bus.subscribe("deal.booked", bad_callback))

        event = Event(event_type=EventType.DEAL_BOOKED)
        # Should not raise
        asyncio.get_event_loop().run_until_complete(bus.publish(event))
        assert len(bus._events) == 1

    def test_get_event(self, bus):
        """Should retrieve a stored event by ID."""
        from ad_buyer.events.models import Event, EventType

        event = Event(event_type=EventType.BUDGET_ALLOCATED)
        asyncio.get_event_loop().run_until_complete(bus.publish(event))

        found = asyncio.get_event_loop().run_until_complete(bus.get_event(event.event_id))
        assert found is not None
        assert found.event_id == event.event_id

    def test_get_event_not_found(self, bus):
        """Should return None for unknown event ID."""
        result = asyncio.get_event_loop().run_until_complete(bus.get_event("nonexistent"))
        assert result is None

    def test_list_events_no_filter(self, bus):
        """Should list all events with no filter."""
        from ad_buyer.events.models import Event, EventType

        for _ in range(3):
            asyncio.get_event_loop().run_until_complete(
                bus.publish(Event(event_type=EventType.DEAL_BOOKED))
            )

        events = asyncio.get_event_loop().run_until_complete(bus.list_events())
        assert len(events) == 3

    def test_list_events_by_flow_id(self, bus):
        """Should filter events by flow_id."""
        from ad_buyer.events.models import Event, EventType

        asyncio.get_event_loop().run_until_complete(
            bus.publish(Event(event_type=EventType.DEAL_BOOKED, flow_id="f1"))
        )
        asyncio.get_event_loop().run_until_complete(
            bus.publish(Event(event_type=EventType.DEAL_BOOKED, flow_id="f2"))
        )

        events = asyncio.get_event_loop().run_until_complete(bus.list_events(flow_id="f1"))
        assert len(events) == 1
        assert events[0].flow_id == "f1"

    def test_list_events_by_event_type(self, bus):
        """Should filter events by event_type."""
        from ad_buyer.events.models import Event, EventType

        asyncio.get_event_loop().run_until_complete(
            bus.publish(Event(event_type=EventType.DEAL_BOOKED))
        )
        asyncio.get_event_loop().run_until_complete(
            bus.publish(Event(event_type=EventType.CAMPAIGN_CREATED))
        )

        events = asyncio.get_event_loop().run_until_complete(
            bus.list_events(event_type="deal.booked")
        )
        assert len(events) == 1

    def test_list_events_by_session_id(self, bus):
        """Should filter events by session_id."""
        from ad_buyer.events.models import Event, EventType

        asyncio.get_event_loop().run_until_complete(
            bus.publish(Event(event_type=EventType.SESSION_CREATED, session_id="s1"))
        )
        asyncio.get_event_loop().run_until_complete(
            bus.publish(Event(event_type=EventType.SESSION_CLOSED, session_id="s2"))
        )

        events = asyncio.get_event_loop().run_until_complete(bus.list_events(session_id="s1"))
        assert len(events) == 1

    def test_list_events_limit(self, bus):
        """Should respect the limit parameter."""
        from ad_buyer.events.models import Event, EventType

        for _ in range(10):
            asyncio.get_event_loop().run_until_complete(
                bus.publish(Event(event_type=EventType.DEAL_BOOKED))
            )

        events = asyncio.get_event_loop().run_until_complete(bus.list_events(limit=3))
        assert len(events) == 3


# ---------------------------------------------------------------------------
# emit_event helper tests
# ---------------------------------------------------------------------------


class TestEmitEvent:
    """Tests for the fail-open emit_event helper."""

    def test_emit_event_success(self):
        """emit_event should publish and return the Event."""
        # Reset singleton
        import ad_buyer.events.bus as bus_mod
        from ad_buyer.events.helpers import emit_event
        from ad_buyer.events.models import EventType

        bus_mod._event_bus_instance = None

        event = asyncio.get_event_loop().run_until_complete(
            emit_event(
                event_type=EventType.DEAL_BOOKED,
                flow_id="f1",
                deal_id="d1",
                payload={"price": 15.0},
            )
        )
        assert event is not None
        assert event.event_type == EventType.DEAL_BOOKED
        assert event.deal_id == "d1"

        # Cleanup
        bus_mod._event_bus_instance = None

    def test_emit_event_fail_open(self):
        """emit_event should return None on failure, not raise."""
        import ad_buyer.events.bus as bus_mod
        from ad_buyer.events.helpers import emit_event
        from ad_buyer.events.models import EventType

        bus_mod._event_bus_instance = None

        with patch(
            "ad_buyer.events.bus.get_event_bus",
            side_effect=RuntimeError("bus down"),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                emit_event(event_type=EventType.DEAL_BOOKED)
            )
            assert result is None

        bus_mod._event_bus_instance = None


# ---------------------------------------------------------------------------
# get_event_bus singleton tests
# ---------------------------------------------------------------------------


class TestGetEventBus:
    """Tests for the get_event_bus factory."""

    def test_returns_in_memory_bus(self):
        """Should return an InMemoryEventBus by default."""
        import ad_buyer.events.bus as bus_mod
        from ad_buyer.events.bus import InMemoryEventBus, get_event_bus

        bus_mod._event_bus_instance = None

        bus = asyncio.get_event_loop().run_until_complete(get_event_bus())
        assert isinstance(bus, InMemoryEventBus)

        bus_mod._event_bus_instance = None

    def test_returns_same_instance(self):
        """Should return the same singleton instance."""
        import ad_buyer.events.bus as bus_mod
        from ad_buyer.events.bus import get_event_bus

        bus_mod._event_bus_instance = None

        bus1 = asyncio.get_event_loop().run_until_complete(get_event_bus())
        bus2 = asyncio.get_event_loop().run_until_complete(get_event_bus())
        assert bus1 is bus2

        bus_mod._event_bus_instance = None

    def test_close_resets_singleton(self):
        """close_event_bus should reset the singleton."""
        import ad_buyer.events.bus as bus_mod
        from ad_buyer.events.bus import close_event_bus, get_event_bus

        bus_mod._event_bus_instance = None

        bus1 = asyncio.get_event_loop().run_until_complete(get_event_bus())
        asyncio.get_event_loop().run_until_complete(close_event_bus())

        bus2 = asyncio.get_event_loop().run_until_complete(get_event_bus())
        assert bus1 is not bus2

        bus_mod._event_bus_instance = None


# ---------------------------------------------------------------------------
# Module __init__ exports
# ---------------------------------------------------------------------------


class TestModuleExports:
    """Tests for events module public API."""

    def test_public_api(self):
        """Events module should export all key names."""
        import ad_buyer.events as events_mod

        assert hasattr(events_mod, "Event")
        assert hasattr(events_mod, "EventType")
        assert hasattr(events_mod, "EventBus")
        assert hasattr(events_mod, "InMemoryEventBus")
        assert hasattr(events_mod, "get_event_bus")
        assert hasattr(events_mod, "close_event_bus")
        assert hasattr(events_mod, "emit_event")


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


class TestEventAPIEndpoints:
    """Tests for GET /events and GET /events/{event_id} endpoints."""

    @pytest.fixture
    def client(self):
        """Create a test client."""
        from fastapi.testclient import TestClient

        from ad_buyer.interfaces.api.main import app

        return TestClient(app)

    def test_get_events_empty(self, client):
        """GET /events should return empty list when no events."""
        response = client.get("/events")
        assert response.status_code == 200
        data = response.json()
        assert "events" in data
        assert isinstance(data["events"], list)

    def test_get_event_not_found(self, client):
        """GET /events/{event_id} should return 404 for unknown ID."""
        response = client.get("/events/nonexistent-id")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# DealStore event persistence tests (optional feature)
# ---------------------------------------------------------------------------


class TestDealStoreEventPersistence:
    """Tests for SQLite-backed event persistence via DealStore."""

    @pytest.fixture
    def store(self):
        """Create an in-memory DealStore."""
        from ad_buyer.storage.deal_store import DealStore

        s = DealStore("sqlite:///:memory:")
        s.connect()
        yield s
        s.disconnect()

    def test_save_and_get_event(self, store):
        """Should save and retrieve an event."""
        event_id = store.save_event(
            event_type="deal.booked",
            flow_id="f1",
            deal_id="d1",
            payload='{"price": 15.0}',
        )
        assert event_id

        event = store.get_event(event_id)
        assert event is not None
        assert event["event_type"] == "deal.booked"
        assert event["flow_id"] == "f1"
        assert event["deal_id"] == "d1"

    def test_list_events_filtered(self, store):
        """Should filter events by type and flow_id."""
        store.save_event(event_type="deal.booked", flow_id="f1")
        store.save_event(event_type="deal.cancelled", flow_id="f1")
        store.save_event(event_type="deal.booked", flow_id="f2")

        # Filter by type
        events = store.list_events(event_type="deal.booked")
        assert len(events) == 2

        # Filter by flow_id
        events = store.list_events(flow_id="f1")
        assert len(events) == 2

        # Filter by both
        events = store.list_events(event_type="deal.booked", flow_id="f1")
        assert len(events) == 1

    def test_list_events_limit(self, store):
        """Should respect limit parameter."""
        for _ in range(10):
            store.save_event(event_type="deal.booked")

        events = store.list_events(limit=3)
        assert len(events) == 3
