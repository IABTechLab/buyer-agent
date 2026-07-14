# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Deal Booking Flow - main workflow for booking advertising deals."""

import json
import logging
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from crewai.flow.flow import Flow, listen, or_, start
from pydantic import ValidationError

from ..async_utils import run_async
from ..booking.spend_ceiling import SpendCeilingExceeded, enforce_spend_ceiling
from ..clients.opendirect_client import OpenDirectClient
from ..crews.channel_crews import (
    create_branding_crew,
    create_ctv_crew,
    create_mobile_crew,
    create_performance_crew,
)
from ..crews.portfolio_crew import create_portfolio_crew
from ..events.helpers import emit_event_sync
from ..events.models import EventType
from ..models.audience_plan import AudiencePlan
from ..models.flow_state import (
    BookedLine,
    BookingState,
    ChannelAllocation,
    ChannelBrief,
    ExecutionStatus,
    ProductRecommendation,
)
from ..models.ucp import SignalType
from ..orchestration.multi_seller import (
    DealParams,
    InventoryRequirements,
    MultiSellerOrchestrator,
    OrchestrationResult,
)
from ..storage.deal_store import DealStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Channel -> deals-API mapping (folded in from the retired CampaignPipeline,
# adapted to this flow's channel vocabulary; bead ar-j2nw)
# ---------------------------------------------------------------------------

# Maps this flow's channel names to the media_type string used by the
# orchestrator's InventoryRequirements for seller discovery.
_CHANNEL_MEDIA_TYPE_MAP: dict[str, str] = {
    "branding": "display",
    "ctv": "ctv",
    "mobile_app": "mobile",
    "performance": "display",
}

# Default deal types to request per channel.
_CHANNEL_DEAL_TYPES: dict[str, list[str]] = {
    "branding": ["PD", "PA"],
    "ctv": ["PG", "PD"],
    "mobile_app": ["PD", "PA"],
    "performance": ["PD", "PA"],
}


def _build_default_orchestrator() -> MultiSellerOrchestrator:
    """Build the production MultiSellerOrchestrator from settings.

    Registry discovery uses the IAB server URL (same wiring as the MCP
    server's registry client); per-seller DealsClients are created for
    whichever seller URLs discovery returns.
    """
    from ..clients.deals_client import DealsClient
    from ..config.settings import settings
    from ..registry.client import RegistryClient

    return MultiSellerOrchestrator(
        registry_client=RegistryClient(registry_url=settings.iab_server_url),
        deals_client_factory=lambda seller_url, **kwargs: DealsClient(seller_url, **kwargs),
    )


class DealBookingFlow(Flow[BookingState]):
    """Event-driven flow for end-to-end deal booking workflow.

    Flow steps:
    1. Receive and validate campaign brief
    2. Portfolio manager allocates budget across channels
    3. Channel specialists research inventory (parallel)
    4. Consolidate recommendations for approval
    5. Human approval checkpoint
    6. Execute bookings
    7. Confirm and report
    """

    def __init__(
        self,
        client: OpenDirectClient,
        store: DealStore | None = None,
        orchestrator: MultiSellerOrchestrator | None = None,
        **state_kwargs: Any,
    ):
        """Initialize the flow with OpenDirect client and optional persistence.

        Args:
            client: OpenDirect API client for publisher interactions
            store: Optional DealStore for persisting deal state. When None,
                the flow behaves identically to before (in-memory only).
            orchestrator: MultiSellerOrchestrator used to execute approved
                bookings against real sellers (quotes -> deals contract).
                When None, a default production orchestrator is built
                lazily from settings on first booking.
            **state_kwargs: Initial state field values for the underlying
                ``BookingState``.  CrewAI >=1.14 made ``Flow`` a Pydantic
                model and removed the legacy ``state`` setter, so initial
                state is now supplied via the ``initial_state=`` field on
                ``Flow.__init__`` rather than as ad-hoc keyword arguments.
        """
        if state_kwargs:
            # CrewAI >=1.14 expects ``initial_state`` to be the typed
            # state model instance (or None), not a loose dict.  Build a
            # ``BookingState`` from the supplied kwargs so callers can
            # keep passing fields by name (e.g. ``campaign_brief=...``).
            super().__init__(initial_state=BookingState(**state_kwargs))
        else:
            super().__init__()
        self._client = client
        self._store = store
        self._orchestrator = orchestrator

    def _get_orchestrator(self) -> MultiSellerOrchestrator:
        """Return the booking orchestrator, building the default lazily."""
        if self._orchestrator is None:
            self._orchestrator = _build_default_orchestrator()
        return self._orchestrator

    # ------------------------------------------------------------------
    # Persistence helpers (best-effort dual-write)
    # ------------------------------------------------------------------

    def _persist_booking(self, deal_id: str, booked_line: Any) -> None:
        """Best-effort persist a booking record to the store.

        The record is keyed by the SELLER-issued deal id, the quote id it
        was booked from, and the confirmed terms (carried in metadata; the
        booking_records schema is unchanged).

        Never raises -- logs and continues on failure so the flow is
        unaffected by persistence errors.

        Args:
            deal_id: The store deal row this booking belongs to.
            booked_line: A BookedLine instance from flow state.
        """
        if self._store is None:
            return
        try:
            metadata = json.dumps(
                {
                    "seller_deal_id": getattr(booked_line, "deal_id", None),
                    "quote_id": getattr(booked_line, "quote_id", None),
                    "seller_id": getattr(booked_line, "seller_id", None),
                    "final_cpm": getattr(booked_line, "cpm", None),
                }
            )
            self._store.save_booking_record(
                deal_id=deal_id,
                order_id=getattr(booked_line, "order_id", None),
                line_id=getattr(booked_line, "line_id", None),
                channel=getattr(booked_line, "channel", ""),
                impressions=getattr(booked_line, "impressions", 0),
                cost=getattr(booked_line, "cost", 0.0),
                booking_status=getattr(booked_line, "booking_status", "pending"),
                metadata=metadata,
            )
        except (sqlite3.Error, OSError, ValueError, AttributeError):
            logger.exception("Failed to persist booking for deal %s", deal_id)

    def _persist_deal_status(self, deal_id: str, new_status: str) -> None:
        """Best-effort update deal status in the store.

        Uses DealStore.update_deal_status() which enforces state machine
        transitions when both statuses are valid BuyerDealStatus values.

        Args:
            deal_id: The deal to update.
            new_status: New status value.
        """
        if self._store is None:
            return
        try:
            ok = self._store.update_deal_status(deal_id, new_status, triggered_by="system")
            if not ok:
                logger.warning(
                    "State machine rejected transition to %s for deal %s",
                    new_status,
                    deal_id,
                )
        except (sqlite3.Error, OSError, ValueError, AttributeError):
            logger.exception(
                "Failed to persist status change to %s for deal %s",
                new_status,
                deal_id,
            )

    @start()
    def receive_campaign_brief(self) -> dict[str, Any]:
        """Entry point: validate and store campaign brief."""
        brief = self.state.campaign_brief

        # Validate required fields
        required = ["objectives", "budget", "start_date", "end_date", "target_audience"]
        missing = [f for f in required if f not in brief]

        if missing:
            self.state.errors.append(f"Missing required fields: {missing}")
            self.state.execution_status = ExecutionStatus.VALIDATION_FAILED
            return {"status": "failed", "errors": self.state.errors}

        # Validate budget
        if brief.get("budget", 0) <= 0:
            self.state.errors.append("Budget must be greater than 0")
            self.state.execution_status = ExecutionStatus.VALIDATION_FAILED
            return {"status": "failed", "errors": self.state.errors}

        self.state.execution_status = ExecutionStatus.BRIEF_RECEIVED
        self.state.updated_at = datetime.now(UTC)

        # Emit campaign.created event
        emit_event_sync(
            EventType.CAMPAIGN_CREATED,
            flow_type="deal_booking",
            payload={"name": brief.get("name", ""), "budget": brief.get("budget", 0)},
        )

        return {"status": "success", "brief": brief}

    @listen(receive_campaign_brief)
    def plan_audience(self, brief_result: dict[str, Any]) -> dict[str, Any]:
        """Plan audience targeting strategy using UCP.

        This step analyzes the target_audience from the campaign brief and:
        1. Discovers available signals from sellers via UCP
        2. Matches requirements to inventory capabilities
        3. Estimates coverage per channel
        4. Identifies any audience gaps

        The audience plan is used to inform budget allocation.
        """
        if brief_result.get("status") != "success":
            return brief_result

        target_audience = self.state.campaign_brief.get("target_audience", {})

        if not target_audience:
            # No audience targeting specified - skip planning
            return {
                "status": "success",
                "audience_plan": None,
                "message": "No audience targeting specified",
            }

        try:
            # Create audience plan from campaign requirements
            audience_plan = self._create_audience_plan(target_audience)
            self.state.audience_plan = audience_plan

            # Estimate coverage per channel using UCP
            coverage_estimates = self._estimate_channel_coverage(target_audience)
            self.state.audience_coverage_estimates = coverage_estimates

            # Identify gaps
            gaps = self._identify_audience_gaps(target_audience, coverage_estimates)
            self.state.audience_gaps = gaps

            self.state.updated_at = datetime.now(UTC)

            return {
                "status": "success",
                "audience_plan": audience_plan,
                "coverage_estimates": coverage_estimates,
                "gaps": gaps,
            }

        except Exception as e:  # noqa: BLE001 - audience planning is optional; must not fail the flow
            # Don't fail the flow - audience planning is optional
            self.state.errors.append(f"Audience planning warning: {e}")
            return {"status": "success", "audience_plan": None, "error": str(e)}

    def _create_audience_plan(self, target_audience: dict[str, Any]) -> dict[str, Any]:
        """Create an audience plan from target_audience specification."""
        plan_id = f"plan_{uuid.uuid4().hex[:8]}"

        # Extract targeting components
        demographics = target_audience.get("demographics", {})
        interests = target_audience.get("interests", [])
        behaviors = target_audience.get("behaviors", [])
        exclusions = target_audience.get("exclusions", [])

        # Determine required signal types
        signal_types = []
        if demographics:
            signal_types.append(SignalType.IDENTITY.value)
        if interests or target_audience.get("content_categories"):
            signal_types.append(SignalType.CONTEXTUAL.value)
        if behaviors or target_audience.get("intent"):
            signal_types.append(SignalType.REINFORCEMENT.value)

        return {
            "plan_id": plan_id,
            "target_demographics": demographics,
            "target_interests": interests if isinstance(interests, list) else [],
            "target_behaviors": behaviors if isinstance(behaviors, list) else [],
            "exclusions": exclusions if isinstance(exclusions, list) else [],
            "requested_signal_types": signal_types,
            "audience_expansion_enabled": target_audience.get("expand_audience", True),
            "expansion_factor": target_audience.get("expansion_factor", 1.0),
        }

    def _estimate_channel_coverage(self, target_audience: dict[str, Any]) -> dict[str, float]:
        """Estimate audience coverage per channel."""
        # Base coverage factors
        base_factors = {
            "branding": 0.85,  # Display/video has broad reach
            "ctv": 0.65,  # CTV is more limited
            "mobile_app": 0.70,  # App inventory varies
            "performance": 0.80,  # Remarketing depends on pools
        }

        # Adjust based on targeting complexity
        complexity_penalty = 0.0

        if target_audience.get("demographics"):
            complexity_penalty += 0.10

        if target_audience.get("behaviors"):
            complexity_penalty += 0.20  # Behavioral has lower coverage

        if target_audience.get("interests"):
            complexity_penalty += 0.05  # Contextual is widely available

        # Calculate coverage per channel
        coverage = {}
        for channel, base in base_factors.items():
            adjusted = max(0.1, base - complexity_penalty)
            coverage[channel] = round(adjusted * 100, 1)

        return coverage

    def _identify_audience_gaps(
        self,
        target_audience: dict[str, Any],
        coverage_estimates: dict[str, float],
    ) -> list[str]:
        """Identify audience requirements that may have gaps."""
        gaps = []

        # Check for low-coverage targeting
        if target_audience.get("behaviors"):
            gaps.append("behavioral_targeting: coverage may be limited (35-45%)")

        if target_audience.get("demographics", {}).get("income"):
            gaps.append("income_targeting: coverage typically 50-60%")

        # Check for channels with very low coverage
        for channel, coverage in coverage_estimates.items():
            if coverage < 40:
                gaps.append(f"{channel}: low coverage ({coverage}%), consider broader targeting")

        return gaps

    @listen(plan_audience)
    def allocate_budget(self, audience_result: dict[str, Any]) -> dict[str, Any]:
        """Portfolio manager determines channel budget allocation."""
        if audience_result.get("status") != "success":
            return audience_result

        try:
            # Create and run portfolio crew
            portfolio_crew = create_portfolio_crew(
                client=self._client,
                campaign_brief=self.state.campaign_brief,
            )

            result = portfolio_crew.kickoff()

            # The portfolio crew has two tasks; only the first
            # (budget_allocation_task) carries the allocation output. The crew's
            # top-level ``raw`` reflects the LAST task (channel coordination)
            # which has a different schema and would silently produce zero
            # allocations if used directly.
            allocations = self._extract_allocations(result)

            # Store allocations
            for channel, alloc_data in allocations.items():
                if alloc_data.get("budget", 0) > 0:
                    self.state.budget_allocations[channel] = ChannelAllocation(
                        channel=channel,
                        budget=alloc_data["budget"],
                        percentage=alloc_data.get("percentage", 0),
                        rationale=alloc_data.get("rationale", ""),
                    )

            self.state.execution_status = ExecutionStatus.BUDGET_ALLOCATED
            self.state.updated_at = datetime.now(UTC)

            # Emit budget.allocated event
            emit_event_sync(
                EventType.BUDGET_ALLOCATED,
                flow_type="deal_booking",
                payload={
                    "channels": list(self.state.budget_allocations.keys()),
                    "total_budget": self.state.campaign_brief.get("budget", 0),
                },
            )

            return {
                "status": "success",
                "allocations": {
                    k: v.model_dump() for k, v in self.state.budget_allocations.items()
                },
            }

        except Exception as e:  # noqa: BLE001 - flow step must capture any failure from CrewAI
            self.state.errors.append(f"Budget allocation failed: {e}")
            self.state.execution_status = ExecutionStatus.FAILED
            return {"status": "failed", "error": str(e)}

    def _extract_allocations(self, result: Any) -> dict[str, Any]:
        """Pull the budget allocation from a portfolio_crew kickoff result.

        Prefers the typed ``output_pydantic`` on the first task. Falls back
        to ``json_dict`` on the first task. Final fallback is a default split.

        Note: the crew has two tasks (budget allocation, channel coordination).
        The crew's top-level ``raw``/``str()`` carries the LAST task's output,
        which has the wrong schema. We must read ``tasks_output[0]`` directly.
        """
        first_task = None
        if getattr(result, "tasks_output", None):
            first_task = result.tasks_output[0]

        if first_task is not None:
            # Typed pydantic output is the authoritative source.
            pyd = getattr(first_task, "pydantic", None)
            if pyd is not None and hasattr(pyd, "model_dump"):
                dumped = pyd.model_dump()
                if any(c.get("budget", 0) > 0 for c in dumped.values()):
                    return dumped

            # If output_pydantic didn't capture for some reason, try json_dict.
            json_dict = getattr(first_task, "json_dict", None)
            if isinstance(json_dict, dict) and any(
                c.get("budget", 0) > 0 for c in json_dict.values() if isinstance(c, dict)
            ):
                return json_dict

            # Last resort: extract JSON block from the first task's raw text.
            raw = getattr(first_task, "raw", "") or ""
            parsed = self._extract_json_block(str(raw))
            if parsed is not None:
                return parsed

        self.state.errors.append(
            "Budget allocation: could not extract typed output from portfolio crew; "
            "falling back to default split."
        )
        return self._default_allocations()

    @staticmethod
    def _extract_json_block(text: str) -> dict[str, Any] | None:
        """Find and parse the first ``{...}`` block in text. Returns None on failure."""
        start_idx = text.find("{")
        end_idx = text.rfind("}") + 1
        if start_idx < 0 or end_idx <= start_idx:
            return None
        try:
            parsed = json.loads(text[start_idx:end_idx])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None

    def _default_allocations(self) -> dict[str, Any]:
        """Default channel split used when the portfolio crew output is unusable."""
        total_budget = self.state.campaign_brief.get("budget", 0)
        return {
            "branding": {
                "budget": total_budget * 0.4,
                "percentage": 40,
                "rationale": "Default allocation (portfolio crew output unparseable)",
            },
            "performance": {
                "budget": total_budget * 0.4,
                "percentage": 40,
                "rationale": "Default allocation (portfolio crew output unparseable)",
            },
            "ctv": {
                "budget": total_budget * 0.2,
                "percentage": 20,
                "rationale": "Default allocation (portfolio crew output unparseable)",
            },
            "mobile_app": {"budget": 0, "percentage": 0, "rationale": "Not allocated"},
        }

    @listen(allocate_budget)
    def research_branding(self, allocation_result: dict[str, Any]) -> dict[str, Any]:
        """Branding specialist researches display/video inventory."""
        if allocation_result.get("status") != "success":
            return {"channel": "branding", "status": "skipped"}

        branding_alloc = self.state.budget_allocations.get("branding")
        if not branding_alloc or branding_alloc.budget <= 0:
            return {"channel": "branding", "status": "no_budget"}

        try:
            self.state.execution_status = ExecutionStatus.RESEARCHING

            channel_brief = self._create_channel_brief("branding", branding_alloc)
            crew = create_branding_crew(
                self._client,
                channel_brief,
                audience_plan=self.state.audience_plan,
            )
            result = crew.kickoff()

            recommendations = self._parse_recommendations(str(result), "branding")
            self.state.channel_recommendations["branding"] = recommendations
            self.state.updated_at = datetime.now(UTC)

            return {"channel": "branding", "status": "success", "count": len(recommendations)}

        except Exception as e:  # noqa: BLE001 - flow step must capture any failure from CrewAI
            self.state.errors.append(f"Branding research failed: {e}")
            return {"channel": "branding", "status": "failed", "error": str(e)}

    @listen(allocate_budget)
    def research_ctv(self, allocation_result: dict[str, Any]) -> dict[str, Any]:
        """CTV specialist researches streaming inventory."""
        if allocation_result.get("status") != "success":
            return {"channel": "ctv", "status": "skipped"}

        ctv_alloc = self.state.budget_allocations.get("ctv")
        if not ctv_alloc or ctv_alloc.budget <= 0:
            return {"channel": "ctv", "status": "no_budget"}

        try:
            channel_brief = self._create_channel_brief("ctv", ctv_alloc)
            crew = create_ctv_crew(
                self._client,
                channel_brief,
                audience_plan=self.state.audience_plan,
            )
            result = crew.kickoff()

            recommendations = self._parse_recommendations(str(result), "ctv")
            self.state.channel_recommendations["ctv"] = recommendations
            self.state.updated_at = datetime.now(UTC)

            return {"channel": "ctv", "status": "success", "count": len(recommendations)}

        except Exception as e:  # noqa: BLE001 - flow step must capture any failure from CrewAI
            self.state.errors.append(f"CTV research failed: {e}")
            return {"channel": "ctv", "status": "failed", "error": str(e)}

    @listen(allocate_budget)
    def research_mobile(self, allocation_result: dict[str, Any]) -> dict[str, Any]:
        """Mobile specialist researches app install inventory."""
        if allocation_result.get("status") != "success":
            return {"channel": "mobile_app", "status": "skipped"}

        mobile_alloc = self.state.budget_allocations.get("mobile_app")
        if not mobile_alloc or mobile_alloc.budget <= 0:
            return {"channel": "mobile_app", "status": "no_budget"}

        try:
            channel_brief = self._create_channel_brief("mobile_app", mobile_alloc)
            crew = create_mobile_crew(
                self._client,
                channel_brief,
                audience_plan=self.state.audience_plan,
            )
            result = crew.kickoff()

            recommendations = self._parse_recommendations(str(result), "mobile_app")
            self.state.channel_recommendations["mobile_app"] = recommendations
            self.state.updated_at = datetime.now(UTC)

            return {"channel": "mobile_app", "status": "success", "count": len(recommendations)}

        except Exception as e:  # noqa: BLE001 - flow step must capture any failure from CrewAI
            self.state.errors.append(f"Mobile research failed: {e}")
            return {"channel": "mobile_app", "status": "failed", "error": str(e)}

    @listen(allocate_budget)
    def research_performance(self, allocation_result: dict[str, Any]) -> dict[str, Any]:
        """Performance specialist researches remarketing inventory."""
        if allocation_result.get("status") != "success":
            return {"channel": "performance", "status": "skipped"}

        perf_alloc = self.state.budget_allocations.get("performance")
        if not perf_alloc or perf_alloc.budget <= 0:
            return {"channel": "performance", "status": "no_budget"}

        try:
            channel_brief = self._create_channel_brief("performance", perf_alloc)
            crew = create_performance_crew(
                self._client,
                channel_brief,
                audience_plan=self.state.audience_plan,
            )
            result = crew.kickoff()

            recommendations = self._parse_recommendations(str(result), "performance")
            self.state.channel_recommendations["performance"] = recommendations
            self.state.updated_at = datetime.now(UTC)

            return {"channel": "performance", "status": "success", "count": len(recommendations)}

        except Exception as e:  # noqa: BLE001 - flow step must capture any failure from CrewAI
            self.state.errors.append(f"Performance research failed: {e}")
            return {"channel": "performance", "status": "failed", "error": str(e)}

    def _create_channel_brief(self, channel: str, allocation: ChannelAllocation) -> dict[str, Any]:
        """Create a channel-specific brief from campaign brief and allocation."""
        return ChannelBrief(
            channel=channel,
            budget=allocation.budget,
            start_date=self.state.campaign_brief.get("start_date", ""),
            end_date=self.state.campaign_brief.get("end_date", ""),
            target_audience=self.state.campaign_brief.get("target_audience", {}),
            objectives=self.state.campaign_brief.get("objectives", []),
            kpis=self.state.campaign_brief.get("kpis", {}),
        ).model_dump(by_alias=True)

    def _parse_recommendations(self, result_str: str, channel: str) -> list[ProductRecommendation]:
        """Parse recommendations from crew result."""
        recommendations = []

        try:
            # Try to find JSON array in result
            start_idx = result_str.find("[")
            end_idx = result_str.rfind("]") + 1
            if start_idx >= 0 and end_idx > start_idx:
                json_str = result_str[start_idx:end_idx]
                items = json.loads(json_str)

                for item in items:
                    rec = ProductRecommendation(
                        product_id=item.get("product_id", "unknown"),
                        product_name=item.get("product_name", "Unknown Product"),
                        publisher=item.get("publisher", "Unknown"),
                        channel=channel,
                        format=item.get("format"),
                        impressions=item.get("impressions", 0),
                        cpm=item.get("cpm", 0),
                        cost=item.get("cost", 0),
                        rationale=item.get("rationale"),
                    )
                    recommendations.append(rec)
        except (json.JSONDecodeError, KeyError):
            # If parsing fails, create a placeholder recommendation
            pass

        return recommendations

    @listen(or_(research_branding, research_ctv, research_mobile, research_performance))
    def consolidate_recommendations(self, channel_result: dict[str, Any]) -> dict[str, Any]:
        """Consolidate all channel recommendations for approval."""
        # Check if all active channels have reported
        active_channels = [
            ch for ch, alloc in self.state.budget_allocations.items() if alloc.budget > 0
        ]
        completed_channels = list(self.state.channel_recommendations.keys())

        # Check if we're still waiting for channels
        pending = set(active_channels) - set(completed_channels)
        if pending:
            return {"status": "waiting", "pending": list(pending)}

        # All channels complete - consolidate
        self.state.pending_approvals = []

        for channel, recs in self.state.channel_recommendations.items():
            for rec in recs:
                rec.status = "pending_approval"
                self.state.pending_approvals.append(rec)

        self.state.execution_status = ExecutionStatus.AWAITING_APPROVAL
        self.state.updated_at = datetime.now(UTC)

        # Persist awaiting-approval status for each recommendation's deal
        if self._store is not None:
            for rec in self.state.pending_approvals:
                try:
                    deal_id = self._store.save_deal(
                        seller_url=getattr(rec, "publisher", ""),
                        product_id=rec.product_id,
                        product_name=rec.product_name,
                        deal_type="PD",
                        status="awaiting_approval",
                    )
                    # Stash the store deal_id on the recommendation for
                    # later use when booking
                    rec._store_deal_id = deal_id  # type: ignore[attr-defined]
                except (sqlite3.Error, OSError, ValueError, AttributeError):
                    logger.exception(
                        "Failed to persist deal for recommendation %s",
                        rec.product_id,
                    )

        return {
            "status": "ready_for_approval",
            "total_recommendations": len(self.state.pending_approvals),
            "by_channel": {
                ch: len(recs) for ch, recs in self.state.channel_recommendations.items()
            },
        }

    def approve_recommendations(self, approved_ids: list[str]) -> dict[str, Any]:
        """Approve specific recommendations for booking.

        This method is called externally (from CLI/API) after human review.

        Args:
            approved_ids: List of product IDs to approve for booking

        Returns:
            Status of the approval and next steps
        """
        approved_set = set(approved_ids)

        for rec in self.state.pending_approvals:
            if rec.product_id in approved_set:
                rec.status = "approved"
            else:
                rec.status = "rejected"

        self.state.execution_status = ExecutionStatus.EXECUTING_BOOKINGS
        self.state.updated_at = datetime.now(UTC)

        return self._execute_bookings()

    def approve_all(self) -> dict[str, Any]:
        """Approve all pending recommendations.

        Returns:
            Status of the approval and booking execution
        """
        all_ids = [rec.product_id for rec in self.state.pending_approvals]
        return self.approve_recommendations(all_ids)

    def _typed_audience_plan(self) -> AudiencePlan | None:
        """Return the state's audience plan as a typed AudiencePlan, if valid.

        A parent pipeline may pre-seed ``state.audience_plan`` with a typed
        plan (as a dict). When it validates, the plan is threaded onto
        DealParams / InventoryRequirements so it survives the buyer ->
        seller boundary. The flow's own UCP coverage-estimation dict does
        not follow the AudiencePlan schema and coerces to None.
        """
        raw = self.state.audience_plan
        if not raw:
            return None
        if isinstance(raw, AudiencePlan):
            return raw
        try:
            return AudiencePlan.model_validate(raw)
        except ValidationError:
            return None

    def _execute_bookings(self) -> dict[str, Any]:
        """Execute bookings for approved recommendations via the orchestrator.

        Canonical handoff (bead ar-j2nw): each approved recommendation is
        translated into DealParams / InventoryRequirements and executed by
        MultiSellerOrchestrator (discover -> quote -> rank ->
        select_and_book). Booking records key on the SELLER-issued deal_id
        + quote_id + confirmed terms; the buyer never mints deal ids or
        placeholder order ids on this path.
        """
        approved = [rec for rec in self.state.pending_approvals if rec.status == "approved"]

        if not approved:
            self.state.execution_status = ExecutionStatus.COMPLETED
            return {"status": "success", "booked": 0, "message": "No recommendations approved"}

        # Deterministic spend-ceiling guard (bead ar-70eh / EP-0.1): the
        # approved recommendations come from LLM-parsed crew output, so
        # their total cost must be checked against the campaign budget
        # BEFORE any money is committed to a seller. A missing budget fails
        # open (allow + warning log) — an explicit choice to preserve demo
        # behavior for briefs without a budget; a supplied budget is always
        # enforced.
        budget = self.state.campaign_brief.get("budget")
        total_cost = sum(rec.cost for rec in approved)
        try:
            enforce_spend_ceiling(total_cost=total_cost, budget=budget)
        except SpendCeilingExceeded as e:
            logger.warning("Booking rejected by spend ceiling: %s", e)
            self.state.errors.append(f"Booking rejected: {e}")
            self.state.execution_status = ExecutionStatus.FAILED
            self.state.updated_at = datetime.now(UTC)
            return {
                "status": "rejected",
                "booked": 0,
                "error": str(e),
                "total_cost": total_cost,
                "budget": budget,
            }

        # Real handoff to the multi-seller execution engine. `run_async`
        # bridges this synchronous approval entry point (CLI/API/chat) to
        # the async orchestrator.
        failed_bookings: list[dict[str, Any]] = []
        booking_results = run_async(self._book_approved(approved))

        for rec, result, error in booking_results:
            store_deal_id = getattr(rec, "_store_deal_id", None)

            if error is not None or result is None:
                msg = f"Booking failed for {rec.product_id}: {error}"
                logger.warning(msg)
                self.state.errors.append(msg)
                failed_bookings.append({"product_id": rec.product_id, "error": str(error)})
                if store_deal_id:
                    self._persist_deal_status(store_deal_id, "failed")
                continue

            booked_deals = result.selection.booked_deals
            if not booked_deals:
                # Orchestrator ran but no seller issued a deal (no sellers,
                # no viable quotes, or every booking attempt failed).
                details = result.selection.failed_bookings or [
                    {"error": "no viable quotes from any seller"}
                ]
                msg = f"No deal booked for {rec.product_id}: {details}"
                logger.warning(msg)
                self.state.errors.append(msg)
                failed_bookings.append({"product_id": rec.product_id, "details": details})
                if store_deal_id:
                    self._persist_deal_status(store_deal_id, "failed")
                continue

            quote_seller_ids = {
                qr.quote.quote_id: qr.seller_id
                for qr in result.quote_results
                if qr.quote is not None
            }
            for deal in booked_deals:
                # Confirmed terms from the seller's 201 DealResponse take
                # precedence over the researched estimates.
                impressions = deal.terms.impressions or rec.impressions
                final_cpm = deal.pricing.final_cpm
                cost = (
                    round(impressions * final_cpm / 1000.0, 2)
                    if final_cpm is not None and impressions
                    else rec.cost
                )
                booked = BookedLine(
                    deal_id=deal.deal_id,
                    quote_id=deal.quote_id,
                    product_id=rec.product_id,
                    product_name=rec.product_name,
                    channel=rec.channel,
                    impressions=impressions,
                    cpm=final_cpm,
                    cost=cost,
                    booking_status=deal.status or "booked",
                    booked_at=datetime.now(UTC),
                    seller_id=quote_seller_ids.get(deal.quote_id or ""),
                )
                self.state.booked_lines.append(booked)

                # Persist booking record and update deal status
                if store_deal_id:
                    self._persist_booking(store_deal_id, booked)
                    self._persist_deal_status(store_deal_id, "booked")

                # Emit deal.booked event keyed by the seller-issued deal id
                emit_event_sync(
                    EventType.DEAL_BOOKED,
                    flow_type="deal_booking",
                    deal_id=deal.deal_id,
                    payload={
                        "deal_id": deal.deal_id,
                        "quote_id": deal.quote_id,
                        "product_id": rec.product_id,
                        "channel": rec.channel,
                        "impressions": impressions,
                        "cost": cost,
                        "final_cpm": final_cpm,
                    },
                )

        if self.state.booked_lines:
            self.state.execution_status = ExecutionStatus.COMPLETED
            status = "success"
        else:
            # Every approved recommendation failed to book.
            self.state.execution_status = ExecutionStatus.FAILED
            status = "failed"
        self.state.updated_at = datetime.now(UTC)

        return {
            "status": status,
            "booked": len(self.state.booked_lines),
            "failed": failed_bookings,
            "total_impressions": sum(b.impressions for b in self.state.booked_lines),
            "total_cost": sum(b.cost for b in self.state.booked_lines),
        }

    async def _book_approved(
        self,
        approved: list[ProductRecommendation],
    ) -> list[tuple[ProductRecommendation, OrchestrationResult | None, str | None]]:
        """Book each approved recommendation through the orchestrator.

        The approved terms are binding on execution: the recommendation's
        cost is the budget ceiling and its CPM the max acceptable CPM for
        that line, so the orchestrator cannot commit money beyond what the
        human approved. Per-recommendation isolation: one failure records
        an error tuple and the rest continue.

        Returns:
            List of (recommendation, orchestration_result, error) tuples.
            Exactly one of result/error is non-None per entry.
        """
        orchestrator = self._get_orchestrator()
        audience_plan = self._typed_audience_plan()
        brief = self.state.campaign_brief

        results: list[tuple[ProductRecommendation, OrchestrationResult | None, str | None]] = []
        for rec in approved:
            media_type = _CHANNEL_MEDIA_TYPE_MAP.get(rec.channel, rec.channel)
            deal_types = _CHANNEL_DEAL_TYPES.get(rec.channel, ["PD"])

            inventory_requirements = InventoryRequirements(
                media_type=media_type,
                deal_types=deal_types,
                max_cpm=rec.cpm if rec.cpm > 0 else None,
                audience_plan=audience_plan,
            )
            deal_params = DealParams(
                product_id=rec.product_id,
                deal_type=deal_types[0],
                impressions=rec.impressions,
                flight_start=brief.get("start_date", ""),
                flight_end=brief.get("end_date", ""),
                target_cpm=rec.cpm if rec.cpm > 0 else None,
                media_type=media_type,
                audience_plan=audience_plan,
            )

            try:
                result = await orchestrator.orchestrate(
                    inventory_requirements=inventory_requirements,
                    deal_params=deal_params,
                    budget=rec.cost,
                    max_deals=1,
                )
                results.append((rec, result, None))
            except Exception as exc:  # noqa: BLE001 - per-recommendation isolation
                results.append((rec, None, str(exc)))
        return results

    def get_status(self) -> dict[str, Any]:
        """Get current flow status.

        Returns:
            Current state summary
        """
        return {
            "execution_status": self.state.execution_status.value,
            "budget_allocations": {
                k: v.model_dump() for k, v in self.state.budget_allocations.items()
            },
            "recommendations_by_channel": {
                ch: len(recs) for ch, recs in self.state.channel_recommendations.items()
            },
            "pending_approvals": len(self.state.pending_approvals),
            "booked_lines": len(self.state.booked_lines),
            "errors": self.state.errors,
            "updated_at": self.state.updated_at.isoformat(),
        }
