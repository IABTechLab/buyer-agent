# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""End-to-end integration test for Path A (CampaignPipeline).

Bead ar-lk23 / proposal §6 row 16 -- the buyer-side end-to-end test for
the brief-driven CampaignPipeline path identified in proposal §5.3:

    Path A: CampaignPipeline.ingest_brief -> plan_campaign -> execute_booking

The seller side is **mocked**: a MultiSellerOrchestrator stand-in captures
the InventoryRequirements / DealParams that the pipeline forwards, so we
can assert the full typed AudiencePlan (Standard primary + Contextual
constraint + Agentic extension) survives every stage and arrives at the
seller-facing boundary intact.

Part 1 of 2 (this file): fixtures + happy-path scenario. Part 2 will add
the legacy migration, serialization round-trip, and capability
degradation scenarios that mirror the Path B test layout.

Reference:
  - AUDIENCE_PLANNER_3TYPE_EXTENSION_2026-04-25.md §5.1, §5.3, §6 row 16
  - tests/integration/test_path_b_audience_e2e.py (sister Path B tests)
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

# Stub the Anthropic key BEFORE any ad_buyer.crews / agents imports.
# CrewAI Agent factories instantiate an LLM eagerly in __init__; we never
# make a network call here. Mirrors the Path B + unit test pattern.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-path-a-e2e")

import pytest

from ad_buyer.events.bus import InMemoryEventBus
from ad_buyer.models.audience_plan import (
    AudiencePlan,
    AudienceRef,
    ComplianceContext,
)
from ad_buyer.models.campaign_brief import CampaignBrief, parse_campaign_brief
from ad_buyer.models.state_machine import CampaignStatus
from ad_buyer.orchestration.multi_seller import (
    DealSelection,
    MultiSellerOrchestrator,
    OrchestrationResult,
)
from ad_buyer.pipelines.campaign_pipeline import CampaignPipeline


# ===========================================================================
# Fixtures
# ===========================================================================


def _three_type_plan_dict() -> dict[str, Any]:
    """Build a 3-type AudiencePlan dict (Standard + Contextual + Agentic).

    Mirrors the canonical example from proposal §5.1 -- a Standard
    primary narrowed by a Contextual constraint and extended by an
    Agentic lookalike. The agentic ref carries a compliance context.
    """

    return {
        "primary": {
            "type": "standard",
            "identifier": "3-7",
            "taxonomy": "iab-audience",
            "version": "1.1",
            "source": "explicit",
        },
        "constraints": [
            {
                "type": "contextual",
                "identifier": "1",  # Automotive (Content Tax 3.1)
                "taxonomy": "iab-content",
                "version": "3.1",
                "source": "resolved",
                "confidence": 0.92,
            }
        ],
        "extensions": [
            {
                "type": "agentic",
                "identifier": (
                    "emb://buyer.example.com/audiences/auto-converters-q1"
                ),
                "taxonomy": "agentic-audiences",
                "version": "draft-2026-01",
                "source": "explicit",
                "compliance_context": {
                    "jurisdiction": "US",
                    "consent_framework": "IAB-TCFv2",
                    "consent_string_ref": "tcf:CPxxxx-test",
                },
            }
        ],
        "rationale": (
            "Auto Intenders 25-54 (Standard primary), narrowed to "
            "Automotive content (Contextual constraint), extended by Q1 "
            "converter lookalikes (Agentic extension)."
        ),
    }


def _base_brief_dict(**overrides: Any) -> dict[str, Any]:
    """Minimum CampaignBrief skeleton with a valid 2-channel allocation."""

    today = date.today()
    base: dict[str, Any] = {
        "advertiser_id": "adv-patha-001",
        "campaign_name": "Path A integration test",
        "objective": "AWARENESS",
        "total_budget": 100_000.0,
        "currency": "USD",
        "flight_start": (today + timedelta(days=7)).isoformat(),
        "flight_end": (today + timedelta(days=37)).isoformat(),
        "channels": [
            {"channel": "CTV", "budget_pct": 60},
            {"channel": "DISPLAY", "budget_pct": 40},
        ],
    }
    base.update(overrides)
    return base


def _three_type_brief() -> CampaignBrief:
    """Brief carrying an explicit 3-type AudiencePlan."""

    return parse_campaign_brief(
        _base_brief_dict(target_audience=_three_type_plan_dict())
    )


# ---------------------------------------------------------------------------
# FakeCampaignStore -- mirrors the unit-test fake from
# tests/unit/test_campaign_pipeline.py so the pipeline can exercise its
# state-machine transitions without a real SQLite-backed store.
# ---------------------------------------------------------------------------


class FakeCampaignStore:
    """In-memory CampaignStore stand-in for pipeline integration tests."""

    def __init__(self) -> None:
        self._campaigns: dict[str, dict[str, Any]] = {}

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def create_campaign(self, brief: dict[str, Any]) -> str:
        campaign_id = str(uuid.uuid4())
        self._campaigns[campaign_id] = {
            "campaign_id": campaign_id,
            "advertiser_id": brief["advertiser_id"],
            "campaign_name": brief["campaign_name"],
            "status": CampaignStatus.DRAFT.value,
            "total_budget": brief["total_budget"],
            "currency": brief.get("currency", "USD"),
            "flight_start": brief["flight_start"],
            "flight_end": brief["flight_end"],
            "channels": brief.get("channels"),
            "target_audience": brief.get("target_audience"),
        }
        return campaign_id

    def get_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        return self._campaigns.get(campaign_id)

    def start_planning(self, campaign_id: str) -> None:
        self._campaigns[campaign_id]["status"] = CampaignStatus.PLANNING.value

    def start_booking(self, campaign_id: str) -> None:
        self._campaigns[campaign_id]["status"] = CampaignStatus.BOOKING.value

    def mark_ready(self, campaign_id: str) -> None:
        self._campaigns[campaign_id]["status"] = CampaignStatus.READY.value

    def update_campaign(self, campaign_id: str, **kwargs: Any) -> bool:
        if campaign_id not in self._campaigns:
            return False
        self._campaigns[campaign_id].update(kwargs)
        return True


def _booked_orchestration_result(
    deal_id: str = "deal-patha-001",
    spend: float = 50_000.0,
    remaining: float = 10_000.0,
) -> OrchestrationResult:
    """Build an OrchestrationResult that looks like a successful booking."""

    deal = MagicMock()
    deal.deal_id = deal_id
    deal.deal_type = "PD"
    deal.pricing = MagicMock()
    deal.pricing.final_cpm = 12.50
    return OrchestrationResult(
        discovered_sellers=[MagicMock(agent_id=f"seller-{i}") for i in range(2)],
        quote_results=[],
        ranked_quotes=[],
        selection=DealSelection(
            booked_deals=[deal],
            failed_bookings=[],
            total_spend=spend,
            remaining_budget=remaining,
        ),
    )


@pytest.fixture
def fake_store() -> FakeCampaignStore:
    return FakeCampaignStore()


@pytest.fixture
def event_bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
def mock_orchestrator() -> AsyncMock:
    """A MultiSellerOrchestrator AsyncMock that captures every orchestrate call.

    The pipeline forwards InventoryRequirements / DealParams (each with
    an `audience_plan` attached per proposal §5.3 / bead ar-fgyq §6) into
    `orchestrate`. Inspecting the captured call args is how we verify
    the typed AudiencePlan reaches the seller boundary.
    """

    orch = AsyncMock(spec=MultiSellerOrchestrator)
    orch.orchestrate.return_value = _booked_orchestration_result()
    return orch


@pytest.fixture
def pipeline(
    fake_store: FakeCampaignStore,
    mock_orchestrator: AsyncMock,
    event_bus: InMemoryEventBus,
) -> CampaignPipeline:
    return CampaignPipeline(
        store=fake_store,
        orchestrator=mock_orchestrator,
        event_bus=event_bus,
    )


# ===========================================================================
# 1. CampaignPipeline happy path -- 3 audience types
# ===========================================================================


class TestCampaignPipelineThreeTypeHappyPath:
    """3-type plan flows brief -> plan -> book through CampaignPipeline."""

    def test_happy_path_three_types_through_path_a(
        self,
        pipeline: CampaignPipeline,
        mock_orchestrator: AsyncMock,
    ) -> None:
        """Full Path A: brief -> plan -> book; audience plan reaches seller.

        The brief carries an explicit 3-type plan (Standard primary +
        Contextual constraint + Agentic extension). After
        ingest_brief -> plan_campaign -> execute_booking the pipeline
        must:

          - Call the orchestrator once per channel (2 channels here).
          - Forward the typed AudiencePlan on BOTH InventoryRequirements
            and DealParams (the §5 wiring -- both surfaces carry it so
            seller discovery and the materialized DealRequest agree).
          - Preserve every audience type (standard / contextual /
            agentic) at the boundary.
          - Keep the audience_plan_id stable from CampaignPlan onwards
            -- the post-planner plan_id and the plan_id observed at the
            seller boundary must match. The pre-planner brief plan_id
            and the post-planner plan_id may legitimately differ when
            the planner adds inferred refs (§5.5 / §7); we only assert
            equality with the ingested id when the planner added none.
        """

        brief = _three_type_brief()
        assert brief.target_audience is not None
        original_plan_id = brief.target_audience.audience_plan_id

        loop = asyncio.new_event_loop()
        try:
            campaign_id = loop.run_until_complete(
                pipeline.ingest_brief(brief.model_dump(mode="json"))
            )
            campaign_plan = loop.run_until_complete(
                pipeline.plan_campaign(campaign_id)
            )
            loop.run_until_complete(pipeline.execute_booking(campaign_id))
        finally:
            loop.close()

        # Planner step ran and attached a typed AudiencePlan to the plan.
        assert campaign_plan.target_audience is not None
        plan_after_planner = campaign_plan.target_audience
        post_planner_plan_id = plan_after_planner.audience_plan_id
        # The pre-planner -> post-planner hash is stable only when no
        # inferred refs were added (proposal §5.5). Mirror the existing
        # unit test pattern in tests/unit/test_audience_planner_wiring.py.
        no_inferred_constraints = not any(
            c.source == "inferred" for c in plan_after_planner.constraints
        )
        no_inferred_extensions = not any(
            e.source == "inferred" for e in plan_after_planner.extensions
        )
        if no_inferred_constraints and no_inferred_extensions:
            assert post_planner_plan_id == original_plan_id
        # All three audience types survived the planner pass.
        assert plan_after_planner.primary.type == "standard"
        assert plan_after_planner.primary.identifier == "3-7"
        assert any(c.type == "contextual" for c in plan_after_planner.constraints)
        assert any(e.type == "agentic" for e in plan_after_planner.extensions)

        # Orchestrator called once per channel (CTV + DISPLAY = 2 calls).
        assert mock_orchestrator.orchestrate.call_count == 2

        # Inspect every orchestrate call: both InventoryRequirements and
        # DealParams must carry the typed AudiencePlan with the same id.
        for call in mock_orchestrator.orchestrate.call_args_list:
            inv_req = call.kwargs["inventory_requirements"]
            deal_params = call.kwargs["deal_params"]

            assert inv_req.audience_plan is not None
            assert isinstance(inv_req.audience_plan, AudiencePlan)
            assert deal_params.audience_plan is not None
            assert isinstance(deal_params.audience_plan, AudiencePlan)

            # End-to-end identity hash stability: post-planner plan_id
            # MUST survive plan -> seller (no in-flight mutation). This
            # is the §5.1 step-2 wire-format guarantee for the buyer
            # side of Path A.
            assert (
                inv_req.audience_plan.audience_plan_id == post_planner_plan_id
            )
            assert (
                deal_params.audience_plan.audience_plan_id
                == post_planner_plan_id
            )

            # All three types still present at the seller boundary.
            assert inv_req.audience_plan.primary.type == "standard"
            assert inv_req.audience_plan.primary.identifier == "3-7"
            assert any(
                c.type == "contextual" for c in inv_req.audience_plan.constraints
            )
            assert any(
                e.type == "agentic" for e in inv_req.audience_plan.extensions
            )

            # Compliance context survives for the agentic extension --
            # required by §5.2's consent-regime guarantee.
            agentic = next(
                e for e in inv_req.audience_plan.extensions if e.type == "agentic"
            )
            assert isinstance(agentic.compliance_context, ComplianceContext)
            assert agentic.compliance_context.jurisdiction == "US"
            assert agentic.compliance_context.consent_framework == "IAB-TCFv2"


# ===========================================================================
# Re-exports for part 2 (legacy migration / round-trip / degradation).
# Helpers are intentionally module-level so part 2 can extend without
# refactoring this file. AudienceRef is imported above for that purpose.
# ===========================================================================

__all__ = [
    "FakeCampaignStore",
    "AudienceRef",  # used by part 2 fixtures
]
