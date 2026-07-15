# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the deterministic spend-ceiling guard on money-committing paths.

Covers:
- The pure enforce_spend_ceiling guard (CPM ceiling, budget ceiling,
  at-limit passes, fail-open on missing limits).
- RequestDealTool: a deal whose computed final CPM exceeds max_cpm is
  rejected with a structured error and NO Deal ID is minted.
- DealBookingFlow._execute_bookings: a booking whose total cost would
  exceed the campaign budget is rejected before any line is booked.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from ad_buyer.booking import SpendCeilingExceeded, enforce_spend_ceiling
from ad_buyer.flows.deal_booking_flow import DealBookingFlow
from ad_buyer.models.buyer_identity import BuyerContext, BuyerIdentity
from ad_buyer.models.flow_state import ExecutionStatus, ProductRecommendation
from ad_buyer.tools.buyer_deals import RequestDealTool

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client():
    """Create a mock UnifiedClient."""
    client = MagicMock()
    client.get_product = AsyncMock()
    return client


@pytest.fixture
def agency_context():
    """Create an agency tier buyer context."""
    identity = BuyerIdentity(
        seat_id="ttd-seat-123",
        agency_id="omnicom-456",
        agency_name="OMD",
    )
    return BuyerContext(identity=identity, is_authenticated=True)


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
# 1. Pure guard: enforce_spend_ceiling
# ===========================================================================


class TestEnforceSpendCeiling:
    """Tests for the pure enforce_spend_ceiling guard function."""

    def test_cpm_over_ceiling_raises(self):
        """Final CPM above max_cpm raises SpendCeilingExceeded."""
        with pytest.raises(SpendCeilingExceeded) as exc_info:
            enforce_spend_ceiling(final_cpm=170.0, max_cpm=20.0)

        assert exc_info.value.final_cpm == 170.0
        assert exc_info.value.max_cpm == 20.0
        assert "CPM" in str(exc_info.value)

    def test_cost_over_budget_raises(self):
        """Total cost above budget raises SpendCeilingExceeded."""
        with pytest.raises(SpendCeilingExceeded) as exc_info:
            enforce_spend_ceiling(total_cost=7500.0, budget=5000.0)

        assert exc_info.value.total_cost == 7500.0
        assert exc_info.value.budget == 5000.0
        assert "budget" in str(exc_info.value).lower()

    def test_at_cpm_limit_passes(self):
        """Final CPM exactly at max_cpm is allowed (at-or-under)."""
        enforce_spend_ceiling(final_cpm=20.0, max_cpm=20.0)

    def test_under_cpm_limit_passes(self):
        """Final CPM below max_cpm is allowed."""
        enforce_spend_ceiling(final_cpm=17.0, max_cpm=20.0)

    def test_at_budget_limit_passes(self):
        """Total cost exactly at budget is allowed."""
        enforce_spend_ceiling(total_cost=5000.0, budget=5000.0)

    def test_both_limits_checked(self):
        """Budget breach raises even when CPM is under its ceiling."""
        with pytest.raises(SpendCeilingExceeded):
            enforce_spend_ceiling(
                final_cpm=10.0,
                total_cost=99999.0,
                max_cpm=20.0,
                budget=5000.0,
            )

    def test_missing_limits_fail_open_with_warning(self, caplog):
        """No limits at all: fail-open (allow) but log a warning.

        This is an explicit choice to preserve current demo behavior for
        callers that never supplied max_cpm/budget.
        """
        with caplog.at_level(logging.WARNING):
            enforce_spend_ceiling(final_cpm=170.0, total_cost=99999.0)

        assert any("ceiling" in rec.message.lower() for rec in caplog.records)

    def test_missing_actuals_pass(self):
        """Limits set but no actuals to compare: nothing to enforce."""
        enforce_spend_ceiling(max_cpm=20.0, budget=5000.0)


# ===========================================================================
# 2. RequestDealTool: CPM ceiling at Deal ID mint time
# ===========================================================================


class TestRequestDealCeiling:
    """RequestDealTool must refuse to mint a Deal ID above max_cpm."""

    def _product(self, base_price):
        return MagicMock(
            success=True,
            data={"id": "prod_1", "name": "Test Product", "basePrice": base_price},
        )

    @pytest.mark.asyncio
    async def test_deal_over_max_cpm_rejected(self, mock_client, agency_context):
        """Seller basePrice 200 vs max_cpm 20: rejected, no Deal ID minted."""
        mock_client.get_product.return_value = self._product(200.0)

        tool = RequestDealTool(
            client=mock_client,
            buyer_context=agency_context,
            max_cpm=20.0,
        )

        result = await tool._arun(
            product_id="prod_1",
            deal_type="PD",
            impressions=1_000_000,
        )

        assert "DEAL-" not in result  # no Deal ID was minted
        assert "REJECTED" in result
        assert "20.00" in result  # the ceiling is reported

    @pytest.mark.asyncio
    async def test_deal_under_max_cpm_books(self, mock_client, agency_context):
        """Final CPM under the ceiling books normally (no regression)."""
        # Agency tier: 10% discount -> $20 * 0.9 = $18.00 <= 20.0
        mock_client.get_product.return_value = self._product(20.0)

        tool = RequestDealTool(
            client=mock_client,
            buyer_context=agency_context,
            max_cpm=20.0,
        )

        result = await tool._arun(product_id="prod_1", deal_type="PD")

        assert "DEAL-" in result
        assert "REJECTED" not in result

    @pytest.mark.asyncio
    async def test_deal_at_max_cpm_books(self, mock_client, agency_context):
        """Final CPM exactly at the ceiling books normally."""
        # Agency tier: 10% discount -> $20 * 0.9 = $18.00 == max_cpm 18.0
        mock_client.get_product.return_value = self._product(20.0)

        tool = RequestDealTool(
            client=mock_client,
            buyer_context=agency_context,
            max_cpm=18.0,
        )

        result = await tool._arun(product_id="prod_1", deal_type="PD")

        assert "DEAL-" in result

    @pytest.mark.asyncio
    async def test_no_max_cpm_fails_open(self, mock_client, agency_context):
        """max_cpm=None: fail-open, deal books (current demo behavior)."""
        mock_client.get_product.return_value = self._product(200.0)

        tool = RequestDealTool(
            client=mock_client,
            buyer_context=agency_context,
        )

        result = await tool._arun(product_id="prod_1", deal_type="PD")

        assert "DEAL-" in result


# ===========================================================================
# 3. DealBookingFlow._execute_bookings: budget ceiling
# ===========================================================================


class TestBookingBudgetCeiling:
    """_execute_bookings must refuse bookings whose total exceeds budget."""

    def _flow_with_approved(self, budget, recs):
        flow = DealBookingFlow(client=MagicMock())
        brief = {
            "objectives": ["reach"],
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
            "target_audience": {"geo": ["US"]},
        }
        if budget is not None:
            brief["budget"] = budget
        flow.state.campaign_brief = brief
        for rec in recs:
            rec.status = "approved"
        flow.state.pending_approvals = recs
        flow.state.execution_status = ExecutionStatus.EXECUTING_BOOKINGS
        return flow

    def test_booking_over_budget_rejected(self):
        """Total cost 7500 vs budget 5000: rejected, nothing booked."""
        recs = [_make_recommendation("prod_a", "branding", 500000, 15.0)]  # cost 7500
        flow = self._flow_with_approved(5000, recs)

        result = flow._execute_bookings()

        assert result["status"] == "rejected"
        assert result["booked"] == 0
        assert len(flow.state.booked_lines) == 0
        assert flow.state.execution_status == ExecutionStatus.FAILED
        assert any("budget" in err.lower() for err in flow.state.errors)

    def test_booking_at_budget_books(self):
        """Total cost exactly at budget books normally."""
        recs = [_make_recommendation("prod_a", "branding", 500000, 15.0)]  # cost 7500
        flow = self._flow_with_approved(7500, recs)

        result = flow._execute_bookings()

        assert result["status"] == "success"
        assert result["booked"] == 1
        assert flow.state.execution_status == ExecutionStatus.COMPLETED

    def test_booking_under_budget_books(self):
        """Total cost under budget books normally (no regression)."""
        recs = [
            _make_recommendation("prod_a", "branding", 500000, 15.0),  # 7500
            _make_recommendation("prod_b", "ctv", 300000, 25.0),  # 7500
        ]
        flow = self._flow_with_approved(100000, recs)

        result = flow._execute_bookings()

        assert result["status"] == "success"
        assert result["booked"] == 2
        assert result["total_cost"] == 15000.0

    def test_missing_budget_fails_open(self, caplog):
        """No budget in brief: fail-open, booking proceeds (demo behavior)."""
        recs = [_make_recommendation("prod_a", "branding", 500000, 15.0)]
        flow = self._flow_with_approved(None, recs)

        with caplog.at_level(logging.WARNING):
            result = flow._execute_bookings()

        assert result["status"] == "success"
        assert result["booked"] == 1
