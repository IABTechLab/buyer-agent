# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""End-to-end campaign pipeline: brief in -> plan -> book -> ready.

Orchestrates the full campaign lifecycle from a JSON brief to booked
deals with a READY campaign.  Integrates:

  - CampaignBrief (buyer-80k): brief parsing and validation
  - CampaignStore (buyer-0u9): campaign state persistence
  - MultiSellerOrchestrator (buyer-8ih): multi-seller deal booking
  - EventBus (buyer-ppi): lifecycle event emission

Pipeline stages:
  1. ingest_brief   -- Parse brief, create campaign in DRAFT
  2. plan_campaign  -- Transition to PLANNING, produce channel plans
  3. execute_booking -- Transition to BOOKING, orchestrate deals per channel
  4. finalize       -- Transition to READY
  5. run            -- End-to-end convenience method

Reference: Campaign Automation Strategic Plan, Section 7.1
Bead: buyer-u8l (2B: Campaign Brief to Deal Pipeline)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from ..events.bus import EventBus
from ..events.models import Event, EventType
from ..models.audience_plan import AudiencePlan, coerce_audience_field
from ..models.campaign_brief import (
    CampaignBrief,
    ChannelAllocation,
    ChannelType,
    parse_campaign_brief,
)
from ..models.state_machine import CampaignStatus
from ..orchestration.multi_seller import (
    DealParams,
    DealSelection,
    InventoryRequirements,
    MultiSellerOrchestrator,
    OrchestrationResult,
)
from .audience_planner_step import (
    AudiencePlannerResult,
    run_audience_planner_step,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Channel -> media_type mapping
# ---------------------------------------------------------------------------

# Maps ChannelType to the media_type string used by the orchestrator's
# InventoryRequirements for seller discovery.
_CHANNEL_MEDIA_TYPE_MAP: dict[ChannelType, str] = {
    ChannelType.CTV: "ctv",
    ChannelType.DISPLAY: "display",
    ChannelType.AUDIO: "audio",
    ChannelType.NATIVE: "native",
    ChannelType.DOOH: "dooh",
    ChannelType.LINEAR_TV: "linear_tv",
}

# Default deal types to request per channel
_CHANNEL_DEAL_TYPES: dict[ChannelType, list[str]] = {
    ChannelType.CTV: ["PG", "PD"],
    ChannelType.DISPLAY: ["PD", "PA"],
    ChannelType.AUDIO: ["PD", "PA"],
    ChannelType.NATIVE: ["PD", "PA"],
    ChannelType.DOOH: ["PG", "PD"],
    ChannelType.LINEAR_TV: ["PG"],
}


# ---------------------------------------------------------------------------
# Data models for pipeline results
# ---------------------------------------------------------------------------


@dataclass
class ChannelPlan:
    """Plan for a single channel within a campaign.

    Attributes:
        channel: The advertising channel (CTV, DISPLAY, etc.).
        budget: Budget allocated to this channel in currency units.
        budget_pct: Percentage of total campaign budget.
        media_type: Media type string for seller discovery.
        deal_types: Deal types to request from sellers.
        format_prefs: Preferred ad formats for this channel.
    """

    channel: ChannelType
    budget: float
    budget_pct: float
    media_type: str
    deal_types: list[str] = field(default_factory=list)
    format_prefs: list[str] = field(default_factory=list)


@dataclass
class CampaignPlan:
    """Complete campaign plan produced by plan_campaign.

    Attributes:
        campaign_id: The campaign UUID.
        channel_plans: Per-channel planning data.
        total_budget: Total campaign budget.
        flight_start: Campaign start date (ISO string).
        flight_end: Campaign end date (ISO string).
        target_audience: Typed AudiencePlan from the brief (may be None
            when the brief omitted audience targeting; the Audience
            Planner agent fills it in downstream per proposal §5.3).
    """

    campaign_id: str
    channel_plans: list[ChannelPlan]
    total_budget: float
    flight_start: str
    flight_end: str
    target_audience: AudiencePlan | None = None


# ---------------------------------------------------------------------------
# CampaignPipeline
# ---------------------------------------------------------------------------


class CampaignPipeline:
    """End-to-end campaign pipeline: brief -> plan -> book -> ready.

    Coordinates CampaignBrief parsing, CampaignStore state management,
    MultiSellerOrchestrator deal booking, and EventBus event emission
    to take a campaign from a JSON brief to READY status with booked deals.

    Args:
        store: CampaignStore instance for state persistence.
        orchestrator: MultiSellerOrchestrator for deal booking.
        event_bus: Optional EventBus for lifecycle events.  When None,
            events are silently skipped.
    """

    def __init__(
        self,
        store: Any,  # CampaignStore or compatible fake
        orchestrator: MultiSellerOrchestrator,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self._store = store
        self._orchestrator = orchestrator
        self._event_bus = event_bus

        # Internal state: tracks the parsed brief per campaign for use
        # across pipeline stages within a single run.
        self._briefs: dict[str, CampaignBrief] = {}
        self._plans: dict[str, CampaignPlan] = {}
        self._booking_results: dict[str, dict[str, OrchestrationResult]] = {}

        # Audience Planner outputs per campaign. Populated by
        # `plan_campaign` and exposed via `get_audience_planner_result`
        # for tests / observability. Bead ar-fgyq §6 wiring.
        self._audience_planners: dict[str, AudiencePlannerResult] = {}

    # ------------------------------------------------------------------
    # Event helpers
    # ------------------------------------------------------------------

    async def _emit(
        self,
        event_type: EventType,
        campaign_id: str = "",
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        """Emit an event to the event bus. Fail-open."""
        if self._event_bus is None:
            return
        try:
            event = Event(
                event_type=event_type,
                campaign_id=campaign_id,
                payload=payload or {},
            )
            await self._event_bus.publish(event)
        except Exception as exc:  # noqa: BLE001 - event emission is fail-open by design
            logger.warning(
                "Failed to emit event %s: %s", event_type, exc
            )

    # ------------------------------------------------------------------
    # Stage 1: Ingest brief
    # ------------------------------------------------------------------

    async def ingest_brief(
        self, brief_input: Union[str, dict[str, Any]]
    ) -> str:
        """Parse and validate a campaign brief, create campaign in DRAFT.

        Args:
            brief_input: Campaign brief as a JSON string or dict.

        Returns:
            The new campaign_id.

        Raises:
            ValueError: If the brief JSON is invalid.
            pydantic.ValidationError: If the brief fails schema validation.
        """
        # Parse and validate the brief
        brief = parse_campaign_brief(brief_input)
        logger.info(
            "Brief validated: campaign=%s, budget=%.2f, channels=%d",
            brief.campaign_name,
            brief.total_budget,
            len(brief.channels),
        )

        # Build the dict for CampaignStore.create_campaign
        # Serialize complex fields to JSON strings for SQLite storage.
        # `target_audience` is now a typed AudiencePlan (or None); we
        # persist it as a dict so future loads see the new shape and
        # legacy rows lazily migrate as briefs are touched.
        if brief.target_audience is None:
            target_audience_json = json.dumps(None)
        else:
            target_audience_json = json.dumps(
                brief.target_audience.model_dump(mode="json")
            )
        store_brief = {
            "advertiser_id": brief.advertiser_id,
            "campaign_name": brief.campaign_name,
            "total_budget": brief.total_budget,
            "currency": brief.currency,
            "flight_start": brief.flight_start.isoformat(),
            "flight_end": brief.flight_end.isoformat(),
            "channels": json.dumps(
                [ch.model_dump(mode="json") for ch in brief.channels]
            ),
            "target_audience": target_audience_json,
        }

        # Include optional fields if present
        if brief.target_geo:
            store_brief["target_geo"] = json.dumps(
                [g.model_dump(mode="json") for g in brief.target_geo]
            )
        if brief.kpis:
            store_brief["kpis"] = json.dumps(
                [k.model_dump(mode="json") for k in brief.kpis]
            )
        if brief.brand_safety:
            store_brief["brand_safety"] = json.dumps(
                brief.brand_safety.model_dump(mode="json")
            )
        if brief.approval_config:
            store_brief["approval_config"] = json.dumps(
                brief.approval_config.model_dump(mode="json")
            )

        campaign_id = self._store.create_campaign(store_brief)

        # Cache the parsed brief for later stages
        self._briefs[campaign_id] = brief

        # Emit campaign.created event
        await self._emit(
            EventType.CAMPAIGN_CREATED,
            campaign_id=campaign_id,
            payload={
                "campaign_name": brief.campaign_name,
                "advertiser_id": brief.advertiser_id,
                "total_budget": brief.total_budget,
                "channels": [ch.channel.value for ch in brief.channels],
            },
        )

        logger.info("Campaign created: %s (DRAFT)", campaign_id)
        return campaign_id

    # ------------------------------------------------------------------
    # Stage 2: Plan campaign
    # ------------------------------------------------------------------

    async def plan_campaign(self, campaign_id: str) -> CampaignPlan:
        """Transition to PLANNING and produce per-channel plans.

        For each channel in the brief, determines:
        - Budget allocation (from brief percentages)
        - Media type for seller discovery
        - Deal types to request
        - Format preferences

        Args:
            campaign_id: The campaign to plan.

        Returns:
            CampaignPlan with per-channel ChannelPlan entries.

        Raises:
            KeyError: If the campaign does not exist.
        """
        campaign = self._store.get_campaign(campaign_id)
        if campaign is None:
            raise KeyError(f"Campaign not found: {campaign_id}")

        # Transition to PLANNING
        self._store.start_planning(campaign_id)

        # Get the cached brief, or reconstruct from stored data
        brief = self._briefs.get(campaign_id)
        if brief is None:
            brief = self._reconstruct_brief(campaign)

        # Build per-channel plans
        channel_plans: list[ChannelPlan] = []
        for ch in brief.channels:
            media_type = _CHANNEL_MEDIA_TYPE_MAP.get(ch.channel, ch.channel.value.lower())
            deal_types = _CHANNEL_DEAL_TYPES.get(ch.channel, ["PD"])
            budget = round(brief.total_budget * ch.budget_pct / 100.0, 2)

            channel_plans.append(ChannelPlan(
                channel=ch.channel,
                budget=budget,
                budget_pct=ch.budget_pct,
                media_type=media_type,
                deal_types=deal_types,
                format_prefs=ch.format_prefs,
            ))

        # Run the Audience Planner step BEFORE building the CampaignPlan
        # so the resolved plan rides on `target_audience` from this point
        # forward (per proposal §5.3). This is the keystone bead ar-fgyq
        # wiring -- the planner is a stub passthrough today; bead §7
        # replaces the stub with the full reasoning loop.
        planner_result = run_audience_planner_step(brief)
        # Cache the agent for tests/introspection -- the agent's `tools`
        # attribute is the source of truth for the §6 tool-relocation
        # invariant (3 UCP tools + TaxonomyLookup + EmbeddingMint).
        self._audience_planners[campaign_id] = planner_result

        plan = CampaignPlan(
            campaign_id=campaign_id,
            channel_plans=channel_plans,
            total_budget=brief.total_budget,
            flight_start=brief.flight_start.isoformat(),
            flight_end=brief.flight_end.isoformat(),
            target_audience=planner_result.plan,
        )

        # Cache the plan for execute_booking
        self._plans[campaign_id] = plan

        # Emit plan generated event
        await self._emit(
            EventType.CAMPAIGN_PLAN_GENERATED,
            campaign_id=campaign_id,
            payload={
                "channels": [
                    {
                        "channel": cp.channel.value,
                        "budget": cp.budget,
                        "media_type": cp.media_type,
                        "deal_types": cp.deal_types,
                    }
                    for cp in channel_plans
                ],
                "total_budget": brief.total_budget,
            },
        )

        logger.info(
            "Campaign plan generated: %s (%d channels)",
            campaign_id,
            len(channel_plans),
        )
        return plan

    # ------------------------------------------------------------------
    # Stage 3: Execute booking
    # ------------------------------------------------------------------

    async def execute_booking(
        self, campaign_id: str
    ) -> dict[str, OrchestrationResult]:
        """Transition to BOOKING and orchestrate deals for each channel.

        For each channel in the plan, invokes MultiSellerOrchestrator
        to discover sellers, get quotes, rank, and book deals.

        Args:
            campaign_id: The campaign to book deals for.

        Returns:
            Dict mapping channel names to OrchestrationResult.

        Raises:
            KeyError: If the campaign does not exist.
        """
        campaign = self._store.get_campaign(campaign_id)
        if campaign is None:
            raise KeyError(f"Campaign not found: {campaign_id}")

        # Transition to BOOKING
        self._store.start_booking(campaign_id)

        # Emit booking started event
        await self._emit(
            EventType.CAMPAIGN_BOOKING_STARTED,
            campaign_id=campaign_id,
        )

        # Get the cached plan
        plan = self._plans.get(campaign_id)
        if plan is None:
            raise KeyError(
                f"No plan found for campaign {campaign_id}. "
                "Call plan_campaign() first."
            )

        # Get the brief for excluded_sellers and other params
        brief = self._briefs.get(campaign_id)
        excluded_sellers = brief.excluded_sellers if brief else []

        # Orchestrate deals per channel
        results: dict[str, OrchestrationResult] = {}

        for cp in plan.channel_plans:
            channel_key = cp.channel.value

            # Build inventory requirements for this channel.
            # `audience_plan` is forwarded from the planner step's output
            # (proposal §5.3 / bead ar-fgyq §6); §5 wired the
            # `audience_plan` field on InventoryRequirements / DealParams.
            inv_req = InventoryRequirements(
                media_type=cp.media_type,
                deal_types=cp.deal_types,
                excluded_sellers=excluded_sellers,
                max_cpm=(
                    brief.deal_preferences.max_cpm
                    if brief and brief.deal_preferences
                    else None
                ),
                audience_plan=plan.target_audience,
            )

            # Build deal params
            deal_params = DealParams(
                product_id=f"campaign-{campaign_id}-{channel_key.lower()}",
                deal_type=cp.deal_types[0] if cp.deal_types else "PD",
                impressions=self._estimate_impressions(cp.budget),
                flight_start=plan.flight_start,
                flight_end=plan.flight_end,
                media_type=cp.media_type,
                audience_plan=plan.target_audience,
            )

            try:
                result = await self._orchestrator.orchestrate(
                    inventory_requirements=inv_req,
                    deal_params=deal_params,
                    budget=cp.budget,
                    max_deals=3,
                )
                results[channel_key] = result

                logger.info(
                    "Channel %s: booked %d deals (spend: %.2f)",
                    channel_key,
                    len(result.selection.booked_deals),
                    result.selection.total_spend,
                )

            except Exception as exc:  # noqa: BLE001 - per-channel isolation; one failure must not abort pipeline
                logger.warning(
                    "Channel %s booking failed: %s", channel_key, exc
                )
                # Record empty result for failed channels rather than
                # aborting the entire pipeline
                results[channel_key] = OrchestrationResult(
                    discovered_sellers=[],
                    quote_results=[],
                    ranked_quotes=[],
                    selection=DealSelection(
                        booked_deals=[],
                        failed_bookings=[{"channel": channel_key, "error": str(exc)}],
                        total_spend=0.0,
                        remaining_budget=cp.budget,
                    ),
                )

        # Cache booking results
        self._booking_results[campaign_id] = results

        # Emit booking completed event
        total_deals = sum(
            len(r.selection.booked_deals) for r in results.values()
        )
        total_spend = sum(
            r.selection.total_spend for r in results.values()
        )

        await self._emit(
            EventType.CAMPAIGN_BOOKING_COMPLETED,
            campaign_id=campaign_id,
            payload={
                "channels_booked": len(results),
                "total_deals": total_deals,
                "total_spend": total_spend,
                "channel_summary": {
                    ch: {
                        "deals_booked": len(r.selection.booked_deals),
                        "spend": r.selection.total_spend,
                        "remaining_budget": r.selection.remaining_budget,
                    }
                    for ch, r in results.items()
                },
            },
        )

        logger.info(
            "Campaign booking complete: %s (%d deals across %d channels)",
            campaign_id,
            total_deals,
            len(results),
        )
        return results

    # ------------------------------------------------------------------
    # Stage 4: Finalize
    # ------------------------------------------------------------------

    async def finalize(self, campaign_id: str) -> None:
        """Transition campaign from BOOKING to READY.

        Called after all channels have been booked. Campaign awaits
        flight start date or manual activation.

        Args:
            campaign_id: The campaign to finalize.

        Raises:
            KeyError: If the campaign does not exist.
        """
        campaign = self._store.get_campaign(campaign_id)
        if campaign is None:
            raise KeyError(f"Campaign not found: {campaign_id}")

        # Transition to READY
        self._store.mark_ready(campaign_id)

        # Emit ready event
        await self._emit(
            EventType.CAMPAIGN_READY,
            campaign_id=campaign_id,
            payload={
                "campaign_id": campaign_id,
            },
        )

        logger.info("Campaign finalized: %s (READY)", campaign_id)

    # ------------------------------------------------------------------
    # End-to-end: run
    # ------------------------------------------------------------------

    async def run(
        self, brief_input: Union[str, dict[str, Any]]
    ) -> dict[str, Any]:
        """Run the complete pipeline: ingest -> plan -> book -> finalize.

        Args:
            brief_input: Campaign brief as JSON string or dict.

        Returns:
            Campaign summary dict with campaign_id, status, and
            per-channel booking results.

        Raises:
            ValueError: If the brief is invalid.
        """
        # Stage 1: Ingest
        campaign_id = await self.ingest_brief(brief_input)

        # Stage 2: Plan
        plan = await self.plan_campaign(campaign_id)

        # Stage 3: Book
        booking_results = await self.execute_booking(campaign_id)

        # Stage 4: Finalize
        await self.finalize(campaign_id)

        # Build summary
        channels_summary: dict[str, Any] = {}
        for ch_key, result in booking_results.items():
            channels_summary[ch_key] = {
                "deals_booked": len(result.selection.booked_deals),
                "deal_ids": [
                    d.deal_id for d in result.selection.booked_deals
                ],
                "total_spend": result.selection.total_spend,
                "remaining_budget": result.selection.remaining_budget,
                "sellers_discovered": len(result.discovered_sellers),
                "failed_bookings": len(result.selection.failed_bookings),
            }

        summary = {
            "campaign_id": campaign_id,
            "status": CampaignStatus.READY.value,
            "total_budget": plan.total_budget,
            "channels": channels_summary,
            "flight_start": plan.flight_start,
            "flight_end": plan.flight_end,
        }

        logger.info(
            "Pipeline complete: campaign %s is READY "
            "(%d channels, %d total deals)",
            campaign_id,
            len(channels_summary),
            sum(ch["deals_booked"] for ch in channels_summary.values()),
        )
        return summary

    # ------------------------------------------------------------------
    # Public accessors (Audience Planner introspection)
    # ------------------------------------------------------------------

    def get_audience_planner_result(
        self, campaign_id: str
    ) -> AudiencePlannerResult | None:
        """Return the Audience Planner output for `campaign_id`, if any.

        Populated by `plan_campaign`. Returns None when planning has not
        yet run for the campaign. Tests use this to introspect the
        agent's bound tools and the stub flag (bead ar-fgyq §6).
        """

        return self._audience_planners.get(campaign_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reconstruct_brief(self, campaign: dict[str, Any]) -> CampaignBrief:
        """Reconstruct a CampaignBrief from stored campaign data.

        Used when the brief was not cached (e.g., pipeline stages
        called independently across different instances). Applies the
        legacy-list compat shim on the way in so existing SQLite rows
        carrying `list[str]` audiences keep working without a migration.
        """
        channels_raw = campaign.get("channels")
        if isinstance(channels_raw, str):
            channels_raw = json.loads(channels_raw)

        audience_raw = campaign.get("target_audience")
        if isinstance(audience_raw, str):
            try:
                audience_raw = json.loads(audience_raw)
            except (json.JSONDecodeError, TypeError):
                audience_raw = None

        # Legacy rows store list[str]; new rows store an AudiencePlan
        # dict. The shim handles both via coerce_audience_field. Empty
        # legacy lists fall through to None so the brief schema treats
        # the campaign as audience-less rather than rejecting it on
        # reconstruction (different from the ingestion path, which
        # rejects a fresh empty list).
        if isinstance(audience_raw, list) and not audience_raw:
            audience_raw = None
        else:
            audience_raw = coerce_audience_field(
                audience_raw,
                source_context="campaign_pipeline._reconstruct_brief",
            )

        return parse_campaign_brief({
            "advertiser_id": campaign["advertiser_id"],
            "campaign_name": campaign["campaign_name"],
            "objective": "AWARENESS",  # default when not stored
            "total_budget": campaign["total_budget"],
            "currency": campaign.get("currency", "USD"),
            "flight_start": campaign["flight_start"],
            "flight_end": campaign["flight_end"],
            "channels": channels_raw or [],
            "target_audience": audience_raw,
        })

    @staticmethod
    def _estimate_impressions(budget: float, assumed_cpm: float = 15.0) -> int:
        """Estimate impression count from budget and assumed CPM.

        Args:
            budget: Channel budget in currency units.
            assumed_cpm: Assumed CPM for estimation (default $15).

        Returns:
            Estimated number of impressions.
        """
        if budget <= 0 or assumed_cpm <= 0:
            return 0
        # impressions = (budget / CPM) * 1000
        return int((budget / assumed_cpm) * 1000)
