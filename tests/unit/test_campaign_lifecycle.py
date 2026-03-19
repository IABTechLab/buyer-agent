# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for campaign lifecycle operations (buyer-0u9).

Tests the integration of CampaignAutomationStateMachine with CampaignStore:
- transition_campaign_status() validates transitions before DB update
- Lifecycle convenience methods (create_campaign, start_planning, etc.)
- Event emission on each transition
- Invalid transition rejection
- Campaign not found handling
"""

import json
import pytest

from ad_buyer.models.state_machine import (
    CampaignAutomationStateMachine,
    CampaignStatus,
    InvalidTransitionError,
)
from ad_buyer.storage.campaign_store import CampaignStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store():
    """Create an in-memory CampaignStore, connected and ready."""
    s = CampaignStore("sqlite:///:memory:")
    s.connect()
    yield s
    s.disconnect()


@pytest.fixture
def sample_brief():
    """A minimal campaign brief dict."""
    return {
        "advertiser_id": "adv-001",
        "campaign_name": "Test Campaign",
        "total_budget": 100000.0,
        "currency": "USD",
        "flight_start": "2026-04-01",
        "flight_end": "2026-05-01",
        "channels": json.dumps(["CTV", "DISPLAY"]),
        "target_audience": json.dumps(["auto_intenders_25_54"]),
        "target_geo": json.dumps(["US"]),
        "kpis": json.dumps(["completed_views", "ctr"]),
    }


# ---------------------------------------------------------------------------
# transition_campaign_status
# ---------------------------------------------------------------------------

class TestTransitionCampaignStatus:
    """Tests for CampaignStore.transition_campaign_status()."""

    def test_valid_transition_updates_status(self, store, sample_brief):
        """A valid transition updates the campaign status in the DB."""
        cid = store.create_campaign(sample_brief)
        result = store.transition_campaign_status(cid, CampaignStatus.PLANNING)
        assert result is True
        campaign = store.get_campaign(cid)
        assert campaign["status"] == CampaignStatus.PLANNING.value

    def test_invalid_transition_raises(self, store, sample_brief):
        """An invalid transition raises InvalidTransitionError."""
        cid = store.create_campaign(sample_brief)
        # DRAFT -> ACTIVE is not a valid transition
        with pytest.raises(InvalidTransitionError):
            store.transition_campaign_status(cid, CampaignStatus.ACTIVE)

    def test_invalid_transition_does_not_change_status(self, store, sample_brief):
        """After an invalid transition, status is unchanged."""
        cid = store.create_campaign(sample_brief)
        with pytest.raises(InvalidTransitionError):
            store.transition_campaign_status(cid, CampaignStatus.ACTIVE)
        campaign = store.get_campaign(cid)
        assert campaign["status"] == CampaignStatus.DRAFT.value

    def test_campaign_not_found_raises(self, store):
        """Transitioning a nonexistent campaign raises KeyError."""
        with pytest.raises(KeyError):
            store.transition_campaign_status("nonexistent-id", CampaignStatus.PLANNING)

    def test_chained_transitions(self, store, sample_brief):
        """Multiple valid transitions in sequence all succeed."""
        cid = store.create_campaign(sample_brief)
        store.transition_campaign_status(cid, CampaignStatus.PLANNING)
        store.transition_campaign_status(cid, CampaignStatus.BOOKING)
        store.transition_campaign_status(cid, CampaignStatus.READY)
        store.transition_campaign_status(cid, CampaignStatus.ACTIVE)
        store.transition_campaign_status(cid, CampaignStatus.COMPLETED)
        campaign = store.get_campaign(cid)
        assert campaign["status"] == CampaignStatus.COMPLETED.value

    def test_transition_from_terminal_raises(self, store, sample_brief):
        """Cannot transition from a terminal state (COMPLETED)."""
        cid = store.create_campaign(sample_brief)
        # Walk to COMPLETED
        for status in [
            CampaignStatus.PLANNING,
            CampaignStatus.BOOKING,
            CampaignStatus.READY,
            CampaignStatus.ACTIVE,
            CampaignStatus.COMPLETED,
        ]:
            store.transition_campaign_status(cid, status)
        with pytest.raises(InvalidTransitionError):
            store.transition_campaign_status(cid, CampaignStatus.ACTIVE)

    def test_transition_updates_updated_at(self, store, sample_brief):
        """Transitioning updates the updated_at timestamp."""
        cid = store.create_campaign(sample_brief)
        campaign_before = store.get_campaign(cid)
        store.transition_campaign_status(cid, CampaignStatus.PLANNING)
        campaign_after = store.get_campaign(cid)
        assert campaign_after["updated_at"] >= campaign_before["updated_at"]


# ---------------------------------------------------------------------------
# create_campaign
# ---------------------------------------------------------------------------

class TestCreateCampaign:
    """Tests for CampaignStore.create_campaign()."""

    def test_creates_in_draft_status(self, store, sample_brief):
        """New campaigns start in DRAFT status."""
        cid = store.create_campaign(sample_brief)
        campaign = store.get_campaign(cid)
        assert campaign["status"] == CampaignStatus.DRAFT.value

    def test_returns_campaign_id(self, store, sample_brief):
        """create_campaign returns a non-empty campaign ID."""
        cid = store.create_campaign(sample_brief)
        assert cid
        assert isinstance(cid, str)

    def test_brief_fields_stored(self, store, sample_brief):
        """All brief fields are stored in the campaign record."""
        cid = store.create_campaign(sample_brief)
        campaign = store.get_campaign(cid)
        assert campaign["advertiser_id"] == "adv-001"
        assert campaign["campaign_name"] == "Test Campaign"
        assert campaign["total_budget"] == 100000.0
        assert campaign["currency"] == "USD"
        assert campaign["flight_start"] == "2026-04-01"
        assert campaign["flight_end"] == "2026-05-01"

    def test_emits_campaign_created_event(self, store, sample_brief):
        """create_campaign emits a campaign.created event."""
        cid = store.create_campaign(sample_brief)
        events = store.get_campaign_events(cid)
        assert len(events) >= 1
        assert events[0]["event_type"] == "campaign.created"
        assert events[0]["campaign_id"] == cid


# ---------------------------------------------------------------------------
# Lifecycle methods
# ---------------------------------------------------------------------------

class TestStartPlanning:
    """Tests for CampaignStore.start_planning()."""

    def test_draft_to_planning(self, store, sample_brief):
        """start_planning transitions DRAFT -> PLANNING."""
        cid = store.create_campaign(sample_brief)
        store.start_planning(cid)
        assert store.get_campaign(cid)["status"] == CampaignStatus.PLANNING.value

    def test_emits_plan_generated_event(self, store, sample_brief):
        """start_planning emits a campaign.plan_generated event."""
        cid = store.create_campaign(sample_brief)
        store.start_planning(cid)
        events = store.get_campaign_events(cid)
        event_types = [e["event_type"] for e in events]
        assert "campaign.plan_generated" in event_types

    def test_invalid_state_raises(self, store, sample_brief):
        """start_planning from ACTIVE raises InvalidTransitionError."""
        cid = store.create_campaign(sample_brief)
        # Walk to ACTIVE
        for status in [
            CampaignStatus.PLANNING,
            CampaignStatus.BOOKING,
            CampaignStatus.READY,
            CampaignStatus.ACTIVE,
        ]:
            store.transition_campaign_status(cid, status)
        with pytest.raises(InvalidTransitionError):
            store.start_planning(cid)


class TestStartBooking:
    """Tests for CampaignStore.start_booking()."""

    def test_planning_to_booking(self, store, sample_brief):
        """start_booking transitions PLANNING -> BOOKING."""
        cid = store.create_campaign(sample_brief)
        store.start_planning(cid)
        store.start_booking(cid)
        assert store.get_campaign(cid)["status"] == CampaignStatus.BOOKING.value

    def test_emits_booking_started_event(self, store, sample_brief):
        """start_booking emits a campaign.booking_started event."""
        cid = store.create_campaign(sample_brief)
        store.start_planning(cid)
        store.start_booking(cid)
        events = store.get_campaign_events(cid)
        event_types = [e["event_type"] for e in events]
        assert "campaign.booking_started" in event_types

    def test_invalid_state_raises(self, store, sample_brief):
        """start_booking from DRAFT raises InvalidTransitionError."""
        cid = store.create_campaign(sample_brief)
        with pytest.raises(InvalidTransitionError):
            store.start_booking(cid)


class TestMarkReady:
    """Tests for CampaignStore.mark_ready()."""

    def test_booking_to_ready(self, store, sample_brief):
        """mark_ready transitions BOOKING -> READY."""
        cid = store.create_campaign(sample_brief)
        store.start_planning(cid)
        store.start_booking(cid)
        store.mark_ready(cid)
        assert store.get_campaign(cid)["status"] == CampaignStatus.READY.value

    def test_emits_ready_event(self, store, sample_brief):
        """mark_ready emits a campaign.ready event."""
        cid = store.create_campaign(sample_brief)
        store.start_planning(cid)
        store.start_booking(cid)
        store.mark_ready(cid)
        events = store.get_campaign_events(cid)
        event_types = [e["event_type"] for e in events]
        assert "campaign.ready" in event_types

    def test_invalid_state_raises(self, store, sample_brief):
        """mark_ready from DRAFT raises InvalidTransitionError."""
        cid = store.create_campaign(sample_brief)
        with pytest.raises(InvalidTransitionError):
            store.mark_ready(cid)


class TestActivateCampaign:
    """Tests for CampaignStore.activate_campaign()."""

    def test_ready_to_active(self, store, sample_brief):
        """activate_campaign transitions READY -> ACTIVE."""
        cid = store.create_campaign(sample_brief)
        store.start_planning(cid)
        store.start_booking(cid)
        store.mark_ready(cid)
        store.activate_campaign(cid)
        assert store.get_campaign(cid)["status"] == CampaignStatus.ACTIVE.value

    def test_emits_activated_event(self, store, sample_brief):
        """activate_campaign emits a campaign.activated event."""
        cid = store.create_campaign(sample_brief)
        store.start_planning(cid)
        store.start_booking(cid)
        store.mark_ready(cid)
        store.activate_campaign(cid)
        events = store.get_campaign_events(cid)
        event_types = [e["event_type"] for e in events]
        assert "campaign.activated" in event_types

    def test_invalid_state_raises(self, store, sample_brief):
        """activate_campaign from DRAFT raises InvalidTransitionError."""
        cid = store.create_campaign(sample_brief)
        with pytest.raises(InvalidTransitionError):
            store.activate_campaign(cid)


class TestPauseCampaign:
    """Tests for CampaignStore.pause_campaign()."""

    def test_active_to_paused(self, store, sample_brief):
        """pause_campaign transitions ACTIVE -> PAUSED."""
        cid = store.create_campaign(sample_brief)
        for s in [CampaignStatus.PLANNING, CampaignStatus.BOOKING,
                   CampaignStatus.READY, CampaignStatus.ACTIVE]:
            store.transition_campaign_status(cid, s)
        store.pause_campaign(cid)
        assert store.get_campaign(cid)["status"] == CampaignStatus.PAUSED.value

    def test_invalid_state_raises(self, store, sample_brief):
        """pause_campaign from DRAFT raises InvalidTransitionError."""
        cid = store.create_campaign(sample_brief)
        with pytest.raises(InvalidTransitionError):
            store.pause_campaign(cid)


class TestResumeCampaign:
    """Tests for CampaignStore.resume_campaign()."""

    def test_paused_to_active(self, store, sample_brief):
        """resume_campaign transitions PAUSED -> ACTIVE."""
        cid = store.create_campaign(sample_brief)
        for s in [CampaignStatus.PLANNING, CampaignStatus.BOOKING,
                   CampaignStatus.READY, CampaignStatus.ACTIVE]:
            store.transition_campaign_status(cid, s)
        store.pause_campaign(cid)
        store.resume_campaign(cid)
        assert store.get_campaign(cid)["status"] == CampaignStatus.ACTIVE.value

    def test_emits_activated_event_on_resume(self, store, sample_brief):
        """resume_campaign emits a campaign.activated event."""
        cid = store.create_campaign(sample_brief)
        for s in [CampaignStatus.PLANNING, CampaignStatus.BOOKING,
                   CampaignStatus.READY, CampaignStatus.ACTIVE]:
            store.transition_campaign_status(cid, s)
        store.pause_campaign(cid)
        store.resume_campaign(cid)
        events = store.get_campaign_events(cid)
        event_types = [e["event_type"] for e in events]
        # The last activated event should be from resume
        assert event_types.count("campaign.activated") >= 2  # initial + resume

    def test_invalid_state_raises(self, store, sample_brief):
        """resume_campaign from DRAFT raises InvalidTransitionError."""
        cid = store.create_campaign(sample_brief)
        with pytest.raises(InvalidTransitionError):
            store.resume_campaign(cid)


class TestCompleteCampaign:
    """Tests for CampaignStore.complete_campaign()."""

    def test_active_to_completed(self, store, sample_brief):
        """complete_campaign transitions ACTIVE -> COMPLETED."""
        cid = store.create_campaign(sample_brief)
        for s in [CampaignStatus.PLANNING, CampaignStatus.BOOKING,
                   CampaignStatus.READY, CampaignStatus.ACTIVE]:
            store.transition_campaign_status(cid, s)
        store.complete_campaign(cid)
        assert store.get_campaign(cid)["status"] == CampaignStatus.COMPLETED.value

    def test_emits_completed_event(self, store, sample_brief):
        """complete_campaign emits a campaign.completed event."""
        cid = store.create_campaign(sample_brief)
        for s in [CampaignStatus.PLANNING, CampaignStatus.BOOKING,
                   CampaignStatus.READY, CampaignStatus.ACTIVE]:
            store.transition_campaign_status(cid, s)
        store.complete_campaign(cid)
        events = store.get_campaign_events(cid)
        event_types = [e["event_type"] for e in events]
        assert "campaign.completed" in event_types

    def test_invalid_state_raises(self, store, sample_brief):
        """complete_campaign from DRAFT raises InvalidTransitionError."""
        cid = store.create_campaign(sample_brief)
        with pytest.raises(InvalidTransitionError):
            store.complete_campaign(cid)


class TestCancelCampaign:
    """Tests for CampaignStore.cancel_campaign()."""

    def test_cancel_from_planning(self, store, sample_brief):
        """cancel_campaign from PLANNING succeeds."""
        cid = store.create_campaign(sample_brief)
        store.start_planning(cid)
        store.cancel_campaign(cid)
        assert store.get_campaign(cid)["status"] == CampaignStatus.CANCELED.value

    def test_cancel_from_booking(self, store, sample_brief):
        """cancel_campaign from BOOKING succeeds."""
        cid = store.create_campaign(sample_brief)
        store.start_planning(cid)
        store.start_booking(cid)
        store.cancel_campaign(cid)
        assert store.get_campaign(cid)["status"] == CampaignStatus.CANCELED.value

    def test_cancel_from_ready(self, store, sample_brief):
        """cancel_campaign from READY succeeds."""
        cid = store.create_campaign(sample_brief)
        for s in [CampaignStatus.PLANNING, CampaignStatus.BOOKING,
                   CampaignStatus.READY]:
            store.transition_campaign_status(cid, s)
        store.cancel_campaign(cid)
        assert store.get_campaign(cid)["status"] == CampaignStatus.CANCELED.value

    def test_cancel_from_active(self, store, sample_brief):
        """cancel_campaign from ACTIVE succeeds."""
        cid = store.create_campaign(sample_brief)
        for s in [CampaignStatus.PLANNING, CampaignStatus.BOOKING,
                   CampaignStatus.READY, CampaignStatus.ACTIVE]:
            store.transition_campaign_status(cid, s)
        store.cancel_campaign(cid)
        assert store.get_campaign(cid)["status"] == CampaignStatus.CANCELED.value

    def test_cancel_from_paused(self, store, sample_brief):
        """cancel_campaign from PAUSED succeeds."""
        cid = store.create_campaign(sample_brief)
        for s in [CampaignStatus.PLANNING, CampaignStatus.BOOKING,
                   CampaignStatus.READY, CampaignStatus.ACTIVE,
                   CampaignStatus.PAUSED]:
            store.transition_campaign_status(cid, s)
        store.cancel_campaign(cid)
        assert store.get_campaign(cid)["status"] == CampaignStatus.CANCELED.value

    def test_cancel_from_pacing_hold(self, store, sample_brief):
        """cancel_campaign from PACING_HOLD succeeds."""
        cid = store.create_campaign(sample_brief)
        for s in [CampaignStatus.PLANNING, CampaignStatus.BOOKING,
                   CampaignStatus.READY, CampaignStatus.ACTIVE,
                   CampaignStatus.PACING_HOLD]:
            store.transition_campaign_status(cid, s)
        store.cancel_campaign(cid)
        assert store.get_campaign(cid)["status"] == CampaignStatus.CANCELED.value

    def test_cancel_from_draft_raises(self, store, sample_brief):
        """cancel_campaign from DRAFT raises (no DRAFT->CANCELED rule)."""
        cid = store.create_campaign(sample_brief)
        with pytest.raises(InvalidTransitionError):
            store.cancel_campaign(cid)

    def test_cancel_from_completed_raises(self, store, sample_brief):
        """cancel_campaign from COMPLETED raises (terminal state)."""
        cid = store.create_campaign(sample_brief)
        for s in [CampaignStatus.PLANNING, CampaignStatus.BOOKING,
                   CampaignStatus.READY, CampaignStatus.ACTIVE,
                   CampaignStatus.COMPLETED]:
            store.transition_campaign_status(cid, s)
        with pytest.raises(InvalidTransitionError):
            store.cancel_campaign(cid)

    def test_cancel_from_canceled_raises(self, store, sample_brief):
        """cancel_campaign from CANCELED raises (already terminal)."""
        cid = store.create_campaign(sample_brief)
        store.start_planning(cid)
        store.cancel_campaign(cid)
        with pytest.raises(InvalidTransitionError):
            store.cancel_campaign(cid)

    def test_emits_canceled_event(self, store, sample_brief):
        """cancel_campaign emits a campaign.canceled event."""
        cid = store.create_campaign(sample_brief)
        store.start_planning(cid)
        store.cancel_campaign(cid)
        events = store.get_campaign_events(cid)
        event_types = [e["event_type"] for e in events]
        assert "campaign.canceled" in event_types


# ---------------------------------------------------------------------------
# Event tracking
# ---------------------------------------------------------------------------

class TestEventTracking:
    """Tests for event recording through the campaign lifecycle."""

    def test_full_lifecycle_events(self, store, sample_brief):
        """A full happy-path lifecycle emits the right sequence of events."""
        cid = store.create_campaign(sample_brief)
        store.start_planning(cid)
        store.start_booking(cid)
        store.mark_ready(cid)
        store.activate_campaign(cid)
        store.complete_campaign(cid)

        events = store.get_campaign_events(cid)
        event_types = [e["event_type"] for e in events]

        assert event_types == [
            "campaign.created",
            "campaign.plan_generated",
            "campaign.booking_started",
            "campaign.ready",
            "campaign.activated",
            "campaign.completed",
        ]

    def test_events_have_campaign_id(self, store, sample_brief):
        """All events have the correct campaign_id."""
        cid = store.create_campaign(sample_brief)
        store.start_planning(cid)
        events = store.get_campaign_events(cid)
        for event in events:
            assert event["campaign_id"] == cid

    def test_events_have_timestamps(self, store, sample_brief):
        """All events have a timestamp."""
        cid = store.create_campaign(sample_brief)
        store.start_planning(cid)
        events = store.get_campaign_events(cid)
        for event in events:
            assert event["timestamp"] is not None
            assert event["timestamp"] != ""

    def test_pause_resume_events(self, store, sample_brief):
        """Pause and resume produce the correct event types."""
        cid = store.create_campaign(sample_brief)
        for s in [CampaignStatus.PLANNING, CampaignStatus.BOOKING,
                   CampaignStatus.READY, CampaignStatus.ACTIVE]:
            store.transition_campaign_status(cid, s)
        store.pause_campaign(cid)
        store.resume_campaign(cid)

        events = store.get_campaign_events(cid)
        event_types = [e["event_type"] for e in events]
        # The last two events should be the pause (no specific event mapped)
        # and resume (campaign.activated)
        assert "campaign.activated" in event_types

    def test_no_events_for_nonexistent_campaign(self, store):
        """get_campaign_events for a nonexistent campaign returns empty list."""
        events = store.get_campaign_events("nonexistent-id")
        assert events == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge case and regression tests."""

    def test_multiple_campaigns_independent(self, store, sample_brief):
        """Transitions on one campaign don't affect another."""
        cid1 = store.create_campaign(sample_brief)
        cid2 = store.create_campaign(sample_brief)
        store.start_planning(cid1)
        assert store.get_campaign(cid1)["status"] == CampaignStatus.PLANNING.value
        assert store.get_campaign(cid2)["status"] == CampaignStatus.DRAFT.value

    def test_replanning_from_booking(self, store, sample_brief):
        """BOOKING -> PLANNING replanning transition works."""
        cid = store.create_campaign(sample_brief)
        store.start_planning(cid)
        store.start_booking(cid)
        # Replan
        store.transition_campaign_status(cid, CampaignStatus.PLANNING)
        assert store.get_campaign(cid)["status"] == CampaignStatus.PLANNING.value

    def test_replanning_from_ready(self, store, sample_brief):
        """READY -> PLANNING replanning transition works."""
        cid = store.create_campaign(sample_brief)
        store.start_planning(cid)
        store.start_booking(cid)
        store.mark_ready(cid)
        # Replan
        store.transition_campaign_status(cid, CampaignStatus.PLANNING)
        assert store.get_campaign(cid)["status"] == CampaignStatus.PLANNING.value

    def test_pacing_hold_from_active(self, store, sample_brief):
        """ACTIVE -> PACING_HOLD transition works."""
        cid = store.create_campaign(sample_brief)
        for s in [CampaignStatus.PLANNING, CampaignStatus.BOOKING,
                   CampaignStatus.READY, CampaignStatus.ACTIVE]:
            store.transition_campaign_status(cid, s)
        store.transition_campaign_status(cid, CampaignStatus.PACING_HOLD)
        assert store.get_campaign(cid)["status"] == CampaignStatus.PACING_HOLD.value

    def test_pacing_hold_to_active(self, store, sample_brief):
        """PACING_HOLD -> ACTIVE (auto-resume) works."""
        cid = store.create_campaign(sample_brief)
        for s in [CampaignStatus.PLANNING, CampaignStatus.BOOKING,
                   CampaignStatus.READY, CampaignStatus.ACTIVE,
                   CampaignStatus.PACING_HOLD]:
            store.transition_campaign_status(cid, s)
        store.transition_campaign_status(cid, CampaignStatus.ACTIVE)
        assert store.get_campaign(cid)["status"] == CampaignStatus.ACTIVE.value

    def test_pacing_hold_escalate_to_paused(self, store, sample_brief):
        """PACING_HOLD -> PAUSED (escalation) works."""
        cid = store.create_campaign(sample_brief)
        for s in [CampaignStatus.PLANNING, CampaignStatus.BOOKING,
                   CampaignStatus.READY, CampaignStatus.ACTIVE,
                   CampaignStatus.PACING_HOLD]:
            store.transition_campaign_status(cid, s)
        store.transition_campaign_status(cid, CampaignStatus.PAUSED)
        assert store.get_campaign(cid)["status"] == CampaignStatus.PAUSED.value
