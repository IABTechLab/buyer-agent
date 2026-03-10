# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Integration tests: end-to-end deal booking flow.

Tests the DealBookingFlow from campaign brief reception through
budget allocation, audience planning, and recommendation consolidation.
Mocks CrewAI crews but exercises real flow state management and
module interactions.
"""

import json
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from ad_buyer.clients.opendirect_client import OpenDirectClient
from ad_buyer.flows.deal_booking_flow import DealBookingFlow
from ad_buyer.models.flow_state import (
    BookedLine,
    BookingState,
    ChannelAllocation,
    ExecutionStatus,
    ProductRecommendation,
)


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
        _set_flow_brief(flow, {
            "objectives": ["reach"],
            "budget": 0,
            "start_date": "2025-03-01",
            "end_date": "2025-03-31",
            "target_audience": {"geo": ["US"]},
        })

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
        _set_flow_brief(flow, {
            "objectives": ["reach"],
            "budget": 50000,
            "start_date": "2025-03-01",
            "end_date": "2025-03-31",
            "target_audience": {},
        })

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
        crew_result = json.dumps({
            "branding": {"budget": 40000, "percentage": 40, "rationale": "Display for awareness"},
            "performance": {"budget": 35000, "percentage": 35, "rationale": "SEM and remarketing"},
            "ctv": {"budget": 25000, "percentage": 25, "rationale": "CTV for reach"},
            "mobile_app": {"budget": 0, "percentage": 0, "rationale": "Not needed"},
        })

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
        self, campaign_brief: dict
    ) -> DealBookingFlow:
        """Create a flow with pre-set budget allocations."""
        client = OpenDirectClient(base_url="http://fake.test")
        flow = DealBookingFlow(client)
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

    def test_approve_specific_recommendations(self, sample_campaign_brief: dict):
        """Approving specific products should book only those."""
        flow = self._make_flow_with_allocations(sample_campaign_brief)

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

        result = flow.approve_recommendations(["prod_1"])

        assert result["status"] == "success"
        assert result["booked"] == 1
        assert len(flow.state.booked_lines) == 1
        assert flow.state.booked_lines[0].product_id == "prod_1"
        assert flow.state.execution_status == ExecutionStatus.COMPLETED

    def test_approve_all_recommendations(self, sample_campaign_brief: dict):
        """approve_all should book all pending recommendations."""
        flow = self._make_flow_with_allocations(sample_campaign_brief)

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
