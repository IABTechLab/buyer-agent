# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Configurable human approval gates for the campaign pipeline (buyer-2qs).

Implements D-3 (Option C): configurable per campaign, default requires
approval for plan review and booking.  Each campaign brief includes an
``approval_config`` that specifies which stages require human approval.

Approval stages (from the strategic plan):
  - PLAN_REVIEW:      after plan generation, before deal booking
  - BOOKING:          after deals selected, before committing budget
  - CREATIVE:         after creative matched, before ad server push
  - PACING_ADJUSTMENT: after pacing reallocation recommended, before applying

Components:
  - ApprovalStatus: enum for request status (pending/approved/rejected)
  - ApprovalRequest: data model for an approval request
  - ApprovalResult: result of waiting for an approval decision
  - ApprovalGate: main class with check/request/record/wait methods

Event integration:
  - Emits ``approval.requested`` when approval is needed
  - Emits ``approval.granted`` when request is approved
  - Emits ``approval.rejected`` when request is rejected

Storage:
  - Approval requests are persisted in the ``approval_requests`` table
    via CampaignStore.

References:
  - Campaign Automation Strategic Plan, UC-1, D-3, Section 5.3
  - bead: buyer-2qs
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..events.bus import EventBus
from ..events.models import Event, EventType
from ..models.campaign_brief import ApprovalConfig, ApprovalStage
from ..storage.campaign_store import CampaignStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums and data models
# ---------------------------------------------------------------------------


class ApprovalStatus(str, Enum):
    """Status of an approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalRequest(BaseModel):
    """An approval request for a campaign pipeline stage.

    Created when a stage requires human approval. Tracks the request
    lifecycle from pending through to approved/rejected.
    """

    approval_request_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for this approval request",
    )
    campaign_id: str = Field(
        ..., description="Campaign this approval belongs to"
    )
    stage: ApprovalStage = Field(
        ..., description="Pipeline stage requiring approval"
    )
    status: ApprovalStatus = Field(
        default=ApprovalStatus.PENDING,
        description="Current status of the request",
    )
    requested_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the approval was requested",
    )
    decided_at: Optional[datetime] = Field(
        default=None, description="When the decision was made"
    )
    reviewer: Optional[str] = Field(
        default=None, description="Who made the decision"
    )
    notes: Optional[str] = Field(
        default=None, description="Notes from the reviewer"
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional context for the reviewer (e.g., plan summary)",
    )


class ApprovalResult(BaseModel):
    """Result returned by wait_for_approval().

    Captures whether the request was approved, rejected, or timed out.
    """

    approved: bool = Field(
        ..., description="Whether the request was approved"
    )
    approval_request_id: str = Field(
        ..., description="The approval request this result is for"
    )
    stage: ApprovalStage = Field(
        ..., description="The pipeline stage"
    )
    timed_out: bool = Field(
        default=False, description="Whether the wait timed out"
    )
    reviewer: Optional[str] = Field(
        default=None, description="Who made the decision (if not timed out)"
    )
    notes: Optional[str] = Field(
        default=None, description="Reviewer notes (if any)"
    )


# ---------------------------------------------------------------------------
# Stage-to-config mapping
# ---------------------------------------------------------------------------

# Maps ApprovalStage enum values to the corresponding field name
# on the ApprovalConfig model.
_STAGE_TO_CONFIG_FIELD: dict[ApprovalStage, str] = {
    ApprovalStage.PLAN_REVIEW: "plan_review",
    ApprovalStage.BOOKING: "booking",
    ApprovalStage.CREATIVE: "creative",
    ApprovalStage.PACING_ADJUSTMENT: "pacing_adjustment",
}


# ---------------------------------------------------------------------------
# ApprovalGate
# ---------------------------------------------------------------------------


class ApprovalGate:
    """Configurable human approval gates for the campaign pipeline.

    Integrates with:
      - CampaignStore: reads approval_config from campaign records,
        persists approval requests
      - EventBus: emits approval lifecycle events

    Usage::

        gate = ApprovalGate(event_bus=bus, campaign_store=store)

        # Check if approval is needed
        if gate.check_approval_required(campaign_id, ApprovalStage.PLAN_REVIEW):
            request_id = await gate.request_approval(
                campaign_id, ApprovalStage.PLAN_REVIEW,
                context={"plan_summary": "..."}
            )
            result = await gate.wait_for_approval(request_id, timeout=3600)
            if not result.approved:
                # Handle rejection or timeout
                ...

    Args:
        event_bus: EventBus instance for publishing approval events.
        campaign_store: CampaignStore instance for reading campaign data
            and persisting approval requests.
    """

    def __init__(
        self,
        event_bus: EventBus,
        campaign_store: CampaignStore,
    ) -> None:
        self._event_bus = event_bus
        self._store = campaign_store

        # In-memory cache of approval requests for fast lookups.
        # Populated from DB on first access and updated on mutations.
        self._requests: dict[str, ApprovalRequest] = {}

        # Ensure the approval_requests table exists
        self._store.create_approval_requests_table()

        # Load existing requests from DB
        self._load_from_db()

    def _load_from_db(self) -> None:
        """Load approval requests from the database into the in-memory cache."""
        rows = self._store.list_approval_requests()
        for row in rows:
            req = ApprovalRequest(
                approval_request_id=row["approval_request_id"],
                campaign_id=row["campaign_id"],
                stage=ApprovalStage(row["stage"]),
                status=ApprovalStatus(row["status"]),
                requested_at=datetime.fromisoformat(row["requested_at"]),
                decided_at=(
                    datetime.fromisoformat(row["decided_at"])
                    if row.get("decided_at")
                    else None
                ),
                reviewer=row.get("reviewer"),
                notes=row.get("notes"),
                context=json.loads(row.get("context") or "{}"),
            )
            self._requests[req.approval_request_id] = req

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_approval_required(
        self, campaign_id: str, stage: ApprovalStage
    ) -> bool:
        """Check whether a campaign requires approval at the given stage.

        Reads the campaign's ``approval_config`` from the store. If the
        campaign is not found or has no approval config, returns the
        default (plan_review=True, booking=True, others=False).

        Args:
            campaign_id: The campaign to check.
            stage: The pipeline stage to check.

        Returns:
            True if approval is required, False otherwise.
        """
        campaign = self._store.get_campaign(campaign_id)
        if campaign is None:
            return False

        config = self._parse_approval_config(campaign)
        field_name = _STAGE_TO_CONFIG_FIELD.get(stage)
        if field_name is None:
            return False

        return getattr(config, field_name, False)

    async def request_approval(
        self,
        campaign_id: str,
        stage: ApprovalStage,
        context: Optional[dict[str, Any]] = None,
    ) -> str:
        """Create an approval request and emit an event.

        Args:
            campaign_id: The campaign needing approval.
            stage: The pipeline stage requiring approval.
            context: Optional context for the reviewer.

        Returns:
            The approval_request_id for tracking.
        """
        request = ApprovalRequest(
            campaign_id=campaign_id,
            stage=stage,
            context=context or {},
        )

        # Persist to DB
        self._store.save_approval_request(
            approval_request_id=request.approval_request_id,
            campaign_id=request.campaign_id,
            stage=request.stage.value,
            status=request.status.value,
            requested_at=request.requested_at.isoformat(),
            context=json.dumps(request.context),
        )

        # Update in-memory cache
        self._requests[request.approval_request_id] = request

        # Emit event
        event = Event(
            event_type=EventType.APPROVAL_REQUESTED,
            campaign_id=campaign_id,
            payload={
                "approval_request_id": request.approval_request_id,
                "stage": stage.value,
                "context": request.context,
            },
        )
        await self._event_bus.publish(event)

        logger.info(
            "Approval requested: campaign=%s stage=%s request=%s",
            campaign_id,
            stage.value,
            request.approval_request_id,
        )

        return request.approval_request_id

    async def record_approval(
        self,
        approval_request_id: str,
        approved: bool,
        reviewer: str,
        notes: Optional[str] = None,
    ) -> None:
        """Record an approval or rejection decision.

        Args:
            approval_request_id: The request to decide on.
            approved: True to approve, False to reject.
            reviewer: Identifier of the person or system making the decision.
            notes: Optional notes from the reviewer.

        Raises:
            ValueError: If the request is not found or already decided.
        """
        request = self._requests.get(approval_request_id)
        if request is None:
            # Try loading from DB in case another instance created it
            self._load_from_db()
            request = self._requests.get(approval_request_id)
            if request is None:
                raise ValueError(
                    f"Approval request {approval_request_id} not found"
                )

        if request.status != ApprovalStatus.PENDING:
            raise ValueError(
                f"Approval request {approval_request_id} already decided "
                f"(status={request.status.value})"
            )

        # Update the request
        now = datetime.now(timezone.utc)
        new_status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        request.status = new_status
        request.decided_at = now
        request.reviewer = reviewer
        request.notes = notes

        # Persist to DB
        self._store.update_approval_request(
            approval_request_id=approval_request_id,
            status=new_status.value,
            decided_at=now.isoformat(),
            reviewer=reviewer,
            notes=notes,
        )

        # Emit event
        event_type = (
            EventType.APPROVAL_GRANTED if approved else EventType.APPROVAL_REJECTED
        )
        event = Event(
            event_type=event_type,
            campaign_id=request.campaign_id,
            payload={
                "approval_request_id": approval_request_id,
                "stage": request.stage.value,
                "reviewer": reviewer,
                "notes": notes,
            },
        )
        await self._event_bus.publish(event)

        logger.info(
            "Approval %s: campaign=%s stage=%s request=%s reviewer=%s",
            "granted" if approved else "rejected",
            request.campaign_id,
            request.stage.value,
            approval_request_id,
            reviewer,
        )

    def is_approved(self, approval_request_id: str) -> bool:
        """Check whether an approval request has been approved.

        Args:
            approval_request_id: The request to check.

        Returns:
            True if the request exists and is approved, False otherwise.
        """
        request = self._requests.get(approval_request_id)
        if request is None:
            # Try loading from DB
            self._load_from_db()
            request = self._requests.get(approval_request_id)
        if request is None:
            return False
        return request.status == ApprovalStatus.APPROVED

    def get_approval_request(
        self, approval_request_id: str
    ) -> Optional[ApprovalRequest]:
        """Retrieve an approval request by ID.

        Args:
            approval_request_id: The request to retrieve.

        Returns:
            The ApprovalRequest, or None if not found.
        """
        request = self._requests.get(approval_request_id)
        if request is None:
            # Try loading from DB
            self._load_from_db()
            request = self._requests.get(approval_request_id)
        return request

    def list_approval_requests(
        self,
        campaign_id: Optional[str] = None,
        stage: Optional[ApprovalStage] = None,
        status: Optional[ApprovalStatus] = None,
    ) -> list[ApprovalRequest]:
        """List approval requests with optional filters.

        Args:
            campaign_id: Filter by campaign ID.
            stage: Filter by approval stage.
            status: Filter by approval status.

        Returns:
            List of matching ApprovalRequest objects.
        """
        results = list(self._requests.values())

        if campaign_id is not None:
            results = [r for r in results if r.campaign_id == campaign_id]
        if stage is not None:
            results = [r for r in results if r.stage == stage]
        if status is not None:
            results = [r for r in results if r.status == status]

        return results

    async def wait_for_approval(
        self,
        approval_request_id: str,
        timeout: float = 3600.0,
        poll_interval: float = 0.5,
    ) -> ApprovalResult:
        """Wait for an approval decision, polling until decided or timed out.

        Args:
            approval_request_id: The request to wait on.
            timeout: Maximum seconds to wait (default: 1 hour).
            poll_interval: Seconds between status checks (default: 0.5s).

        Returns:
            ApprovalResult indicating the outcome.
        """
        deadline = asyncio.get_event_loop().time() + timeout

        while asyncio.get_event_loop().time() < deadline:
            request = self._requests.get(approval_request_id)
            if request is None:
                # Try loading from DB
                self._load_from_db()
                request = self._requests.get(approval_request_id)

            if request is not None and request.status != ApprovalStatus.PENDING:
                return ApprovalResult(
                    approved=request.status == ApprovalStatus.APPROVED,
                    approval_request_id=approval_request_id,
                    stage=request.stage,
                    timed_out=False,
                    reviewer=request.reviewer,
                    notes=request.notes,
                )

            await asyncio.sleep(poll_interval)

        # Timed out -- determine stage from request if available
        request = self._requests.get(approval_request_id)
        stage = request.stage if request else ApprovalStage.PLAN_REVIEW

        return ApprovalResult(
            approved=False,
            approval_request_id=approval_request_id,
            stage=stage,
            timed_out=True,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_approval_config(campaign: dict[str, Any]) -> ApprovalConfig:
        """Parse the approval_config from a campaign record.

        Falls back to default ApprovalConfig if the field is missing
        or cannot be parsed.
        """
        raw = campaign.get("approval_config")
        if raw is None:
            return ApprovalConfig()

        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return ApprovalConfig()
        elif isinstance(raw, dict):
            data = raw
        else:
            return ApprovalConfig()

        try:
            return ApprovalConfig(**data)
        except Exception:
            return ApprovalConfig()
