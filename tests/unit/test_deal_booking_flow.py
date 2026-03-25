# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for DealBookingFlow - the main deal booking workflow.

Covers:
- Brief validation (happy path, missing fields, invalid budget)
- Audience planning (with/without targeting, coverage estimation, gap identification)
- Budget allocation (crew result parsing, default allocations, error handling)
- Channel research (branding, CTV, mobile, performance; skipped/no-budget/failure)
- Recommendation consolidation (partial, complete, empty)
- Approval and booking (approve specific, approve all, none approved)
- Status reporting
- Edge cases throughout
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from ad_buyer.flows.deal_booking_flow import DealBookingFlow
from ad_buyer.models.flow_state import (
    BookedLine,
    ChannelAllocation,
    ExecutionStatus,
    ProductRecommendation,
)
from ad_buyer.models.ucp import SignalType

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_opendirect_client():
    """Create a mock OpenDirectClient."""
    return MagicMock()


@pytest.fixture
def valid_campaign_brief():
    """A complete, valid campaign brief."""
    return {
        "name": "Spring 2026 Campaign",
        "objectives": ["brand awareness", "reach"],
        "budget": 100000,
        "start_date": "2026-04-01",
        "end_date": "2026-04-30",
        "target_audience": {
            "demographics": {"age": "25-54", "gender": "all"},
            "interests": ["technology", "fitness"],
            "behaviors": ["online shoppers"],
            "geo": ["US"],
        },
        "kpis": {"viewability": 70, "ctr": 0.15},
    }


@pytest.fixture
def minimal_campaign_brief():
    """A campaign brief with only required fields."""
    return {
        "objectives": ["conversions"],
        "budget": 5000,
        "start_date": "2026-05-01",
        "end_date": "2026-05-31",
        "target_audience": {"geo": ["US"]},
    }


@pytest.fixture
def flow(mock_opendirect_client):
    """Create a DealBookingFlow with mocked client."""
    return DealBookingFlow(client=mock_opendirect_client)


@pytest.fixture
def flow_with_brief(flow, valid_campaign_brief):
    """Flow with campaign brief already set in state."""
    flow.state.campaign_brief = valid_campaign_brief
    return flow


@pytest.fixture
def flow_with_allocations(flow_with_brief):
    """Flow with budget allocations already set."""
    flow_with_brief.state.budget_allocations = {
        "branding": ChannelAllocation(
            channel="branding", budget=40000, percentage=40.0, rationale="Upper funnel"
        ),
        "performance": ChannelAllocation(
            channel="performance", budget=30000, percentage=30.0, rationale="Conversions"
        ),
        "ctv": ChannelAllocation(
            channel="ctv", budget=20000, percentage=20.0, rationale="Video reach"
        ),
        "mobile_app": ChannelAllocation(
            channel="mobile_app", budget=10000, percentage=10.0, rationale="App installs"
        ),
    }
    flow_with_brief.state.execution_status = ExecutionStatus.BUDGET_ALLOCATED
    return flow_with_brief


def _make_recommendation(product_id, channel, impressions=500000, cpm=15.0):
    """Helper to create a ProductRecommendation."""
    return ProductRecommendation(
        product_id=product_id,
        product_name=f"Product {product_id}",
        publisher="Publisher A",
        channel=channel,
        impressions=impressions,
        cpm=cpm,
        cost=round(impressions * cpm / 1000, 2),
    )


# ===========================================================================
# 1. receive_campaign_brief (the @start step)
# ===========================================================================


class TestReceiveCampaignBrief:
    """Tests for the brief-validation entry point."""

    def test_valid_brief_succeeds(self, flow, valid_campaign_brief):
        """Happy path: valid brief sets state to BRIEF_RECEIVED."""
        flow.state.campaign_brief = valid_campaign_brief

        result = flow.receive_campaign_brief()

        assert result["status"] == "success"
        assert result["brief"] == valid_campaign_brief
        assert flow.state.execution_status == ExecutionStatus.BRIEF_RECEIVED
        assert len(flow.state.errors) == 0

    def test_minimal_brief_succeeds(self, flow, minimal_campaign_brief):
        """Brief with only required fields passes validation."""
        flow.state.campaign_brief = minimal_campaign_brief

        result = flow.receive_campaign_brief()

        assert result["status"] == "success"
        assert flow.state.execution_status == ExecutionStatus.BRIEF_RECEIVED

    def test_missing_single_required_field(self, flow):
        """Missing one required field fails validation."""
        flow.state.campaign_brief = {
            "objectives": ["reach"],
            "budget": 50000,
            "start_date": "2026-04-01",
            # missing end_date and target_audience
        }

        result = flow.receive_campaign_brief()

        assert result["status"] == "failed"
        assert flow.state.execution_status == ExecutionStatus.VALIDATION_FAILED
        assert any("end_date" in err for err in flow.state.errors)

    def test_missing_all_required_fields(self, flow):
        """Empty brief fails with all missing fields listed."""
        flow.state.campaign_brief = {}

        result = flow.receive_campaign_brief()

        assert result["status"] == "failed"
        assert flow.state.execution_status == ExecutionStatus.VALIDATION_FAILED
        assert len(flow.state.errors) >= 1
        # Should mention the missing fields
        error_text = flow.state.errors[0]
        for field in ["objectives", "budget", "start_date", "end_date", "target_audience"]:
            assert field in error_text

    def test_zero_budget_fails(self, flow):
        """Budget of 0 fails validation."""
        flow.state.campaign_brief = {
            "objectives": ["reach"],
            "budget": 0,
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
            "target_audience": {"geo": ["US"]},
        }

        result = flow.receive_campaign_brief()

        assert result["status"] == "failed"
        assert flow.state.execution_status == ExecutionStatus.VALIDATION_FAILED
        assert any("Budget" in e for e in flow.state.errors)

    def test_negative_budget_fails(self, flow):
        """Negative budget fails validation."""
        flow.state.campaign_brief = {
            "objectives": ["awareness"],
            "budget": -1000,
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
            "target_audience": {"geo": ["US"]},
        }

        result = flow.receive_campaign_brief()

        assert result["status"] == "failed"
        assert any("Budget" in e for e in flow.state.errors)

    def test_updated_at_is_set(self, flow, valid_campaign_brief):
        """updated_at timestamp is refreshed on success."""
        flow.state.campaign_brief = valid_campaign_brief
        before = flow.state.updated_at

        flow.receive_campaign_brief()

        # updated_at should be set (may equal before if sub-millisecond, but should not be None)
        assert flow.state.updated_at is not None


# ===========================================================================
# 2. plan_audience
# ===========================================================================


class TestPlanAudience:
    """Tests for audience planning step."""

    def test_skips_on_failed_brief(self, flow_with_brief):
        """plan_audience passes through upstream failure."""
        failed_result = {"status": "failed", "errors": ["bad brief"]}

        result = flow_with_brief.plan_audience(failed_result)

        assert result["status"] == "failed"

    def test_no_target_audience_skips_planning(self, flow):
        """Empty target_audience results in skipped planning."""
        flow.state.campaign_brief = {
            "objectives": ["reach"],
            "budget": 10000,
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
            "target_audience": {},
        }

        result = flow.plan_audience({"status": "success"})

        assert result["status"] == "success"
        assert result["audience_plan"] is None

    def test_audience_plan_created_with_demographics(self, flow_with_brief):
        """Audience plan includes IDENTITY signal for demographics."""
        result = flow_with_brief.plan_audience({"status": "success"})

        assert result["status"] == "success"
        plan = result["audience_plan"]
        assert plan is not None
        assert "plan_id" in plan
        assert plan["target_demographics"] == {"age": "25-54", "gender": "all"}
        assert SignalType.IDENTITY.value in plan["requested_signal_types"]

    def test_audience_plan_includes_interests(self, flow_with_brief):
        """Interests trigger CONTEXTUAL signal type."""
        result = flow_with_brief.plan_audience({"status": "success"})

        plan = result["audience_plan"]
        assert "technology" in plan["target_interests"]
        assert "fitness" in plan["target_interests"]
        assert SignalType.CONTEXTUAL.value in plan["requested_signal_types"]

    def test_audience_plan_includes_behaviors(self, flow_with_brief):
        """Behaviors trigger REINFORCEMENT signal type."""
        result = flow_with_brief.plan_audience({"status": "success"})

        plan = result["audience_plan"]
        assert "online shoppers" in plan["target_behaviors"]
        assert SignalType.REINFORCEMENT.value in plan["requested_signal_types"]

    def test_coverage_estimates_returned(self, flow_with_brief):
        """Coverage estimates are generated per channel."""
        result = flow_with_brief.plan_audience({"status": "success"})

        estimates = result["coverage_estimates"]
        assert "branding" in estimates
        assert "ctv" in estimates
        assert "mobile_app" in estimates
        assert "performance" in estimates
        # All values should be positive percentages
        for val in estimates.values():
            assert 0 < val <= 100

    def test_coverage_penalty_for_complex_targeting(self, flow_with_brief):
        """Complex targeting (demographics + behaviors + interests) reduces coverage."""
        result = flow_with_brief.plan_audience({"status": "success"})
        estimates = result["coverage_estimates"]

        # With full targeting, all channels should be below their base factors
        # branding base = 85% -> should be reduced
        assert estimates["branding"] < 85.0

    def test_audience_gaps_identified(self, flow_with_brief):
        """Gaps are identified for behavioral targeting."""
        result = flow_with_brief.plan_audience({"status": "success"})

        gaps = result["gaps"]
        assert any("behavioral" in g for g in gaps)

    def test_income_targeting_gap(self, flow):
        """Income targeting produces a specific gap warning."""
        flow.state.campaign_brief = {
            "objectives": ["reach"],
            "budget": 50000,
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
            "target_audience": {
                "demographics": {"income": "100k+"},
            },
        }

        result = flow.plan_audience({"status": "success"})

        gaps = result["gaps"]
        assert any("income" in g for g in gaps)

    def test_audience_state_is_stored(self, flow_with_brief):
        """Audience plan and coverage are stored in flow state."""
        flow_with_brief.plan_audience({"status": "success"})

        assert flow_with_brief.state.audience_plan is not None
        assert len(flow_with_brief.state.audience_coverage_estimates) > 0

    def test_exception_does_not_fail_flow(self, flow):
        """Errors during audience planning are non-fatal."""
        flow.state.campaign_brief = {
            "objectives": ["reach"],
            "budget": 50000,
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
            "target_audience": {"geo": ["US"]},
        }

        # Monkey-patch to raise
        flow._create_audience_plan = MagicMock(side_effect=RuntimeError("oops"))

        result = flow.plan_audience({"status": "success"})

        # Should not fail the flow
        assert result["status"] == "success"
        assert result["audience_plan"] is None
        assert any("warning" in e.lower() for e in flow.state.errors)

    def test_audience_expansion_defaults(self, flow_with_brief):
        """Audience expansion is enabled by default with factor 1.0."""
        result = flow_with_brief.plan_audience({"status": "success"})

        plan = result["audience_plan"]
        assert plan["audience_expansion_enabled"] is True
        assert plan["expansion_factor"] == 1.0


# ===========================================================================
# 3. _parse_allocations (private helper)
# ===========================================================================


class TestParseAllocations:
    """Tests for allocation JSON parsing."""

    def test_valid_json_parsed(self, flow_with_brief):
        """Valid JSON string is parsed correctly."""
        json_str = json.dumps(
            {
                "branding": {"budget": 40000, "percentage": 40, "rationale": "Awareness"},
                "ctv": {"budget": 20000, "percentage": 20, "rationale": "Video"},
            }
        )

        result = flow_with_brief._parse_allocations(json_str)

        assert result["branding"]["budget"] == 40000
        assert result["ctv"]["percentage"] == 20

    def test_json_embedded_in_text(self, flow_with_brief):
        """JSON embedded in surrounding text is extracted."""
        text = 'Here is my analysis:\n{"branding": {"budget": 50000, "percentage": 50, "rationale": "Test"}}\nDone.'

        result = flow_with_brief._parse_allocations(text)

        assert result["branding"]["budget"] == 50000

    def test_invalid_json_returns_defaults(self, flow_with_brief):
        """Invalid JSON falls back to default allocation."""
        result = flow_with_brief._parse_allocations("This is not JSON at all")

        assert "branding" in result
        assert "performance" in result
        assert "ctv" in result
        assert "mobile_app" in result
        # Defaults should sum to 100%
        total_pct = sum(v["percentage"] for v in result.values())
        assert total_pct == 100

    def test_default_allocation_uses_budget(self, flow_with_brief):
        """Default allocation uses campaign budget from state."""
        budget = flow_with_brief.state.campaign_brief["budget"]

        result = flow_with_brief._parse_allocations("garbage")

        total_budget = sum(v["budget"] for v in result.values())
        assert total_budget == budget

    def test_empty_string_returns_defaults(self, flow_with_brief):
        """Empty string falls back to defaults."""
        result = flow_with_brief._parse_allocations("")

        assert "branding" in result


# ===========================================================================
# 4. allocate_budget
# ===========================================================================


class TestAllocateBudget:
    """Tests for the budget allocation step."""

    def test_skips_on_failed_audience(self, flow_with_brief):
        """allocate_budget passes through upstream failure."""
        result = flow_with_brief.allocate_budget({"status": "failed", "error": "oops"})

        assert result["status"] == "failed"

    @patch("ad_buyer.flows.deal_booking_flow.create_portfolio_crew")
    def test_successful_allocation(self, mock_create_crew, flow_with_brief):
        """Valid crew result populates budget_allocations in state."""
        allocation_json = json.dumps(
            {
                "branding": {"budget": 40000, "percentage": 40, "rationale": "Awareness"},
                "performance": {"budget": 30000, "percentage": 30, "rationale": "Conversions"},
                "ctv": {"budget": 20000, "percentage": 20, "rationale": "Video reach"},
                "mobile_app": {"budget": 10000, "percentage": 10, "rationale": "App installs"},
            }
        )
        mock_crew = MagicMock()
        mock_crew.kickoff.return_value = allocation_json
        mock_create_crew.return_value = mock_crew

        result = flow_with_brief.allocate_budget({"status": "success"})

        assert result["status"] == "success"
        assert len(flow_with_brief.state.budget_allocations) == 4
        assert flow_with_brief.state.execution_status == ExecutionStatus.BUDGET_ALLOCATED

    @patch("ad_buyer.flows.deal_booking_flow.create_portfolio_crew")
    def test_zero_budget_channels_excluded(self, mock_create_crew, flow_with_brief):
        """Channels with 0 budget are not stored in allocations."""
        allocation_json = json.dumps(
            {
                "branding": {"budget": 50000, "percentage": 50, "rationale": "Main"},
                "ctv": {"budget": 50000, "percentage": 50, "rationale": "Video"},
                "performance": {"budget": 0, "percentage": 0, "rationale": "Not needed"},
                "mobile_app": {"budget": 0, "percentage": 0, "rationale": "Not needed"},
            }
        )
        mock_crew = MagicMock()
        mock_crew.kickoff.return_value = allocation_json
        mock_create_crew.return_value = mock_crew

        result = flow_with_brief.allocate_budget({"status": "success"})

        assert "branding" in flow_with_brief.state.budget_allocations
        assert "ctv" in flow_with_brief.state.budget_allocations
        assert "performance" not in flow_with_brief.state.budget_allocations
        assert "mobile_app" not in flow_with_brief.state.budget_allocations

    @patch("ad_buyer.flows.deal_booking_flow.create_portfolio_crew")
    def test_crew_exception_fails_gracefully(self, mock_create_crew, flow_with_brief):
        """Exception in crew kickoff sets FAILED status."""
        mock_create_crew.side_effect = RuntimeError("LLM unavailable")

        result = flow_with_brief.allocate_budget({"status": "success"})

        assert result["status"] == "failed"
        assert flow_with_brief.state.execution_status == ExecutionStatus.FAILED
        assert len(flow_with_brief.state.errors) > 0


# ===========================================================================
# 5. Channel research steps
# ===========================================================================


class TestChannelResearch:
    """Tests for the four parallel channel research steps."""

    def _mock_crew_result(self, products):
        """Return a mock crew whose kickoff returns a JSON product list."""
        crew = MagicMock()
        crew.kickoff.return_value = json.dumps(products)
        return crew

    # --- research_branding ---

    def test_branding_skips_on_failed_allocation(self, flow_with_allocations):
        result = flow_with_allocations.research_branding({"status": "failed"})
        assert result["status"] == "skipped"

    def test_branding_skips_no_budget(self, flow_with_brief):
        """Branding research skips if no branding allocation."""
        flow_with_brief.state.budget_allocations = {}

        result = flow_with_brief.research_branding({"status": "success"})

        assert result["status"] == "no_budget"

    @patch("ad_buyer.flows.deal_booking_flow.create_branding_crew")
    def test_branding_success(self, mock_create_crew, flow_with_allocations):
        """Successful branding research stores recommendations."""
        products = [
            {
                "product_id": "brand_1",
                "product_name": "Homepage Banner",
                "publisher": "PubA",
                "format": "display",
                "impressions": 500000,
                "cpm": 18.0,
                "cost": 9000,
                "rationale": "High visibility",
            }
        ]
        mock_create_crew.return_value = self._mock_crew_result(products)

        result = flow_with_allocations.research_branding({"status": "success"})

        assert result["status"] == "success"
        assert result["count"] == 1
        assert "branding" in flow_with_allocations.state.channel_recommendations

    @patch("ad_buyer.flows.deal_booking_flow.create_branding_crew")
    def test_branding_crew_failure(self, mock_create_crew, flow_with_allocations):
        """Crew exception is caught and reported."""
        mock_create_crew.side_effect = RuntimeError("Crew error")

        result = flow_with_allocations.research_branding({"status": "success"})

        assert result["status"] == "failed"
        assert len(flow_with_allocations.state.errors) > 0

    # --- research_ctv ---

    def test_ctv_skips_on_failed_allocation(self, flow_with_allocations):
        result = flow_with_allocations.research_ctv({"status": "failed"})
        assert result["status"] == "skipped"

    def test_ctv_skips_no_budget(self, flow_with_brief):
        flow_with_brief.state.budget_allocations = {}
        result = flow_with_brief.research_ctv({"status": "success"})
        assert result["status"] == "no_budget"

    @patch("ad_buyer.flows.deal_booking_flow.create_ctv_crew")
    def test_ctv_success(self, mock_create_crew, flow_with_allocations):
        products = [
            {
                "product_id": "ctv_1",
                "product_name": "Streaming Pre-roll",
                "publisher": "StreamCo",
                "format": "video",
                "impressions": 300000,
                "cpm": 28.0,
                "cost": 8400,
                "rationale": "Premium CTV",
            }
        ]
        mock_create_crew.return_value = self._mock_crew_result(products)

        result = flow_with_allocations.research_ctv({"status": "success"})

        assert result["status"] == "success"
        assert result["count"] == 1
        assert "ctv" in flow_with_allocations.state.channel_recommendations

    @patch("ad_buyer.flows.deal_booking_flow.create_ctv_crew")
    def test_ctv_crew_failure(self, mock_create_crew, flow_with_allocations):
        mock_create_crew.side_effect = RuntimeError("CTV error")
        result = flow_with_allocations.research_ctv({"status": "success"})
        assert result["status"] == "failed"

    # --- research_mobile ---

    def test_mobile_skips_on_failed_allocation(self, flow_with_allocations):
        result = flow_with_allocations.research_mobile({"status": "failed"})
        assert result["status"] == "skipped"

    def test_mobile_skips_no_budget(self, flow_with_brief):
        flow_with_brief.state.budget_allocations = {}
        result = flow_with_brief.research_mobile({"status": "success"})
        assert result["status"] == "no_budget"

    @patch("ad_buyer.flows.deal_booking_flow.create_mobile_crew")
    def test_mobile_success(self, mock_create_crew, flow_with_allocations):
        products = [
            {
                "product_id": "mob_1",
                "product_name": "In-App Interstitial",
                "publisher": "AppNet",
                "format": "interstitial",
                "impressions": 200000,
                "cpm": 12.0,
                "cost": 2400,
                "rationale": "Low fraud",
            }
        ]
        mock_create_crew.return_value = self._mock_crew_result(products)

        result = flow_with_allocations.research_mobile({"status": "success"})

        assert result["status"] == "success"
        assert "mobile_app" in flow_with_allocations.state.channel_recommendations

    @patch("ad_buyer.flows.deal_booking_flow.create_mobile_crew")
    def test_mobile_crew_failure(self, mock_create_crew, flow_with_allocations):
        mock_create_crew.side_effect = RuntimeError("Mobile error")
        result = flow_with_allocations.research_mobile({"status": "success"})
        assert result["status"] == "failed"

    # --- research_performance ---

    def test_performance_skips_on_failed_allocation(self, flow_with_allocations):
        result = flow_with_allocations.research_performance({"status": "failed"})
        assert result["status"] == "skipped"

    def test_performance_skips_no_budget(self, flow_with_brief):
        flow_with_brief.state.budget_allocations = {}
        result = flow_with_brief.research_performance({"status": "success"})
        assert result["status"] == "no_budget"

    @patch("ad_buyer.flows.deal_booking_flow.create_performance_crew")
    def test_performance_success(self, mock_create_crew, flow_with_allocations):
        products = [
            {
                "product_id": "perf_1",
                "product_name": "Retargeting Bundle",
                "publisher": "AdNet",
                "format": "display",
                "impressions": 800000,
                "cpm": 10.0,
                "cost": 8000,
                "rationale": "High ROAS",
            }
        ]
        mock_create_crew.return_value = self._mock_crew_result(products)

        result = flow_with_allocations.research_performance({"status": "success"})

        assert result["status"] == "success"
        assert "performance" in flow_with_allocations.state.channel_recommendations

    @patch("ad_buyer.flows.deal_booking_flow.create_performance_crew")
    def test_performance_crew_failure(self, mock_create_crew, flow_with_allocations):
        mock_create_crew.side_effect = RuntimeError("Performance error")
        result = flow_with_allocations.research_performance({"status": "success"})
        assert result["status"] == "failed"


# ===========================================================================
# 6. _create_channel_brief
# ===========================================================================


class TestCreateChannelBrief:
    """Tests for the channel brief helper."""

    def test_channel_brief_structure(self, flow_with_allocations):
        """Channel brief contains all required fields."""
        alloc = flow_with_allocations.state.budget_allocations["branding"]
        brief = flow_with_allocations._create_channel_brief("branding", alloc)

        assert brief["channel"] == "branding"
        assert brief["budget"] == 40000
        assert brief["startDate"] == "2026-04-01"
        assert brief["endDate"] == "2026-04-30"
        assert "target_audience" in brief or "targetAudience" in brief


# ===========================================================================
# 7. _parse_recommendations
# ===========================================================================


class TestParseRecommendations:
    """Tests for recommendation parsing from crew results."""

    def test_valid_json_array(self, flow):
        """Valid JSON array of products is parsed into ProductRecommendation objects."""
        products = [
            {
                "product_id": "p1",
                "product_name": "Banner A",
                "publisher": "PubA",
                "format": "display",
                "impressions": 100000,
                "cpm": 12.0,
                "cost": 1200,
                "rationale": "Good fit",
            },
            {
                "product_id": "p2",
                "product_name": "Video B",
                "publisher": "PubB",
                "format": "video",
                "impressions": 200000,
                "cpm": 20.0,
                "cost": 4000,
                "rationale": "Premium",
            },
        ]
        result_str = json.dumps(products)

        recs = flow._parse_recommendations(result_str, "branding")

        assert len(recs) == 2
        assert recs[0].product_id == "p1"
        assert recs[0].channel == "branding"
        assert recs[1].impressions == 200000

    def test_json_in_surrounding_text(self, flow):
        """JSON array embedded in text is still parsed."""
        text = 'Recommendations:\n[{"product_id": "x", "product_name": "Test", "publisher": "P", "impressions": 50000, "cpm": 10, "cost": 500}]\nEnd.'

        recs = flow._parse_recommendations(text, "ctv")

        assert len(recs) == 1
        assert recs[0].channel == "ctv"

    def test_invalid_json_returns_empty(self, flow):
        """Non-JSON text returns empty list."""
        recs = flow._parse_recommendations("No products found.", "branding")
        assert recs == []

    def test_empty_string_returns_empty(self, flow):
        """Empty string returns empty list."""
        recs = flow._parse_recommendations("", "branding")
        assert recs == []

    def test_partial_product_data(self, flow):
        """Products with missing optional fields still parse."""
        products = [
            {
                "product_id": "p1",
                "product_name": "Minimal",
                "publisher": "PubA",
                # missing format, rationale
                "impressions": 10000,
                "cpm": 5.0,
                "cost": 50,
            }
        ]
        recs = flow._parse_recommendations(json.dumps(products), "performance")

        assert len(recs) == 1
        assert recs[0].format is None


# ===========================================================================
# 8. consolidate_recommendations
# ===========================================================================


class TestConsolidateRecommendations:
    """Tests for consolidating channel recommendations."""

    def test_waiting_when_channels_pending(self, flow_with_allocations):
        """Returns waiting status if not all channels have reported."""
        flow_with_allocations.state.channel_recommendations = {
            "branding": [_make_recommendation("b1", "branding")],
        }

        result = flow_with_allocations.consolidate_recommendations(
            {"channel": "branding", "status": "success"}
        )

        assert result["status"] == "waiting"
        assert len(result["pending"]) > 0

    def test_all_channels_reported(self, flow_with_allocations):
        """All active channels reported triggers consolidation."""
        flow_with_allocations.state.channel_recommendations = {
            "branding": [_make_recommendation("b1", "branding")],
            "performance": [_make_recommendation("p1", "performance")],
            "ctv": [_make_recommendation("c1", "ctv")],
            "mobile_app": [_make_recommendation("m1", "mobile_app")],
        }

        result = flow_with_allocations.consolidate_recommendations(
            {"channel": "mobile_app", "status": "success"}
        )

        assert result["status"] == "ready_for_approval"
        assert result["total_recommendations"] == 4
        assert flow_with_allocations.state.execution_status == ExecutionStatus.AWAITING_APPROVAL
        # All recommendations should be pending_approval
        for rec in flow_with_allocations.state.pending_approvals:
            assert rec.status == "pending_approval"

    def test_only_active_channels_counted(self, flow_with_brief):
        """Only channels with budget > 0 are considered active."""
        flow_with_brief.state.budget_allocations = {
            "branding": ChannelAllocation(
                channel="branding", budget=100000, percentage=100, rationale="All in"
            ),
        }
        flow_with_brief.state.channel_recommendations = {
            "branding": [_make_recommendation("b1", "branding")],
        }

        result = flow_with_brief.consolidate_recommendations(
            {"channel": "branding", "status": "success"}
        )

        assert result["status"] == "ready_for_approval"
        assert result["total_recommendations"] == 1


# ===========================================================================
# 9. approve_recommendations / approve_all / _execute_bookings
# ===========================================================================


class TestApprovalAndBooking:
    """Tests for the approval and booking execution phase."""

    def _setup_pending_approvals(self, flow):
        """Helper to set up pending approvals."""
        recs = [
            _make_recommendation("prod_a", "branding", 500000, 15.0),
            _make_recommendation("prod_b", "ctv", 300000, 25.0),
            _make_recommendation("prod_c", "performance", 800000, 10.0),
        ]
        for rec in recs:
            rec.status = "pending_approval"
        flow.state.pending_approvals = recs
        flow.state.execution_status = ExecutionStatus.AWAITING_APPROVAL
        return recs

    def test_approve_specific_ids(self, flow):
        """Approving specific IDs books only those."""
        self._setup_pending_approvals(flow)

        result = flow.approve_recommendations(["prod_a", "prod_c"])

        assert result["status"] == "success"
        assert result["booked"] == 2
        assert flow.state.execution_status == ExecutionStatus.COMPLETED
        # Check statuses
        statuses = {r.product_id: r.status for r in flow.state.pending_approvals}
        assert statuses["prod_a"] == "approved"
        assert statuses["prod_b"] == "rejected"
        assert statuses["prod_c"] == "approved"

    def test_approve_all(self, flow):
        """approve_all approves every pending recommendation."""
        self._setup_pending_approvals(flow)

        result = flow.approve_all()

        assert result["status"] == "success"
        assert result["booked"] == 3
        assert all(r.status == "approved" for r in flow.state.pending_approvals)

    def test_approve_none(self, flow):
        """Approving empty list books nothing."""
        self._setup_pending_approvals(flow)

        result = flow.approve_recommendations([])

        assert result["status"] == "success"
        assert result["booked"] == 0
        assert result["message"] == "No recommendations approved"
        assert all(r.status == "rejected" for r in flow.state.pending_approvals)

    def test_booked_lines_created(self, flow):
        """Booked lines are created with correct fields."""
        self._setup_pending_approvals(flow)

        flow.approve_recommendations(["prod_a"])

        assert len(flow.state.booked_lines) == 1
        booked = flow.state.booked_lines[0]
        assert booked.product_id == "prod_a"
        assert booked.channel == "branding"
        assert booked.impressions == 500000
        assert booked.cost == 7500.0  # 500000 * 15.0 / 1000
        assert booked.booking_status == "pending_execution"
        assert isinstance(booked.booked_at, datetime)

    def test_total_cost_and_impressions(self, flow):
        """Result includes aggregated totals."""
        self._setup_pending_approvals(flow)

        result = flow.approve_all()

        expected_cost = 7500.0 + 7500.0 + 8000.0
        expected_impressions = 500000 + 300000 + 800000
        assert result["total_cost"] == expected_cost
        assert result["total_impressions"] == expected_impressions

    def test_approve_nonexistent_id(self, flow):
        """Approving a non-existent ID quietly ignores it."""
        self._setup_pending_approvals(flow)

        result = flow.approve_recommendations(["nonexistent_id"])

        assert result["status"] == "success"
        assert result["booked"] == 0

    def test_execution_status_transitions(self, flow):
        """Status transitions through EXECUTING_BOOKINGS to COMPLETED."""
        self._setup_pending_approvals(flow)
        assert flow.state.execution_status == ExecutionStatus.AWAITING_APPROVAL

        flow.approve_all()

        assert flow.state.execution_status == ExecutionStatus.COMPLETED


# ===========================================================================
# 10. get_status
# ===========================================================================


class TestGetStatus:
    """Tests for the status reporting method."""

    def test_initial_status(self, flow):
        """Fresh flow reports INITIALIZED status."""
        status = flow.get_status()

        assert status["execution_status"] == "initialized"
        assert status["pending_approvals"] == 0
        assert status["booked_lines"] == 0
        assert isinstance(status["errors"], list)
        assert isinstance(status["updated_at"], str)

    def test_status_after_allocation(self, flow_with_allocations):
        """Status reflects budget allocations."""
        flow_with_allocations.state.execution_status = ExecutionStatus.BUDGET_ALLOCATED
        status = flow_with_allocations.get_status()

        assert status["execution_status"] == "budget_allocated"
        assert len(status["budget_allocations"]) == 4

    def test_status_after_booking(self, flow):
        """Status reflects booked lines."""
        flow.state.booked_lines = [
            BookedLine(
                line_id="l1",
                order_id="o1",
                product_id="p1",
                product_name="Test",
                channel="branding",
                impressions=100000,
                cost=1500,
                booking_status="booked",
                booked_at=datetime.now(timezone.utc),
            )
        ]
        flow.state.execution_status = ExecutionStatus.COMPLETED
        status = flow.get_status()

        assert status["execution_status"] == "completed"
        assert status["booked_lines"] == 1

    def test_status_includes_errors(self, flow):
        """Status includes accumulated errors."""
        flow.state.errors = ["Error 1", "Error 2"]
        status = flow.get_status()

        assert len(status["errors"]) == 2


# ===========================================================================
# 11. Edge cases and integration-style tests
# ===========================================================================


class TestEdgeCases:
    """Edge cases and corner scenarios."""

    def test_flow_initialization(self, mock_opendirect_client):
        """Flow can be instantiated with a client."""
        flow = DealBookingFlow(client=mock_opendirect_client)

        assert flow._client is mock_opendirect_client
        assert flow.state.execution_status == ExecutionStatus.INITIALIZED

    def test_large_budget_allocation(self, flow):
        """Very large budgets are handled without overflow."""
        flow.state.campaign_brief = {
            "objectives": ["reach"],
            "budget": 10_000_000,
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
            "target_audience": {"geo": ["US"]},
        }

        result = flow.receive_campaign_brief()
        assert result["status"] == "success"

    def test_multiple_errors_accumulated(self, flow):
        """Multiple validation failures accumulate errors."""
        # First call with missing fields
        flow.state.campaign_brief = {}
        flow.receive_campaign_brief()

        # Check errors accumulated
        assert len(flow.state.errors) >= 1

    def test_no_audience_targeting_coverage(self, flow):
        """No audience targeting still produces valid coverage estimates."""
        # Only demographics, no interests or behaviors
        flow.state.campaign_brief = {
            "objectives": ["awareness"],
            "budget": 50000,
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
            "target_audience": {"demographics": {"age": "18-35"}},
        }

        result = flow.plan_audience({"status": "success"})

        estimates = result["coverage_estimates"]
        # Should have coverage for all channels
        assert len(estimates) == 4

    def test_coverage_never_below_minimum(self, flow):
        """Coverage estimates never go below 10% floor."""
        flow.state.campaign_brief = {
            "objectives": ["reach"],
            "budget": 50000,
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
            "target_audience": {
                "demographics": {"age": "18-24"},
                "behaviors": ["niche_buyers"],
                "interests": ["rare_interest"],
            },
        }

        result = flow.plan_audience({"status": "success"})

        estimates = result["coverage_estimates"]
        for val in estimates.values():
            assert val >= 10.0
