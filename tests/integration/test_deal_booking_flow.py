# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Integration tests: end-to-end deal booking flow.

Tests the DealBookingFlow from campaign brief reception through
budget allocation, audience planning, and recommendation consolidation.
Mocks CrewAI crews but exercises real flow state management and
module interactions.
"""

import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ad_buyer.clients.opendirect_client import OpenDirectClient
from ad_buyer.flows.deal_booking_flow import DealBookingFlow
from ad_buyer.models.flow_state import (
    ChannelAllocation,
    ExecutionStatus,
    ProductRecommendation,
)

# Faithful shape of the branding crew's real final output from paid rig
# run #13 (2026-07-19, brand-direct-real.log ~3540-3690): prose, then a
# ```json fence containing an OBJECT whose "recommendations" key holds the
# array, then trailing prose. Abbreviated but structurally identical.
REAL_CREW_OUTPUT = """Based on my comprehensive review of the research findings, I'm presenting \
my final recommendations for this branding campaign:

```json
{
    "recommendations": [
        {
            "priority": 1,
            "product_id": "prod-b41e2339",
            "product_name": "Premium Display - Homepage",
            "publisher": "seller-premium-pub-001",
            "format": "Display - Banner",
            "impressions": 1666666,
            "cpm": 15.00,
            "cost": 24999.99,
            "rationale": "Optimal choice for the branding campaign."
        },
        {
            "priority": 2,
            "product_id": "prod-6fa6d961",
            "product_name": "Standard Display - ROS",
            "publisher": "seller-premium-pub-001",
            "format": "Display - Banner",
            "impressions": 3125000,
            "cpm": 8.00,
            "cost": 25000.00,
            "rationale": "Alternative option that maximizes impression volume."
        },
        {
            "priority": 3,
            "product_id": "prod-d22919d3",
            "product_name": "Pre-Roll Video",
            "publisher": "seller-premium-pub-001",
            "format": "Video",
            "impressions": 1000000,
            "cpm": 25.00,
            "cost": 25000.00,
            "rationale": "Premium video placement exceeds the CPM ceiling."
        }
    ],
    "total_impressions": 1666666,
    "total_cost": 24999.99,
    "summary": "FINAL RECOMMENDATION: Book prod-b41e2339."
}
```

**CRITICAL NOTES:**

1. **Inventory Scarcity**: The marketplace research revealed extremely
limited programmatic guaranteed inventory.

This recommendation prioritizes campaign quality and alignment with
branding objectives.
"""


def _set_flow_brief(flow: DealBookingFlow, campaign_brief: dict) -> None:
    """Set campaign_brief on a flow's state (CrewAI Flow.state is read-only)."""
    flow.state.campaign_brief = campaign_brief


class TestDealBookingFlowValidation:
    """Tests campaign brief validation at the flow entry point."""

    def test_valid_brief_sets_received_status(self, sample_campaign_brief: dict):
        """Valid brief should transition to BRIEF_RECEIVED status."""
        client = OpenDirectClient(base_url="http://fake.test")
        flow = DealBookingFlow(client)
        _set_flow_brief(flow, sample_campaign_brief)

        result = flow.receive_campaign_brief()

        assert result["status"] == "success"
        assert flow.state.execution_status == ExecutionStatus.BRIEF_RECEIVED
        assert len(flow.state.errors) == 0

    def test_missing_fields_sets_validation_failed(self):
        """Brief with missing required fields should fail validation."""
        client = OpenDirectClient(base_url="http://fake.test")
        flow = DealBookingFlow(client)
        _set_flow_brief(flow, {"name": "Incomplete", "budget": 50000})

        result = flow.receive_campaign_brief()

        assert result["status"] == "failed"
        assert flow.state.execution_status == ExecutionStatus.VALIDATION_FAILED
        assert len(flow.state.errors) > 0
        assert "Missing required fields" in flow.state.errors[0]

    def test_zero_budget_fails_validation(self):
        """Brief with zero budget should fail validation."""
        client = OpenDirectClient(base_url="http://fake.test")
        flow = DealBookingFlow(client)
        _set_flow_brief(
            flow,
            {
                "objectives": ["reach"],
                "budget": 0,
                "start_date": "2025-03-01",
                "end_date": "2025-03-31",
                "target_audience": {"geo": ["US"]},
            },
        )

        result = flow.receive_campaign_brief()

        assert result["status"] == "failed"
        assert flow.state.execution_status == ExecutionStatus.VALIDATION_FAILED
        assert "Budget must be greater than 0" in flow.state.errors[0]


class TestAudiencePlanningIntegration:
    """Tests audience planning step integrated with flow state."""

    def test_audience_planning_with_targeting(self, sample_campaign_brief: dict):
        """Audience planning should generate coverage estimates and gaps."""
        client = OpenDirectClient(base_url="http://fake.test")
        flow = DealBookingFlow(client)
        _set_flow_brief(flow, sample_campaign_brief)

        # Run brief reception first
        brief_result = flow.receive_campaign_brief()
        assert brief_result["status"] == "success"

        # Run audience planning
        audience_result = flow.plan_audience(brief_result)

        assert audience_result["status"] == "success"
        assert flow.state.audience_coverage_estimates is not None
        # Coverage estimates should be per channel
        for channel in ["branding", "ctv", "mobile_app", "performance"]:
            assert channel in flow.state.audience_coverage_estimates

    def test_audience_planning_skips_when_no_targeting(self):
        """No target_audience should skip audience planning gracefully."""
        client = OpenDirectClient(base_url="http://fake.test")
        flow = DealBookingFlow(client)
        _set_flow_brief(
            flow,
            {
                "objectives": ["reach"],
                "budget": 50000,
                "start_date": "2025-03-01",
                "end_date": "2025-03-31",
                "target_audience": {},
            },
        )

        brief_result = flow.receive_campaign_brief()
        audience_result = flow.plan_audience(brief_result)

        assert audience_result["status"] == "success"
        assert audience_result["audience_plan"] is None

    def test_audience_planning_propagates_failure_from_brief(self):
        """Failed brief validation should propagate through audience planning."""
        client = OpenDirectClient(base_url="http://fake.test")
        flow = DealBookingFlow(client)
        _set_flow_brief(flow, {"name": "Bad"})

        brief_result = flow.receive_campaign_brief()
        assert brief_result["status"] == "failed"

        audience_result = flow.plan_audience(brief_result)
        assert audience_result["status"] == "failed"


class TestBudgetAllocationIntegration:
    """Tests budget allocation with mocked CrewAI portfolio crew."""

    def test_budget_allocation_with_crew_result(self, sample_campaign_brief: dict):
        """Budget allocation should parse crew result into ChannelAllocations."""
        client = OpenDirectClient(base_url="http://fake.test")
        flow = DealBookingFlow(client)
        _set_flow_brief(flow, sample_campaign_brief)

        # Mock the portfolio crew to return JSON allocations
        crew_result = json.dumps(
            {
                "branding": {
                    "budget": 40000,
                    "percentage": 40,
                    "rationale": "Display for awareness",
                },
                "performance": {
                    "budget": 35000,
                    "percentage": 35,
                    "rationale": "SEM and remarketing",
                },
                "ctv": {"budget": 25000, "percentage": 25, "rationale": "CTV for reach"},
                "mobile_app": {"budget": 0, "percentage": 0, "rationale": "Not needed"},
            }
        )

        mock_crew = MagicMock()
        mock_crew.kickoff.return_value = crew_result

        with patch(
            "ad_buyer.flows.deal_booking_flow.create_portfolio_crew",
            return_value=mock_crew,
        ):
            # Must go through brief and audience first
            brief_result = flow.receive_campaign_brief()
            audience_result = flow.plan_audience(brief_result)
            alloc_result = flow.allocate_budget(audience_result)

        assert alloc_result["status"] == "success"
        assert flow.state.execution_status == ExecutionStatus.BUDGET_ALLOCATED

        # Check allocations were stored correctly
        assert "branding" in flow.state.budget_allocations
        assert flow.state.budget_allocations["branding"].budget == 40000
        assert flow.state.budget_allocations["branding"].percentage == 40

        # Zero-budget channels should not be allocated
        assert "mobile_app" not in flow.state.budget_allocations

    def test_budget_allocation_default_fallback(self, sample_campaign_brief: dict):
        """When crew returns unparseable result, default allocation should be used."""
        client = OpenDirectClient(base_url="http://fake.test")
        flow = DealBookingFlow(client)
        _set_flow_brief(flow, sample_campaign_brief)

        mock_crew = MagicMock()
        mock_crew.kickoff.return_value = "I recommend a balanced approach."  # No JSON

        with patch(
            "ad_buyer.flows.deal_booking_flow.create_portfolio_crew",
            return_value=mock_crew,
        ):
            brief_result = flow.receive_campaign_brief()
            audience_result = flow.plan_audience(brief_result)
            alloc_result = flow.allocate_budget(audience_result)

        assert alloc_result["status"] == "success"
        # Default allocation: 40% branding, 40% performance, 20% ctv
        assert "branding" in flow.state.budget_allocations
        assert "performance" in flow.state.budget_allocations
        assert "ctv" in flow.state.budget_allocations


class TestRecommendationConsolidation:
    """Tests recommendation consolidation and approval flow."""

    def _make_flow_with_allocations(
        self, campaign_brief: dict, orchestrator=None
    ) -> DealBookingFlow:
        """Create a flow with pre-set budget allocations."""
        client = OpenDirectClient(base_url="http://fake.test")
        flow = DealBookingFlow(client, orchestrator=orchestrator)
        _set_flow_brief(flow, campaign_brief)

        # Pre-set allocations
        flow.state.budget_allocations["branding"] = ChannelAllocation(
            channel="branding", budget=40000, percentage=40, rationale="Display"
        )
        flow.state.budget_allocations["ctv"] = ChannelAllocation(
            channel="ctv", budget=25000, percentage=25, rationale="CTV"
        )
        flow.state.execution_status = ExecutionStatus.BUDGET_ALLOCATED
        return flow

    def test_consolidation_waits_for_all_channels(self, sample_campaign_brief: dict):
        """Consolidation should wait until all active channels report."""
        flow = self._make_flow_with_allocations(sample_campaign_brief)

        # Only branding has reported
        flow.state.channel_recommendations["branding"] = [
            ProductRecommendation(
                product_id="prod_1",
                product_name="Banner Ad",
                publisher="Publisher A",
                channel="branding",
                impressions=500_000,
                cpm=12.0,
                cost=6000,
            )
        ]

        result = flow.consolidate_recommendations({"channel": "branding", "status": "success"})
        assert result["status"] == "waiting"
        assert "ctv" in result["pending"]

    def test_consolidation_completes_when_all_report(self, sample_campaign_brief: dict):
        """Consolidation should complete when all channels have reported."""
        flow = self._make_flow_with_allocations(sample_campaign_brief)

        # Both channels have reported
        flow.state.channel_recommendations["branding"] = [
            ProductRecommendation(
                product_id="prod_1",
                product_name="Banner Ad",
                publisher="Publisher A",
                channel="branding",
                impressions=500_000,
                cpm=12.0,
                cost=6000,
            )
        ]
        flow.state.channel_recommendations["ctv"] = [
            ProductRecommendation(
                product_id="prod_2",
                product_name="CTV Spot",
                publisher="Publisher B",
                channel="ctv",
                impressions=200_000,
                cpm=30.0,
                cost=6000,
            )
        ]

        result = flow.consolidate_recommendations({"channel": "ctv", "status": "success"})
        assert result["status"] == "ready_for_approval"
        assert result["total_recommendations"] == 2
        assert flow.state.execution_status == ExecutionStatus.AWAITING_APPROVAL

    def test_approve_specific_recommendations(
        self, sample_campaign_brief: dict, fake_booking_orchestrator
    ):
        """Approving specific products should book only those."""
        flow = self._make_flow_with_allocations(
            sample_campaign_brief, orchestrator=fake_booking_orchestrator
        )

        recs = [
            ProductRecommendation(
                product_id="prod_1",
                product_name="Banner Ad",
                publisher="Publisher A",
                channel="branding",
                impressions=500_000,
                cpm=12.0,
                cost=6000,
            ),
            ProductRecommendation(
                product_id="prod_2",
                product_name="CTV Spot",
                publisher="Publisher B",
                channel="ctv",
                impressions=200_000,
                cpm=30.0,
                cost=6000,
            ),
        ]
        flow.state.pending_approvals = recs

        mock_rv = ("quote_1", "deal_1", "order_1")
        with patch.object(flow, "_book_via_seller_api", return_value=mock_rv):
            result = flow.approve_recommendations(["prod_1"])

        assert result["status"] == "success"
        assert result["booked"] == 1
        assert len(flow.state.booked_lines) == 1
        assert flow.state.booked_lines[0].product_id == "prod_1"
        assert flow.state.execution_status == ExecutionStatus.COMPLETED

    def test_approve_all_recommendations(
        self, sample_campaign_brief: dict, fake_booking_orchestrator
    ):
        """approve_all should book all pending recommendations."""
        flow = self._make_flow_with_allocations(
            sample_campaign_brief, orchestrator=fake_booking_orchestrator
        )

        recs = [
            ProductRecommendation(
                product_id="prod_1",
                product_name="Banner",
                publisher="Pub A",
                channel="branding",
                impressions=500_000,
                cpm=12.0,
                cost=6000,
            ),
            ProductRecommendation(
                product_id="prod_2",
                product_name="CTV",
                publisher="Pub B",
                channel="ctv",
                impressions=200_000,
                cpm=30.0,
                cost=6000,
            ),
        ]
        flow.state.pending_approvals = recs

        mock_rv = ("quote_1", "deal_1", "order_1")
        with patch.object(flow, "_book_via_seller_api", return_value=mock_rv):
            result = flow.approve_all()

        assert result["status"] == "success"
        assert result["booked"] == 2
        assert result["total_impressions"] == 700_000
        assert result["total_cost"] == 12000

    def test_approve_none_completes_with_zero_bookings(self, sample_campaign_brief: dict):
        """Approving an empty list should complete with zero bookings."""
        flow = self._make_flow_with_allocations(sample_campaign_brief)
        flow.state.pending_approvals = [
            ProductRecommendation(
                product_id="prod_1",
                product_name="Banner",
                publisher="Pub A",
                channel="branding",
                impressions=500_000,
                cpm=12.0,
                cost=6000,
            ),
        ]

        result = flow.approve_recommendations([])  # Empty list

        assert result["status"] == "success"
        assert result["booked"] == 0
        assert flow.state.execution_status == ExecutionStatus.COMPLETED


def _branding_only_portfolio_output() -> SimpleNamespace:
    """CrewOutput-shaped portfolio result funding ONLY the branding channel.

    ``_extract_allocations`` reads ``tasks_output[0].json_dict`` (the typed
    first-task output); a bare string would fall through to the default
    40/40/20 split and fund channels this test must keep unfunded.
    """
    allocations = {
        "branding": {"budget": 25000, "percentage": 100, "rationale": "Brand direct"},
        "performance": {"budget": 0, "percentage": 0, "rationale": "Not allocated"},
        "ctv": {"budget": 0, "percentage": 0, "rationale": "Not allocated"},
        "mobile_app": {"budget": 0, "percentage": 0, "rationale": "Not allocated"},
    }
    first_task = SimpleNamespace(pydantic=None, json_dict=allocations, raw="")
    return SimpleNamespace(tasks_output=[first_task], raw="")


class TestKickoffConsolidationHandoff:
    """kickoff()-level reproduction of the real-mode no_booking bug (ar-h2o6).

    The unit tests above call the flow methods directly and never exercise
    the CrewAI flow ENGINE. In CrewAI >=1.14 the engine (a) treats the four
    ``research_*`` methods as a racing group for the multi-source ``or_``
    consolidate listener -- once the FIRST one completes the others are
    CANCELLED -- and (b) fires a multi-source OR listener only ONCE per run.
    A fast no-budget channel therefore wins the race while the real crew is
    still researching; ``consolidate_recommendations`` fires exactly once,
    sees the funded channel still pending, returns "waiting", and never
    fires again. ``pending_approvals`` stays empty, approval approves an
    empty set, and the job "completes" with nothing booked.
    """

    def _kickoff_with_slow_branding_crew(self, brief: dict) -> DealBookingFlow:
        """Run a real flow.kickoff() with mocked crews.

        Portfolio crew allocates 100% to branding; the branding crew takes
        ~0.5s (like a real LLM crew, which takes minutes) so every
        no-budget channel research method finishes first.
        """
        portfolio_crew = MagicMock()
        portfolio_crew.kickoff.return_value = _branding_only_portfolio_output()

        def _slow_branding_kickoff():
            time.sleep(0.5)
            return REAL_CREW_OUTPUT

        branding_crew = MagicMock()
        branding_crew.kickoff.side_effect = _slow_branding_kickoff

        client = OpenDirectClient(base_url="http://fake.test")
        flow = DealBookingFlow(client, campaign_brief=brief)

        with (
            patch(
                "ad_buyer.flows.deal_booking_flow.create_portfolio_crew",
                return_value=portfolio_crew,
            ),
            patch(
                "ad_buyer.flows.deal_booking_flow.create_branding_crew",
                return_value=branding_crew,
            ),
        ):
            flow.kickoff()
        return flow

    def test_kickoff_slow_funded_channel_reaches_pending_approvals(
        self, sample_campaign_brief: dict
    ):
        """The funded channel's recommendations MUST become pending approvals.

        Failing-first proof of the ar-h2o6 root cause: with the or_
        consolidate trigger, this kickoff ends with pending_approvals == []
        because a no-budget channel wins the racing group and the OR
        listener has already fired by the time branding research lands.
        """
        brief = dict(sample_campaign_brief)
        brief["budget"] = 25000

        flow = self._kickoff_with_slow_branding_crew(brief)

        assert flow.state.channel_recommendations.get("branding"), (
            "branding research results never landed in state"
        )
        assert len(flow.state.pending_approvals) == 3, (
            f"research recommendations never became pending approvals (errors={flow.state.errors})"
        )
        assert flow.state.execution_status == ExecutionStatus.AWAITING_APPROVAL
        for rec in flow.state.pending_approvals:
            assert rec.status == "pending_approval"

    def test_kickoff_then_approval_books_the_recommendation(
        self, sample_campaign_brief: dict, fake_booking_orchestrator
    ):
        """Approving after kickoff must book, not silently approve nothing."""
        brief = dict(sample_campaign_brief)
        brief["budget"] = 25000

        portfolio_crew = MagicMock()
        portfolio_crew.kickoff.return_value = _branding_only_portfolio_output()

        def _slow_branding_kickoff():
            time.sleep(0.5)
            return REAL_CREW_OUTPUT

        branding_crew = MagicMock()
        branding_crew.kickoff.side_effect = _slow_branding_kickoff

        client = OpenDirectClient(base_url="http://fake.test")
        flow = DealBookingFlow(client, orchestrator=fake_booking_orchestrator, campaign_brief=brief)

        with (
            patch(
                "ad_buyer.flows.deal_booking_flow.create_portfolio_crew",
                return_value=portfolio_crew,
            ),
            patch(
                "ad_buyer.flows.deal_booking_flow.create_branding_crew",
                return_value=branding_crew,
            ),
        ):
            flow.kickoff()
            # Approve the affordable top pick (approving all three would trip
            # the deterministic spend ceiling, which is correct behavior).
            result = flow.approve_recommendations(["prod-b41e2339"])

        assert result["booked"] > 0, f"approval booked nothing: {result}"
        assert flow.state.booked_lines


class TestFlowStatusTracking:
    """Tests flow status reporting across the pipeline."""

    def test_get_status_reflects_current_state(self, sample_campaign_brief: dict):
        """get_status should accurately reflect the flow's current state."""
        client = OpenDirectClient(base_url="http://fake.test")
        flow = DealBookingFlow(client)
        _set_flow_brief(flow, sample_campaign_brief)

        # Initial status
        status = flow.get_status()
        assert status["execution_status"] == "initialized"
        assert status["booked_lines"] == 0

        # After brief reception
        flow.receive_campaign_brief()
        status = flow.get_status()
        assert status["execution_status"] == "brief_received"

        # Manually set some state for testing
        flow.state.budget_allocations["branding"] = ChannelAllocation(
            channel="branding", budget=40000, percentage=40, rationale="Test"
        )
        flow.state.execution_status = ExecutionStatus.BUDGET_ALLOCATED
        status = flow.get_status()
        assert status["execution_status"] == "budget_allocated"
        assert "branding" in status["budget_allocations"]
