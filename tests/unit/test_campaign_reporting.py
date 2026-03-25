# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for campaign reporting module (buyer-f58).

Covers:
- Campaign status summary reports
- Pacing dashboard data (expected vs actual delivery)
- Creative performance reports
- Deal-level reporting
- JSON and human-readable output formats
"""

import json
from datetime import UTC, datetime, timedelta

import pytest

from ad_buyer.models.campaign import (
    ChannelSnapshot,
    DealSnapshot,
    PacingSnapshot,
)
from ad_buyer.models.state_machine import CampaignStatus
from ad_buyer.reporting.campaign_report import (
    CampaignReport,
    CampaignReporter,
    CampaignStatusSummary,
    CreativePerformanceReport,
    DealReport,
    PacingDashboard,
)
from ad_buyer.storage.campaign_store import CampaignStore
from ad_buyer.storage.pacing_store import PacingStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def campaign_store():
    """Create an in-memory CampaignStore for testing."""
    store = CampaignStore("sqlite:///:memory:")
    store.connect()
    yield store
    store.disconnect()


@pytest.fixture
def pacing_store():
    """Create an in-memory PacingStore for testing."""
    store = PacingStore("sqlite:///:memory:")
    store.connect()
    yield store
    store.disconnect()


@pytest.fixture
def sample_campaign(campaign_store):
    """Create a sample campaign in ACTIVE status."""
    campaign_id = campaign_store.save_campaign(
        advertiser_id="adv-001",
        campaign_name="Summer Promo 2026",
        status=CampaignStatus.ACTIVE.value,
        total_budget=50000.0,
        currency="USD",
        flight_start="2026-06-01",
        flight_end="2026-08-31",
        channels=json.dumps(
            [
                {"channel": "CTV", "budget_pct": 0.6},
                {"channel": "DISPLAY", "budget_pct": 0.4},
            ]
        ),
        target_audience=json.dumps(["sports_fans", "m18_34"]),
        target_geo=json.dumps(["US"]),
        kpis=json.dumps([{"metric": "CPM", "target": 12.0}]),
    )
    return campaign_id


@pytest.fixture
def sample_draft_campaign(campaign_store):
    """Create a sample campaign in DRAFT status."""
    campaign_id = campaign_store.save_campaign(
        advertiser_id="adv-002",
        campaign_name="Fall Launch",
        status=CampaignStatus.DRAFT.value,
        total_budget=25000.0,
        currency="USD",
        flight_start="2026-09-01",
        flight_end="2026-11-30",
    )
    return campaign_id


@pytest.fixture
def campaign_with_pacing(campaign_store, pacing_store, sample_campaign):
    """Create a campaign with pacing snapshots."""
    now = datetime.now(UTC)

    # Snapshot 1: day 1 — on pace
    pacing_store.save_pacing_snapshot(
        PacingSnapshot(
            campaign_id=sample_campaign,
            timestamp=now - timedelta(days=2),
            total_budget=50000.0,
            total_spend=5000.0,
            pacing_pct=100.0,
            expected_spend=5000.0,
            deviation_pct=0.0,
            channel_snapshots=[
                ChannelSnapshot(
                    channel="CTV",
                    allocated_budget=30000.0,
                    spend=3000.0,
                    pacing_pct=100.0,
                    impressions=250000,
                    effective_cpm=12.0,
                    fill_rate=0.85,
                ),
                ChannelSnapshot(
                    channel="DISPLAY",
                    allocated_budget=20000.0,
                    spend=2000.0,
                    pacing_pct=100.0,
                    impressions=200000,
                    effective_cpm=10.0,
                    fill_rate=0.90,
                ),
            ],
            deal_snapshots=[
                DealSnapshot(
                    deal_id="deal-001",
                    allocated_budget=15000.0,
                    spend=1500.0,
                    impressions=125000,
                    effective_cpm=12.0,
                    fill_rate=0.85,
                    win_rate=0.45,
                ),
                DealSnapshot(
                    deal_id="deal-002",
                    allocated_budget=15000.0,
                    spend=1500.0,
                    impressions=125000,
                    effective_cpm=12.0,
                    fill_rate=0.80,
                    win_rate=0.50,
                ),
                DealSnapshot(
                    deal_id="deal-003",
                    allocated_budget=10000.0,
                    spend=1000.0,
                    impressions=100000,
                    effective_cpm=10.0,
                    fill_rate=0.90,
                    win_rate=0.55,
                ),
                DealSnapshot(
                    deal_id="deal-004",
                    allocated_budget=10000.0,
                    spend=1000.0,
                    impressions=100000,
                    effective_cpm=10.0,
                    fill_rate=0.90,
                    win_rate=0.60,
                ),
            ],
        )
    )

    # Snapshot 2: day 2 — slightly over-pacing
    pacing_store.save_pacing_snapshot(
        PacingSnapshot(
            campaign_id=sample_campaign,
            timestamp=now - timedelta(days=1),
            total_budget=50000.0,
            total_spend=12000.0,
            pacing_pct=110.0,
            expected_spend=10909.0,
            deviation_pct=10.0,
            channel_snapshots=[
                ChannelSnapshot(
                    channel="CTV",
                    allocated_budget=30000.0,
                    spend=7500.0,
                    pacing_pct=115.0,
                    impressions=600000,
                    effective_cpm=12.5,
                    fill_rate=0.82,
                ),
                ChannelSnapshot(
                    channel="DISPLAY",
                    allocated_budget=20000.0,
                    spend=4500.0,
                    pacing_pct=103.0,
                    impressions=430000,
                    effective_cpm=10.47,
                    fill_rate=0.88,
                ),
            ],
            deal_snapshots=[
                DealSnapshot(
                    deal_id="deal-001",
                    allocated_budget=15000.0,
                    spend=4000.0,
                    impressions=320000,
                    effective_cpm=12.5,
                    fill_rate=0.83,
                    win_rate=0.44,
                ),
                DealSnapshot(
                    deal_id="deal-002",
                    allocated_budget=15000.0,
                    spend=3500.0,
                    impressions=280000,
                    effective_cpm=12.5,
                    fill_rate=0.80,
                    win_rate=0.48,
                ),
                DealSnapshot(
                    deal_id="deal-003",
                    allocated_budget=10000.0,
                    spend=2300.0,
                    impressions=220000,
                    effective_cpm=10.45,
                    fill_rate=0.88,
                    win_rate=0.54,
                ),
                DealSnapshot(
                    deal_id="deal-004",
                    allocated_budget=10000.0,
                    spend=2200.0,
                    impressions=210000,
                    effective_cpm=10.48,
                    fill_rate=0.87,
                    win_rate=0.58,
                ),
            ],
        )
    )

    return sample_campaign


@pytest.fixture
def campaign_with_creatives(campaign_store, sample_campaign):
    """Create a campaign with creative assets."""
    campaign_store.save_creative_asset(
        asset_id="creative-001",
        campaign_id=sample_campaign,
        asset_name="Hero Video 30s",
        asset_type="video",
        format_spec=json.dumps(
            {
                "duration_sec": 30,
                "vast_version": "4.2",
                "width": 1920,
                "height": 1080,
            }
        ),
        source_url="https://cdn.example.com/hero30s.mp4",
        validation_status="valid",
    )
    campaign_store.save_creative_asset(
        asset_id="creative-002",
        campaign_id=sample_campaign,
        asset_name="Companion Banner 300x250",
        asset_type="display",
        format_spec=json.dumps(
            {
                "width": 300,
                "height": 250,
                "mime_type": "image/png",
            }
        ),
        source_url="https://cdn.example.com/banner300x250.png",
        validation_status="valid",
    )
    campaign_store.save_creative_asset(
        asset_id="creative-003",
        campaign_id=sample_campaign,
        asset_name="Audio Spot 15s",
        asset_type="audio",
        format_spec=json.dumps(
            {
                "duration_sec": 15,
                "bitrate": 128,
            }
        ),
        source_url="https://cdn.example.com/audio15s.mp3",
        validation_status="pending",
    )
    return sample_campaign


@pytest.fixture
def reporter(campaign_store, pacing_store):
    """Create a CampaignReporter instance."""
    return CampaignReporter(
        campaign_store=campaign_store,
        pacing_store=pacing_store,
    )


# ---------------------------------------------------------------------------
# Campaign Status Summary
# ---------------------------------------------------------------------------


class TestCampaignStatusSummary:
    """Test campaign status summary report generation."""

    def test_status_summary_active_campaign(self, reporter, sample_campaign, campaign_store):
        """Status summary for an active campaign includes key metrics."""
        summary = reporter.campaign_status_summary(sample_campaign)

        assert isinstance(summary, CampaignStatusSummary)
        assert summary.campaign_id == sample_campaign
        assert summary.campaign_name == "Summer Promo 2026"
        assert summary.status == CampaignStatus.ACTIVE.value
        assert summary.total_budget == 50000.0
        assert summary.currency == "USD"
        assert summary.flight_start == "2026-06-01"
        assert summary.flight_end == "2026-08-31"
        assert summary.advertiser_id == "adv-001"

    def test_status_summary_draft_campaign(self, reporter, sample_draft_campaign):
        """Status summary for a draft campaign shows zero delivery."""
        summary = reporter.campaign_status_summary(sample_draft_campaign)

        assert summary.status == CampaignStatus.DRAFT.value
        assert summary.total_spend == 0.0
        assert summary.delivery_pct == 0.0

    def test_status_summary_with_pacing_data(self, reporter, campaign_with_pacing):
        """Status summary includes pacing data when snapshots exist."""
        summary = reporter.campaign_status_summary(campaign_with_pacing)

        assert summary.total_spend == 12000.0
        assert summary.delivery_pct == pytest.approx(24.0, rel=0.01)
        assert summary.pacing_pct == 110.0

    def test_status_summary_nonexistent_campaign(self, reporter):
        """Status summary raises KeyError for nonexistent campaign."""
        with pytest.raises(KeyError):
            reporter.campaign_status_summary("nonexistent-id")

    def test_status_summary_to_json(self, reporter, campaign_with_pacing):
        """Status summary can be serialized to JSON."""
        summary = reporter.campaign_status_summary(campaign_with_pacing)
        json_data = summary.to_json()

        parsed = json.loads(json_data)
        assert parsed["campaign_id"] == campaign_with_pacing
        assert parsed["status"] == "active"
        assert "total_budget" in parsed
        assert "total_spend" in parsed

    def test_status_summary_to_text(self, reporter, campaign_with_pacing):
        """Status summary can render as human-readable text."""
        summary = reporter.campaign_status_summary(campaign_with_pacing)
        text = summary.to_text()

        assert "Summer Promo 2026" in text
        assert "active" in text.lower()
        assert "$50,000.00" in text or "50000" in text
        assert "$12,000.00" in text or "12000" in text


# ---------------------------------------------------------------------------
# Pacing Dashboard
# ---------------------------------------------------------------------------


class TestPacingDashboard:
    """Test pacing dashboard data generation."""

    def test_pacing_dashboard_basic(self, reporter, campaign_with_pacing):
        """Pacing dashboard contains expected vs actual delivery data."""
        dashboard = reporter.pacing_dashboard(campaign_with_pacing)

        assert isinstance(dashboard, PacingDashboard)
        assert dashboard.campaign_id == campaign_with_pacing
        assert dashboard.total_budget == 50000.0
        assert dashboard.total_spend == 12000.0
        assert dashboard.expected_spend == 10909.0
        assert dashboard.pacing_pct == 110.0
        assert dashboard.deviation_pct == 10.0

    def test_pacing_dashboard_channel_breakdown(self, reporter, campaign_with_pacing):
        """Pacing dashboard includes per-channel pacing data."""
        dashboard = reporter.pacing_dashboard(campaign_with_pacing)

        assert len(dashboard.channel_pacing) == 2

        ctv = next(
            (ch for ch in dashboard.channel_pacing if ch.channel == "CTV"),
            None,
        )
        assert ctv is not None
        assert ctv.allocated_budget == 30000.0
        assert ctv.spend == 7500.0
        assert ctv.pacing_pct == 115.0

    def test_pacing_dashboard_deviation_alerts(self, reporter, campaign_with_pacing):
        """Pacing dashboard generates alerts when deviation exceeds threshold."""
        dashboard = reporter.pacing_dashboard(campaign_with_pacing, deviation_threshold=5.0)

        assert len(dashboard.alerts) > 0
        # At least one alert for the CTV channel which is over-pacing
        ctv_alert = next(
            (a for a in dashboard.alerts if "CTV" in a.message),
            None,
        )
        assert ctv_alert is not None
        assert ctv_alert.severity in ("warning", "critical")

    def test_pacing_dashboard_no_alerts_when_on_pace(self, reporter, campaign_with_pacing):
        """Pacing dashboard generates no alerts when threshold is high."""
        dashboard = reporter.pacing_dashboard(campaign_with_pacing, deviation_threshold=50.0)

        # All deviations are below 50%, so no alerts
        assert len(dashboard.alerts) == 0

    def test_pacing_dashboard_no_snapshots(self, reporter, sample_campaign):
        """Pacing dashboard handles campaign with no pacing snapshots."""
        dashboard = reporter.pacing_dashboard(sample_campaign)

        assert dashboard.total_spend == 0.0
        assert dashboard.expected_spend == 0.0
        assert dashboard.pacing_pct == 0.0
        assert dashboard.deviation_pct == 0.0
        assert len(dashboard.channel_pacing) == 0

    def test_pacing_dashboard_to_json(self, reporter, campaign_with_pacing):
        """Pacing dashboard can be serialized to JSON."""
        dashboard = reporter.pacing_dashboard(campaign_with_pacing)
        json_data = dashboard.to_json()

        parsed = json.loads(json_data)
        assert "total_budget" in parsed
        assert "channel_pacing" in parsed
        assert isinstance(parsed["channel_pacing"], list)

    def test_pacing_dashboard_to_text(self, reporter, campaign_with_pacing):
        """Pacing dashboard can render as human-readable text."""
        dashboard = reporter.pacing_dashboard(campaign_with_pacing)
        text = dashboard.to_text()

        assert "Pacing" in text
        assert "CTV" in text
        assert "DISPLAY" in text


# ---------------------------------------------------------------------------
# Creative Performance Report
# ---------------------------------------------------------------------------


class TestCreativePerformanceReport:
    """Test creative performance report generation."""

    def test_creative_report_lists_assets(self, reporter, campaign_with_creatives):
        """Creative report lists all assets for a campaign."""
        report = reporter.creative_performance_report(campaign_with_creatives)

        assert isinstance(report, CreativePerformanceReport)
        assert report.campaign_id == campaign_with_creatives
        assert len(report.creatives) == 3

    def test_creative_report_asset_details(self, reporter, campaign_with_creatives):
        """Creative report includes asset type and validation status."""
        report = reporter.creative_performance_report(campaign_with_creatives)

        hero = next(
            (c for c in report.creatives if c.asset_id == "creative-001"),
            None,
        )
        assert hero is not None
        assert hero.asset_name == "Hero Video 30s"
        assert hero.asset_type == "video"
        assert hero.validation_status == "valid"

    def test_creative_report_pending_validation(self, reporter, campaign_with_creatives):
        """Creative report correctly shows pending validation status."""
        report = reporter.creative_performance_report(campaign_with_creatives)

        audio = next(
            (c for c in report.creatives if c.asset_id == "creative-003"),
            None,
        )
        assert audio is not None
        assert audio.validation_status == "pending"

    def test_creative_report_validation_summary(self, reporter, campaign_with_creatives):
        """Creative report includes a validation summary."""
        report = reporter.creative_performance_report(campaign_with_creatives)

        assert report.total_assets == 3
        assert report.valid_assets == 2
        assert report.pending_assets == 1
        assert report.invalid_assets == 0

    def test_creative_report_no_assets(self, reporter, sample_campaign):
        """Creative report handles campaign with no assets."""
        report = reporter.creative_performance_report(sample_campaign)

        assert len(report.creatives) == 0
        assert report.total_assets == 0

    def test_creative_report_to_json(self, reporter, campaign_with_creatives):
        """Creative report can be serialized to JSON."""
        report = reporter.creative_performance_report(campaign_with_creatives)
        json_data = report.to_json()

        parsed = json.loads(json_data)
        assert "creatives" in parsed
        assert len(parsed["creatives"]) == 3

    def test_creative_report_to_text(self, reporter, campaign_with_creatives):
        """Creative report renders as human-readable text."""
        report = reporter.creative_performance_report(campaign_with_creatives)
        text = report.to_text()

        assert "Hero Video 30s" in text
        assert "video" in text.lower()


# ---------------------------------------------------------------------------
# Deal-Level Reporting
# ---------------------------------------------------------------------------


class TestDealReport:
    """Test deal-level reporting."""

    def test_deal_report_basic(self, reporter, campaign_with_pacing):
        """Deal report contains per-deal metrics from latest snapshot."""
        report = reporter.deal_report(campaign_with_pacing)

        assert isinstance(report, DealReport)
        assert report.campaign_id == campaign_with_pacing
        assert len(report.deals) == 4

    def test_deal_report_metrics(self, reporter, campaign_with_pacing):
        """Deal report includes fill rate, win rate, spend per deal."""
        report = reporter.deal_report(campaign_with_pacing)

        deal1 = next(
            (d for d in report.deals if d.deal_id == "deal-001"),
            None,
        )
        assert deal1 is not None
        assert deal1.spend == 4000.0
        assert deal1.impressions == 320000
        assert deal1.fill_rate == 0.83
        assert deal1.win_rate == 0.44
        assert deal1.effective_cpm == 12.5

    def test_deal_report_aggregate_stats(self, reporter, campaign_with_pacing):
        """Deal report includes aggregate deal statistics."""
        report = reporter.deal_report(campaign_with_pacing)

        assert report.total_deals == 4
        assert report.total_spend == 12000.0
        assert report.total_impressions == 1030000
        assert report.avg_fill_rate == pytest.approx(0.845, rel=0.01)
        assert report.avg_win_rate == pytest.approx(0.51, rel=0.01)

    def test_deal_report_no_snapshots(self, reporter, sample_campaign):
        """Deal report handles campaign with no pacing snapshots."""
        report = reporter.deal_report(sample_campaign)

        assert len(report.deals) == 0
        assert report.total_deals == 0
        assert report.total_spend == 0.0

    def test_deal_report_to_json(self, reporter, campaign_with_pacing):
        """Deal report can be serialized to JSON."""
        report = reporter.deal_report(campaign_with_pacing)
        json_data = report.to_json()

        parsed = json.loads(json_data)
        assert "deals" in parsed
        assert len(parsed["deals"]) == 4

    def test_deal_report_to_text(self, reporter, campaign_with_pacing):
        """Deal report renders as human-readable text."""
        report = reporter.deal_report(campaign_with_pacing)
        text = report.to_text()

        assert "deal-001" in text
        assert "Fill Rate" in text or "fill_rate" in text


# ---------------------------------------------------------------------------
# Full Campaign Report
# ---------------------------------------------------------------------------


class TestCampaignReport:
    """Test combined campaign report."""

    def test_full_report_combines_all_sections(
        self, reporter, campaign_with_pacing, campaign_store
    ):
        """Full campaign report includes all sub-reports."""
        # Add some creatives
        campaign_store.save_creative_asset(
            campaign_id=campaign_with_pacing,
            asset_name="Test Creative",
            asset_type="video",
            format_spec=json.dumps({"duration_sec": 15}),
            source_url="https://cdn.example.com/test.mp4",
            validation_status="valid",
        )

        report = reporter.full_report(campaign_with_pacing)

        assert isinstance(report, CampaignReport)
        assert report.status_summary is not None
        assert report.pacing_dashboard is not None
        assert report.creative_performance is not None
        assert report.deal_report is not None

    def test_full_report_to_json(self, reporter, campaign_with_pacing):
        """Full report can be serialized to JSON."""
        report = reporter.full_report(campaign_with_pacing)
        json_data = report.to_json()

        parsed = json.loads(json_data)
        assert "status_summary" in parsed
        assert "pacing_dashboard" in parsed
        assert "creative_performance" in parsed
        assert "deal_report" in parsed

    def test_full_report_to_text(self, reporter, campaign_with_pacing):
        """Full report can render as human-readable text."""
        report = reporter.full_report(campaign_with_pacing)
        text = report.to_text()

        assert "CAMPAIGN STATUS SUMMARY" in text
        assert "PACING DASHBOARD" in text
        assert "CREATIVE PERFORMANCE REPORT" in text
        assert "DEAL REPORT" in text

    def test_full_report_for_draft_campaign(self, reporter, sample_draft_campaign):
        """Full report works for draft campaigns with no delivery data."""
        report = reporter.full_report(sample_draft_campaign)

        assert report.status_summary.status == "draft"
        assert report.pacing_dashboard.total_spend == 0.0
        assert len(report.creative_performance.creatives) == 0
        assert len(report.deal_report.deals) == 0


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_report_with_zero_budget(self, reporter, campaign_store):
        """Report handles zero budget gracefully."""
        cid = campaign_store.save_campaign(
            advertiser_id="adv-zero",
            campaign_name="Zero Budget Test",
            status="draft",
            total_budget=0.0,
            currency="USD",
            flight_start="2026-01-01",
            flight_end="2026-12-31",
        )
        summary = reporter.campaign_status_summary(cid)
        assert summary.total_budget == 0.0
        assert summary.delivery_pct == 0.0

    def test_pacing_deviation_critical_alert(self, reporter, campaign_store, pacing_store):
        """Critical alert generated for large pacing deviation."""
        cid = campaign_store.save_campaign(
            advertiser_id="adv-critical",
            campaign_name="Critical Pacing Test",
            status="active",
            total_budget=100000.0,
            currency="USD",
            flight_start="2026-01-01",
            flight_end="2026-12-31",
        )

        pacing_store.save_pacing_snapshot(
            PacingSnapshot(
                campaign_id=cid,
                timestamp=datetime.now(UTC),
                total_budget=100000.0,
                total_spend=60000.0,
                pacing_pct=200.0,
                expected_spend=30000.0,
                deviation_pct=100.0,
                channel_snapshots=[
                    ChannelSnapshot(
                        channel="CTV",
                        allocated_budget=100000.0,
                        spend=60000.0,
                        pacing_pct=200.0,
                        impressions=5000000,
                        effective_cpm=12.0,
                        fill_rate=0.95,
                    ),
                ],
            )
        )

        dashboard = reporter.pacing_dashboard(cid, deviation_threshold=10.0)
        assert any(a.severity == "critical" for a in dashboard.alerts)

    def test_multiple_campaigns_independent(
        self, reporter, campaign_store, sample_campaign, sample_draft_campaign
    ):
        """Reports for different campaigns are independent."""
        summary1 = reporter.campaign_status_summary(sample_campaign)
        summary2 = reporter.campaign_status_summary(sample_draft_campaign)

        assert summary1.campaign_name != summary2.campaign_name
        assert summary1.campaign_id != summary2.campaign_id
