# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Out-of-bounds LLM recommendations are clamped/rejected, never booked (EP-4.3).

Channel-crew output is untrusted free text. These tests feed deliberately
out-of-bounds and malformed "LLM" recommendations through the deterministic
validation+clamp boundary and prove:

* a CPM 10x the campaign max is clamped down to the max;
* a negative impression / cost is clamped to 0;
* a non-JSON / non-object payload is rejected (no recommendation at all);
* an uncoercible numeric field rejects the item;
* end-to-end, the ceiling handed to the booking orchestrator is the CLAMPED
  value -- the inflated LLM CPM never authorizes a booking above the buyer's
  own max.

Bead ar-1ow7 (EP-4.3).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from ad_buyer.booking.recommendation_guard import (
    RecommendationBounds,
    validate_and_clamp_recommendation,
)
from ad_buyer.flows.deal_booking_flow import DealBookingFlow
from ad_buyer.models.flow_state import ChannelAllocation, ExecutionStatus

# ---------------------------------------------------------------------------
# 1. The boundary helper in isolation
# ---------------------------------------------------------------------------


class TestValidateAndClampBoundary:
    def test_cpm_above_max_is_clamped_down(self):
        bounds = RecommendationBounds(max_cpm=20.0, max_cost=10_000.0)
        rec = validate_and_clamp_recommendation(
            {"product_id": "p1", "cpm": 200.0, "impressions": 100_000, "cost": 5_000.0},
            "branding",
            bounds,
        )
        assert rec is not None
        assert rec.cpm == 20.0  # clamped from 10x the max, not trusted

    def test_cost_above_budget_is_clamped_down(self):
        bounds = RecommendationBounds(max_cpm=20.0, max_cost=1_000.0)
        rec = validate_and_clamp_recommendation(
            {"product_id": "p1", "cpm": 10.0, "impressions": 100_000, "cost": 999_999.0},
            "branding",
            bounds,
        )
        assert rec is not None
        assert rec.cost == 1_000.0

    def test_negative_impressions_clamped_to_zero(self):
        rec = validate_and_clamp_recommendation(
            {"product_id": "p1", "cpm": 10.0, "impressions": -500, "cost": 100.0},
            "ctv",
            RecommendationBounds(),
        )
        assert rec is not None
        assert rec.impressions == 0

    def test_negative_cpm_and_cost_clamped_to_zero(self):
        rec = validate_and_clamp_recommendation(
            {"product_id": "p1", "cpm": -5.0, "impressions": 10, "cost": -9.0},
            "ctv",
            RecommendationBounds(max_cpm=20.0),
        )
        assert rec is not None
        assert rec.cpm == 0.0
        assert rec.cost == 0.0

    def test_non_dict_item_rejected(self):
        bounds = RecommendationBounds()
        assert validate_and_clamp_recommendation("not-an-object", "branding", bounds) is None
        assert validate_and_clamp_recommendation(42, "branding", bounds) is None

    def test_uncoercible_numeric_rejected(self):
        rec = validate_and_clamp_recommendation(
            {"product_id": "p1", "cpm": "cheap!", "impressions": 10, "cost": 100.0},
            "branding",
            RecommendationBounds(),
        )
        assert rec is None

    def test_in_bounds_values_pass_through_unchanged(self):
        bounds = RecommendationBounds(max_cpm=50.0, max_cost=10_000.0)
        rec = validate_and_clamp_recommendation(
            {"product_id": "p1", "cpm": 12.0, "impressions": 100_000, "cost": 1_200.0},
            "branding",
            bounds,
        )
        assert rec is not None
        assert (rec.cpm, rec.impressions, rec.cost) == (12.0, 100_000, 1_200.0)


# ---------------------------------------------------------------------------
# 2. Through the flow's _parse_recommendations (bounds derived from brief)
# ---------------------------------------------------------------------------


def _flow_with_bounds(max_cpm: float | None = 20.0, budget: float = 10_000.0) -> DealBookingFlow:
    brief: dict = {
        "objectives": ["awareness"],
        "budget": budget,
        "start_date": "2026-04-01",
        "end_date": "2026-04-30",
        "target_audience": {"geo": ["US"]},
    }
    if max_cpm is not None:
        brief["max_cpm"] = max_cpm
    flow = DealBookingFlow(client=MagicMock(), orchestrator=MagicMock(), campaign_brief=brief)
    flow.state.budget_allocations = {
        "branding": ChannelAllocation(
            channel="branding", budget=4_000.0, percentage=40.0, rationale="x"
        )
    }
    return flow


class TestParseClampsAndRejects:
    def test_out_of_bounds_cpm_clamped_via_parse(self):
        flow = _flow_with_bounds(max_cpm=20.0)
        payload = json.dumps(
            [{"product_id": "p1", "product_name": "N", "publisher": "P",
              "impressions": 100_000, "cpm": 200.0, "cost": 500.0}]
        )
        recs = flow._parse_recommendations(payload, "branding")
        assert len(recs) == 1
        assert recs[0].cpm == 20.0  # 10x max -> clamped to the campaign ceiling

    def test_per_line_cost_clamped_to_channel_budget_via_parse(self):
        flow = _flow_with_bounds()
        payload = json.dumps(
            [{"product_id": "p1", "product_name": "N", "publisher": "P",
              "impressions": 100_000, "cpm": 10.0, "cost": 999_999.0}]
        )
        recs = flow._parse_recommendations(payload, "branding")
        assert len(recs) == 1
        assert recs[0].cost == 4_000.0  # clamped to the branding channel budget

    def test_negative_impressions_clamped_via_parse(self):
        flow = _flow_with_bounds()
        payload = json.dumps(
            [{"product_id": "p1", "product_name": "N", "publisher": "P",
              "impressions": -100, "cpm": 10.0, "cost": 100.0}]
        )
        recs = flow._parse_recommendations(payload, "branding")
        assert len(recs) == 1
        assert recs[0].impressions == 0

    def test_non_json_rejected_via_parse(self):
        flow = _flow_with_bounds()
        assert flow._parse_recommendations("The seller has no inventory today.", "branding") == []

    def test_malformed_item_rejected_but_valid_sibling_kept(self):
        flow = _flow_with_bounds(max_cpm=20.0)
        payload = json.dumps(
            [
                {"product_id": "bad", "cpm": "free", "impressions": 10, "cost": 1.0},
                {"product_id": "good", "product_name": "N", "publisher": "P",
                 "impressions": 100_000, "cpm": 500.0, "cost": 100.0},
            ]
        )
        recs = flow._parse_recommendations(payload, "branding")
        assert [r.product_id for r in recs] == ["good"]
        assert recs[0].cpm == 20.0  # clamped


# ---------------------------------------------------------------------------
# 2b. Bounds derived from kpis-shaped briefs (real-driver path, bead ar-0wev)
# ---------------------------------------------------------------------------


def _flow_with_kpis_brief(
    kpis: dict | None,
    top_level_max_cpm: float | None = None,
    budget: float = 100_000.0,
) -> DealBookingFlow:
    """Flow whose brief carries constraints the way the rig real_driver does:
    inside the ``kpis`` dict (``max_cpm_usd``), not as top-level ``max_cpm``."""
    brief: dict = {
        "objectives": ["awareness"],
        "budget": budget,
        "start_date": "2026-04-01",
        "end_date": "2026-04-30",
        "target_audience": {"geo": ["US"]},
    }
    if kpis is not None:
        brief["kpis"] = kpis
    if top_level_max_cpm is not None:
        brief["max_cpm"] = top_level_max_cpm
    flow = DealBookingFlow(client=MagicMock(), orchestrator=MagicMock(), campaign_brief=brief)
    flow.state.budget_allocations = {
        "branding": ChannelAllocation(
            channel="branding", budget=50_000.0, percentage=50.0, rationale="x"
        )
    }
    return flow


class TestBoundsFromKpisShapedBrief:
    """The CPM clamp must engage for briefs that carry the ceiling in
    ``kpis.max_cpm_usd`` (the CampaignBrief / rig shape). Run #13 regression:
    a $25-CPM item sailed past an $18 ceiling because bounds only read the
    top-level ``max_cpm`` key. Bead ar-0wev."""

    def test_kpis_max_cpm_usd_engages_cpm_clamp(self):
        flow = _flow_with_kpis_brief(kpis={"max_cpm_usd": 18.0})
        bounds = flow._recommendation_bounds("branding")
        assert bounds.max_cpm == 18.0

    def test_run13_shape_25_cpm_item_clamped_to_18_ceiling(self):
        flow = _flow_with_kpis_brief(kpis={"max_cpm_usd": 18.0})
        payload = json.dumps(
            [{"product_id": "prod-b41e2339", "product_name": "N", "publisher": "P",
              "impressions": 1_666_666, "cpm": 25.0, "cost": 24_999.99}]
        )
        recs = flow._parse_recommendations(payload, "branding")
        assert len(recs) == 1
        assert recs[0].cpm == 18.0  # clamped, not sailed through unbounded

    def test_top_level_max_cpm_still_honored_as_fallback(self):
        flow = _flow_with_kpis_brief(kpis={"viewability": 70}, top_level_max_cpm=18.0)
        bounds = flow._recommendation_bounds("branding")
        assert bounds.max_cpm == 18.0

    def test_kpis_value_takes_precedence_over_top_level(self):
        flow = _flow_with_kpis_brief(kpis={"max_cpm_usd": 18.0}, top_level_max_cpm=30.0)
        bounds = flow._recommendation_bounds("branding")
        assert bounds.max_cpm == 18.0

    def test_non_positive_or_garbage_kpis_value_falls_back(self):
        flow = _flow_with_kpis_brief(kpis={"max_cpm_usd": 0}, top_level_max_cpm=18.0)
        assert flow._recommendation_bounds("branding").max_cpm == 18.0
        flow = _flow_with_kpis_brief(kpis={"max_cpm_usd": "cheap"}, top_level_max_cpm=18.0)
        assert flow._recommendation_bounds("branding").max_cpm == 18.0

    def test_no_ceiling_anywhere_disables_cpm_clamp(self):
        flow = _flow_with_kpis_brief(kpis={"viewability": 70})
        assert flow._recommendation_bounds("branding").max_cpm is None


# ---------------------------------------------------------------------------
# 3. End-to-end: the clamped ceiling -- not the inflated LLM value --
#    is what reaches the booking orchestrator.
# ---------------------------------------------------------------------------


class _CapturingOrchestrator:
    """Records the ceiling/budget handed to orchestrate; books nothing."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def orchestrate(self, inventory_requirements, deal_params, budget, max_deals=3):
        from ad_buyer.orchestration.multi_seller import DealSelection, OrchestrationResult

        self.calls.append(
            {
                "max_cpm": inventory_requirements.max_cpm,
                "target_cpm": deal_params.target_cpm,
                "budget": budget,
            }
        )
        return OrchestrationResult(
            discovered_sellers=[],
            quote_results=[],
            ranked_quotes=[],
            selection=DealSelection(
                booked_deals=[], failed_bookings=[], total_spend=0.0, remaining_budget=budget
            ),
        )


class TestClampProtectsBooking:
    def test_orchestrator_receives_clamped_ceiling_not_inflated_llm_value(self):
        orchestrator = _CapturingOrchestrator()
        flow = _flow_with_bounds(max_cpm=20.0, budget=10_000.0)
        flow._orchestrator = orchestrator

        # An LLM proposes a CPM 10x the buyer's max and a wild cost.
        payload = json.dumps(
            [{"product_id": "p1", "product_name": "N", "publisher": "P",
              "impressions": 100_000, "cpm": 200.0, "cost": 999_999.0}]
        )
        recs = flow._parse_recommendations(payload, "branding")
        flow.state.pending_approvals = recs
        flow.state.execution_status = ExecutionStatus.AWAITING_APPROVAL

        result = flow.approve_all()

        # The ceiling handed to the booking engine is the CLAMPED value.
        assert len(orchestrator.calls) == 1
        call = orchestrator.calls[0]
        assert call["max_cpm"] == 20.0
        assert call["target_cpm"] == 20.0
        assert call["budget"] == 4_000.0  # cost clamped to channel budget
        # Nothing booked above the ceiling (the fake seller books nothing).
        assert result["booked"] == 0

    def test_over_budget_after_clamp_still_hard_rejected_by_spend_ceiling(self):
        """Even a clamped total over the campaign budget is rejected pre-contact."""
        orchestrator = _CapturingOrchestrator()
        # Tiny total budget so the clamped per-line cost still exceeds it.
        flow = _flow_with_bounds(max_cpm=20.0, budget=100.0)
        flow.state.budget_allocations = {
            "branding": ChannelAllocation(
                channel="branding", budget=50_000.0, percentage=100.0, rationale="x"
            )
        }
        flow._orchestrator = orchestrator
        payload = json.dumps(
            [{"product_id": "p1", "product_name": "N", "publisher": "P",
              "impressions": 100_000, "cpm": 10.0, "cost": 40_000.0}]
        )
        recs = flow._parse_recommendations(payload, "branding")
        flow.state.pending_approvals = recs
        flow.state.execution_status = ExecutionStatus.AWAITING_APPROVAL

        result = flow.approve_all()

        assert result["status"] == "rejected"
        assert result["booked"] == 0
        assert orchestrator.calls == []  # spend ceiling fired before any handoff


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
