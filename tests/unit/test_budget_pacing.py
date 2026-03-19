# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the Budget Pacing & Reallocation engine.

TDD RED: These tests define the expected behavior of the BudgetPacingEngine
module before implementation.

bead: buyer-9zz (2C: Budget Pacing & Reallocation)
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ad_buyer.models.campaign import (
    ChannelSnapshot,
    DealSnapshot,
    PacingRecommendation,
    PacingSnapshot,
    RecommendationStatus,
    RecommendationType,
)
from ad_buyer.pacing.engine import (
    BudgetPacingEngine,
    PacingAlert,
    PacingAlertLevel,
    PacingConfig,
    ReallocationProposal,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pacing_config() -> PacingConfig:
    """Default pacing configuration for tests."""
    return PacingConfig(
        underpacing_warning_pct=10.0,
        underpacing_critical_pct=25.0,
        overpacing_warning_pct=10.0,
        overpacing_critical_pct=25.0,
        min_reallocation_amount=100.0,
        max_reallocation_pct=30.0,
    )


@pytest.fixture
def engine(pacing_config: PacingConfig) -> BudgetPacingEngine:
    """Create a BudgetPacingEngine with default config."""
    return BudgetPacingEngine(config=pacing_config)


@pytest.fixture
def campaign_start() -> datetime:
    return datetime(2025, 3, 1, tzinfo=timezone.utc)


@pytest.fixture
def campaign_end() -> datetime:
    return datetime(2025, 3, 31, tzinfo=timezone.utc)


@pytest.fixture
def now_midway() -> datetime:
    """Now at the midpoint of the campaign (March 16)."""
    return datetime(2025, 3, 16, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Test: Spend Tracking Against Plan
# ---------------------------------------------------------------------------


class TestSpendTracking:
    """Test expected vs actual spend calculations."""

    def test_calculate_expected_spend_at_midpoint(
        self, engine: BudgetPacingEngine, campaign_start: datetime,
        campaign_end: datetime, now_midway: datetime,
    ) -> None:
        """At the midpoint, expected spend should be ~50% of total budget."""
        total_budget = 100_000.0
        expected = engine.calculate_expected_spend(
            total_budget=total_budget,
            flight_start=campaign_start,
            flight_end=campaign_end,
            current_time=now_midway,
        )
        # March 1-31 = 30 days, March 16 = 15 days elapsed
        # Expected ~50% of budget
        assert expected == pytest.approx(50_000.0, rel=0.05)

    def test_calculate_expected_spend_at_start(
        self, engine: BudgetPacingEngine, campaign_start: datetime,
        campaign_end: datetime,
    ) -> None:
        """At flight start, expected spend should be 0."""
        expected = engine.calculate_expected_spend(
            total_budget=100_000.0,
            flight_start=campaign_start,
            flight_end=campaign_end,
            current_time=campaign_start,
        )
        assert expected == 0.0

    def test_calculate_expected_spend_at_end(
        self, engine: BudgetPacingEngine, campaign_start: datetime,
        campaign_end: datetime,
    ) -> None:
        """At flight end, expected spend should be 100% of budget."""
        expected = engine.calculate_expected_spend(
            total_budget=100_000.0,
            flight_start=campaign_start,
            flight_end=campaign_end,
            current_time=campaign_end,
        )
        assert expected == pytest.approx(100_000.0, rel=0.01)

    def test_calculate_expected_spend_before_start(
        self, engine: BudgetPacingEngine, campaign_start: datetime,
        campaign_end: datetime,
    ) -> None:
        """Before campaign start, expected spend should be 0."""
        before = campaign_start - timedelta(days=1)
        expected = engine.calculate_expected_spend(
            total_budget=100_000.0,
            flight_start=campaign_start,
            flight_end=campaign_end,
            current_time=before,
        )
        assert expected == 0.0

    def test_calculate_expected_spend_after_end(
        self, engine: BudgetPacingEngine, campaign_start: datetime,
        campaign_end: datetime,
    ) -> None:
        """After campaign end, expected spend should be full budget."""
        after = campaign_end + timedelta(days=5)
        expected = engine.calculate_expected_spend(
            total_budget=100_000.0,
            flight_start=campaign_start,
            flight_end=campaign_end,
            current_time=after,
        )
        assert expected == 100_000.0

    def test_calculate_pacing_percentage(
        self, engine: BudgetPacingEngine,
    ) -> None:
        """Pacing pct = (actual_spend / expected_spend) * 100."""
        pct = engine.calculate_pacing_pct(
            actual_spend=40_000.0, expected_spend=50_000.0
        )
        assert pct == pytest.approx(80.0, rel=0.01)

    def test_calculate_pacing_percentage_zero_expected(
        self, engine: BudgetPacingEngine,
    ) -> None:
        """If expected_spend is zero (campaign hasn't started), return 0."""
        pct = engine.calculate_pacing_pct(actual_spend=0.0, expected_spend=0.0)
        assert pct == 0.0

    def test_calculate_deviation_pct(
        self, engine: BudgetPacingEngine,
    ) -> None:
        """Deviation = pacing_pct - 100. Negative means underpacing."""
        deviation = engine.calculate_deviation_pct(
            actual_spend=40_000.0, expected_spend=50_000.0
        )
        assert deviation == pytest.approx(-20.0, rel=0.01)

    def test_calculate_deviation_overpacing(
        self, engine: BudgetPacingEngine,
    ) -> None:
        """Positive deviation means overpacing."""
        deviation = engine.calculate_deviation_pct(
            actual_spend=60_000.0, expected_spend=50_000.0
        )
        assert deviation == pytest.approx(20.0, rel=0.01)


# ---------------------------------------------------------------------------
# Test: Pacing Deviation Detection
# ---------------------------------------------------------------------------


class TestPacingDeviationDetection:
    """Test pacing alert/deviation detection logic."""

    def test_no_alert_when_on_pace(
        self, engine: BudgetPacingEngine,
    ) -> None:
        """No alert generated when spend is within thresholds."""
        alert = engine.detect_deviation(
            actual_spend=48_000.0,
            expected_spend=50_000.0,
        )
        assert alert is None

    def test_warning_underpacing(
        self, engine: BudgetPacingEngine,
    ) -> None:
        """Warning when underpacing exceeds warning threshold (10%)."""
        alert = engine.detect_deviation(
            actual_spend=42_000.0,  # -16% deviation
            expected_spend=50_000.0,
        )
        assert alert is not None
        assert alert.level == PacingAlertLevel.WARNING
        assert alert.direction == "underpacing"

    def test_critical_underpacing(
        self, engine: BudgetPacingEngine,
    ) -> None:
        """Critical alert when underpacing exceeds critical threshold (25%)."""
        alert = engine.detect_deviation(
            actual_spend=30_000.0,  # -40% deviation
            expected_spend=50_000.0,
        )
        assert alert is not None
        assert alert.level == PacingAlertLevel.CRITICAL
        assert alert.direction == "underpacing"

    def test_warning_overpacing(
        self, engine: BudgetPacingEngine,
    ) -> None:
        """Warning when overpacing exceeds warning threshold (10%)."""
        alert = engine.detect_deviation(
            actual_spend=58_000.0,  # +16% deviation
            expected_spend=50_000.0,
        )
        assert alert is not None
        assert alert.level == PacingAlertLevel.WARNING
        assert alert.direction == "overpacing"

    def test_critical_overpacing(
        self, engine: BudgetPacingEngine,
    ) -> None:
        """Critical alert when overpacing exceeds critical threshold (25%)."""
        alert = engine.detect_deviation(
            actual_spend=65_000.0,  # +30% deviation
            expected_spend=50_000.0,
        )
        assert alert is not None
        assert alert.level == PacingAlertLevel.CRITICAL
        assert alert.direction == "overpacing"

    def test_threshold_boundary_no_alert(
        self, engine: BudgetPacingEngine,
    ) -> None:
        """Exactly at threshold boundary: no alert (threshold is exclusive)."""
        # 10% under: actual_spend = 45_000 / 50_000 = 90% pacing = -10% deviation
        alert = engine.detect_deviation(
            actual_spend=45_000.0,
            expected_spend=50_000.0,
        )
        # At exactly the boundary, no alert (must EXCEED threshold)
        assert alert is None


# ---------------------------------------------------------------------------
# Test: Cross-Channel Budget Reallocation
# ---------------------------------------------------------------------------


class TestCrossChannelReallocation:
    """Test budget reallocation proposals between channels."""

    def test_reallocate_from_underpacing_to_overpacing(
        self, engine: BudgetPacingEngine,
    ) -> None:
        """Shift budget from underpacing channels to overpacing ones."""
        channel_snapshots = [
            ChannelSnapshot(
                channel="display",
                allocated_budget=40_000.0,
                spend=12_000.0,
                pacing_pct=60.0,  # underpacing
                impressions=800_000,
                effective_cpm=15.0,
            ),
            ChannelSnapshot(
                channel="ctv",
                allocated_budget=40_000.0,
                spend=36_000.0,
                pacing_pct=180.0,  # overpacing
                impressions=1_200_000,
                effective_cpm=30.0,
            ),
            ChannelSnapshot(
                channel="mobile",
                allocated_budget=20_000.0,
                spend=10_000.0,
                pacing_pct=100.0,  # on pace
                impressions=500_000,
                effective_cpm=20.0,
            ),
        ]

        proposals = engine.propose_reallocations(
            channel_snapshots=channel_snapshots,
            total_budget=100_000.0,
            expected_spend=50_000.0,
        )

        assert len(proposals) > 0
        # Should propose moving budget FROM display TO ctv
        sources = [p.source_channel for p in proposals]
        targets = [p.target_channel for p in proposals]
        assert "display" in sources
        assert "ctv" in targets

    def test_no_reallocation_when_all_on_pace(
        self, engine: BudgetPacingEngine,
    ) -> None:
        """No reallocation proposals when all channels are on pace."""
        channel_snapshots = [
            ChannelSnapshot(
                channel="display",
                allocated_budget=50_000.0,
                spend=25_000.0,
                pacing_pct=100.0,
            ),
            ChannelSnapshot(
                channel="ctv",
                allocated_budget=50_000.0,
                spend=25_000.0,
                pacing_pct=100.0,
            ),
        ]

        proposals = engine.propose_reallocations(
            channel_snapshots=channel_snapshots,
            total_budget=100_000.0,
            expected_spend=50_000.0,
        )

        assert proposals == []

    def test_reallocation_respects_max_percentage(
        self, engine: BudgetPacingEngine,
    ) -> None:
        """No single reallocation should exceed max_reallocation_pct of budget."""
        channel_snapshots = [
            ChannelSnapshot(
                channel="display",
                allocated_budget=80_000.0,
                spend=10_000.0,
                pacing_pct=25.0,  # severely underpacing
            ),
            ChannelSnapshot(
                channel="ctv",
                allocated_budget=20_000.0,
                spend=18_000.0,
                pacing_pct=180.0,  # overpacing
            ),
        ]

        proposals = engine.propose_reallocations(
            channel_snapshots=channel_snapshots,
            total_budget=100_000.0,
            expected_spend=50_000.0,
        )

        for proposal in proposals:
            # max_reallocation_pct is 30%, so max amount = 30_000
            assert proposal.amount <= 100_000.0 * 0.30

    def test_reallocation_respects_min_amount(
        self, engine: BudgetPacingEngine,
    ) -> None:
        """No reallocation below min_reallocation_amount ($100)."""
        channel_snapshots = [
            ChannelSnapshot(
                channel="display",
                allocated_budget=1_000.0,
                spend=480.0,
                pacing_pct=96.0,  # just slightly off (4% deviation)
            ),
            ChannelSnapshot(
                channel="ctv",
                allocated_budget=1_000.0,
                spend=520.0,
                pacing_pct=104.0,  # just slightly off
            ),
        ]

        proposals = engine.propose_reallocations(
            channel_snapshots=channel_snapshots,
            total_budget=2_000.0,
            expected_spend=1_000.0,
        )

        # Deviations are small, so no proposal should be generated
        assert proposals == []


class TestReallocationProposalModel:
    """Test ReallocationProposal data model."""

    def test_proposal_has_required_fields(self) -> None:
        """ReallocationProposal must have source, target, amount, reason."""
        proposal = ReallocationProposal(
            source_channel="display",
            target_channel="ctv",
            amount=5_000.0,
            reason="Display underpacing at 60%, CTV overpacing at 180%",
        )
        assert proposal.source_channel == "display"
        assert proposal.target_channel == "ctv"
        assert proposal.amount == 5_000.0
        assert "underpacing" in proposal.reason.lower() or len(proposal.reason) > 0


# ---------------------------------------------------------------------------
# Test: Deal-Level Spend Tracking
# ---------------------------------------------------------------------------


class TestDealLevelSpendTracking:
    """Test deal-level pacing and spend monitoring."""

    def test_calculate_deal_pacing(
        self, engine: BudgetPacingEngine,
        campaign_start: datetime, campaign_end: datetime, now_midway: datetime,
    ) -> None:
        """Calculate pacing for individual deals."""
        deal_snapshot = DealSnapshot(
            deal_id="deal-001",
            allocated_budget=10_000.0,
            spend=3_000.0,
            impressions=200_000,
            effective_cpm=15.0,
        )

        pacing_info = engine.calculate_deal_pacing(
            deal_snapshot=deal_snapshot,
            flight_start=campaign_start,
            flight_end=campaign_end,
            current_time=now_midway,
        )

        # Expected spend at midpoint is ~5000
        assert pacing_info["expected_spend"] == pytest.approx(5_000.0, rel=0.05)
        assert pacing_info["pacing_pct"] == pytest.approx(60.0, rel=0.05)
        assert pacing_info["deviation_pct"] < 0  # underpacing

    def test_deal_level_alerts(
        self, engine: BudgetPacingEngine,
        campaign_start: datetime, campaign_end: datetime, now_midway: datetime,
    ) -> None:
        """Deal-level alerts should fire based on same thresholds."""
        deal_snapshot = DealSnapshot(
            deal_id="deal-002",
            allocated_budget=10_000.0,
            spend=2_000.0,  # Only 40% of expected -> -60% deviation
            impressions=100_000,
        )

        pacing_info = engine.calculate_deal_pacing(
            deal_snapshot=deal_snapshot,
            flight_start=campaign_start,
            flight_end=campaign_end,
            current_time=now_midway,
        )

        assert pacing_info["alert"] is not None
        assert pacing_info["alert"].level == PacingAlertLevel.CRITICAL


# ---------------------------------------------------------------------------
# Test: Full Pacing Snapshot Generation
# ---------------------------------------------------------------------------


class TestPacingSnapshotGeneration:
    """Test end-to-end pacing snapshot generation."""

    def test_generate_snapshot(
        self, engine: BudgetPacingEngine,
        campaign_start: datetime, campaign_end: datetime, now_midway: datetime,
    ) -> None:
        """Generate a full pacing snapshot from channel and deal data."""
        channel_data = {
            "display": {"allocated_budget": 50_000.0, "spend": 20_000.0, "impressions": 1_000_000},
            "ctv": {"allocated_budget": 30_000.0, "spend": 18_000.0, "impressions": 600_000},
            "mobile": {"allocated_budget": 20_000.0, "spend": 8_000.0, "impressions": 400_000},
        }

        deal_data = [
            {"deal_id": "deal-001", "allocated_budget": 25_000.0, "spend": 10_000.0, "impressions": 500_000},
            {"deal_id": "deal-002", "allocated_budget": 25_000.0, "spend": 10_000.0, "impressions": 500_000},
            {"deal_id": "deal-003", "allocated_budget": 30_000.0, "spend": 18_000.0, "impressions": 600_000},
            {"deal_id": "deal-004", "allocated_budget": 20_000.0, "spend": 8_000.0, "impressions": 400_000},
        ]

        snapshot = engine.generate_snapshot(
            campaign_id="campaign-001",
            total_budget=100_000.0,
            flight_start=campaign_start,
            flight_end=campaign_end,
            current_time=now_midway,
            channel_data=channel_data,
            deal_data=deal_data,
        )

        assert isinstance(snapshot, PacingSnapshot)
        assert snapshot.campaign_id == "campaign-001"
        assert snapshot.total_budget == 100_000.0
        assert snapshot.total_spend == pytest.approx(46_000.0, rel=0.01)
        assert len(snapshot.channel_snapshots) == 3
        assert len(snapshot.deal_snapshots) == 4
        # Expected spend at midpoint is ~50_000
        assert snapshot.expected_spend == pytest.approx(50_000.0, rel=0.05)

    def test_snapshot_includes_recommendations(
        self, engine: BudgetPacingEngine,
        campaign_start: datetime, campaign_end: datetime, now_midway: datetime,
    ) -> None:
        """Snapshot should include reallocation recommendations when channels deviate."""
        channel_data = {
            "display": {"allocated_budget": 50_000.0, "spend": 15_000.0, "impressions": 750_000},
            "ctv": {"allocated_budget": 50_000.0, "spend": 40_000.0, "impressions": 1_000_000},
        }

        snapshot = engine.generate_snapshot(
            campaign_id="campaign-002",
            total_budget=100_000.0,
            flight_start=campaign_start,
            flight_end=campaign_end,
            current_time=now_midway,
            channel_data=channel_data,
            deal_data=[],
        )

        # Display is underpacing (60%), CTV is overpacing (160%)
        # Should have recommendations
        assert len(snapshot.recommendations) > 0
        rec_types = [r.type for r in snapshot.recommendations]
        assert RecommendationType.REALLOCATE in rec_types


# ---------------------------------------------------------------------------
# Test: Event Emission for Pacing Events
# ---------------------------------------------------------------------------


class TestPacingEventEmission:
    """Test that the pacing engine emits appropriate events."""

    @pytest.mark.asyncio
    async def test_emit_snapshot_taken_event(
        self, engine: BudgetPacingEngine,
        campaign_start: datetime, campaign_end: datetime, now_midway: datetime,
    ) -> None:
        """Should emit PACING_SNAPSHOT_TAKEN event when snapshot is generated."""
        from ad_buyer.events.bus import InMemoryEventBus
        from ad_buyer.events.models import EventType as ET

        event_bus = InMemoryEventBus()
        engine.event_bus = event_bus

        channel_data = {
            "display": {"allocated_budget": 50_000.0, "spend": 25_000.0, "impressions": 1_000_000},
        }

        snapshot = engine.generate_snapshot(
            campaign_id="campaign-003",
            total_budget=50_000.0,
            flight_start=campaign_start,
            flight_end=campaign_end,
            current_time=now_midway,
            channel_data=channel_data,
            deal_data=[],
        )

        # Check that event was emitted
        events = await event_bus.list_events(event_type=ET.PACING_SNAPSHOT_TAKEN.value)
        assert len(events) == 1
        assert events[0].campaign_id == "campaign-003"

    @pytest.mark.asyncio
    async def test_emit_deviation_detected_event(
        self, engine: BudgetPacingEngine,
        campaign_start: datetime, campaign_end: datetime, now_midway: datetime,
    ) -> None:
        """Should emit PACING_DEVIATION_DETECTED when deviation exceeds threshold."""
        from ad_buyer.events.bus import InMemoryEventBus
        from ad_buyer.events.models import EventType as ET

        event_bus = InMemoryEventBus()
        engine.event_bus = event_bus

        channel_data = {
            "display": {"allocated_budget": 50_000.0, "spend": 15_000.0, "impressions": 500_000},
        }

        snapshot = engine.generate_snapshot(
            campaign_id="campaign-004",
            total_budget=50_000.0,
            flight_start=campaign_start,
            flight_end=campaign_end,
            current_time=now_midway,
            channel_data=channel_data,
            deal_data=[],
        )

        events = await event_bus.list_events(event_type=ET.PACING_DEVIATION_DETECTED.value)
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_emit_reallocation_events(
        self, engine: BudgetPacingEngine,
        campaign_start: datetime, campaign_end: datetime, now_midway: datetime,
    ) -> None:
        """Should emit PACING_REALLOCATION_RECOMMENDED when proposing reallocations."""
        from ad_buyer.events.bus import InMemoryEventBus
        from ad_buyer.events.models import EventType as ET

        event_bus = InMemoryEventBus()
        engine.event_bus = event_bus

        channel_data = {
            "display": {"allocated_budget": 50_000.0, "spend": 15_000.0, "impressions": 500_000},
            "ctv": {"allocated_budget": 50_000.0, "spend": 40_000.0, "impressions": 1_000_000},
        }

        snapshot = engine.generate_snapshot(
            campaign_id="campaign-005",
            total_budget=100_000.0,
            flight_start=campaign_start,
            flight_end=campaign_end,
            current_time=now_midway,
            channel_data=channel_data,
            deal_data=[],
        )

        events = await event_bus.list_events(
            event_type=ET.PACING_REALLOCATION_RECOMMENDED.value
        )
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_no_deviation_event_when_on_pace(
        self, engine: BudgetPacingEngine,
        campaign_start: datetime, campaign_end: datetime, now_midway: datetime,
    ) -> None:
        """No deviation event when campaign is pacing normally."""
        from ad_buyer.events.bus import InMemoryEventBus
        from ad_buyer.events.models import EventType as ET

        event_bus = InMemoryEventBus()
        engine.event_bus = event_bus

        channel_data = {
            "display": {"allocated_budget": 50_000.0, "spend": 25_000.0, "impressions": 1_000_000},
        }

        snapshot = engine.generate_snapshot(
            campaign_id="campaign-006",
            total_budget=50_000.0,
            flight_start=campaign_start,
            flight_end=campaign_end,
            current_time=now_midway,
            channel_data=channel_data,
            deal_data=[],
        )

        events = await event_bus.list_events(
            event_type=ET.PACING_DEVIATION_DETECTED.value
        )
        assert len(events) == 0


# ---------------------------------------------------------------------------
# Test: Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test edge cases for the pacing engine."""

    def test_zero_budget_campaign(
        self, engine: BudgetPacingEngine,
        campaign_start: datetime, campaign_end: datetime, now_midway: datetime,
    ) -> None:
        """Handle zero-budget campaigns gracefully."""
        snapshot = engine.generate_snapshot(
            campaign_id="campaign-zero",
            total_budget=0.0,
            flight_start=campaign_start,
            flight_end=campaign_end,
            current_time=now_midway,
            channel_data={},
            deal_data=[],
        )
        assert snapshot.total_budget == 0.0
        assert snapshot.total_spend == 0.0
        assert snapshot.pacing_pct == 0.0

    def test_single_day_flight(
        self, engine: BudgetPacingEngine,
    ) -> None:
        """Handle single-day flights."""
        start = datetime(2025, 3, 15, tzinfo=timezone.utc)
        end = datetime(2025, 3, 15, 23, 59, 59, tzinfo=timezone.utc)
        now = datetime(2025, 3, 15, 12, 0, 0, tzinfo=timezone.utc)

        expected = engine.calculate_expected_spend(
            total_budget=10_000.0,
            flight_start=start,
            flight_end=end,
            current_time=now,
        )
        # About halfway through the day, expect ~50%
        assert expected == pytest.approx(5_000.0, rel=0.1)

    def test_no_channel_data(
        self, engine: BudgetPacingEngine,
        campaign_start: datetime, campaign_end: datetime, now_midway: datetime,
    ) -> None:
        """Handle empty channel data."""
        snapshot = engine.generate_snapshot(
            campaign_id="campaign-empty",
            total_budget=100_000.0,
            flight_start=campaign_start,
            flight_end=campaign_end,
            current_time=now_midway,
            channel_data={},
            deal_data=[],
        )
        assert snapshot.total_spend == 0.0
        assert len(snapshot.channel_snapshots) == 0
        assert len(snapshot.deal_snapshots) == 0
