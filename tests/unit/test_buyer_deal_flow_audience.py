# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for AudiencePlan threading through BuyerDealFlow (Path B).

Bead ar-ts30 §18 -- Path B of the Audience Planner wiring. Verifies the
deal-flow path (`BuyerDealFlow`, the renamed BuyerDealFlow) invokes the
same Audience Planner step that ``CampaignPipeline`` (Path A) uses, and
that the resulting ``AudiencePlan`` survives every flow stage and rides
on the seller-bound ``DealRequest`` payload.

Coverage (per bead deliverable):

1. Brief -> deal pipeline produces a plan via the audience planner step.
2. Explicit-typed brief preserved through BuyerDealFlow (no mutation).
3. Legacy list[str] brief migrated correctly through BuyerDealFlow.
4. AudiencePlan survives BuyerDealFlow -> seller boundary (mocked).
5. End-to-end audience_plan_id stable through BuyerDealFlow stages.

Reference: AUDIENCE_PLANNER_3TYPE_EXTENSION_2026-04-25.md §5.1, §5.3,
§6 row 18.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

# Stub Anthropic key BEFORE any ad_buyer.crews / agents imports (mirrors
# pattern in test_audience_planner_wiring.py).
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

import pytest

from ad_buyer.flows.buyer_deal_flow import BuyerDealFlow, BuyerDealFlowStatus
from ad_buyer.models.audience_plan import AudiencePlan, AudienceRef
from ad_buyer.models.buyer_identity import (
    BuyerContext,
    BuyerIdentity,
    DealRequest,
    DealType,
)
from ad_buyer.models.campaign_brief import CampaignBrief, parse_campaign_brief


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _legacy_brief_dict(**overrides: Any) -> dict[str, Any]:
    """Build a minimal brief carrying a legacy `list[str]` target_audience."""

    today = date.today()
    base: dict[str, Any] = {
        "advertiser_id": "adv-001",
        "campaign_name": "Path B legacy migration",
        "objective": "AWARENESS",
        "total_budget": 50_000.0,
        "currency": "USD",
        "flight_start": (today + timedelta(days=7)).isoformat(),
        "flight_end": (today + timedelta(days=37)).isoformat(),
        "channels": [
            {"channel": "CTV", "budget_pct": 60},
            {"channel": "DISPLAY", "budget_pct": 40},
        ],
        "target_audience": ["auto_intenders_25_54"],
    }
    base.update(overrides)
    return base


def _typed_brief_dict(**overrides: Any) -> dict[str, Any]:
    """Build a brief that already carries a typed AudiencePlan dict."""

    plan = {
        "primary": {
            "type": "standard",
            "identifier": "3-7",
            "taxonomy": "iab-audience",
            "version": "1.1",
            "source": "explicit",
        },
        "rationale": "User-supplied: focus on auto intenders aged 25-54.",
    }
    return _legacy_brief_dict(target_audience=plan, **overrides)


def _make_brief(**overrides: Any) -> CampaignBrief:
    return parse_campaign_brief(_typed_brief_dict(**overrides))


def _make_legacy_brief(**overrides: Any) -> CampaignBrief:
    return parse_campaign_brief(_legacy_brief_dict(**overrides))


def _agency_buyer_context() -> BuyerContext:
    identity = BuyerIdentity(
        seat_id="ttd-seat-001",
        agency_id="agency-123",
        agency_name="Test Agency",
    )
    return BuyerContext(identity=identity, is_authenticated=True)


@pytest.fixture
def mock_unified_client() -> MagicMock:
    client = MagicMock()
    client.search_products = AsyncMock()
    client.list_products = AsyncMock()
    client.get_product = AsyncMock()
    return client


def _seed_request_state(flow: BuyerDealFlow) -> None:
    """Populate the minimal request fields the @start step expects."""

    flow.state.request = "CTV inventory for auto intenders under $30 CPM"
    flow.state.deal_type = DealType.PREFERRED_DEAL
    flow.state.impressions = 1_000_000
    flow.state.max_cpm = 30.0
    flow.state.flight_start = "2026-05-01"
    flow.state.flight_end = "2026-05-31"


# ===========================================================================
# 1. Brief -> deal pipeline produces a plan via the audience planner step
# ===========================================================================


class TestPlannerRunsOnReceiveRequest:
    """When a brief is supplied, receive_request must run the planner."""

    def test_brief_yields_audience_plan_on_state(
        self, mock_unified_client: MagicMock
    ) -> None:
        brief = _make_brief()
        flow = BuyerDealFlow(
            client=mock_unified_client,
            buyer_context=_agency_buyer_context(),
            brief=brief,
        )
        _seed_request_state(flow)

        result = flow.receive_request()

        assert result["status"] == "success"
        assert flow.state.status == BuyerDealFlowStatus.REQUEST_RECEIVED
        # The planner must have produced a typed AudiencePlan on state.
        assert isinstance(flow.state.audience_plan, AudiencePlan)
        # And cached the planner result for introspection.
        planner_result = flow.get_audience_planner_result()
        assert planner_result is not None
        assert planner_result.plan is flow.state.audience_plan

    def test_no_brief_keeps_flow_audience_blind(
        self, mock_unified_client: MagicMock
    ) -> None:
        """Legacy callers (no brief) must keep the original audience-blind path."""

        flow = BuyerDealFlow(
            client=mock_unified_client,
            buyer_context=_agency_buyer_context(),
        )
        _seed_request_state(flow)

        result = flow.receive_request()

        assert result["status"] == "success"
        assert flow.state.audience_plan is None
        assert flow.get_audience_planner_result() is None


# ===========================================================================
# 2. Explicit-typed brief preserved through BuyerDealFlow
# ===========================================================================


class TestExplicitBriefPreserved:
    """Explicit user-supplied AudiencePlans are NEVER mutated by the planner."""

    def test_explicit_primary_preserved_verbatim(
        self, mock_unified_client: MagicMock
    ) -> None:
        brief = _make_brief()
        # Capture the explicit plan as authored by the user.
        original = brief.target_audience
        assert original is not None
        original_id = original.audience_plan_id
        original_primary_identifier = original.primary.identifier
        original_primary_source = original.primary.source

        flow = BuyerDealFlow(
            client=mock_unified_client,
            buyer_context=_agency_buyer_context(),
            brief=brief,
        )
        _seed_request_state(flow)
        flow.receive_request()

        plan = flow.state.audience_plan
        assert plan is not None
        # Primary is preserved verbatim -- type, identifier, source.
        assert plan.primary.type == "standard"
        assert plan.primary.identifier == original_primary_identifier
        assert plan.primary.source == original_primary_source
        # When the planner only enriches around the primary (no constraints
        # / extensions added), the audience_plan_id is stable; if it does
        # add inferred refs the rationale records that. Either way the
        # primary identity must not drift -- assert on the primary.
        assert plan.primary.identifier == "3-7"
        # And if no enrichment landed, the hash itself is stable.
        if not plan.constraints and not plan.extensions:
            assert plan.audience_plan_id == original_id


# ===========================================================================
# 3. Legacy list[str] brief migrated correctly through BuyerDealFlow
# ===========================================================================


class TestLegacyBriefMigration:
    """Legacy `list[str]` audience field must round-trip through the flow."""

    def test_legacy_brief_yields_inferred_primary(
        self, mock_unified_client: MagicMock
    ) -> None:
        brief = _make_legacy_brief()
        # Confirm the parser already migrated the list[str] to a typed plan
        # marked source=inferred (the contract from §4 / coerce_audience_field).
        assert brief.target_audience is not None
        assert brief.target_audience.primary.identifier == "auto_intenders_25_54"
        assert brief.target_audience.primary.source == "inferred"

        flow = BuyerDealFlow(
            client=mock_unified_client,
            buyer_context=_agency_buyer_context(),
            brief=brief,
        )
        _seed_request_state(flow)
        flow.receive_request()

        plan = flow.state.audience_plan
        assert plan is not None
        assert plan.primary.identifier == "auto_intenders_25_54"
        # Migrated primary stays inferred -- the planner must NOT promote
        # a migrated primary to source=explicit (§5.5 hard rule).
        assert plan.primary.source == "inferred"


# ===========================================================================
# 4. AudiencePlan survives BuyerDealFlow -> seller boundary
# ===========================================================================


def _make_minimal_plan(identifier: str = "3-7") -> AudiencePlan:
    """Build a minimal AudiencePlan for direct injection into flow state."""

    return AudiencePlan(
        primary=AudienceRef(
            type="standard",
            identifier=identifier,
            taxonomy="iab-audience",
            version="1.1",
            source="explicit",
        ),
        rationale="Boundary-test plan.",
    )


class TestAudiencePlanCrossesSellerBoundary:
    """The plan threaded onto state must reach the seller-bound DealRequest."""

    def test_request_deal_id_threads_plan_into_tool(
        self, mock_unified_client: MagicMock
    ) -> None:
        """request_deal_id must call the deal tool with the AudiencePlan."""

        flow = BuyerDealFlow(
            client=mock_unified_client,
            buyer_context=_agency_buyer_context(),
        )
        _seed_request_state(flow)

        plan = _make_minimal_plan()
        flow.state.audience_plan = plan
        flow.state.selected_product_id = "ctv-pkg-1"

        # Mock the deal tool so we can inspect the call.
        flow._deal_tool = MagicMock()
        flow._deal_tool._run = MagicMock(
            return_value="DEAL CREATED: deal-test-001"
        )

        result = flow.request_deal_id({"status": "success"})

        assert result["status"] == "success"
        flow._deal_tool._run.assert_called_once()
        call_kwargs = flow._deal_tool._run.call_args.kwargs
        # The plan crossed the flow -> tool boundary intact.
        assert call_kwargs.get("audience_plan") is plan
        # Deal type / impressions / flights came along too.
        assert call_kwargs.get("product_id") == "ctv-pkg-1"

    def test_request_deal_payload_carries_plan(
        self, mock_unified_client: MagicMock
    ) -> None:
        """The seller-bound DealRequest payload must carry the plan."""

        from ad_buyer.tools.dsp.request_deal import RequestDealTool

        # Real tool so we exercise build_deal_request_payload end to end.
        tool = RequestDealTool(
            client=mock_unified_client,
            buyer_context=_agency_buyer_context(),
        )
        plan = _make_minimal_plan(identifier="contextual-IAB1-2")

        payload = tool.build_deal_request_payload(
            product_id="ctv-pkg-1",
            deal_type="PD",
            impressions=500_000,
            flight_start="2026-05-01",
            flight_end="2026-05-31",
            target_cpm=None,
            audience_plan=plan,
        )

        assert isinstance(payload, DealRequest)
        assert payload.audience_plan is plan
        # Round-trip through model_dump -> model_validate must preserve
        # the plan's content hash (proposal §5.1 step 2).
        raw = payload.model_dump(mode="json")
        rebuilt = DealRequest.model_validate(raw)
        assert rebuilt.audience_plan is not None
        assert rebuilt.audience_plan.audience_plan_id == plan.audience_plan_id

    def test_legacy_payload_still_works_without_plan(
        self, mock_unified_client: MagicMock
    ) -> None:
        """No plan supplied -> DealRequest carries audience_plan=None."""

        from ad_buyer.tools.dsp.request_deal import RequestDealTool

        tool = RequestDealTool(
            client=mock_unified_client,
            buyer_context=_agency_buyer_context(),
        )
        payload = tool.build_deal_request_payload(
            product_id="ctv-pkg-1",
            deal_type="PD",
            impressions=500_000,
            flight_start="2026-05-01",
            flight_end="2026-05-31",
            target_cpm=None,
            audience_plan=None,
        )
        assert payload.audience_plan is None


# ===========================================================================
# 5. End-to-end audience_plan_id stable through BuyerDealFlow stages
# ===========================================================================


class TestPlanIdStableThroughStages:
    """The audience_plan_id must NOT drift between receive_request and the deal tool."""

    def test_plan_id_preserved_from_brief_to_tool_call(
        self, mock_unified_client: MagicMock
    ) -> None:
        brief = _make_brief()
        flow = BuyerDealFlow(
            client=mock_unified_client,
            buyer_context=_agency_buyer_context(),
            brief=brief,
        )
        _seed_request_state(flow)

        # Stage 1: receive_request -> planner runs -> plan on state.
        flow.receive_request()
        plan_after_receive = flow.state.audience_plan
        assert plan_after_receive is not None
        plan_id_after_receive = plan_after_receive.audience_plan_id

        # Skip ahead to request_deal_id with a mocked deal tool so we can
        # observe the plan that crosses the boundary.
        flow.state.selected_product_id = "ctv-pkg-1"
        flow._deal_tool = MagicMock()
        flow._deal_tool._run = MagicMock(
            return_value="DEAL CREATED: deal-test-002"
        )

        flow.request_deal_id({"status": "success"})

        observed_plan = flow._deal_tool._run.call_args.kwargs.get(
            "audience_plan"
        )
        assert observed_plan is not None
        # Same audience_plan_id from brief through state to tool kwargs.
        assert observed_plan.audience_plan_id == plan_id_after_receive

    def test_plan_id_surfaced_on_status(
        self, mock_unified_client: MagicMock
    ) -> None:
        """get_status() exposes audience_plan_id once the planner has run."""

        brief = _make_brief()
        flow = BuyerDealFlow(
            client=mock_unified_client,
            buyer_context=_agency_buyer_context(),
            brief=brief,
        )
        _seed_request_state(flow)
        flow.receive_request()

        status = flow.get_status()
        assert status["audience_plan_id"] is not None
        plan = flow.state.audience_plan
        assert plan is not None
        assert status["audience_plan_id"] == plan.audience_plan_id

    def test_explicit_plan_takes_precedence_over_brief(
        self, mock_unified_client: MagicMock
    ) -> None:
        """A pre-set audience_plan on state must NOT be overwritten by the planner."""

        brief = _make_brief()
        flow = BuyerDealFlow(
            client=mock_unified_client,
            buyer_context=_agency_buyer_context(),
            brief=brief,
        )
        _seed_request_state(flow)

        injected = _make_minimal_plan(identifier="9-99")
        flow.state.audience_plan = injected
        flow.receive_request()

        # The planner did NOT overwrite the pre-set plan.
        assert flow.state.audience_plan is injected
        # And no planner result was cached because the planner did not run.
        assert flow.get_audience_planner_result() is None
