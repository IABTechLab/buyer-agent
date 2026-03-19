# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for configurable human approval gates (buyer-2qs).

Tests the ApprovalGate class and ApprovalRequest model that implement
configurable human approval gates at each stage of the campaign pipeline.

Per D-3 (Option C): Configurable per campaign, default requires approval
for plan review and booking.

Test categories:
  1. ApprovalRequest model creation and validation
  2. check_approval_required() -- checks campaign's approval_config
  3. request_approval() -- creates approval request, emits event
  4. record_approval() -- records approval/rejection decision
  5. is_approved() / get_approval_request() -- query approval state
  6. wait_for_approval() -- polling with timeout
  7. Event emission integration
  8. Storage persistence (SQLite)
"""

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ad_buyer.models.campaign_brief import ApprovalConfig, ApprovalStage
from ad_buyer.pipelines.approval import (
    ApprovalGate,
    ApprovalRequest,
    ApprovalResult,
    ApprovalStatus,
)
from ad_buyer.events.bus import InMemoryEventBus
from ad_buyer.events.models import Event, EventType
from ad_buyer.storage.campaign_store import CampaignStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def event_bus():
    """Create a fresh InMemoryEventBus."""
    return InMemoryEventBus()


@pytest.fixture
def store():
    """Create an in-memory CampaignStore, connected and ready."""
    s = CampaignStore("sqlite:///:memory:")
    s.connect()
    yield s
    s.disconnect()


@pytest.fixture
def gate(event_bus, store):
    """Create an ApprovalGate with in-memory bus and store."""
    return ApprovalGate(event_bus=event_bus, campaign_store=store)


@pytest.fixture
def sample_brief():
    """A minimal campaign brief dict for creating a campaign."""
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


@pytest.fixture
def campaign_with_default_approval(store, sample_brief):
    """Create a campaign with default approval config (plan_review + booking)."""
    sample_brief["approval_config"] = json.dumps(
        ApprovalConfig().model_dump()
    )
    return store.create_campaign(sample_brief)


@pytest.fixture
def campaign_fully_automated(store, sample_brief):
    """Create a campaign with all approval gates disabled."""
    sample_brief["approval_config"] = json.dumps(
        ApprovalConfig(
            plan_review=False,
            booking=False,
            creative=False,
            pacing_adjustment=False,
        ).model_dump()
    )
    return store.create_campaign(sample_brief)


@pytest.fixture
def campaign_all_gates(store, sample_brief):
    """Create a campaign with all approval gates enabled."""
    sample_brief["approval_config"] = json.dumps(
        ApprovalConfig(
            plan_review=True,
            booking=True,
            creative=True,
            pacing_adjustment=True,
        ).model_dump()
    )
    return store.create_campaign(sample_brief)


# ---------------------------------------------------------------------------
# ApprovalRequest model tests
# ---------------------------------------------------------------------------


class TestApprovalRequest:
    """Tests for the ApprovalRequest data model."""

    def test_create_request_defaults(self):
        """A new ApprovalRequest has pending status and auto-generated ID."""
        req = ApprovalRequest(
            campaign_id="camp-001",
            stage=ApprovalStage.PLAN_REVIEW,
        )
        assert req.approval_request_id  # auto-generated UUID
        assert req.campaign_id == "camp-001"
        assert req.stage == ApprovalStage.PLAN_REVIEW
        assert req.status == ApprovalStatus.PENDING
        assert req.requested_at is not None
        assert req.decided_at is None
        assert req.reviewer is None
        assert req.notes is None

    def test_create_request_with_context(self):
        """ApprovalRequest can store additional context."""
        req = ApprovalRequest(
            campaign_id="camp-002",
            stage=ApprovalStage.BOOKING,
            context={"deal_count": 5, "total_cost": 50000},
        )
        assert req.context["deal_count"] == 5
        assert req.context["total_cost"] == 50000

    def test_approval_status_values(self):
        """ApprovalStatus has exactly three values."""
        assert ApprovalStatus.PENDING == "pending"
        assert ApprovalStatus.APPROVED == "approved"
        assert ApprovalStatus.REJECTED == "rejected"

    def test_approval_result_approved(self):
        """ApprovalResult captures an approval decision."""
        result = ApprovalResult(
            approved=True,
            approval_request_id="req-001",
            stage=ApprovalStage.PLAN_REVIEW,
        )
        assert result.approved is True
        assert result.timed_out is False

    def test_approval_result_timed_out(self):
        """ApprovalResult can indicate a timeout."""
        result = ApprovalResult(
            approved=False,
            approval_request_id="req-001",
            stage=ApprovalStage.PLAN_REVIEW,
            timed_out=True,
        )
        assert result.approved is False
        assert result.timed_out is True


# ---------------------------------------------------------------------------
# check_approval_required tests
# ---------------------------------------------------------------------------


class TestCheckApprovalRequired:
    """Tests for ApprovalGate.check_approval_required()."""

    def test_default_config_requires_plan_review(
        self, gate, campaign_with_default_approval
    ):
        """Default config requires approval for PLAN_REVIEW."""
        required = gate.check_approval_required(
            campaign_with_default_approval, ApprovalStage.PLAN_REVIEW
        )
        assert required is True

    def test_default_config_requires_booking(
        self, gate, campaign_with_default_approval
    ):
        """Default config requires approval for BOOKING."""
        required = gate.check_approval_required(
            campaign_with_default_approval, ApprovalStage.BOOKING
        )
        assert required is True

    def test_default_config_does_not_require_creative(
        self, gate, campaign_with_default_approval
    ):
        """Default config does NOT require approval for CREATIVE."""
        required = gate.check_approval_required(
            campaign_with_default_approval, ApprovalStage.CREATIVE
        )
        assert required is False

    def test_default_config_does_not_require_pacing(
        self, gate, campaign_with_default_approval
    ):
        """Default config does NOT require approval for PACING_ADJUSTMENT."""
        required = gate.check_approval_required(
            campaign_with_default_approval, ApprovalStage.PACING_ADJUSTMENT
        )
        assert required is False

    def test_fully_automated_requires_nothing(
        self, gate, campaign_fully_automated
    ):
        """A fully automated campaign requires no approvals."""
        for stage in ApprovalStage:
            required = gate.check_approval_required(
                campaign_fully_automated, stage
            )
            assert required is False, f"Stage {stage} should not require approval"

    def test_all_gates_enabled(self, gate, campaign_all_gates):
        """A campaign with all gates enabled requires all approvals."""
        for stage in ApprovalStage:
            required = gate.check_approval_required(
                campaign_all_gates, stage
            )
            assert required is True, f"Stage {stage} should require approval"

    def test_campaign_not_found_returns_false(self, gate):
        """check_approval_required returns False for nonexistent campaign."""
        required = gate.check_approval_required(
            "nonexistent-campaign", ApprovalStage.PLAN_REVIEW
        )
        assert required is False

    def test_campaign_without_approval_config_uses_defaults(
        self, gate, store, sample_brief
    ):
        """Campaign without explicit approval_config uses defaults."""
        # Create campaign without setting approval_config
        cid = store.create_campaign(sample_brief)
        # Default ApprovalConfig has plan_review=True, booking=True
        assert gate.check_approval_required(cid, ApprovalStage.PLAN_REVIEW) is True
        assert gate.check_approval_required(cid, ApprovalStage.BOOKING) is True
        assert gate.check_approval_required(cid, ApprovalStage.CREATIVE) is False


# ---------------------------------------------------------------------------
# request_approval tests
# ---------------------------------------------------------------------------


class TestRequestApproval:
    """Tests for ApprovalGate.request_approval()."""

    @pytest.mark.asyncio
    async def test_creates_approval_request(
        self, gate, campaign_with_default_approval
    ):
        """request_approval creates and returns an approval request ID."""
        request_id = await gate.request_approval(
            campaign_with_default_approval,
            ApprovalStage.PLAN_REVIEW,
            context={"plan_summary": "CTV + Display mix"},
        )
        assert request_id is not None
        assert isinstance(request_id, str)
        assert len(request_id) > 0

    @pytest.mark.asyncio
    async def test_approval_request_is_retrievable(
        self, gate, campaign_with_default_approval
    ):
        """After requesting, the approval request can be retrieved."""
        request_id = await gate.request_approval(
            campaign_with_default_approval,
            ApprovalStage.PLAN_REVIEW,
        )
        req = gate.get_approval_request(request_id)
        assert req is not None
        assert req.campaign_id == campaign_with_default_approval
        assert req.stage == ApprovalStage.PLAN_REVIEW
        assert req.status == ApprovalStatus.PENDING

    @pytest.mark.asyncio
    async def test_emits_approval_requested_event(
        self, gate, event_bus, campaign_with_default_approval
    ):
        """request_approval emits an approval.requested event."""
        events_received = []

        def capture(event):
            events_received.append(event)

        await event_bus.subscribe(
            EventType.APPROVAL_REQUESTED.value, capture
        )

        await gate.request_approval(
            campaign_with_default_approval,
            ApprovalStage.PLAN_REVIEW,
        )

        assert len(events_received) == 1
        event = events_received[0]
        assert event.event_type == EventType.APPROVAL_REQUESTED
        assert event.campaign_id == campaign_with_default_approval
        assert event.payload["stage"] == ApprovalStage.PLAN_REVIEW.value

    @pytest.mark.asyncio
    async def test_request_with_context_stored(
        self, gate, campaign_with_default_approval
    ):
        """Context passed to request_approval is stored on the request."""
        context = {"deal_count": 3, "estimated_cpm": 12.50}
        request_id = await gate.request_approval(
            campaign_with_default_approval,
            ApprovalStage.BOOKING,
            context=context,
        )
        req = gate.get_approval_request(request_id)
        assert req.context == context

    @pytest.mark.asyncio
    async def test_multiple_requests_for_same_campaign(
        self, gate, campaign_all_gates
    ):
        """Multiple approval requests can exist for different stages."""
        id1 = await gate.request_approval(
            campaign_all_gates, ApprovalStage.PLAN_REVIEW
        )
        id2 = await gate.request_approval(
            campaign_all_gates, ApprovalStage.BOOKING
        )
        assert id1 != id2
        req1 = gate.get_approval_request(id1)
        req2 = gate.get_approval_request(id2)
        assert req1.stage == ApprovalStage.PLAN_REVIEW
        assert req2.stage == ApprovalStage.BOOKING


# ---------------------------------------------------------------------------
# record_approval tests
# ---------------------------------------------------------------------------


class TestRecordApproval:
    """Tests for ApprovalGate.record_approval()."""

    @pytest.mark.asyncio
    async def test_approve_request(
        self, gate, campaign_with_default_approval
    ):
        """record_approval with approved=True marks request as approved."""
        request_id = await gate.request_approval(
            campaign_with_default_approval, ApprovalStage.PLAN_REVIEW
        )
        await gate.record_approval(
            request_id, approved=True, reviewer="user-123", notes="LGTM"
        )
        req = gate.get_approval_request(request_id)
        assert req.status == ApprovalStatus.APPROVED
        assert req.reviewer == "user-123"
        assert req.notes == "LGTM"
        assert req.decided_at is not None

    @pytest.mark.asyncio
    async def test_reject_request(
        self, gate, campaign_with_default_approval
    ):
        """record_approval with approved=False marks request as rejected."""
        request_id = await gate.request_approval(
            campaign_with_default_approval, ApprovalStage.BOOKING
        )
        await gate.record_approval(
            request_id,
            approved=False,
            reviewer="user-456",
            notes="Budget too high",
        )
        req = gate.get_approval_request(request_id)
        assert req.status == ApprovalStatus.REJECTED
        assert req.reviewer == "user-456"
        assert req.notes == "Budget too high"

    @pytest.mark.asyncio
    async def test_emits_approval_granted_event(
        self, gate, event_bus, campaign_with_default_approval
    ):
        """record_approval with approved=True emits approval.granted event."""
        events_received = []

        def capture(event):
            events_received.append(event)

        await event_bus.subscribe(
            EventType.APPROVAL_GRANTED.value, capture
        )

        request_id = await gate.request_approval(
            campaign_with_default_approval, ApprovalStage.PLAN_REVIEW
        )
        await gate.record_approval(
            request_id, approved=True, reviewer="user-1"
        )

        assert len(events_received) == 1
        event = events_received[0]
        assert event.event_type == EventType.APPROVAL_GRANTED
        assert event.campaign_id == campaign_with_default_approval
        assert event.payload["stage"] == ApprovalStage.PLAN_REVIEW.value
        assert event.payload["reviewer"] == "user-1"

    @pytest.mark.asyncio
    async def test_emits_approval_rejected_event(
        self, gate, event_bus, campaign_with_default_approval
    ):
        """record_approval with approved=False emits approval.rejected event."""
        events_received = []

        def capture(event):
            events_received.append(event)

        await event_bus.subscribe(
            EventType.APPROVAL_REJECTED.value, capture
        )

        request_id = await gate.request_approval(
            campaign_with_default_approval, ApprovalStage.BOOKING
        )
        await gate.record_approval(
            request_id, approved=False, reviewer="user-2", notes="No"
        )

        assert len(events_received) == 1
        event = events_received[0]
        assert event.event_type == EventType.APPROVAL_REJECTED
        assert event.payload["notes"] == "No"

    @pytest.mark.asyncio
    async def test_record_approval_nonexistent_request_raises(self, gate):
        """record_approval raises ValueError for unknown request ID."""
        with pytest.raises(ValueError, match="not found"):
            await gate.record_approval(
                "nonexistent-id", approved=True, reviewer="user-1"
            )

    @pytest.mark.asyncio
    async def test_record_approval_already_decided_raises(
        self, gate, campaign_with_default_approval
    ):
        """record_approval raises ValueError if request already decided."""
        request_id = await gate.request_approval(
            campaign_with_default_approval, ApprovalStage.PLAN_REVIEW
        )
        await gate.record_approval(
            request_id, approved=True, reviewer="user-1"
        )
        with pytest.raises(ValueError, match="already decided"):
            await gate.record_approval(
                request_id, approved=False, reviewer="user-2"
            )


# ---------------------------------------------------------------------------
# is_approved / get_approval_request tests
# ---------------------------------------------------------------------------


class TestIsApproved:
    """Tests for ApprovalGate.is_approved()."""

    @pytest.mark.asyncio
    async def test_pending_is_not_approved(
        self, gate, campaign_with_default_approval
    ):
        """A pending request is not approved."""
        request_id = await gate.request_approval(
            campaign_with_default_approval, ApprovalStage.PLAN_REVIEW
        )
        assert gate.is_approved(request_id) is False

    @pytest.mark.asyncio
    async def test_approved_is_approved(
        self, gate, campaign_with_default_approval
    ):
        """An approved request is approved."""
        request_id = await gate.request_approval(
            campaign_with_default_approval, ApprovalStage.PLAN_REVIEW
        )
        await gate.record_approval(
            request_id, approved=True, reviewer="user-1"
        )
        assert gate.is_approved(request_id) is True

    @pytest.mark.asyncio
    async def test_rejected_is_not_approved(
        self, gate, campaign_with_default_approval
    ):
        """A rejected request is not approved."""
        request_id = await gate.request_approval(
            campaign_with_default_approval, ApprovalStage.PLAN_REVIEW
        )
        await gate.record_approval(
            request_id, approved=False, reviewer="user-1"
        )
        assert gate.is_approved(request_id) is False

    def test_nonexistent_is_not_approved(self, gate):
        """An unknown request ID returns False."""
        assert gate.is_approved("nonexistent-id") is False


# ---------------------------------------------------------------------------
# list_approval_requests tests
# ---------------------------------------------------------------------------


class TestListApprovalRequests:
    """Tests for ApprovalGate.list_approval_requests()."""

    @pytest.mark.asyncio
    async def test_list_by_campaign(
        self, gate, campaign_with_default_approval, campaign_all_gates
    ):
        """list_approval_requests filters by campaign_id."""
        await gate.request_approval(
            campaign_with_default_approval, ApprovalStage.PLAN_REVIEW
        )
        await gate.request_approval(
            campaign_all_gates, ApprovalStage.BOOKING
        )
        results = gate.list_approval_requests(
            campaign_id=campaign_with_default_approval
        )
        assert len(results) == 1
        assert results[0].campaign_id == campaign_with_default_approval

    @pytest.mark.asyncio
    async def test_list_by_stage(
        self, gate, campaign_all_gates
    ):
        """list_approval_requests filters by stage."""
        await gate.request_approval(
            campaign_all_gates, ApprovalStage.PLAN_REVIEW
        )
        await gate.request_approval(
            campaign_all_gates, ApprovalStage.BOOKING
        )
        results = gate.list_approval_requests(
            stage=ApprovalStage.BOOKING
        )
        assert len(results) == 1
        assert results[0].stage == ApprovalStage.BOOKING

    @pytest.mark.asyncio
    async def test_list_by_status(
        self, gate, campaign_with_default_approval
    ):
        """list_approval_requests filters by status."""
        id1 = await gate.request_approval(
            campaign_with_default_approval, ApprovalStage.PLAN_REVIEW
        )
        await gate.request_approval(
            campaign_with_default_approval, ApprovalStage.BOOKING
        )
        await gate.record_approval(id1, approved=True, reviewer="user-1")

        pending = gate.list_approval_requests(
            status=ApprovalStatus.PENDING
        )
        approved = gate.list_approval_requests(
            status=ApprovalStatus.APPROVED
        )
        assert len(pending) == 1
        assert len(approved) == 1
        assert approved[0].stage == ApprovalStage.PLAN_REVIEW

    @pytest.mark.asyncio
    async def test_list_empty(self, gate):
        """list_approval_requests returns empty list when none exist."""
        results = gate.list_approval_requests(campaign_id="nonexistent")
        assert results == []


# ---------------------------------------------------------------------------
# wait_for_approval tests
# ---------------------------------------------------------------------------


class TestWaitForApproval:
    """Tests for ApprovalGate.wait_for_approval()."""

    @pytest.mark.asyncio
    async def test_returns_approved_immediately_if_already_decided(
        self, gate, campaign_with_default_approval
    ):
        """wait_for_approval returns immediately if already approved."""
        request_id = await gate.request_approval(
            campaign_with_default_approval, ApprovalStage.PLAN_REVIEW
        )
        await gate.record_approval(
            request_id, approved=True, reviewer="user-1"
        )
        result = await gate.wait_for_approval(request_id, timeout=1.0)
        assert result.approved is True
        assert result.timed_out is False

    @pytest.mark.asyncio
    async def test_returns_rejected_immediately_if_already_decided(
        self, gate, campaign_with_default_approval
    ):
        """wait_for_approval returns immediately if already rejected."""
        request_id = await gate.request_approval(
            campaign_with_default_approval, ApprovalStage.PLAN_REVIEW
        )
        await gate.record_approval(
            request_id, approved=False, reviewer="user-1"
        )
        result = await gate.wait_for_approval(request_id, timeout=1.0)
        assert result.approved is False
        assert result.timed_out is False

    @pytest.mark.asyncio
    async def test_times_out_when_pending(
        self, gate, campaign_with_default_approval
    ):
        """wait_for_approval times out if no decision within timeout."""
        request_id = await gate.request_approval(
            campaign_with_default_approval, ApprovalStage.PLAN_REVIEW
        )
        result = await gate.wait_for_approval(request_id, timeout=0.1)
        assert result.approved is False
        assert result.timed_out is True

    @pytest.mark.asyncio
    async def test_wait_resolves_when_approved_during_wait(
        self, gate, campaign_with_default_approval
    ):
        """wait_for_approval resolves when approval recorded during wait."""
        request_id = await gate.request_approval(
            campaign_with_default_approval, ApprovalStage.PLAN_REVIEW
        )

        async def approve_later():
            await asyncio.sleep(0.05)
            await gate.record_approval(
                request_id, approved=True, reviewer="user-1"
            )

        # Run approval in background while waiting
        task = asyncio.create_task(approve_later())
        result = await gate.wait_for_approval(request_id, timeout=2.0)
        await task

        assert result.approved is True
        assert result.timed_out is False

    @pytest.mark.asyncio
    async def test_wait_nonexistent_request_times_out(self, gate):
        """wait_for_approval for nonexistent request returns timed_out."""
        result = await gate.wait_for_approval("nonexistent-id", timeout=0.1)
        assert result.approved is False
        assert result.timed_out is True


# ---------------------------------------------------------------------------
# Storage persistence tests
# ---------------------------------------------------------------------------


class TestApprovalStorage:
    """Tests for approval request persistence via CampaignStore."""

    @pytest.mark.asyncio
    async def test_approval_request_persisted_to_db(
        self, gate, store, campaign_with_default_approval
    ):
        """Approval requests are persisted in the database."""
        request_id = await gate.request_approval(
            campaign_with_default_approval, ApprovalStage.PLAN_REVIEW
        )
        # Verify by reading directly from the store
        rows = store.list_approval_requests(
            campaign_id=campaign_with_default_approval
        )
        assert len(rows) == 1
        assert rows[0]["approval_request_id"] == request_id
        assert rows[0]["stage"] == ApprovalStage.PLAN_REVIEW.value
        assert rows[0]["status"] == ApprovalStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_approval_decision_persisted_to_db(
        self, gate, store, campaign_with_default_approval
    ):
        """Approval decisions are persisted in the database."""
        request_id = await gate.request_approval(
            campaign_with_default_approval, ApprovalStage.PLAN_REVIEW
        )
        await gate.record_approval(
            request_id, approved=True, reviewer="user-1", notes="OK"
        )
        rows = store.list_approval_requests(
            campaign_id=campaign_with_default_approval
        )
        assert rows[0]["status"] == ApprovalStatus.APPROVED.value
        assert rows[0]["reviewer"] == "user-1"
        assert rows[0]["notes"] == "OK"
        assert rows[0]["decided_at"] is not None

    @pytest.mark.asyncio
    async def test_persisted_request_survives_gate_recreation(
        self, event_bus, store, campaign_with_default_approval
    ):
        """A new ApprovalGate instance can read previously persisted requests."""
        gate1 = ApprovalGate(event_bus=event_bus, campaign_store=store)
        request_id = await gate1.request_approval(
            campaign_with_default_approval, ApprovalStage.PLAN_REVIEW
        )
        await gate1.record_approval(
            request_id, approved=True, reviewer="user-1"
        )

        # Create a new ApprovalGate -- should load from same store
        gate2 = ApprovalGate(event_bus=event_bus, campaign_store=store)
        assert gate2.is_approved(request_id) is True
        req = gate2.get_approval_request(request_id)
        assert req is not None
        assert req.status == ApprovalStatus.APPROVED


# ---------------------------------------------------------------------------
# Event bus integration tests
# ---------------------------------------------------------------------------


class TestEventBusIntegration:
    """Tests for event bus integration with approval gates."""

    @pytest.mark.asyncio
    async def test_subscriber_receives_all_approval_events(
        self, gate, event_bus, campaign_with_default_approval
    ):
        """A wildcard subscriber receives requested, granted, rejected events."""
        all_events = []

        def capture_all(event):
            all_events.append(event)

        # Subscribe with sync callback (InMemoryEventBus calls cb(event))
        await event_bus.subscribe("*", capture_all)

        # Request approval
        id1 = await gate.request_approval(
            campaign_with_default_approval, ApprovalStage.PLAN_REVIEW
        )
        # Approve it
        await gate.record_approval(id1, approved=True, reviewer="user-1")

        # Request another
        id2 = await gate.request_approval(
            campaign_with_default_approval, ApprovalStage.BOOKING
        )
        # Reject it
        await gate.record_approval(id2, approved=False, reviewer="user-2")

        # Should have 4 events: 2 requested, 1 granted, 1 rejected
        event_types = [e.event_type for e in all_events]
        assert event_types.count(EventType.APPROVAL_REQUESTED) == 2
        assert event_types.count(EventType.APPROVAL_GRANTED) == 1
        assert event_types.count(EventType.APPROVAL_REJECTED) == 1

    @pytest.mark.asyncio
    async def test_approval_event_contains_request_id(
        self, gate, event_bus, campaign_with_default_approval
    ):
        """Approval events contain the approval_request_id in payload."""
        events_received = []

        def capture(event):
            events_received.append(event)

        await event_bus.subscribe(
            EventType.APPROVAL_REQUESTED.value, capture
        )

        request_id = await gate.request_approval(
            campaign_with_default_approval, ApprovalStage.PLAN_REVIEW
        )

        assert len(events_received) == 1
        assert events_received[0].payload["approval_request_id"] == request_id


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case and boundary tests for approval gates."""

    @pytest.mark.asyncio
    async def test_approval_for_stage_not_requiring_approval(
        self, gate, campaign_with_default_approval
    ):
        """Can still request approval for a stage that doesn't require it."""
        # Creative doesn't require approval with default config,
        # but a request can still be created (e.g., manual override)
        request_id = await gate.request_approval(
            campaign_with_default_approval,
            ApprovalStage.CREATIVE,
        )
        assert request_id is not None
        req = gate.get_approval_request(request_id)
        assert req.stage == ApprovalStage.CREATIVE

    @pytest.mark.asyncio
    async def test_multiple_pending_requests_for_same_stage(
        self, gate, campaign_all_gates
    ):
        """Multiple pending requests for the same stage are allowed."""
        id1 = await gate.request_approval(
            campaign_all_gates, ApprovalStage.PLAN_REVIEW
        )
        id2 = await gate.request_approval(
            campaign_all_gates, ApprovalStage.PLAN_REVIEW
        )
        assert id1 != id2
        # Both should be retrievable
        assert gate.get_approval_request(id1) is not None
        assert gate.get_approval_request(id2) is not None
