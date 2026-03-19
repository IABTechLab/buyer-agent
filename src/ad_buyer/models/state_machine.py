# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Buyer Order State Machine (buyer-5er).

Formal state machine for buyer deal and campaign lifecycles, adapted from
the seller's OrderStateMachine framework.  Provides:

- BuyerDealStatus: deal lifecycle (quoted -> negotiating -> ... -> completed)
- BuyerCampaignStatus: campaign/booking lifecycle (maps to ExecutionStatus)
- CampaignStatus: campaign automation lifecycle with READY state
  (draft -> planning -> booking -> ready -> active -> completed)
- DealStateMachine / CampaignStateMachine: configurable transitions, guard
  conditions, and a full audit log of every state change
- CampaignAutomationStateMachine: campaign automation with READY state,
  PAUSED/PACING_HOLD distinction, and validate_transition() method
- Linear TV extensions: makegood_pending, partially_canceled

Existing code continues to work: ExecutionStatus and DSPFlowStatus are
preserved and mapped into the new enums where flows need the machine.

Pure Pydantic + stdlib -- no external dependencies.
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Buyer Deal Status
# ---------------------------------------------------------------------------


class BuyerDealStatus(str, Enum):
    """Status for buyer deal lifecycle.

    Happy path:
        quoted -> negotiating -> accepted -> booking -> booked ->
        delivering -> completed

    Linear TV extensions:
        delivering -> makegood_pending -> delivering
        booked -> partially_canceled -> delivering

    Terminal states: completed, failed, cancelled, expired
    """

    # Entry
    QUOTED = "quoted"

    # Negotiation
    NEGOTIATING = "negotiating"

    # Acceptance
    ACCEPTED = "accepted"

    # Booking
    BOOKING = "booking"
    BOOKED = "booked"

    # Delivery
    DELIVERING = "delivering"

    # Terminal
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

    # Linear TV extensions
    MAKEGOOD_PENDING = "makegood_pending"
    PARTIALLY_CANCELED = "partially_canceled"


# ---------------------------------------------------------------------------
# Buyer Campaign Status
# ---------------------------------------------------------------------------


class BuyerCampaignStatus(str, Enum):
    """Status for buyer campaign/booking lifecycle.

    Maps to the existing ExecutionStatus enum used by BookingState.
    """

    INITIALIZED = "initialized"
    BRIEF_RECEIVED = "brief_received"
    VALIDATION_FAILED = "validation_failed"
    BUDGET_ALLOCATED = "budget_allocated"
    RESEARCHING = "researching"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING_BOOKINGS = "executing_bookings"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Campaign Status (Campaign Automation)
# ---------------------------------------------------------------------------


class CampaignStatus(str, Enum):
    """Status for campaign automation lifecycle.

    Happy path:
        draft -> planning -> booking -> ready -> active -> completed

    READY separates "campaign is prepared" from "campaign is live."
    A campaign enters READY when all deals are booked and creative is
    validated.  Transition to ACTIVE can be automatic (flight start date)
    or manual (buyer activates).

    PAUSED vs PACING_HOLD:
        PAUSED is manual (human decision).
        PACING_HOLD is automated (system detected pacing deviation
        exceeding threshold).

    Terminal states: completed, canceled
    """

    # Entry
    DRAFT = "draft"

    # Planning & booking
    PLANNING = "planning"
    BOOKING = "booking"

    # Ready -- all deals booked, creative validated, awaiting activation
    READY = "ready"

    # Live
    ACTIVE = "active"

    # Hold states
    PAUSED = "paused"          # manual pause (human decision)
    PACING_HOLD = "pacing_hold"  # automated (pacing deviation threshold)

    # Terminal
    COMPLETED = "completed"
    CANCELED = "canceled"


# ---------------------------------------------------------------------------
# Audit models
# ---------------------------------------------------------------------------


class StateTransition(BaseModel):
    """Immutable record of a single state change."""

    transition_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    from_status: str
    to_status: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    actor: str = "system"  # "system", "human:<user_id>", "agent:<agent_id>"
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrderAuditLog(BaseModel):
    """Append-only audit trail for an entity's lifecycle."""

    order_id: str
    transitions: list[StateTransition] = Field(default_factory=list)

    @property
    def current_status(self) -> Optional[str]:
        if self.transitions:
            return self.transitions[-1].to_status
        return None

    def append(self, transition: StateTransition) -> None:
        self.transitions.append(transition)


# ---------------------------------------------------------------------------
# Transition rules
# ---------------------------------------------------------------------------

# Type alias for guard functions: (order_id, from_status, to_status, context) -> bool
GuardFn = Callable[[str, Any, Any, dict[str, Any]], bool]


class TransitionRule(BaseModel):
    """Defines a permitted state transition with optional guard condition."""

    model_config = {"arbitrary_types_allowed": True}

    from_status: Any  # enum member
    to_status: Any  # enum member
    guard: Optional[GuardFn] = Field(default=None, exclude=True)
    description: str = ""


# ---------------------------------------------------------------------------
# InvalidTransitionError
# ---------------------------------------------------------------------------


class InvalidTransitionError(Exception):
    """Raised when a state transition is not allowed."""

    def __init__(
        self, order_id: str, from_status: Any, to_status: Any, reason: str = ""
    ):
        self.order_id = order_id
        self.from_status = from_status
        self.to_status = to_status
        from_val = from_status.value if hasattr(from_status, "value") else str(from_status)
        to_val = to_status.value if hasattr(to_status, "value") else str(to_status)
        msg = f"Cannot transition order {order_id} from {from_val} to {to_val}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Generic State Machine base
# ---------------------------------------------------------------------------


class _BaseStateMachine:
    """Generic state machine with configurable transitions and audit trail.

    Subclasses set the status enum type; this base provides the machinery.
    """

    def __init__(
        self,
        order_id: str,
        initial_status: Any,
        rules: Optional[list[TransitionRule]] = None,
    ):
        self.order_id = order_id
        self._status = initial_status
        self._rules = list(rules) if rules else []
        self._audit = OrderAuditLog(order_id=order_id)

        # Index rules for fast lookup: (from, to) -> rule
        self._rule_index: dict[tuple[Any, Any], TransitionRule] = {
            (r.from_status, r.to_status): r for r in self._rules
        }

    @property
    def status(self) -> Any:
        return self._status

    @property
    def audit_log(self) -> OrderAuditLog:
        return self._audit

    @property
    def history(self) -> list[StateTransition]:
        return self._audit.transitions

    def allowed_transitions(self) -> list[Any]:
        """Return the list of states reachable from the current state."""
        return [
            to for (frm, to), _ in self._rule_index.items()
            if frm == self._status
        ]

    def can_transition(
        self, to_status: Any, context: Optional[dict[str, Any]] = None
    ) -> bool:
        """Check whether a transition is permitted (including guard)."""
        rule = self._rule_index.get((self._status, to_status))
        if rule is None:
            return False
        if rule.guard is not None:
            return rule.guard(self.order_id, self._status, to_status, context or {})
        return True

    def transition(
        self,
        to_status: Any,
        *,
        actor: str = "system",
        reason: str = "",
        context: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> StateTransition:
        """Execute a state transition.

        Raises InvalidTransitionError if not allowed.
        Returns the StateTransition audit record.
        """
        rule = self._rule_index.get((self._status, to_status))
        if rule is None:
            raise InvalidTransitionError(
                self.order_id, self._status, to_status, "no matching transition rule"
            )

        if rule.guard is not None:
            ctx = context or {}
            if not rule.guard(self.order_id, self._status, to_status, ctx):
                raise InvalidTransitionError(
                    self.order_id, self._status, to_status, "guard condition failed"
                )

        from_status = self._status
        self._status = to_status

        record = StateTransition(
            from_status=from_status.value if hasattr(from_status, "value") else str(from_status),
            to_status=to_status.value if hasattr(to_status, "value") else str(to_status),
            actor=actor,
            reason=reason or rule.description,
            metadata=metadata or {},
        )
        self._audit.append(record)
        return record

    def add_rule(self, rule: TransitionRule) -> None:
        """Add a custom transition rule."""
        self._rules.append(rule)
        self._rule_index[(rule.from_status, rule.to_status)] = rule

    def remove_rule(self, from_status: Any, to_status: Any) -> bool:
        """Remove a transition rule. Returns True if a rule was removed."""
        key = (from_status, to_status)
        if key in self._rule_index:
            rule = self._rule_index.pop(key)
            self._rules.remove(rule)
            return True
        return False

    # -- Serialization --

    def to_dict(self) -> dict[str, Any]:
        """Serialize the machine state for storage."""
        return {
            "order_id": self.order_id,
            "status": self._status.value if hasattr(self._status, "value") else str(self._status),
            "audit_log": self._audit.model_dump(mode="json"),
        }


# ---------------------------------------------------------------------------
# Default transition tables
# ---------------------------------------------------------------------------


def _build_deal_rules() -> list[TransitionRule]:
    """Build the default transition rules for buyer deals."""
    S = BuyerDealStatus
    transitions: list[tuple[BuyerDealStatus, BuyerDealStatus, str]] = [
        # Happy path
        (S.QUOTED, S.NEGOTIATING, "Buyer initiates negotiation"),
        (S.QUOTED, S.ACCEPTED, "Quote accepted without negotiation"),
        (S.NEGOTIATING, S.ACCEPTED, "Deal terms accepted"),
        (S.NEGOTIATING, S.QUOTED, "Counter-offer received, re-quoting"),
        (S.ACCEPTED, S.BOOKING, "Booking process started"),
        (S.BOOKING, S.BOOKED, "Booking confirmed by seller"),
        (S.BOOKED, S.DELIVERING, "Campaign delivery started"),
        (S.DELIVERING, S.COMPLETED, "Campaign delivery completed"),

        # Failure from active states
        (S.QUOTED, S.FAILED, "Quote processing failed"),
        (S.NEGOTIATING, S.FAILED, "Negotiation failed"),
        (S.BOOKING, S.FAILED, "Booking failed"),
        (S.DELIVERING, S.FAILED, "Delivery failed"),

        # Cancellation from non-terminal states
        (S.QUOTED, S.CANCELLED, "Deal cancelled"),
        (S.NEGOTIATING, S.CANCELLED, "Deal cancelled during negotiation"),
        (S.ACCEPTED, S.CANCELLED, "Deal cancelled after acceptance"),
        (S.BOOKING, S.CANCELLED, "Deal cancelled during booking"),
        (S.BOOKED, S.CANCELLED, "Booked deal cancelled"),
        (S.DELIVERING, S.CANCELLED, "Delivery cancelled"),

        # Expiry
        (S.QUOTED, S.EXPIRED, "Quote expired"),
        (S.NEGOTIATING, S.EXPIRED, "Negotiation expired"),

        # Linear TV extensions
        (S.DELIVERING, S.MAKEGOOD_PENDING, "Makegood requested for under-delivery"),
        (S.MAKEGOOD_PENDING, S.DELIVERING, "Makegood resolved, delivery resumed"),
        (S.MAKEGOOD_PENDING, S.COMPLETED, "Makegood resolved, campaign complete"),
        (S.MAKEGOOD_PENDING, S.FAILED, "Makegood could not be fulfilled"),
        (S.BOOKED, S.PARTIALLY_CANCELED, "Partial cancellation of booked units"),
        (S.PARTIALLY_CANCELED, S.DELIVERING, "Partially canceled deal begins delivery"),
        (S.PARTIALLY_CANCELED, S.CANCELLED, "Remaining units cancelled"),
    ]

    return [
        TransitionRule(from_status=f, to_status=t, description=d)
        for f, t, d in transitions
    ]


def _build_campaign_rules() -> list[TransitionRule]:
    """Build the default transition rules for buyer campaigns."""
    S = BuyerCampaignStatus
    transitions: list[tuple[BuyerCampaignStatus, BuyerCampaignStatus, str]] = [
        # Happy path
        (S.INITIALIZED, S.BRIEF_RECEIVED, "Campaign brief received"),
        (S.BRIEF_RECEIVED, S.BUDGET_ALLOCATED, "Budget allocated across channels"),
        (S.BRIEF_RECEIVED, S.VALIDATION_FAILED, "Brief validation failed"),
        (S.BUDGET_ALLOCATED, S.RESEARCHING, "Channel research started"),
        (S.RESEARCHING, S.AWAITING_APPROVAL, "Recommendations ready for approval"),
        (S.AWAITING_APPROVAL, S.EXECUTING_BOOKINGS, "Approvals granted, executing"),
        (S.EXECUTING_BOOKINGS, S.COMPLETED, "All bookings executed"),

        # Failure from active states
        (S.BRIEF_RECEIVED, S.FAILED, "Brief processing failed"),
        (S.BUDGET_ALLOCATED, S.FAILED, "Budget allocation failed"),
        (S.RESEARCHING, S.FAILED, "Research failed"),
        (S.AWAITING_APPROVAL, S.FAILED, "Approval process failed"),
        (S.EXECUTING_BOOKINGS, S.FAILED, "Booking execution failed"),

        # Recovery
        (S.VALIDATION_FAILED, S.INITIALIZED, "Reset after validation failure"),
        (S.FAILED, S.INITIALIZED, "Reset after failure"),
    ]

    return [
        TransitionRule(from_status=f, to_status=t, description=d)
        for f, t, d in transitions
    ]


# ---------------------------------------------------------------------------
# Deal State Machine
# ---------------------------------------------------------------------------


class DealStateMachine(_BaseStateMachine):
    """State machine for buyer deal lifecycle management.

    Provides:
    - Configurable transition rules with guard conditions
    - Full audit trail of every state change
    - Query helpers for allowed next states
    - Linear TV extensions (makegood_pending, partially_canceled)
    """

    def __init__(
        self,
        order_id: str,
        initial_status: BuyerDealStatus = BuyerDealStatus.QUOTED,
        rules: Optional[list[TransitionRule]] = None,
    ):
        super().__init__(
            order_id=order_id,
            initial_status=initial_status,
            rules=rules if rules is not None else _build_deal_rules(),
        )

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        rules: Optional[list[TransitionRule]] = None,
    ) -> "DealStateMachine":
        """Restore a machine from stored state."""
        machine = cls(
            order_id=data["order_id"],
            initial_status=BuyerDealStatus(data["status"]),
            rules=rules,
        )
        audit_data = data.get("audit_log", {})
        if audit_data:
            machine._audit = OrderAuditLog(**audit_data)
        return machine


# ---------------------------------------------------------------------------
# Campaign State Machine
# ---------------------------------------------------------------------------


class CampaignStateMachine(_BaseStateMachine):
    """State machine for buyer campaign/booking lifecycle.

    Maps to the existing ExecutionStatus-based flow in DealBookingFlow.
    """

    def __init__(
        self,
        order_id: str,
        initial_status: BuyerCampaignStatus = BuyerCampaignStatus.INITIALIZED,
        rules: Optional[list[TransitionRule]] = None,
    ):
        super().__init__(
            order_id=order_id,
            initial_status=initial_status,
            rules=rules if rules is not None else _build_campaign_rules(),
        )

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        rules: Optional[list[TransitionRule]] = None,
    ) -> "CampaignStateMachine":
        """Restore a machine from stored state."""
        machine = cls(
            order_id=data["order_id"],
            initial_status=BuyerCampaignStatus(data["status"]),
            rules=rules,
        )
        audit_data = data.get("audit_log", {})
        if audit_data:
            machine._audit = OrderAuditLog(**audit_data)
        return machine


# ---------------------------------------------------------------------------
# Campaign Automation transition rules
# ---------------------------------------------------------------------------


def _build_campaign_automation_rules() -> list[TransitionRule]:
    """Build transition rules for the campaign automation state machine.

    Implements the state machine from Section 6.6 of the Campaign Automation
    Strategic Plan, including the READY state that separates "campaign is
    prepared" from "campaign is live."
    """
    S = CampaignStatus
    transitions: list[tuple[CampaignStatus, CampaignStatus, str]] = [
        # Happy path
        (S.DRAFT, S.PLANNING, "Campaign planning begins"),
        (S.PLANNING, S.BOOKING, "Plan approved, booking starts"),
        (S.BOOKING, S.READY, "All deals booked, creative validated"),
        (S.READY, S.ACTIVE, "Flight start date reached or manual activation"),
        (S.ACTIVE, S.COMPLETED, "Flight end date reached"),

        # Cancellation from non-terminal states
        (S.PLANNING, S.CANCELED, "Campaign canceled during planning"),
        (S.BOOKING, S.CANCELED, "Campaign canceled during booking"),
        (S.READY, S.CANCELED, "Campaign canceled before going live"),
        (S.ACTIVE, S.CANCELED, "Campaign terminated"),
        (S.PAUSED, S.CANCELED, "Paused campaign canceled"),
        (S.PACING_HOLD, S.CANCELED, "Pacing-held campaign canceled"),

        # Replanning
        (S.BOOKING, S.PLANNING, "Needs replanning"),
        (S.READY, S.PLANNING, "Needs replanning before start"),

        # Pause / hold from ACTIVE
        (S.ACTIVE, S.PAUSED, "Manual pause"),
        (S.ACTIVE, S.PACING_HOLD, "Automated pacing deviation threshold"),

        # Resume from PAUSED
        (S.PAUSED, S.ACTIVE, "Manual resume"),

        # Resume / escalate from PACING_HOLD
        (S.PACING_HOLD, S.ACTIVE, "Deviation resolved, auto-resume"),
        (S.PACING_HOLD, S.PAUSED, "Escalated to manual pause"),
    ]

    return [
        TransitionRule(from_status=f, to_status=t, description=d)
        for f, t, d in transitions
    ]


# ---------------------------------------------------------------------------
# Campaign Automation State Machine
# ---------------------------------------------------------------------------


class CampaignAutomationStateMachine(_BaseStateMachine):
    """State machine for campaign automation lifecycle.

    Implements the full campaign lifecycle from Section 6.6 of the Campaign
    Automation Strategic Plan:

        DRAFT -> PLANNING -> BOOKING -> READY -> ACTIVE -> COMPLETED

    The READY state separates "campaign is prepared" from "campaign is live."
    PAUSED is for manual holds; PACING_HOLD is for automated pacing deviation.

    Provides validate_transition(from_state, to_state) for checking whether
    a transition is permitted without requiring a machine in that state.
    """

    # Class-level transition table for static validation
    TRANSITION_TABLE: dict[CampaignStatus, list[CampaignStatus]] = {}

    def __init__(
        self,
        order_id: str,
        initial_status: CampaignStatus = CampaignStatus.DRAFT,
        rules: Optional[list[TransitionRule]] = None,
    ):
        super().__init__(
            order_id=order_id,
            initial_status=initial_status,
            rules=rules if rules is not None else _build_campaign_automation_rules(),
        )

    def validate_transition(
        self, from_state: CampaignStatus, to_state: CampaignStatus
    ) -> bool:
        """Check whether a transition from from_state to to_state is valid.

        This is a static check against the transition rules -- it does not
        require the machine to be in from_state, and it does not evaluate
        guard conditions.
        """
        return (from_state, to_state) in self._rule_index

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        rules: Optional[list[TransitionRule]] = None,
    ) -> "CampaignAutomationStateMachine":
        """Restore a machine from stored state."""
        machine = cls(
            order_id=data["order_id"],
            initial_status=CampaignStatus(data["status"]),
            rules=rules,
        )
        audit_data = data.get("audit_log", {})
        if audit_data:
            machine._audit = OrderAuditLog(**audit_data)
        return machine


# Build class-level transition table for static access
CampaignAutomationStateMachine.TRANSITION_TABLE = {
    state: [
        r.to_status for r in _build_campaign_automation_rules()
        if r.from_status == state
    ]
    for state in CampaignStatus
}


# ---------------------------------------------------------------------------
# Mapping helpers -- bridge old enums to the new status enums
# ---------------------------------------------------------------------------


_EXECUTION_STATUS_MAP: dict[str, BuyerCampaignStatus] = {
    "initialized": BuyerCampaignStatus.INITIALIZED,
    "brief_received": BuyerCampaignStatus.BRIEF_RECEIVED,
    "validation_failed": BuyerCampaignStatus.VALIDATION_FAILED,
    "budget_allocated": BuyerCampaignStatus.BUDGET_ALLOCATED,
    "researching": BuyerCampaignStatus.RESEARCHING,
    "awaiting_approval": BuyerCampaignStatus.AWAITING_APPROVAL,
    "executing_bookings": BuyerCampaignStatus.EXECUTING_BOOKINGS,
    "completed": BuyerCampaignStatus.COMPLETED,
    "failed": BuyerCampaignStatus.FAILED,
}

_DSP_FLOW_STATUS_MAP: dict[str, BuyerDealStatus] = {
    "initialized": BuyerDealStatus.QUOTED,
    "request_received": BuyerDealStatus.QUOTED,
    "discovering_inventory": BuyerDealStatus.QUOTED,
    "evaluating_pricing": BuyerDealStatus.NEGOTIATING,
    "requesting_deal": BuyerDealStatus.BOOKING,
    "deal_created": BuyerDealStatus.BOOKED,
    "failed": BuyerDealStatus.FAILED,
}


def from_execution_status(value: str) -> BuyerCampaignStatus:
    """Map a legacy ExecutionStatus value to BuyerCampaignStatus."""
    return _EXECUTION_STATUS_MAP.get(value, BuyerCampaignStatus.INITIALIZED)


def from_dsp_flow_status(value: str) -> BuyerDealStatus:
    """Map a legacy DSPFlowStatus value to BuyerDealStatus."""
    return _DSP_FLOW_STATUS_MAP.get(value, BuyerDealStatus.QUOTED)
