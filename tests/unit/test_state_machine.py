# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for buyer order state machine.

Covers:
- BuyerDealStatus enum completeness
- BuyerCampaignStatus enum mapping to ExecutionStatus
- Transition enforcement (valid and invalid)
- Guard conditions
- Audit trail recording
- Linear TV lifecycle states
- DealStore integration
- Serialization round-trip
"""

import pytest

from ad_buyer.models.state_machine import (
    BuyerCampaignStatus,
    BuyerDealStatus,
    CampaignStatus,
    DealStateMachine,
    CampaignStateMachine,
    CampaignAutomationStateMachine,
    InvalidTransitionError,
    StateTransition,
    OrderAuditLog,
    TransitionRule,
    GuardFn,
    from_execution_status,
    from_dsp_flow_status,
)


# ---------------------------------------------------------------------------
# BuyerDealStatus enum
# ---------------------------------------------------------------------------


class TestBuyerDealStatus:
    """Test the BuyerDealStatus enum has all required states."""

    def test_happy_path_states_exist(self):
        """Verify all buyer deal lifecycle states are defined."""
        expected = [
            "quoted", "negotiating", "accepted", "booking",
            "booked", "delivering", "completed",
        ]
        for state in expected:
            assert hasattr(BuyerDealStatus, state.upper()), f"Missing state: {state}"

    def test_terminal_states_exist(self):
        """Verify terminal/error states are defined."""
        for state in ["failed", "cancelled", "expired"]:
            assert hasattr(BuyerDealStatus, state.upper()), f"Missing state: {state}"

    def test_linear_tv_states_exist(self):
        """Verify linear TV specific states are defined."""
        assert hasattr(BuyerDealStatus, "MAKEGOOD_PENDING")
        assert hasattr(BuyerDealStatus, "PARTIALLY_CANCELED")

    def test_enum_values_are_lowercase(self):
        """Ensure enum values are lowercase strings."""
        for member in BuyerDealStatus:
            assert member.value == member.value.lower()

    def test_is_str_enum(self):
        """BuyerDealStatus should be a str enum for serialization."""
        assert isinstance(BuyerDealStatus.QUOTED, str)
        assert BuyerDealStatus.QUOTED == "quoted"


# ---------------------------------------------------------------------------
# BuyerCampaignStatus enum
# ---------------------------------------------------------------------------


class TestBuyerCampaignStatus:
    """Test the BuyerCampaignStatus enum maps to existing ExecutionStatus."""

    def test_campaign_states_exist(self):
        """Verify campaign lifecycle states exist."""
        expected = [
            "initialized", "brief_received", "budget_allocated",
            "researching", "awaiting_approval", "executing_bookings",
            "completed", "failed", "validation_failed",
        ]
        for state in expected:
            assert hasattr(BuyerCampaignStatus, state.upper()), f"Missing state: {state}"

    def test_is_str_enum(self):
        """BuyerCampaignStatus should be a str enum."""
        assert isinstance(BuyerCampaignStatus.INITIALIZED, str)


# ---------------------------------------------------------------------------
# DealStateMachine transitions
# ---------------------------------------------------------------------------


class TestDealStateMachineTransitions:
    """Test valid and invalid state transitions for deals."""

    def test_happy_path_quoted_to_completed(self):
        """Walk the full happy path from quoted to completed."""
        sm = DealStateMachine("deal-001")
        assert sm.status == BuyerDealStatus.QUOTED

        sm.transition(BuyerDealStatus.NEGOTIATING)
        assert sm.status == BuyerDealStatus.NEGOTIATING

        sm.transition(BuyerDealStatus.ACCEPTED)
        assert sm.status == BuyerDealStatus.ACCEPTED

        sm.transition(BuyerDealStatus.BOOKING)
        assert sm.status == BuyerDealStatus.BOOKING

        sm.transition(BuyerDealStatus.BOOKED)
        assert sm.status == BuyerDealStatus.BOOKED

        sm.transition(BuyerDealStatus.DELIVERING)
        assert sm.status == BuyerDealStatus.DELIVERING

        sm.transition(BuyerDealStatus.COMPLETED)
        assert sm.status == BuyerDealStatus.COMPLETED

    def test_quoted_directly_to_accepted(self):
        """Auto-accept a quote without negotiation rounds."""
        sm = DealStateMachine("deal-002")
        sm.transition(BuyerDealStatus.ACCEPTED)
        assert sm.status == BuyerDealStatus.ACCEPTED

    def test_invalid_transition_raises(self):
        """Transitioning from quoted directly to completed must fail."""
        sm = DealStateMachine("deal-003")
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.transition(BuyerDealStatus.COMPLETED)
        assert "deal-003" in str(exc_info.value)
        assert "quoted" in str(exc_info.value)
        assert "completed" in str(exc_info.value)

    def test_invalid_transition_preserves_state(self):
        """An invalid transition must NOT change the current state."""
        sm = DealStateMachine("deal-004")
        with pytest.raises(InvalidTransitionError):
            sm.transition(BuyerDealStatus.DELIVERING)
        assert sm.status == BuyerDealStatus.QUOTED

    def test_cancellation_from_active_states(self):
        """Cancellation should be allowed from any non-terminal state."""
        cancellable_states = [
            BuyerDealStatus.QUOTED,
            BuyerDealStatus.NEGOTIATING,
            BuyerDealStatus.ACCEPTED,
            BuyerDealStatus.BOOKING,
            BuyerDealStatus.BOOKED,
        ]
        for idx, state in enumerate(cancellable_states):
            sm = DealStateMachine(f"deal-cancel-{idx}", initial_status=state)
            sm.transition(BuyerDealStatus.CANCELLED)
            assert sm.status == BuyerDealStatus.CANCELLED

    def test_failure_from_booking(self):
        """Booking can fail."""
        sm = DealStateMachine("deal-fail-booking", initial_status=BuyerDealStatus.BOOKING)
        sm.transition(BuyerDealStatus.FAILED)
        assert sm.status == BuyerDealStatus.FAILED

    def test_expiry_from_quoted(self):
        """A quoted deal can expire."""
        sm = DealStateMachine("deal-expire")
        sm.transition(BuyerDealStatus.EXPIRED)
        assert sm.status == BuyerDealStatus.EXPIRED

    def test_cannot_transition_from_terminal_state(self):
        """Once completed, no further transitions allowed (except by custom rule)."""
        sm = DealStateMachine("deal-terminal", initial_status=BuyerDealStatus.COMPLETED)
        with pytest.raises(InvalidTransitionError):
            sm.transition(BuyerDealStatus.DELIVERING)

    def test_allowed_transitions_from_quoted(self):
        """Check the allowed next states from quoted."""
        sm = DealStateMachine("deal-allowed")
        allowed = sm.allowed_transitions()
        assert BuyerDealStatus.NEGOTIATING in allowed
        assert BuyerDealStatus.ACCEPTED in allowed
        assert BuyerDealStatus.CANCELLED in allowed
        assert BuyerDealStatus.EXPIRED in allowed
        # Should NOT include terminal-only destinations
        assert BuyerDealStatus.COMPLETED not in allowed

    def test_can_transition_returns_bool(self):
        """can_transition should return True/False without side effects."""
        sm = DealStateMachine("deal-check")
        assert sm.can_transition(BuyerDealStatus.NEGOTIATING) is True
        assert sm.can_transition(BuyerDealStatus.COMPLETED) is False
        # State unchanged
        assert sm.status == BuyerDealStatus.QUOTED


# ---------------------------------------------------------------------------
# Linear TV states
# ---------------------------------------------------------------------------


class TestLinearTVLifecycle:
    """Test linear TV specific transitions."""

    def test_delivering_to_makegood_pending(self):
        """A delivering deal can move to makegood_pending."""
        sm = DealStateMachine("deal-tv-1", initial_status=BuyerDealStatus.DELIVERING)
        sm.transition(BuyerDealStatus.MAKEGOOD_PENDING)
        assert sm.status == BuyerDealStatus.MAKEGOOD_PENDING

    def test_makegood_pending_to_delivering(self):
        """After makegood resolved, return to delivering."""
        sm = DealStateMachine("deal-tv-2", initial_status=BuyerDealStatus.MAKEGOOD_PENDING)
        sm.transition(BuyerDealStatus.DELIVERING)
        assert sm.status == BuyerDealStatus.DELIVERING

    def test_booked_to_partially_canceled(self):
        """A booked deal can be partially canceled."""
        sm = DealStateMachine("deal-tv-3", initial_status=BuyerDealStatus.BOOKED)
        sm.transition(BuyerDealStatus.PARTIALLY_CANCELED)
        assert sm.status == BuyerDealStatus.PARTIALLY_CANCELED

    def test_partially_canceled_to_delivering(self):
        """A partially canceled deal can still deliver."""
        sm = DealStateMachine("deal-tv-4", initial_status=BuyerDealStatus.PARTIALLY_CANCELED)
        sm.transition(BuyerDealStatus.DELIVERING)
        assert sm.status == BuyerDealStatus.DELIVERING


# ---------------------------------------------------------------------------
# Guard conditions
# ---------------------------------------------------------------------------


class TestGuardConditions:
    """Test guard functions on transitions."""

    def test_guard_blocks_transition(self):
        """A guard returning False should block the transition."""
        def reject_guard(order_id, from_s, to_s, ctx):
            return False

        rule = TransitionRule(
            from_status=BuyerDealStatus.QUOTED,
            to_status=BuyerDealStatus.NEGOTIATING,
            guard=reject_guard,
            description="Always reject",
        )
        sm = DealStateMachine("deal-guard-1", rules=[rule])
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.transition(BuyerDealStatus.NEGOTIATING)
        assert "guard condition failed" in str(exc_info.value)

    def test_guard_allows_transition(self):
        """A guard returning True should allow the transition."""
        def allow_guard(order_id, from_s, to_s, ctx):
            return True

        rule = TransitionRule(
            from_status=BuyerDealStatus.QUOTED,
            to_status=BuyerDealStatus.NEGOTIATING,
            guard=allow_guard,
            description="Always allow",
        )
        sm = DealStateMachine("deal-guard-2", rules=[rule])
        sm.transition(BuyerDealStatus.NEGOTIATING)
        assert sm.status == BuyerDealStatus.NEGOTIATING

    def test_guard_receives_context(self):
        """Guard should receive the context dict passed to transition()."""
        received_ctx = {}

        def capture_guard(order_id, from_s, to_s, ctx):
            received_ctx.update(ctx)
            return True

        rule = TransitionRule(
            from_status=BuyerDealStatus.QUOTED,
            to_status=BuyerDealStatus.NEGOTIATING,
            guard=capture_guard,
            description="Captures context",
        )
        sm = DealStateMachine("deal-guard-3", rules=[rule])
        sm.transition(BuyerDealStatus.NEGOTIATING, context={"budget": 50000})
        assert received_ctx == {"budget": 50000}


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


class TestAuditTrail:
    """Test that every transition is recorded in the audit log."""

    def test_single_transition_recorded(self):
        """A single transition should produce one audit entry."""
        sm = DealStateMachine("deal-audit-1")
        sm.transition(BuyerDealStatus.NEGOTIATING, actor="agent:dsp", reason="Starting negotiation")

        assert len(sm.history) == 1
        entry = sm.history[0]
        assert entry.from_status == BuyerDealStatus.QUOTED
        assert entry.to_status == BuyerDealStatus.NEGOTIATING
        assert entry.actor == "agent:dsp"
        assert entry.reason == "Starting negotiation"

    def test_multiple_transitions_recorded(self):
        """Multiple transitions accumulate in the audit log."""
        sm = DealStateMachine("deal-audit-2")
        sm.transition(BuyerDealStatus.NEGOTIATING)
        sm.transition(BuyerDealStatus.ACCEPTED)
        sm.transition(BuyerDealStatus.BOOKING)

        assert len(sm.history) == 3
        assert sm.history[0].to_status == BuyerDealStatus.NEGOTIATING
        assert sm.history[1].to_status == BuyerDealStatus.ACCEPTED
        assert sm.history[2].to_status == BuyerDealStatus.BOOKING

    def test_audit_log_current_status(self):
        """audit_log.current_status should track the latest transition."""
        sm = DealStateMachine("deal-audit-3")
        sm.transition(BuyerDealStatus.NEGOTIATING)
        sm.transition(BuyerDealStatus.ACCEPTED)

        assert sm.audit_log.current_status == BuyerDealStatus.ACCEPTED

    def test_transition_has_timestamp(self):
        """Each transition record should have a timestamp."""
        sm = DealStateMachine("deal-audit-4")
        record = sm.transition(BuyerDealStatus.NEGOTIATING)
        assert record.timestamp is not None

    def test_transition_has_unique_id(self):
        """Each transition record should have a unique ID."""
        sm = DealStateMachine("deal-audit-5")
        r1 = sm.transition(BuyerDealStatus.NEGOTIATING)
        r2 = sm.transition(BuyerDealStatus.ACCEPTED)
        assert r1.transition_id != r2.transition_id

    def test_metadata_stored_in_transition(self):
        """Custom metadata should be stored in the transition record."""
        sm = DealStateMachine("deal-audit-6")
        record = sm.transition(
            BuyerDealStatus.NEGOTIATING,
            metadata={"round": 1, "offer_cpm": 12.50},
        )
        assert record.metadata == {"round": 1, "offer_cpm": 12.50}

    def test_failed_transition_not_recorded(self):
        """Invalid transitions should NOT create audit entries."""
        sm = DealStateMachine("deal-audit-7")
        with pytest.raises(InvalidTransitionError):
            sm.transition(BuyerDealStatus.COMPLETED)
        assert len(sm.history) == 0


# ---------------------------------------------------------------------------
# CampaignStateMachine
# ---------------------------------------------------------------------------


class TestCampaignStateMachine:
    """Test campaign lifecycle state machine."""

    def test_happy_path(self):
        """Walk through the campaign booking flow happy path."""
        sm = CampaignStateMachine("campaign-001")
        assert sm.status == BuyerCampaignStatus.INITIALIZED

        sm.transition(BuyerCampaignStatus.BRIEF_RECEIVED)
        sm.transition(BuyerCampaignStatus.BUDGET_ALLOCATED)
        sm.transition(BuyerCampaignStatus.RESEARCHING)
        sm.transition(BuyerCampaignStatus.AWAITING_APPROVAL)
        sm.transition(BuyerCampaignStatus.EXECUTING_BOOKINGS)
        sm.transition(BuyerCampaignStatus.COMPLETED)

        assert sm.status == BuyerCampaignStatus.COMPLETED
        assert len(sm.history) == 6

    def test_validation_failure(self):
        """Brief received can go to validation_failed."""
        sm = CampaignStateMachine("campaign-002")
        sm.transition(BuyerCampaignStatus.BRIEF_RECEIVED)
        sm.transition(BuyerCampaignStatus.VALIDATION_FAILED)
        assert sm.status == BuyerCampaignStatus.VALIDATION_FAILED

    def test_failure_during_execution(self):
        """Execution bookings can fail."""
        sm = CampaignStateMachine(
            "campaign-003",
            initial_status=BuyerCampaignStatus.EXECUTING_BOOKINGS,
        )
        sm.transition(BuyerCampaignStatus.FAILED)
        assert sm.status == BuyerCampaignStatus.FAILED


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------


class TestMappingHelpers:
    """Test legacy enum mapping functions."""

    def test_from_execution_status_initialized(self):
        """Map ExecutionStatus.INITIALIZED to campaign status."""
        result = from_execution_status("initialized")
        assert result == BuyerCampaignStatus.INITIALIZED

    def test_from_execution_status_completed(self):
        result = from_execution_status("completed")
        assert result == BuyerCampaignStatus.COMPLETED

    def test_from_execution_status_unknown_returns_initialized(self):
        """Unknown values should default to INITIALIZED."""
        result = from_execution_status("unknown_value")
        assert result == BuyerCampaignStatus.INITIALIZED

    def test_from_dsp_flow_status_deal_created(self):
        """Map DSPFlowStatus values to deal states."""
        result = from_dsp_flow_status("deal_created")
        assert result == BuyerDealStatus.BOOKED

    def test_from_dsp_flow_status_failed(self):
        result = from_dsp_flow_status("failed")
        assert result == BuyerDealStatus.FAILED


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestSerialization:
    """Test state machine serialization/deserialization."""

    def test_round_trip(self):
        """Serialize and deserialize a machine with history."""
        sm = DealStateMachine("deal-serial-1")
        sm.transition(BuyerDealStatus.NEGOTIATING, actor="test")
        sm.transition(BuyerDealStatus.ACCEPTED, reason="Price agreed")

        data = sm.to_dict()
        restored = DealStateMachine.from_dict(data)

        assert restored.order_id == "deal-serial-1"
        assert restored.status == BuyerDealStatus.ACCEPTED
        assert len(restored.history) == 2
        assert restored.history[0].actor == "test"
        assert restored.history[1].reason == "Price agreed"

    def test_to_dict_structure(self):
        """Verify the dict structure from to_dict()."""
        sm = DealStateMachine("deal-serial-2")
        data = sm.to_dict()

        assert "order_id" in data
        assert "status" in data
        assert "audit_log" in data
        assert data["status"] == "quoted"

    def test_add_and_remove_rule(self):
        """Test adding and removing custom rules."""
        sm = DealStateMachine("deal-rules-1")
        # Add a custom rule from completed back to quoted (normally not allowed)
        custom_rule = TransitionRule(
            from_status=BuyerDealStatus.COMPLETED,
            to_status=BuyerDealStatus.QUOTED,
            description="Re-quote after completion",
        )
        sm.add_rule(custom_rule)
        assert sm.can_transition(BuyerDealStatus.COMPLETED) is False  # still at quoted
        # Navigate to completed
        sm2 = DealStateMachine("deal-rules-2", initial_status=BuyerDealStatus.COMPLETED)
        sm2.add_rule(custom_rule)
        assert sm2.can_transition(BuyerDealStatus.QUOTED) is True

        # Remove the custom rule
        removed = sm2.remove_rule(BuyerDealStatus.COMPLETED, BuyerDealStatus.QUOTED)
        assert removed is True
        assert sm2.can_transition(BuyerDealStatus.QUOTED) is False

    def test_remove_nonexistent_rule_returns_false(self):
        """Removing a rule that doesn't exist returns False."""
        sm = DealStateMachine("deal-rules-3")
        result = sm.remove_rule(BuyerDealStatus.COMPLETED, BuyerDealStatus.QUOTED)
        assert result is False


# ---------------------------------------------------------------------------
# CampaignStatus enum (Campaign Automation)
# ---------------------------------------------------------------------------


class TestCampaignStatus:
    """Test the CampaignStatus enum for Campaign Automation state machine."""

    def test_all_states_exist(self):
        """Verify all campaign automation states are defined."""
        expected = [
            "draft", "planning", "booking", "ready",
            "active", "paused", "pacing_hold", "completed", "canceled",
        ]
        for state in expected:
            assert hasattr(CampaignStatus, state.upper()), f"Missing state: {state}"

    def test_is_str_enum(self):
        """CampaignStatus should be a str enum for serialization."""
        assert isinstance(CampaignStatus.DRAFT, str)
        assert CampaignStatus.DRAFT == "draft"

    def test_enum_values_are_lowercase(self):
        """Ensure enum values are lowercase strings."""
        for member in CampaignStatus:
            assert member.value == member.value.lower()

    def test_ready_state_value(self):
        """The READY state should have the value 'ready'."""
        assert CampaignStatus.READY == "ready"

    def test_paused_vs_pacing_hold_are_distinct(self):
        """PAUSED and PACING_HOLD must be distinct states."""
        assert CampaignStatus.PAUSED != CampaignStatus.PACING_HOLD
        assert CampaignStatus.PAUSED.value == "paused"
        assert CampaignStatus.PACING_HOLD.value == "pacing_hold"


# ---------------------------------------------------------------------------
# CampaignAutomationStateMachine transitions
# ---------------------------------------------------------------------------


class TestCampaignAutomationStateMachine:
    """Test the full campaign automation state machine with READY state."""

    def test_happy_path_draft_to_completed(self):
        """Walk the full happy path: DRAFT -> PLANNING -> BOOKING -> READY -> ACTIVE -> COMPLETED."""
        sm = CampaignAutomationStateMachine("campaign-auto-001")
        assert sm.status == CampaignStatus.DRAFT

        sm.transition(CampaignStatus.PLANNING)
        assert sm.status == CampaignStatus.PLANNING

        sm.transition(CampaignStatus.BOOKING)
        assert sm.status == CampaignStatus.BOOKING

        sm.transition(CampaignStatus.READY)
        assert sm.status == CampaignStatus.READY

        sm.transition(CampaignStatus.ACTIVE)
        assert sm.status == CampaignStatus.ACTIVE

        sm.transition(CampaignStatus.COMPLETED)
        assert sm.status == CampaignStatus.COMPLETED

    def test_default_initial_status_is_draft(self):
        """Default initial status should be DRAFT."""
        sm = CampaignAutomationStateMachine("campaign-auto-002")
        assert sm.status == CampaignStatus.DRAFT

    # -- DRAFT transitions --

    def test_draft_to_planning(self):
        """DRAFT -> PLANNING when campaign planning begins."""
        sm = CampaignAutomationStateMachine("campaign-draft-1")
        sm.transition(CampaignStatus.PLANNING)
        assert sm.status == CampaignStatus.PLANNING

    def test_draft_cannot_skip_to_booking(self):
        """DRAFT cannot skip directly to BOOKING."""
        sm = CampaignAutomationStateMachine("campaign-draft-2")
        with pytest.raises(InvalidTransitionError):
            sm.transition(CampaignStatus.BOOKING)

    def test_draft_cannot_skip_to_active(self):
        """DRAFT cannot skip directly to ACTIVE."""
        sm = CampaignAutomationStateMachine("campaign-draft-3")
        with pytest.raises(InvalidTransitionError):
            sm.transition(CampaignStatus.ACTIVE)

    # -- PLANNING transitions --

    def test_planning_to_booking(self):
        """PLANNING -> BOOKING when plan is approved."""
        sm = CampaignAutomationStateMachine("campaign-plan-1", initial_status=CampaignStatus.PLANNING)
        sm.transition(CampaignStatus.BOOKING)
        assert sm.status == CampaignStatus.BOOKING

    def test_planning_to_canceled(self):
        """PLANNING -> CANCELED is allowed."""
        sm = CampaignAutomationStateMachine("campaign-plan-2", initial_status=CampaignStatus.PLANNING)
        sm.transition(CampaignStatus.CANCELED)
        assert sm.status == CampaignStatus.CANCELED

    # -- BOOKING transitions --

    def test_booking_to_ready(self):
        """BOOKING -> READY when all deals booked and creative validated."""
        sm = CampaignAutomationStateMachine("campaign-book-1", initial_status=CampaignStatus.BOOKING)
        sm.transition(CampaignStatus.READY)
        assert sm.status == CampaignStatus.READY

    def test_booking_to_planning(self):
        """BOOKING -> PLANNING when replanning is needed."""
        sm = CampaignAutomationStateMachine("campaign-book-2", initial_status=CampaignStatus.BOOKING)
        sm.transition(CampaignStatus.PLANNING)
        assert sm.status == CampaignStatus.PLANNING

    def test_booking_to_canceled(self):
        """BOOKING -> CANCELED is allowed."""
        sm = CampaignAutomationStateMachine("campaign-book-3", initial_status=CampaignStatus.BOOKING)
        sm.transition(CampaignStatus.CANCELED)
        assert sm.status == CampaignStatus.CANCELED

    # -- READY transitions --

    def test_ready_to_active(self):
        """READY -> ACTIVE when flight start date reached or manual activation."""
        sm = CampaignAutomationStateMachine("campaign-ready-1", initial_status=CampaignStatus.READY)
        sm.transition(CampaignStatus.ACTIVE)
        assert sm.status == CampaignStatus.ACTIVE

    def test_ready_to_canceled(self):
        """READY -> CANCELED is allowed."""
        sm = CampaignAutomationStateMachine("campaign-ready-2", initial_status=CampaignStatus.READY)
        sm.transition(CampaignStatus.CANCELED)
        assert sm.status == CampaignStatus.CANCELED

    def test_ready_to_planning(self):
        """READY -> PLANNING for replanning before start."""
        sm = CampaignAutomationStateMachine("campaign-ready-3", initial_status=CampaignStatus.READY)
        sm.transition(CampaignStatus.PLANNING)
        assert sm.status == CampaignStatus.PLANNING

    def test_ready_cannot_go_to_completed(self):
        """READY cannot skip directly to COMPLETED."""
        sm = CampaignAutomationStateMachine("campaign-ready-4", initial_status=CampaignStatus.READY)
        with pytest.raises(InvalidTransitionError):
            sm.transition(CampaignStatus.COMPLETED)

    def test_ready_cannot_go_to_paused(self):
        """READY cannot go to PAUSED (only ACTIVE can)."""
        sm = CampaignAutomationStateMachine("campaign-ready-5", initial_status=CampaignStatus.READY)
        with pytest.raises(InvalidTransitionError):
            sm.transition(CampaignStatus.PAUSED)

    # -- ACTIVE transitions --

    def test_active_to_paused(self):
        """ACTIVE -> PAUSED on manual pause."""
        sm = CampaignAutomationStateMachine("campaign-active-1", initial_status=CampaignStatus.ACTIVE)
        sm.transition(CampaignStatus.PAUSED)
        assert sm.status == CampaignStatus.PAUSED

    def test_active_to_pacing_hold(self):
        """ACTIVE -> PACING_HOLD on automated pacing deviation threshold."""
        sm = CampaignAutomationStateMachine("campaign-active-2", initial_status=CampaignStatus.ACTIVE)
        sm.transition(CampaignStatus.PACING_HOLD)
        assert sm.status == CampaignStatus.PACING_HOLD

    def test_active_to_completed(self):
        """ACTIVE -> COMPLETED when flight end date reached."""
        sm = CampaignAutomationStateMachine("campaign-active-3", initial_status=CampaignStatus.ACTIVE)
        sm.transition(CampaignStatus.COMPLETED)
        assert sm.status == CampaignStatus.COMPLETED

    def test_active_to_canceled(self):
        """ACTIVE -> CANCELED is allowed."""
        sm = CampaignAutomationStateMachine("campaign-active-4", initial_status=CampaignStatus.ACTIVE)
        sm.transition(CampaignStatus.CANCELED)
        assert sm.status == CampaignStatus.CANCELED

    # -- PAUSED transitions --

    def test_paused_to_active(self):
        """PAUSED -> ACTIVE on manual resume."""
        sm = CampaignAutomationStateMachine("campaign-paused-1", initial_status=CampaignStatus.PAUSED)
        sm.transition(CampaignStatus.ACTIVE)
        assert sm.status == CampaignStatus.ACTIVE

    def test_paused_to_canceled(self):
        """PAUSED -> CANCELED is allowed."""
        sm = CampaignAutomationStateMachine("campaign-paused-2", initial_status=CampaignStatus.PAUSED)
        sm.transition(CampaignStatus.CANCELED)
        assert sm.status == CampaignStatus.CANCELED

    # -- PACING_HOLD transitions --

    def test_pacing_hold_to_active(self):
        """PACING_HOLD -> ACTIVE when deviation resolved."""
        sm = CampaignAutomationStateMachine("campaign-ph-1", initial_status=CampaignStatus.PACING_HOLD)
        sm.transition(CampaignStatus.ACTIVE)
        assert sm.status == CampaignStatus.ACTIVE

    def test_pacing_hold_to_paused(self):
        """PACING_HOLD -> PAUSED when escalated to manual."""
        sm = CampaignAutomationStateMachine("campaign-ph-2", initial_status=CampaignStatus.PACING_HOLD)
        sm.transition(CampaignStatus.PAUSED)
        assert sm.status == CampaignStatus.PAUSED

    def test_pacing_hold_to_canceled(self):
        """PACING_HOLD -> CANCELED is allowed."""
        sm = CampaignAutomationStateMachine("campaign-ph-3", initial_status=CampaignStatus.PACING_HOLD)
        sm.transition(CampaignStatus.CANCELED)
        assert sm.status == CampaignStatus.CANCELED

    # -- Terminal states --

    def test_completed_is_terminal(self):
        """COMPLETED is a terminal state -- no transitions out."""
        sm = CampaignAutomationStateMachine("campaign-term-1", initial_status=CampaignStatus.COMPLETED)
        with pytest.raises(InvalidTransitionError):
            sm.transition(CampaignStatus.ACTIVE)

    def test_canceled_is_terminal(self):
        """CANCELED is a terminal state -- no transitions out."""
        sm = CampaignAutomationStateMachine("campaign-term-2", initial_status=CampaignStatus.CANCELED)
        with pytest.raises(InvalidTransitionError):
            sm.transition(CampaignStatus.DRAFT)

    # -- Cancellation from all non-terminal states --

    def test_cancellation_from_all_non_terminal_states(self):
        """All non-terminal states should allow transition to CANCELED."""
        cancellable = [
            CampaignStatus.PLANNING,
            CampaignStatus.BOOKING,
            CampaignStatus.READY,
            CampaignStatus.ACTIVE,
            CampaignStatus.PAUSED,
            CampaignStatus.PACING_HOLD,
        ]
        for idx, state in enumerate(cancellable):
            sm = CampaignAutomationStateMachine(f"campaign-cancel-{idx}", initial_status=state)
            sm.transition(CampaignStatus.CANCELED)
            assert sm.status == CampaignStatus.CANCELED

    # -- Invalid transitions --

    def test_invalid_transition_preserves_state(self):
        """An invalid transition must NOT change the current state."""
        sm = CampaignAutomationStateMachine("campaign-invalid-1")
        with pytest.raises(InvalidTransitionError):
            sm.transition(CampaignStatus.COMPLETED)
        assert sm.status == CampaignStatus.DRAFT

    # -- validate_transition method --

    def test_validate_transition_returns_true_for_valid(self):
        """validate_transition returns True for valid transitions."""
        sm = CampaignAutomationStateMachine("campaign-vt-1")
        assert sm.validate_transition(CampaignStatus.DRAFT, CampaignStatus.PLANNING) is True

    def test_validate_transition_returns_false_for_invalid(self):
        """validate_transition returns False for invalid transitions."""
        sm = CampaignAutomationStateMachine("campaign-vt-2")
        assert sm.validate_transition(CampaignStatus.DRAFT, CampaignStatus.ACTIVE) is False

    def test_validate_transition_all_valid_transitions(self):
        """validate_transition returns True for every valid transition."""
        sm = CampaignAutomationStateMachine("campaign-vt-3")
        valid_transitions = [
            (CampaignStatus.DRAFT, CampaignStatus.PLANNING),
            (CampaignStatus.PLANNING, CampaignStatus.BOOKING),
            (CampaignStatus.PLANNING, CampaignStatus.CANCELED),
            (CampaignStatus.BOOKING, CampaignStatus.READY),
            (CampaignStatus.BOOKING, CampaignStatus.PLANNING),
            (CampaignStatus.BOOKING, CampaignStatus.CANCELED),
            (CampaignStatus.READY, CampaignStatus.ACTIVE),
            (CampaignStatus.READY, CampaignStatus.CANCELED),
            (CampaignStatus.READY, CampaignStatus.PLANNING),
            (CampaignStatus.ACTIVE, CampaignStatus.PAUSED),
            (CampaignStatus.ACTIVE, CampaignStatus.PACING_HOLD),
            (CampaignStatus.ACTIVE, CampaignStatus.COMPLETED),
            (CampaignStatus.ACTIVE, CampaignStatus.CANCELED),
            (CampaignStatus.PAUSED, CampaignStatus.ACTIVE),
            (CampaignStatus.PAUSED, CampaignStatus.CANCELED),
            (CampaignStatus.PACING_HOLD, CampaignStatus.ACTIVE),
            (CampaignStatus.PACING_HOLD, CampaignStatus.PAUSED),
            (CampaignStatus.PACING_HOLD, CampaignStatus.CANCELED),
        ]
        for from_s, to_s in valid_transitions:
            assert sm.validate_transition(from_s, to_s) is True, (
                f"Expected valid: {from_s.value} -> {to_s.value}"
            )

    def test_validate_transition_invalid_pairs(self):
        """validate_transition returns False for known-invalid transitions."""
        sm = CampaignAutomationStateMachine("campaign-vt-4")
        invalid_transitions = [
            (CampaignStatus.DRAFT, CampaignStatus.ACTIVE),
            (CampaignStatus.DRAFT, CampaignStatus.BOOKING),
            (CampaignStatus.DRAFT, CampaignStatus.READY),
            (CampaignStatus.READY, CampaignStatus.COMPLETED),
            (CampaignStatus.READY, CampaignStatus.PAUSED),
            (CampaignStatus.COMPLETED, CampaignStatus.ACTIVE),
            (CampaignStatus.CANCELED, CampaignStatus.DRAFT),
        ]
        for from_s, to_s in invalid_transitions:
            assert sm.validate_transition(from_s, to_s) is False, (
                f"Expected invalid: {from_s.value} -> {to_s.value}"
            )

    # -- Allowed transitions --

    def test_allowed_transitions_from_draft(self):
        """From DRAFT, only PLANNING should be allowed."""
        sm = CampaignAutomationStateMachine("campaign-allowed-1")
        allowed = sm.allowed_transitions()
        assert CampaignStatus.PLANNING in allowed
        assert len(allowed) == 1

    def test_allowed_transitions_from_ready(self):
        """From READY, ACTIVE, CANCELED, and PLANNING should be allowed."""
        sm = CampaignAutomationStateMachine("campaign-allowed-2", initial_status=CampaignStatus.READY)
        allowed = sm.allowed_transitions()
        assert CampaignStatus.ACTIVE in allowed
        assert CampaignStatus.CANCELED in allowed
        assert CampaignStatus.PLANNING in allowed
        assert len(allowed) == 3

    def test_allowed_transitions_from_active(self):
        """From ACTIVE, PAUSED, PACING_HOLD, COMPLETED, and CANCELED should be allowed."""
        sm = CampaignAutomationStateMachine("campaign-allowed-3", initial_status=CampaignStatus.ACTIVE)
        allowed = sm.allowed_transitions()
        assert CampaignStatus.PAUSED in allowed
        assert CampaignStatus.PACING_HOLD in allowed
        assert CampaignStatus.COMPLETED in allowed
        assert CampaignStatus.CANCELED in allowed
        assert len(allowed) == 4

    # -- Serialization round-trip --

    def test_serialization_round_trip(self):
        """Serialize and deserialize preserves state and history."""
        sm = CampaignAutomationStateMachine("campaign-serial-1")
        sm.transition(CampaignStatus.PLANNING, actor="agent:buyer")
        sm.transition(CampaignStatus.BOOKING, reason="Plan approved")

        data = sm.to_dict()
        restored = CampaignAutomationStateMachine.from_dict(data)

        assert restored.order_id == "campaign-serial-1"
        assert restored.status == CampaignStatus.BOOKING
        assert len(restored.history) == 2
        assert restored.history[0].actor == "agent:buyer"
        assert restored.history[1].reason == "Plan approved"

    # -- Audit trail --

    def test_transitions_recorded_in_audit(self):
        """Transitions should be recorded in the audit log."""
        sm = CampaignAutomationStateMachine("campaign-audit-1")
        sm.transition(CampaignStatus.PLANNING)
        sm.transition(CampaignStatus.BOOKING)
        sm.transition(CampaignStatus.READY)

        assert len(sm.history) == 3
        assert sm.history[0].from_status == "draft"
        assert sm.history[0].to_status == "planning"
        assert sm.history[2].to_status == "ready"


# ---------------------------------------------------------------------------
# DealStore integration
# ---------------------------------------------------------------------------


class TestDealStoreIntegration:
    """Test state machine enforcement in DealStore.update_deal_status()."""

    def _make_store(self):
        """Create an in-memory DealStore."""
        from ad_buyer.storage.deal_store import DealStore
        store = DealStore("sqlite:///:memory:")
        store.connect()
        return store

    def test_valid_transition_succeeds(self):
        """A valid transition via DealStore should update status."""
        store = self._make_store()
        deal_id = store.save_deal(
            seller_url="http://seller.example.com",
            product_id="prod_1",
            status="quoted",
        )
        result = store.update_deal_status(deal_id, "negotiating")
        assert result is True

        deal = store.get_deal(deal_id)
        assert deal["status"] == "negotiating"
        store.disconnect()

    def test_invalid_transition_rejected(self):
        """An invalid transition via DealStore should be rejected."""
        store = self._make_store()
        deal_id = store.save_deal(
            seller_url="http://seller.example.com",
            product_id="prod_2",
            status="quoted",
        )
        result = store.update_deal_status(deal_id, "completed")
        assert result is False

        # Status should remain unchanged
        deal = store.get_deal(deal_id)
        assert deal["status"] == "quoted"
        store.disconnect()

    def test_transition_audit_recorded_in_store(self):
        """Valid transitions should be recorded in the status_transitions table."""
        store = self._make_store()
        deal_id = store.save_deal(
            seller_url="http://seller.example.com",
            product_id="prod_3",
            status="quoted",
        )
        store.update_deal_status(deal_id, "negotiating", triggered_by="agent:dsp")
        history = store.get_status_history("deal", deal_id)
        # Initial creation + one transition
        assert len(history) >= 2
        last = history[-1]
        assert last["from_status"] == "quoted"
        assert last["to_status"] == "negotiating"
        assert last["triggered_by"] == "agent:dsp"
        store.disconnect()

    def test_unknown_status_treated_as_unvalidated(self):
        """Deals with non-BuyerDealStatus values bypass validation gracefully."""
        store = self._make_store()
        deal_id = store.save_deal(
            seller_url="http://seller.example.com",
            product_id="prod_4",
            status="custom_legacy_status",
        )
        # When the current status is not a known BuyerDealStatus, the store
        # should still allow the update (backward compatibility).
        result = store.update_deal_status(deal_id, "completed")
        assert result is True
        store.disconnect()
