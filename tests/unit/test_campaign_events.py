# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for Campaign Automation event types.

Verifies that the campaign.*, pacing.*, and creative.* event types are properly
defined, can be used to create Event instances, serialize correctly, can be
emitted on the event bus, and can be persisted via DealStore.save_event().

Bead: buyer-ppi -- Campaign event types.
"""

import asyncio
import json

import pytest


# ---------------------------------------------------------------------------
# EventType enum tests -- campaign.* events
# ---------------------------------------------------------------------------


class TestCampaignEventTypes:
    """Tests for campaign.* EventType enum values."""

    def test_campaign_created_exists(self):
        """CAMPAIGN_CREATED event type must exist (pre-existing)."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "CAMPAIGN_CREATED")
        assert EventType.CAMPAIGN_CREATED.value == "campaign.created"

    def test_campaign_brief_validated_exists(self):
        """CAMPAIGN_BRIEF_VALIDATED event type must exist with correct value."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "CAMPAIGN_BRIEF_VALIDATED")
        assert EventType.CAMPAIGN_BRIEF_VALIDATED.value == "campaign.brief_validated"

    def test_campaign_plan_generated_exists(self):
        """CAMPAIGN_PLAN_GENERATED event type must exist with correct value."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "CAMPAIGN_PLAN_GENERATED")
        assert EventType.CAMPAIGN_PLAN_GENERATED.value == "campaign.plan_generated"

    def test_campaign_plan_approved_exists(self):
        """CAMPAIGN_PLAN_APPROVED event type must exist with correct value."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "CAMPAIGN_PLAN_APPROVED")
        assert EventType.CAMPAIGN_PLAN_APPROVED.value == "campaign.plan_approved"

    def test_campaign_booking_started_exists(self):
        """CAMPAIGN_BOOKING_STARTED event type must exist with correct value."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "CAMPAIGN_BOOKING_STARTED")
        assert EventType.CAMPAIGN_BOOKING_STARTED.value == "campaign.booking_started"

    def test_campaign_booking_completed_exists(self):
        """CAMPAIGN_BOOKING_COMPLETED event type must exist with correct value."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "CAMPAIGN_BOOKING_COMPLETED")
        assert (
            EventType.CAMPAIGN_BOOKING_COMPLETED.value == "campaign.booking_completed"
        )

    def test_campaign_ready_exists(self):
        """CAMPAIGN_READY event type must exist with correct value."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "CAMPAIGN_READY")
        assert EventType.CAMPAIGN_READY.value == "campaign.ready"

    def test_campaign_activated_exists(self):
        """CAMPAIGN_ACTIVATED event type must exist with correct value."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "CAMPAIGN_ACTIVATED")
        assert EventType.CAMPAIGN_ACTIVATED.value == "campaign.activated"

    def test_campaign_completed_exists(self):
        """CAMPAIGN_COMPLETED event type must exist with correct value."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "CAMPAIGN_COMPLETED")
        assert EventType.CAMPAIGN_COMPLETED.value == "campaign.completed"

    def test_campaign_canceled_exists(self):
        """CAMPAIGN_CANCELED event type must exist with correct value."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "CAMPAIGN_CANCELED")
        assert EventType.CAMPAIGN_CANCELED.value == "campaign.canceled"

    def test_all_campaign_types_in_enum(self):
        """All campaign.* event types must be in EventType."""
        from ad_buyer.events.models import EventType

        expected_campaign = [
            "campaign.created",
            "campaign.brief_validated",
            "campaign.plan_generated",
            "campaign.plan_approved",
            "campaign.booking_started",
            "campaign.booking_completed",
            "campaign.ready",
            "campaign.activated",
            "campaign.completed",
            "campaign.canceled",
        ]
        actual_values = [e.value for e in EventType]
        for val in expected_campaign:
            assert val in actual_values, f"Missing campaign event type: {val}"

    def test_campaign_types_are_string_enum(self):
        """campaign.* types should be str Enum for JSON serialization."""
        from ad_buyer.events.models import EventType

        assert isinstance(EventType.CAMPAIGN_BRIEF_VALIDATED, str)
        assert isinstance(EventType.CAMPAIGN_PLAN_GENERATED, str)
        assert isinstance(EventType.CAMPAIGN_PLAN_APPROVED, str)
        assert isinstance(EventType.CAMPAIGN_BOOKING_STARTED, str)
        assert isinstance(EventType.CAMPAIGN_BOOKING_COMPLETED, str)
        assert isinstance(EventType.CAMPAIGN_READY, str)
        assert isinstance(EventType.CAMPAIGN_ACTIVATED, str)
        assert isinstance(EventType.CAMPAIGN_COMPLETED, str)
        assert isinstance(EventType.CAMPAIGN_CANCELED, str)


# ---------------------------------------------------------------------------
# EventType enum tests -- pacing.* events
# ---------------------------------------------------------------------------


class TestPacingEventTypes:
    """Tests for pacing.* EventType enum values."""

    def test_pacing_snapshot_taken_exists(self):
        """PACING_SNAPSHOT_TAKEN event type must exist with correct value."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "PACING_SNAPSHOT_TAKEN")
        assert EventType.PACING_SNAPSHOT_TAKEN.value == "pacing.snapshot_taken"

    def test_pacing_deviation_detected_exists(self):
        """PACING_DEVIATION_DETECTED event type must exist with correct value."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "PACING_DEVIATION_DETECTED")
        assert (
            EventType.PACING_DEVIATION_DETECTED.value == "pacing.deviation_detected"
        )

    def test_pacing_reallocation_recommended_exists(self):
        """PACING_REALLOCATION_RECOMMENDED event type must exist."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "PACING_REALLOCATION_RECOMMENDED")
        assert (
            EventType.PACING_REALLOCATION_RECOMMENDED.value
            == "pacing.reallocation_recommended"
        )

    def test_pacing_reallocation_applied_exists(self):
        """PACING_REALLOCATION_APPLIED event type must exist."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "PACING_REALLOCATION_APPLIED")
        assert (
            EventType.PACING_REALLOCATION_APPLIED.value
            == "pacing.reallocation_applied"
        )

    def test_all_pacing_types_in_enum(self):
        """All pacing.* event types must be in EventType."""
        from ad_buyer.events.models import EventType

        expected_pacing = [
            "pacing.snapshot_taken",
            "pacing.deviation_detected",
            "pacing.reallocation_recommended",
            "pacing.reallocation_applied",
        ]
        actual_values = [e.value for e in EventType]
        for val in expected_pacing:
            assert val in actual_values, f"Missing pacing event type: {val}"

    def test_pacing_types_are_string_enum(self):
        """pacing.* types should be str Enum for JSON serialization."""
        from ad_buyer.events.models import EventType

        assert isinstance(EventType.PACING_SNAPSHOT_TAKEN, str)
        assert isinstance(EventType.PACING_DEVIATION_DETECTED, str)
        assert isinstance(EventType.PACING_REALLOCATION_RECOMMENDED, str)
        assert isinstance(EventType.PACING_REALLOCATION_APPLIED, str)


# ---------------------------------------------------------------------------
# EventType enum tests -- creative.* events
# ---------------------------------------------------------------------------


class TestCreativeEventTypes:
    """Tests for creative.* EventType enum values."""

    def test_creative_uploaded_exists(self):
        """CREATIVE_UPLOADED event type must exist with correct value."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "CREATIVE_UPLOADED")
        assert EventType.CREATIVE_UPLOADED.value == "creative.uploaded"

    def test_creative_validated_exists(self):
        """CREATIVE_VALIDATED event type must exist with correct value."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "CREATIVE_VALIDATED")
        assert EventType.CREATIVE_VALIDATED.value == "creative.validated"

    def test_creative_matched_exists(self):
        """CREATIVE_MATCHED event type must exist with correct value."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "CREATIVE_MATCHED")
        assert EventType.CREATIVE_MATCHED.value == "creative.matched"

    def test_creative_rotation_updated_exists(self):
        """CREATIVE_ROTATION_UPDATED event type must exist with correct value."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "CREATIVE_ROTATION_UPDATED")
        assert (
            EventType.CREATIVE_ROTATION_UPDATED.value == "creative.rotation_updated"
        )

    def test_creative_ad_server_pushed_exists(self):
        """CREATIVE_AD_SERVER_PUSHED event type must exist with correct value."""
        from ad_buyer.events.models import EventType

        assert hasattr(EventType, "CREATIVE_AD_SERVER_PUSHED")
        assert (
            EventType.CREATIVE_AD_SERVER_PUSHED.value == "creative.ad_server_pushed"
        )

    def test_all_creative_types_in_enum(self):
        """All creative.* event types must be in EventType."""
        from ad_buyer.events.models import EventType

        expected_creative = [
            "creative.uploaded",
            "creative.validated",
            "creative.matched",
            "creative.rotation_updated",
            "creative.ad_server_pushed",
        ]
        actual_values = [e.value for e in EventType]
        for val in expected_creative:
            assert val in actual_values, f"Missing creative event type: {val}"

    def test_creative_types_are_string_enum(self):
        """creative.* types should be str Enum for JSON serialization."""
        from ad_buyer.events.models import EventType

        assert isinstance(EventType.CREATIVE_UPLOADED, str)
        assert isinstance(EventType.CREATIVE_VALIDATED, str)
        assert isinstance(EventType.CREATIVE_MATCHED, str)
        assert isinstance(EventType.CREATIVE_ROTATION_UPDATED, str)
        assert isinstance(EventType.CREATIVE_AD_SERVER_PUSHED, str)


# ---------------------------------------------------------------------------
# Event model -- campaign_id field
# ---------------------------------------------------------------------------


class TestCampaignIdField:
    """Tests for the campaign_id field on the Event model."""

    def test_event_has_campaign_id_field(self):
        """Event model should have a campaign_id field."""
        from ad_buyer.events.models import Event, EventType

        event = Event(event_type=EventType.CAMPAIGN_CREATED)
        assert hasattr(event, "campaign_id")

    def test_campaign_id_defaults_to_empty(self):
        """campaign_id should default to empty string, like deal_id."""
        from ad_buyer.events.models import Event, EventType

        event = Event(event_type=EventType.CAMPAIGN_CREATED)
        assert event.campaign_id == ""

    def test_campaign_id_can_be_set(self):
        """campaign_id should be settable at construction."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.CAMPAIGN_CREATED,
            campaign_id="camp-001",
        )
        assert event.campaign_id == "camp-001"

    def test_campaign_id_serializes(self):
        """campaign_id should appear in serialized output."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.CAMPAIGN_BOOKING_STARTED,
            campaign_id="camp-002",
        )
        data = event.model_dump(mode="json")
        assert data["campaign_id"] == "camp-002"


# ---------------------------------------------------------------------------
# Event model tests with campaign.* types
# ---------------------------------------------------------------------------


class TestCampaignEventModel:
    """Tests for creating Event instances with campaign.* types."""

    def test_event_with_campaign_brief_validated(self):
        """Event should be creatable with CAMPAIGN_BRIEF_VALIDATED type."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.CAMPAIGN_BRIEF_VALIDATED,
            campaign_id="camp-100",
            payload={
                "advertiser_id": "adv-1",
                "total_budget": 500000,
                "channels": ["CTV", "DISPLAY"],
            },
        )
        assert event.event_type == EventType.CAMPAIGN_BRIEF_VALIDATED
        assert event.campaign_id == "camp-100"
        assert event.payload["total_budget"] == 500000

    def test_event_with_campaign_plan_generated(self):
        """Event should be creatable with CAMPAIGN_PLAN_GENERATED type."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.CAMPAIGN_PLAN_GENERATED,
            campaign_id="camp-101",
            payload={"deal_count": 5, "total_allocated": 300000},
        )
        assert event.event_type == EventType.CAMPAIGN_PLAN_GENERATED
        assert event.payload["deal_count"] == 5

    def test_event_with_campaign_plan_approved(self):
        """Event should be creatable with CAMPAIGN_PLAN_APPROVED type."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.CAMPAIGN_PLAN_APPROVED,
            campaign_id="camp-102",
            payload={"approved_by": "user-1"},
        )
        assert event.event_type == EventType.CAMPAIGN_PLAN_APPROVED

    def test_event_with_campaign_booking_started(self):
        """Event should be creatable with CAMPAIGN_BOOKING_STARTED type."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.CAMPAIGN_BOOKING_STARTED,
            campaign_id="camp-103",
            payload={"deals_to_book": 3},
        )
        assert event.event_type == EventType.CAMPAIGN_BOOKING_STARTED
        assert event.campaign_id == "camp-103"

    def test_event_with_campaign_booking_completed(self):
        """Event should be creatable with CAMPAIGN_BOOKING_COMPLETED type."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.CAMPAIGN_BOOKING_COMPLETED,
            campaign_id="camp-104",
            payload={"deals_booked": 3, "deals_failed": 0},
        )
        assert event.event_type == EventType.CAMPAIGN_BOOKING_COMPLETED
        assert event.payload["deals_booked"] == 3

    def test_event_with_campaign_ready(self):
        """Event should be creatable with CAMPAIGN_READY type."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.CAMPAIGN_READY,
            campaign_id="camp-105",
            payload={"all_deals_booked": True, "all_creatives_matched": True},
        )
        assert event.event_type == EventType.CAMPAIGN_READY

    def test_event_with_campaign_activated(self):
        """Event should be creatable with CAMPAIGN_ACTIVATED type."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.CAMPAIGN_ACTIVATED,
            campaign_id="camp-106",
            payload={"flight_start": "2026-04-01"},
        )
        assert event.event_type == EventType.CAMPAIGN_ACTIVATED

    def test_event_with_campaign_completed(self):
        """Event should be creatable with CAMPAIGN_COMPLETED type."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.CAMPAIGN_COMPLETED,
            campaign_id="camp-107",
            payload={"total_spend": 480000, "total_impressions": 5000000},
        )
        assert event.event_type == EventType.CAMPAIGN_COMPLETED

    def test_event_with_campaign_canceled(self):
        """Event should be creatable with CAMPAIGN_CANCELED type."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.CAMPAIGN_CANCELED,
            campaign_id="camp-108",
            payload={"reason": "Budget pulled by advertiser"},
        )
        assert event.event_type == EventType.CAMPAIGN_CANCELED
        assert event.payload["reason"] == "Budget pulled by advertiser"

    def test_campaign_event_serialization(self):
        """campaign.* events should serialize to dict correctly."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.CAMPAIGN_READY,
            campaign_id="camp-200",
            payload={"status": "ready"},
        )
        data = event.model_dump(mode="json")
        assert data["event_type"] == "campaign.ready"
        assert data["campaign_id"] == "camp-200"
        assert isinstance(data["timestamp"], str)
        assert data["payload"]["status"] == "ready"


# ---------------------------------------------------------------------------
# Event model tests with pacing.* types
# ---------------------------------------------------------------------------


class TestPacingEventModel:
    """Tests for creating Event instances with pacing.* types."""

    def test_event_with_pacing_snapshot_taken(self):
        """Event should be creatable with PACING_SNAPSHOT_TAKEN type."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.PACING_SNAPSHOT_TAKEN,
            campaign_id="camp-300",
            payload={
                "total_budget": 500000,
                "total_spend": 125000,
                "pacing_pct": 98.5,
                "expected_spend": 126904,
            },
        )
        assert event.event_type == EventType.PACING_SNAPSHOT_TAKEN
        assert event.campaign_id == "camp-300"
        assert event.payload["pacing_pct"] == 98.5

    def test_event_with_pacing_deviation_detected(self):
        """Event should be creatable with PACING_DEVIATION_DETECTED type."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.PACING_DEVIATION_DETECTED,
            campaign_id="camp-301",
            payload={
                "deviation_pct": -15.2,
                "channel": "DISPLAY",
                "severity": "warning",
            },
        )
        assert event.event_type == EventType.PACING_DEVIATION_DETECTED
        assert event.payload["deviation_pct"] == -15.2

    def test_event_with_pacing_reallocation_recommended(self):
        """Event should be creatable with PACING_REALLOCATION_RECOMMENDED type."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.PACING_REALLOCATION_RECOMMENDED,
            campaign_id="camp-302",
            payload={
                "from_channel": "DISPLAY",
                "to_channel": "CTV",
                "amount": 30000,
                "rationale": "CTV over-delivering at efficient CPMs",
            },
        )
        assert event.event_type == EventType.PACING_REALLOCATION_RECOMMENDED
        assert event.payload["amount"] == 30000

    def test_event_with_pacing_reallocation_applied(self):
        """Event should be creatable with PACING_REALLOCATION_APPLIED type."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.PACING_REALLOCATION_APPLIED,
            campaign_id="camp-303",
            payload={
                "from_channel": "DISPLAY",
                "to_channel": "CTV",
                "amount": 30000,
                "approved_by": "user-1",
            },
        )
        assert event.event_type == EventType.PACING_REALLOCATION_APPLIED
        assert event.payload["approved_by"] == "user-1"

    def test_pacing_event_serialization(self):
        """pacing.* events should serialize to dict correctly."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.PACING_DEVIATION_DETECTED,
            campaign_id="camp-310",
            payload={"deviation_pct": -20.0},
        )
        data = event.model_dump(mode="json")
        assert data["event_type"] == "pacing.deviation_detected"
        assert data["campaign_id"] == "camp-310"


# ---------------------------------------------------------------------------
# Event model tests with creative.* types
# ---------------------------------------------------------------------------


class TestCreativeEventModel:
    """Tests for creating Event instances with creative.* types."""

    def test_event_with_creative_uploaded(self):
        """Event should be creatable with CREATIVE_UPLOADED type."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.CREATIVE_UPLOADED,
            campaign_id="camp-400",
            payload={
                "creative_id": "cr-1",
                "format": "VAST",
                "duration_sec": 30,
            },
        )
        assert event.event_type == EventType.CREATIVE_UPLOADED
        assert event.payload["creative_id"] == "cr-1"

    def test_event_with_creative_validated(self):
        """Event should be creatable with CREATIVE_VALIDATED type."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.CREATIVE_VALIDATED,
            campaign_id="camp-401",
            payload={
                "creative_id": "cr-2",
                "spec": "VAST 4.2",
                "valid": True,
                "issues": [],
            },
        )
        assert event.event_type == EventType.CREATIVE_VALIDATED
        assert event.payload["valid"] is True

    def test_event_with_creative_matched(self):
        """Event should be creatable with CREATIVE_MATCHED type."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.CREATIVE_MATCHED,
            campaign_id="camp-402",
            deal_id="deal-50",
            payload={"creative_id": "cr-3", "match_score": 1.0},
        )
        assert event.event_type == EventType.CREATIVE_MATCHED
        assert event.deal_id == "deal-50"
        assert event.payload["match_score"] == 1.0

    def test_event_with_creative_rotation_updated(self):
        """Event should be creatable with CREATIVE_ROTATION_UPDATED type."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.CREATIVE_ROTATION_UPDATED,
            campaign_id="camp-403",
            payload={
                "rotation_type": "A/B",
                "variants": [
                    {"creative_id": "cr-4", "weight": 50},
                    {"creative_id": "cr-5", "weight": 50},
                ],
            },
        )
        assert event.event_type == EventType.CREATIVE_ROTATION_UPDATED
        assert len(event.payload["variants"]) == 2

    def test_event_with_creative_ad_server_pushed(self):
        """Event should be creatable with CREATIVE_AD_SERVER_PUSHED type."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.CREATIVE_AD_SERVER_PUSHED,
            campaign_id="camp-404",
            payload={
                "creative_id": "cr-6",
                "ad_server": "INNOVID",
                "ad_server_creative_id": "innovid-12345",
            },
        )
        assert event.event_type == EventType.CREATIVE_AD_SERVER_PUSHED
        assert event.payload["ad_server"] == "INNOVID"

    def test_creative_event_serialization(self):
        """creative.* events should serialize to dict correctly."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.CREATIVE_VALIDATED,
            campaign_id="camp-410",
            payload={"creative_id": "cr-7", "valid": False},
        )
        data = event.model_dump(mode="json")
        assert data["event_type"] == "creative.validated"
        assert data["campaign_id"] == "camp-410"


# ---------------------------------------------------------------------------
# EventBus tests with campaign automation types
# ---------------------------------------------------------------------------


class TestCampaignAutomationEventBus:
    """Tests for emitting campaign, pacing, and creative events on the bus."""

    @pytest.fixture
    def bus(self):
        from ad_buyer.events.bus import InMemoryEventBus

        return InMemoryEventBus()

    def test_publish_campaign_ready(self, bus):
        """CAMPAIGN_READY events should be publishable on the bus."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.CAMPAIGN_READY,
            campaign_id="camp-500",
        )
        asyncio.get_event_loop().run_until_complete(bus.publish(event))
        assert len(bus._events) == 1
        assert bus._events[0].event_type == EventType.CAMPAIGN_READY

    def test_publish_pacing_deviation_detected(self, bus):
        """PACING_DEVIATION_DETECTED events should be publishable on the bus."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.PACING_DEVIATION_DETECTED,
            campaign_id="camp-501",
            payload={"deviation_pct": -12.5},
        )
        asyncio.get_event_loop().run_until_complete(bus.publish(event))
        assert len(bus._events) == 1
        assert bus._events[0].event_type == EventType.PACING_DEVIATION_DETECTED

    def test_publish_creative_validated(self, bus):
        """CREATIVE_VALIDATED events should be publishable on the bus."""
        from ad_buyer.events.models import Event, EventType

        event = Event(
            event_type=EventType.CREATIVE_VALIDATED,
            payload={"creative_id": "cr-10", "valid": True},
        )
        asyncio.get_event_loop().run_until_complete(bus.publish(event))
        assert len(bus._events) == 1
        assert bus._events[0].event_type == EventType.CREATIVE_VALIDATED

    def test_subscribe_to_campaign_events(self, bus):
        """Subscriber should receive campaign.* events."""
        from ad_buyer.events.models import Event, EventType

        received = []
        asyncio.get_event_loop().run_until_complete(
            bus.subscribe("campaign.ready", lambda e: received.append(e))
        )

        event = Event(event_type=EventType.CAMPAIGN_READY, campaign_id="camp-510")
        asyncio.get_event_loop().run_until_complete(bus.publish(event))

        assert len(received) == 1
        assert received[0].event_type == EventType.CAMPAIGN_READY

    def test_subscribe_to_pacing_events(self, bus):
        """Subscriber should receive pacing.* events."""
        from ad_buyer.events.models import Event, EventType

        received = []
        asyncio.get_event_loop().run_until_complete(
            bus.subscribe(
                "pacing.reallocation_recommended", lambda e: received.append(e)
            )
        )

        event = Event(
            event_type=EventType.PACING_REALLOCATION_RECOMMENDED,
            campaign_id="camp-511",
        )
        asyncio.get_event_loop().run_until_complete(bus.publish(event))

        assert len(received) == 1
        assert received[0].event_type == EventType.PACING_REALLOCATION_RECOMMENDED

    def test_subscribe_to_creative_events(self, bus):
        """Subscriber should receive creative.* events."""
        from ad_buyer.events.models import Event, EventType

        received = []
        asyncio.get_event_loop().run_until_complete(
            bus.subscribe("creative.matched", lambda e: received.append(e))
        )

        event = Event(
            event_type=EventType.CREATIVE_MATCHED, campaign_id="camp-512"
        )
        asyncio.get_event_loop().run_until_complete(bus.publish(event))

        assert len(received) == 1
        assert received[0].event_type == EventType.CREATIVE_MATCHED

    def test_list_events_by_campaign_type(self, bus):
        """Should filter events by campaign automation event types."""
        from ad_buyer.events.models import Event, EventType

        asyncio.get_event_loop().run_until_complete(
            bus.publish(Event(event_type=EventType.CAMPAIGN_READY))
        )
        asyncio.get_event_loop().run_until_complete(
            bus.publish(Event(event_type=EventType.PACING_SNAPSHOT_TAKEN))
        )
        asyncio.get_event_loop().run_until_complete(
            bus.publish(Event(event_type=EventType.CREATIVE_UPLOADED))
        )
        asyncio.get_event_loop().run_until_complete(
            bus.publish(Event(event_type=EventType.DEAL_BOOKED))
        )

        # Filter by campaign.ready
        events = asyncio.get_event_loop().run_until_complete(
            bus.list_events(event_type="campaign.ready")
        )
        assert len(events) == 1
        assert events[0].event_type == EventType.CAMPAIGN_READY

        # Filter by pacing.snapshot_taken
        events = asyncio.get_event_loop().run_until_complete(
            bus.list_events(event_type="pacing.snapshot_taken")
        )
        assert len(events) == 1
        assert events[0].event_type == EventType.PACING_SNAPSHOT_TAKEN

    def test_wildcard_receives_campaign_automation_events(self, bus):
        """Wildcard subscriber should receive all campaign automation events."""
        from ad_buyer.events.models import Event, EventType

        received = []
        asyncio.get_event_loop().run_until_complete(
            bus.subscribe("*", lambda e: received.append(e))
        )

        asyncio.get_event_loop().run_until_complete(
            bus.publish(Event(event_type=EventType.CAMPAIGN_READY))
        )
        asyncio.get_event_loop().run_until_complete(
            bus.publish(Event(event_type=EventType.PACING_DEVIATION_DETECTED))
        )
        asyncio.get_event_loop().run_until_complete(
            bus.publish(Event(event_type=EventType.CREATIVE_VALIDATED))
        )

        assert len(received) == 3


# ---------------------------------------------------------------------------
# DealStore persistence tests with campaign automation types
# ---------------------------------------------------------------------------


class TestCampaignAutomationEventPersistence:
    """Tests for persisting campaign automation events via DealStore."""

    @pytest.fixture
    def store(self):
        """Create an in-memory DealStore."""
        from ad_buyer.storage.deal_store import DealStore

        s = DealStore("sqlite:///:memory:")
        s.connect()
        yield s
        s.disconnect()

    def test_persist_campaign_ready(self, store):
        """campaign.ready event should be persistable via DealStore."""
        event_id = store.save_event(
            event_type="campaign.ready",
            payload=json.dumps({"campaign_id": "camp-600"}),
        )
        assert event_id

        event = store.get_event(event_id)
        assert event is not None
        assert event["event_type"] == "campaign.ready"

    def test_persist_pacing_snapshot_taken(self, store):
        """pacing.snapshot_taken event should be persistable via DealStore."""
        event_id = store.save_event(
            event_type="pacing.snapshot_taken",
            payload=json.dumps({
                "campaign_id": "camp-601",
                "pacing_pct": 95.0,
            }),
        )
        assert event_id

        event = store.get_event(event_id)
        assert event is not None
        assert event["event_type"] == "pacing.snapshot_taken"

    def test_persist_creative_validated(self, store):
        """creative.validated event should be persistable via DealStore."""
        event_id = store.save_event(
            event_type="creative.validated",
            payload=json.dumps({
                "creative_id": "cr-20",
                "valid": True,
            }),
        )
        assert event_id

        event = store.get_event(event_id)
        assert event is not None
        assert event["event_type"] == "creative.validated"

    def test_filter_campaign_events_by_type(self, store):
        """Should filter persisted events by campaign automation types."""
        store.save_event(event_type="campaign.ready")
        store.save_event(event_type="campaign.completed")
        store.save_event(event_type="pacing.snapshot_taken")
        store.save_event(event_type="creative.validated")
        store.save_event(event_type="deal.booked")

        events = store.list_events(event_type="campaign.ready")
        assert len(events) == 1
        assert events[0]["event_type"] == "campaign.ready"

        events = store.list_events(event_type="pacing.snapshot_taken")
        assert len(events) == 1
        assert events[0]["event_type"] == "pacing.snapshot_taken"

        events = store.list_events(event_type="creative.validated")
        assert len(events) == 1
        assert events[0]["event_type"] == "creative.validated"


# ---------------------------------------------------------------------------
# emit_event helper tests with campaign automation types
# ---------------------------------------------------------------------------


class TestCampaignAutomationEmitEvent:
    """Tests for emitting campaign automation events via helpers."""

    def test_emit_campaign_ready(self):
        """emit_event should handle CAMPAIGN_READY type."""
        from ad_buyer.events.helpers import emit_event
        from ad_buyer.events.models import EventType

        import ad_buyer.events.bus as bus_mod

        bus_mod._event_bus_instance = None

        event = asyncio.get_event_loop().run_until_complete(
            emit_event(
                event_type=EventType.CAMPAIGN_READY,
                payload={"campaign_id": "camp-700"},
            )
        )
        assert event is not None
        assert event.event_type == EventType.CAMPAIGN_READY

        bus_mod._event_bus_instance = None

    def test_emit_pacing_deviation_detected(self):
        """emit_event should handle PACING_DEVIATION_DETECTED type."""
        from ad_buyer.events.helpers import emit_event
        from ad_buyer.events.models import EventType

        import ad_buyer.events.bus as bus_mod

        bus_mod._event_bus_instance = None

        event = asyncio.get_event_loop().run_until_complete(
            emit_event(
                event_type=EventType.PACING_DEVIATION_DETECTED,
                payload={"deviation_pct": -10.0},
            )
        )
        assert event is not None
        assert event.event_type == EventType.PACING_DEVIATION_DETECTED

        bus_mod._event_bus_instance = None

    def test_emit_creative_matched(self):
        """emit_event should handle CREATIVE_MATCHED type."""
        from ad_buyer.events.helpers import emit_event
        from ad_buyer.events.models import EventType

        import ad_buyer.events.bus as bus_mod

        bus_mod._event_bus_instance = None

        event = asyncio.get_event_loop().run_until_complete(
            emit_event(
                event_type=EventType.CREATIVE_MATCHED,
                deal_id="deal-60",
                payload={"creative_id": "cr-30"},
            )
        )
        assert event is not None
        assert event.event_type == EventType.CREATIVE_MATCHED
        assert event.deal_id == "deal-60"

        bus_mod._event_bus_instance = None

    def test_emit_sync_campaign_activated(self):
        """emit_event_sync should handle CAMPAIGN_ACTIVATED type."""
        from ad_buyer.events.helpers import emit_event_sync
        from ad_buyer.events.models import EventType

        import ad_buyer.events.bus as bus_mod

        bus_mod._event_bus_instance = None

        event = emit_event_sync(
            event_type=EventType.CAMPAIGN_ACTIVATED,
            payload={"campaign_id": "camp-710"},
        )
        assert event is not None
        assert event.event_type == EventType.CAMPAIGN_ACTIVATED

        bus_mod._event_bus_instance = None

    def test_emit_sync_pacing_reallocation_applied(self):
        """emit_event_sync should handle PACING_REALLOCATION_APPLIED type."""
        from ad_buyer.events.helpers import emit_event_sync
        from ad_buyer.events.models import EventType

        import ad_buyer.events.bus as bus_mod

        bus_mod._event_bus_instance = None

        event = emit_event_sync(
            event_type=EventType.PACING_REALLOCATION_APPLIED,
            payload={"amount": 25000},
        )
        assert event is not None
        assert event.event_type == EventType.PACING_REALLOCATION_APPLIED

        bus_mod._event_bus_instance = None
