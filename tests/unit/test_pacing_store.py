# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for PacingStore — pacing snapshot CRUD operations.

All tests use in-memory SQLite (`:memory:`) for speed and isolation.

bead: buyer-lna (Pacing snapshot storage)
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from ad_buyer.models.campaign import (
    ChannelSnapshot,
    DealSnapshot,
    PacingRecommendation,
    PacingSnapshot,
    RecommendationStatus,
    RecommendationType,
)
from ad_buyer.storage.pacing_store import PacingStore

# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------


@pytest.fixture
def pacing_store():
    """Create a PacingStore backed by in-memory SQLite."""
    store = PacingStore("sqlite:///:memory:")
    store.connect()
    yield store
    store.disconnect()


def _make_snapshot(**overrides) -> PacingSnapshot:
    """Create a PacingSnapshot with sensible defaults, overriding as needed."""
    defaults = dict(
        campaign_id="camp-001",
        total_budget=100000.0,
        total_spend=25000.0,
        pacing_pct=100.0,
        expected_spend=25000.0,
        deviation_pct=0.0,
        channel_snapshots=[],
        deal_snapshots=[],
        recommendations=[],
    )
    defaults.update(overrides)
    return PacingSnapshot(**defaults)


# -----------------------------------------------------------------------
# Model Tests
# -----------------------------------------------------------------------


class TestPacingSnapshotModel:
    """Tests for the PacingSnapshot Pydantic model."""

    def test_create_minimal_snapshot(self):
        """Minimal snapshot with required fields only."""
        snap = PacingSnapshot(
            campaign_id="camp-001",
            total_budget=100000.0,
            total_spend=25000.0,
            pacing_pct=100.0,
            expected_spend=25000.0,
            deviation_pct=0.0,
        )
        assert snap.campaign_id == "camp-001"
        assert snap.snapshot_id is not None  # auto-generated UUID
        assert snap.timestamp is not None  # auto-generated
        assert snap.channel_snapshots == []
        assert snap.deal_snapshots == []
        assert snap.recommendations == []

    def test_snapshot_with_channel_breakdown(self):
        """Snapshot with per-channel data."""
        ch = ChannelSnapshot(
            channel="CTV",
            allocated_budget=50000.0,
            spend=12000.0,
            pacing_pct=96.0,
            impressions=400000,
            effective_cpm=30.0,
            fill_rate=0.85,
        )
        snap = _make_snapshot(channel_snapshots=[ch])
        assert len(snap.channel_snapshots) == 1
        assert snap.channel_snapshots[0].channel == "CTV"
        assert snap.channel_snapshots[0].impressions == 400000

    def test_snapshot_with_deal_breakdown(self):
        """Snapshot with per-deal data."""
        ds = DealSnapshot(
            deal_id="deal-abc",
            allocated_budget=20000.0,
            spend=5000.0,
            impressions=100000,
            effective_cpm=50.0,
            fill_rate=0.9,
            win_rate=0.7,
        )
        snap = _make_snapshot(deal_snapshots=[ds])
        assert len(snap.deal_snapshots) == 1
        assert snap.deal_snapshots[0].deal_id == "deal-abc"

    def test_snapshot_with_recommendations(self):
        """Snapshot with pacing recommendations."""
        rec = PacingRecommendation(
            type=RecommendationType.REALLOCATE,
            source_channel="DISPLAY",
            target_channel="CTV",
            amount=5000.0,
            reason="CTV underpacing, DISPLAY overpacing",
        )
        snap = _make_snapshot(recommendations=[rec])
        assert len(snap.recommendations) == 1
        assert snap.recommendations[0].type == RecommendationType.REALLOCATE
        assert snap.recommendations[0].status == RecommendationStatus.PENDING

    def test_snapshot_id_is_valid_uuid(self):
        """Auto-generated snapshot_id should be a valid UUID."""
        snap = _make_snapshot()
        uuid.UUID(snap.snapshot_id)  # should not raise

    def test_snapshot_custom_id(self):
        """Caller can provide a custom snapshot_id."""
        custom_id = str(uuid.uuid4())
        snap = _make_snapshot(snapshot_id=custom_id)
        assert snap.snapshot_id == custom_id


# -----------------------------------------------------------------------
# CRUD Tests
# -----------------------------------------------------------------------


class TestPacingStoreSave:
    """Tests for save_pacing_snapshot."""

    def test_save_and_get(self, pacing_store):
        """Save a snapshot and retrieve it by ID."""
        snap = _make_snapshot()
        pacing_store.save_pacing_snapshot(snap)
        retrieved = pacing_store.get_pacing_snapshot(snap.snapshot_id)

        assert retrieved is not None
        assert retrieved.snapshot_id == snap.snapshot_id
        assert retrieved.campaign_id == snap.campaign_id
        assert retrieved.total_budget == snap.total_budget
        assert retrieved.total_spend == snap.total_spend
        assert retrieved.pacing_pct == snap.pacing_pct
        assert retrieved.expected_spend == snap.expected_spend
        assert retrieved.deviation_pct == snap.deviation_pct

    def test_save_with_channel_snapshots(self, pacing_store):
        """Channel snapshots round-trip through storage."""
        ch = ChannelSnapshot(
            channel="AUDIO",
            allocated_budget=30000.0,
            spend=7500.0,
            pacing_pct=100.0,
            impressions=250000,
            effective_cpm=30.0,
            fill_rate=0.92,
        )
        snap = _make_snapshot(channel_snapshots=[ch])
        pacing_store.save_pacing_snapshot(snap)
        retrieved = pacing_store.get_pacing_snapshot(snap.snapshot_id)

        assert len(retrieved.channel_snapshots) == 1
        assert retrieved.channel_snapshots[0].channel == "AUDIO"
        assert retrieved.channel_snapshots[0].allocated_budget == 30000.0

    def test_save_with_deal_snapshots(self, pacing_store):
        """Deal snapshots round-trip through storage."""
        ds = DealSnapshot(
            deal_id="deal-xyz",
            allocated_budget=10000.0,
            spend=2500.0,
            impressions=50000,
            effective_cpm=50.0,
            fill_rate=0.88,
            win_rate=0.65,
        )
        snap = _make_snapshot(deal_snapshots=[ds])
        pacing_store.save_pacing_snapshot(snap)
        retrieved = pacing_store.get_pacing_snapshot(snap.snapshot_id)

        assert len(retrieved.deal_snapshots) == 1
        assert retrieved.deal_snapshots[0].deal_id == "deal-xyz"
        assert retrieved.deal_snapshots[0].win_rate == 0.65

    def test_save_with_recommendations(self, pacing_store):
        """Recommendations round-trip through storage."""
        rec = PacingRecommendation(
            type=RecommendationType.PAUSE,
            reason="Channel underperforming",
        )
        snap = _make_snapshot(recommendations=[rec])
        pacing_store.save_pacing_snapshot(snap)
        retrieved = pacing_store.get_pacing_snapshot(snap.snapshot_id)

        assert len(retrieved.recommendations) == 1
        assert retrieved.recommendations[0].type == RecommendationType.PAUSE

    def test_get_nonexistent_returns_none(self, pacing_store):
        """Getting a non-existent snapshot returns None."""
        result = pacing_store.get_pacing_snapshot("nonexistent-id")
        assert result is None


class TestPacingStoreList:
    """Tests for list_pacing_snapshots."""

    def test_list_by_campaign_id(self, pacing_store):
        """Filter snapshots by campaign_id."""
        snap_a = _make_snapshot(campaign_id="camp-A")
        snap_b = _make_snapshot(campaign_id="camp-B")
        pacing_store.save_pacing_snapshot(snap_a)
        pacing_store.save_pacing_snapshot(snap_b)

        results = pacing_store.list_pacing_snapshots(campaign_id="camp-A")
        assert len(results) == 1
        assert results[0].campaign_id == "camp-A"

    def test_list_by_time_range(self, pacing_store):
        """Filter snapshots by time range."""
        now = datetime.now(UTC)
        old = now - timedelta(hours=24)
        recent = now - timedelta(hours=1)

        snap_old = _make_snapshot(timestamp=old)
        snap_recent = _make_snapshot(timestamp=recent)
        pacing_store.save_pacing_snapshot(snap_old)
        pacing_store.save_pacing_snapshot(snap_recent)

        # Only the recent one should match
        cutoff = now - timedelta(hours=2)
        results = pacing_store.list_pacing_snapshots(campaign_id="camp-001", start_time=cutoff)
        assert len(results) == 1
        assert results[0].snapshot_id == snap_recent.snapshot_id

    def test_list_by_end_time(self, pacing_store):
        """Filter snapshots by end time."""
        now = datetime.now(UTC)
        old = now - timedelta(hours=24)
        recent = now - timedelta(hours=1)

        snap_old = _make_snapshot(timestamp=old)
        snap_recent = _make_snapshot(timestamp=recent)
        pacing_store.save_pacing_snapshot(snap_old)
        pacing_store.save_pacing_snapshot(snap_recent)

        # Only the old one should match
        cutoff = now - timedelta(hours=2)
        results = pacing_store.list_pacing_snapshots(campaign_id="camp-001", end_time=cutoff)
        assert len(results) == 1
        assert results[0].snapshot_id == snap_old.snapshot_id

    def test_list_all_for_campaign(self, pacing_store):
        """List all snapshots for a campaign when no time filter."""
        for i in range(5):
            snap = _make_snapshot(campaign_id="camp-multi")
            pacing_store.save_pacing_snapshot(snap)

        results = pacing_store.list_pacing_snapshots(campaign_id="camp-multi")
        assert len(results) == 5

    def test_list_empty(self, pacing_store):
        """List returns empty when no snapshots match."""
        results = pacing_store.list_pacing_snapshots(campaign_id="no-such-campaign")
        assert results == []

    def test_list_ordered_by_timestamp(self, pacing_store):
        """Snapshots are returned in chronological order."""
        now = datetime.now(UTC)
        snaps = []
        for i in range(3):
            snap = _make_snapshot(
                campaign_id="camp-order",
                timestamp=now - timedelta(hours=3 - i),
            )
            snaps.append(snap)

        # Save in reverse order to ensure ordering is by timestamp not insert order
        for snap in reversed(snaps):
            pacing_store.save_pacing_snapshot(snap)

        results = pacing_store.list_pacing_snapshots(campaign_id="camp-order")
        assert len(results) == 3
        for i in range(len(results) - 1):
            assert results[i].timestamp <= results[i + 1].timestamp

    def test_latest_snapshot(self, pacing_store):
        """Get the most recent snapshot for a campaign."""
        now = datetime.now(UTC)
        for i in range(3):
            snap = _make_snapshot(
                campaign_id="camp-latest",
                timestamp=now - timedelta(hours=3 - i),
                total_spend=float(i * 1000),
            )
            pacing_store.save_pacing_snapshot(snap)

        latest = pacing_store.get_latest_pacing_snapshot("camp-latest")
        assert latest is not None
        assert latest.total_spend == 2000.0  # most recent (i=2)

    def test_latest_snapshot_nonexistent(self, pacing_store):
        """Latest snapshot for nonexistent campaign returns None."""
        result = pacing_store.get_latest_pacing_snapshot("no-such-campaign")
        assert result is None
