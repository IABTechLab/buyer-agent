# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for DealJockey Phase 1 event types.

Verifies that the four Phase 1 DealJockey event types are properly
defined, can be used to create Event instances, can be emitted on
the event bus, and can be persisted via DealStore.save_event().
"""

import asyncio
import json

import pytest


# ---------------------------------------------------------------------------
# EventType enum tests - Phase 1 DealJockey events
# ---------------------------------------------------------------------------


class TestDealJockeyEventTypes:
    """Tests for DealJockey Phase 1 EventType enum values."""

    def test_deal_imported_exists(self):
        """DEAL_IMPORTED event type must exist with correct value."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "DEAL_IMPORTED")
        assert EventType.DEAL_IMPORTED == "deal.imported"
        assert EventType.DEAL_IMPORTED.value == "deal.imported"

    def test_deal_template_created_exists(self):
        """DEAL_TEMPLATE_CREATED event type must exist with correct value."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "DEAL_TEMPLATE_CREATED")
        assert EventType.DEAL_TEMPLATE_CREATED == "deal.template_created"
        assert EventType.DEAL_TEMPLATE_CREATED.value == "deal.template_created"

    def test_portfolio_inspected_exists(self):
        """PORTFOLIO_INSPECTED event type must exist with correct value."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "PORTFOLIO_INSPECTED")
        assert EventType.PORTFOLIO_INSPECTED == "portfolio.inspected"
        assert EventType.PORTFOLIO_INSPECTED.value == "portfolio.inspected"

    def test_deal_manual_action_required_exists(self):
        """DEAL_MANUAL_ACTION_REQUIRED event type must exist with correct value."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "DEAL_MANUAL_ACTION_REQUIRED")
        assert EventType.DEAL_MANUAL_ACTION_REQUIRED == "deal.manual_action_required"
        assert (
            EventType.DEAL_MANUAL_ACTION_REQUIRED.value
            == "deal.manual_action_required"
        )

    def test_all_phase1_types_in_enum(self):
        """All four Phase 1 DealJockey event types must be in EventType."""
        from ad_buyer.events.models import EventType

        expected_phase1 = [
            "deal.imported",
            "deal.template_created",
            "portfolio.inspected",
            "deal.manual_action_required",
        ]
        actual_values = [e.value for e in EventType]
        for val in expected_phase1:
            assert val in actual_values, f"Missing Phase 1 event type: {val}"

    def test_phase1_types_are_string_enum(self):
        """Phase 1 DealJockey types should be str Enum for JSON serialization."""
        from ad_buyer.events.models import EventType

        assert isinstance(EventType.DEAL_IMPORTED, str)
        assert isinstance(EventType.DEAL_TEMPLATE_CREATED, str)
        assert isinstance(EventType.PORTFOLIO_INSPECTED, str)
        assert isinstance(EventType.DEAL_MANUAL_ACTION_REQUIRED, str)


# ---------------------------------------------------------------------------
# Event model tests with Phase 1 types
# ---------------------------------------------------------------------------


class TestDealJockeyEventModel:
    """Tests for creating Event instances with DealJockey Phase 1 types."""

    def test_event_with_deal_imported(self):
        """Event should be creatable with DEAL_IMPORTED type."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.DEAL_IMPORTED,
            deal_id="deal-import-1",
            payload={"import_source": "csv", "file_name": "deals.csv"},
        )
        assert event.event_type == EventType.DEAL_IMPORTED
        assert event.deal_id == "deal-import-1"
        assert event.payload["import_source"] == "csv"

    def test_event_with_deal_template_created(self):
        """Event should be creatable with DEAL_TEMPLATE_CREATED type."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.DEAL_TEMPLATE_CREATED,
            payload={"template_id": "tmpl-1", "template_name": "Standard PMP"},
        )
        assert event.event_type == EventType.DEAL_TEMPLATE_CREATED
        assert event.payload["template_id"] == "tmpl-1"

    def test_event_with_portfolio_inspected(self):
        """Event should be creatable with PORTFOLIO_INSPECTED type."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.PORTFOLIO_INSPECTED,
            payload={"query_params": {"status": "active"}, "result_count": 42},
        )
        assert event.event_type == EventType.PORTFOLIO_INSPECTED
        assert event.payload["result_count"] == 42

    def test_event_with_deal_manual_action_required(self):
        """Event should be creatable with DEAL_MANUAL_ACTION_REQUIRED type."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.DEAL_MANUAL_ACTION_REQUIRED,
            deal_id="deal-99",
            payload={
                "action_description": "Seller requires manual deal setup",
                "reason": "No API available",
            },
        )
        assert event.event_type == EventType.DEAL_MANUAL_ACTION_REQUIRED
        assert event.deal_id == "deal-99"
        assert "action_description" in event.payload

    def test_phase1_event_serialization(self):
        """Phase 1 events should serialize to dict correctly."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.DEAL_IMPORTED,
            payload={"source": "csv"},
        )
        data = event.model_dump(mode="json")
        assert data["event_type"] == "deal.imported"
        assert isinstance(data["timestamp"], str)
        assert data["payload"]["source"] == "csv"


# ---------------------------------------------------------------------------
# EventBus tests with Phase 1 types
# ---------------------------------------------------------------------------


class TestDealJockeyEventBus:
    """Tests for emitting DealJockey Phase 1 events on the bus."""

    @pytest.fixture
    def bus(self):
        from ad_buyer.events.bus import InMemoryEventBus

        return InMemoryEventBus()

    def test_publish_deal_imported(self, bus):
        """DEAL_IMPORTED events should be publishable on the bus."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.DEAL_IMPORTED,
            payload={"import_source": "csv"},
        )
        asyncio.get_event_loop().run_until_complete(bus.publish(event))
        assert len(bus._events) == 1
        assert bus._events[0].event_type == EventType.DEAL_IMPORTED

    def test_publish_deal_template_created(self, bus):
        """DEAL_TEMPLATE_CREATED events should be publishable on the bus."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.DEAL_TEMPLATE_CREATED,
            payload={"template_id": "tmpl-1"},
        )
        asyncio.get_event_loop().run_until_complete(bus.publish(event))
        assert len(bus._events) == 1
        assert bus._events[0].event_type == EventType.DEAL_TEMPLATE_CREATED

    def test_publish_portfolio_inspected(self, bus):
        """PORTFOLIO_INSPECTED events should be publishable on the bus."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.PORTFOLIO_INSPECTED,
            payload={"result_count": 10},
        )
        asyncio.get_event_loop().run_until_complete(bus.publish(event))
        assert len(bus._events) == 1
        assert bus._events[0].event_type == EventType.PORTFOLIO_INSPECTED

    def test_publish_deal_manual_action_required(self, bus):
        """DEAL_MANUAL_ACTION_REQUIRED events should be publishable."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.DEAL_MANUAL_ACTION_REQUIRED,
            deal_id="deal-99",
            payload={"action_description": "Manual setup needed"},
        )
        asyncio.get_event_loop().run_until_complete(bus.publish(event))
        assert len(bus._events) == 1
        assert bus._events[0].event_type == EventType.DEAL_MANUAL_ACTION_REQUIRED

    def test_subscribe_to_deal_imported(self, bus):
        """Subscriber should receive DEAL_IMPORTED events."""
        from ad_buyer.events.models import Event, EventType

        received = []
        asyncio.get_event_loop().run_until_complete(
            bus.subscribe("deal.imported", lambda e: received.append(e))
        )

        event = Event(event_type=EventType.DEAL_IMPORTED)
        asyncio.get_event_loop().run_until_complete(bus.publish(event))

        assert len(received) == 1
        assert received[0].event_type == EventType.DEAL_IMPORTED

    def test_subscribe_to_deal_manual_action_required(self, bus):
        """Subscriber should receive DEAL_MANUAL_ACTION_REQUIRED events."""
        from ad_buyer.events.models import Event, EventType

        received = []
        asyncio.get_event_loop().run_until_complete(
            bus.subscribe(
                "deal.manual_action_required", lambda e: received.append(e)
            )
        )

        event = Event(event_type=EventType.DEAL_MANUAL_ACTION_REQUIRED)
        asyncio.get_event_loop().run_until_complete(bus.publish(event))

        assert len(received) == 1
        assert received[0].event_type == EventType.DEAL_MANUAL_ACTION_REQUIRED

    def test_list_events_by_phase1_type(self, bus):
        """Should filter events by Phase 1 event types."""
        from ad_buyer.events.models import Event, EventType

        asyncio.get_event_loop().run_until_complete(
            bus.publish(Event(event_type=EventType.DEAL_IMPORTED))
        )
        asyncio.get_event_loop().run_until_complete(
            bus.publish(Event(event_type=EventType.DEAL_BOOKED))
        )
        asyncio.get_event_loop().run_until_complete(
            bus.publish(Event(event_type=EventType.PORTFOLIO_INSPECTED))
        )

        events = asyncio.get_event_loop().run_until_complete(
            bus.list_events(event_type="deal.imported")
        )
        assert len(events) == 1
        assert events[0].event_type == EventType.DEAL_IMPORTED

    def test_wildcard_receives_phase1_events(self, bus):
        """Wildcard subscriber should receive Phase 1 events."""
        from ad_buyer.events.models import Event, EventType

        received = []
        asyncio.get_event_loop().run_until_complete(
            bus.subscribe("*", lambda e: received.append(e))
        )

        asyncio.get_event_loop().run_until_complete(
            bus.publish(Event(event_type=EventType.DEAL_IMPORTED))
        )
        asyncio.get_event_loop().run_until_complete(
            bus.publish(Event(event_type=EventType.DEAL_TEMPLATE_CREATED))
        )

        assert len(received) == 2


# ---------------------------------------------------------------------------
# DealStore persistence tests with Phase 1 types
# ---------------------------------------------------------------------------


class TestDealJockeyEventPersistence:
    """Tests for persisting Phase 1 DealJockey events via DealStore."""

    @pytest.fixture
    def store(self):
        """Create an in-memory DealStore."""
        from ad_buyer.storage.deal_store import DealStore

        s = DealStore("sqlite:///:memory:")
        s.connect()
        yield s
        s.disconnect()

    def test_persist_deal_imported(self, store):
        """DEAL_IMPORTED event should be persistable via DealStore."""
        event_id = store.save_event(
            event_type="deal.imported",
            deal_id="deal-import-1",
            payload=json.dumps({"import_source": "csv", "file_name": "deals.csv"}),
        )
        assert event_id

        event = store.get_event(event_id)
        assert event is not None
        assert event["event_type"] == "deal.imported"
        assert event["deal_id"] == "deal-import-1"

    def test_persist_deal_template_created(self, store):
        """DEAL_TEMPLATE_CREATED event should be persistable via DealStore."""
        event_id = store.save_event(
            event_type="deal.template_created",
            payload=json.dumps({"template_id": "tmpl-1"}),
        )
        assert event_id

        event = store.get_event(event_id)
        assert event is not None
        assert event["event_type"] == "deal.template_created"

    def test_persist_portfolio_inspected(self, store):
        """PORTFOLIO_INSPECTED event should be persistable via DealStore."""
        event_id = store.save_event(
            event_type="portfolio.inspected",
            payload=json.dumps({"query_params": {}, "result_count": 42}),
        )
        assert event_id

        event = store.get_event(event_id)
        assert event is not None
        assert event["event_type"] == "portfolio.inspected"

    def test_persist_deal_manual_action_required(self, store):
        """DEAL_MANUAL_ACTION_REQUIRED event should be persistable."""
        event_id = store.save_event(
            event_type="deal.manual_action_required",
            deal_id="deal-99",
            payload=json.dumps({"action_description": "Manual setup needed"}),
        )
        assert event_id

        event = store.get_event(event_id)
        assert event is not None
        assert event["event_type"] == "deal.manual_action_required"
        assert event["deal_id"] == "deal-99"

    def test_list_phase1_events_by_type(self, store):
        """Should filter persisted events by Phase 1 type."""
        store.save_event(event_type="deal.imported")
        store.save_event(event_type="deal.template_created")
        store.save_event(event_type="portfolio.inspected")
        store.save_event(event_type="deal.manual_action_required")
        store.save_event(event_type="deal.booked")  # existing type

        events = store.list_events(event_type="deal.imported")
        assert len(events) == 1
        assert events[0]["event_type"] == "deal.imported"

        events = store.list_events(event_type="portfolio.inspected")
        assert len(events) == 1
        assert events[0]["event_type"] == "portfolio.inspected"


# ---------------------------------------------------------------------------
# emit_event helper tests with Phase 1 types
# ---------------------------------------------------------------------------


class TestDealJockeyEmitEvent:
    """Tests for emitting Phase 1 events via the emit_event helper."""

    def test_emit_deal_imported(self):
        """emit_event should handle DEAL_IMPORTED type."""
        from ad_buyer.events.helpers import emit_event
        from ad_buyer.events.models import EventType

        import ad_buyer.events.bus as bus_mod

        bus_mod._event_bus_instance = None

        event = asyncio.get_event_loop().run_until_complete(
            emit_event(
                event_type=EventType.DEAL_IMPORTED,
                deal_id="deal-import-1",
                payload={"import_source": "csv"},
            )
        )
        assert event is not None
        assert event.event_type == EventType.DEAL_IMPORTED
        assert event.deal_id == "deal-import-1"

        bus_mod._event_bus_instance = None

    def test_emit_portfolio_inspected(self):
        """emit_event should handle PORTFOLIO_INSPECTED type."""
        from ad_buyer.events.helpers import emit_event
        from ad_buyer.events.models import EventType

        import ad_buyer.events.bus as bus_mod

        bus_mod._event_bus_instance = None

        event = asyncio.get_event_loop().run_until_complete(
            emit_event(
                event_type=EventType.PORTFOLIO_INSPECTED,
                payload={"result_count": 15},
            )
        )
        assert event is not None
        assert event.event_type == EventType.PORTFOLIO_INSPECTED

        bus_mod._event_bus_instance = None

    def test_emit_deal_manual_action_required(self):
        """emit_event should handle DEAL_MANUAL_ACTION_REQUIRED type."""
        from ad_buyer.events.helpers import emit_event
        from ad_buyer.events.models import EventType

        import ad_buyer.events.bus as bus_mod

        bus_mod._event_bus_instance = None

        event = asyncio.get_event_loop().run_until_complete(
            emit_event(
                event_type=EventType.DEAL_MANUAL_ACTION_REQUIRED,
                deal_id="deal-99",
                payload={"action_description": "Manual setup needed"},
            )
        )
        assert event is not None
        assert event.event_type == EventType.DEAL_MANUAL_ACTION_REQUIRED

        bus_mod._event_bus_instance = None

    def test_emit_sync_deal_imported(self):
        """emit_event_sync should handle DEAL_IMPORTED type."""
        from ad_buyer.events.helpers import emit_event_sync
        from ad_buyer.events.models import EventType

        import ad_buyer.events.bus as bus_mod

        bus_mod._event_bus_instance = None

        event = emit_event_sync(
            event_type=EventType.DEAL_IMPORTED,
            deal_id="deal-sync-1",
            payload={"import_source": "manual"},
        )
        assert event is not None
        assert event.event_type == EventType.DEAL_IMPORTED

        bus_mod._event_bus_instance = None
