# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Campaign reporting tools for the Ad Buyer System.

Provides visibility into campaign state, delivery progress, pacing
status, creative performance, and deal-level metrics. Supports both
JSON (API) and human-readable summary output formats.

Report types:
  - CampaignStatusSummary: campaign state, budget, delivery %
  - PacingDashboard: expected vs actual delivery, deviation alerts
  - CreativePerformanceReport: per-creative stats and validation summary
  - DealReport: per-deal fill rate, win rate, spend
  - CampaignReport: combined full report with all sections

bead: buyer-f58
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from ..storage.campaign_store import CampaignStore
from ..storage.pacing_store import PacingStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models for report sections
# ---------------------------------------------------------------------------


@dataclass
class CampaignStatusSummary:
    """Campaign status summary with key metrics.

    Attributes:
        campaign_id: Unique campaign identifier.
        campaign_name: Human-readable campaign name.
        advertiser_id: Advertiser that owns this campaign.
        status: Current campaign status (draft, active, completed, etc.).
        total_budget: Total campaign budget in currency units.
        currency: ISO 4217 currency code.
        total_spend: Total spend to date.
        delivery_pct: Budget delivery percentage (spend / budget * 100).
        pacing_pct: Pacing percentage from latest snapshot.
        flight_start: Campaign start date (ISO string).
        flight_end: Campaign end date (ISO string).
        channels: Channel allocation data (JSON-parsed list).
    """

    campaign_id: str
    campaign_name: str
    advertiser_id: str
    status: str
    total_budget: float
    currency: str
    total_spend: float
    delivery_pct: float
    pacing_pct: float
    flight_start: str
    flight_end: str
    channels: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self._to_dict(), indent=2)

    def to_text(self) -> str:
        """Render as human-readable text summary."""
        lines = [
            "=" * 60,
            "CAMPAIGN STATUS SUMMARY",
            "=" * 60,
            f"  Campaign:     {self.campaign_name}",
            f"  ID:           {self.campaign_id}",
            f"  Advertiser:   {self.advertiser_id}",
            f"  Status:       {self.status.upper()}",
            "",
            f"  Budget:       ${self.total_budget:,.2f} {self.currency}",
            f"  Spent:        ${self.total_spend:,.2f}",
            f"  Delivery:     {self.delivery_pct:.1f}%",
            f"  Pacing:       {self.pacing_pct:.1f}%",
            "",
            f"  Flight:       {self.flight_start} to {self.flight_end}",
        ]

        if self.channels:
            lines.append("")
            lines.append("  Channels:")
            for ch in self.channels:
                ch_name = ch.get("channel", "unknown")
                ch_pct = ch.get("budget_pct", 0)
                lines.append(f"    - {ch_name}: {ch_pct * 100:.0f}%")

        lines.append("=" * 60)
        return "\n".join(lines)

    def _to_dict(self) -> dict[str, Any]:
        """Convert to a plain dict."""
        return {
            "campaign_id": self.campaign_id,
            "campaign_name": self.campaign_name,
            "advertiser_id": self.advertiser_id,
            "status": self.status,
            "total_budget": self.total_budget,
            "currency": self.currency,
            "total_spend": self.total_spend,
            "delivery_pct": self.delivery_pct,
            "pacing_pct": self.pacing_pct,
            "flight_start": self.flight_start,
            "flight_end": self.flight_end,
            "channels": self.channels,
        }


@dataclass
class PacingAlert:
    """A pacing deviation alert.

    Attributes:
        severity: Alert severity ("warning" or "critical").
        message: Human-readable alert message.
        channel: The channel affected (if channel-specific).
        deviation_pct: The deviation percentage that triggered the alert.
    """

    severity: str  # "warning" or "critical"
    message: str
    channel: str | None = None
    deviation_pct: float = 0.0

    def _to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "message": self.message,
            "channel": self.channel,
            "deviation_pct": self.deviation_pct,
        }


@dataclass
class ChannelPacingData:
    """Per-channel pacing data for the dashboard.

    Attributes:
        channel: Channel name (CTV, DISPLAY, etc.).
        allocated_budget: Budget allocated to this channel.
        spend: Total spend on this channel.
        pacing_pct: Pacing percentage for this channel.
        impressions: Total impressions delivered.
        effective_cpm: Effective CPM achieved.
        fill_rate: Fill rate for this channel.
    """

    channel: str
    allocated_budget: float
    spend: float
    pacing_pct: float
    impressions: int = 0
    effective_cpm: float = 0.0
    fill_rate: float = 0.0

    def _to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "allocated_budget": self.allocated_budget,
            "spend": self.spend,
            "pacing_pct": self.pacing_pct,
            "impressions": self.impressions,
            "effective_cpm": self.effective_cpm,
            "fill_rate": self.fill_rate,
        }


@dataclass
class PacingDashboard:
    """Pacing dashboard data with expected vs actual delivery.

    Attributes:
        campaign_id: The campaign this dashboard is for.
        total_budget: Total campaign budget.
        total_spend: Total spend to date.
        expected_spend: Expected spend at this point in the flight.
        pacing_pct: Overall pacing percentage.
        deviation_pct: Deviation from expected pacing.
        channel_pacing: Per-channel pacing breakdown.
        alerts: Pacing deviation alerts.
        snapshot_timestamp: When the latest pacing data was captured.
    """

    campaign_id: str
    total_budget: float
    total_spend: float
    expected_spend: float
    pacing_pct: float
    deviation_pct: float
    channel_pacing: list[ChannelPacingData] = field(default_factory=list)
    alerts: list[PacingAlert] = field(default_factory=list)
    snapshot_timestamp: str | None = None

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self._to_dict(), indent=2)

    def to_text(self) -> str:
        """Render as human-readable text summary."""
        lines = [
            "=" * 60,
            "PACING DASHBOARD",
            "=" * 60,
            f"  Campaign:     {self.campaign_id}",
            f"  Budget:       ${self.total_budget:,.2f}",
            f"  Spent:        ${self.total_spend:,.2f}",
            f"  Expected:     ${self.expected_spend:,.2f}",
            f"  Pacing:       {self.pacing_pct:.1f}%",
            f"  Deviation:    {self.deviation_pct:+.1f}%",
        ]

        if self.snapshot_timestamp:
            lines.append(f"  Snapshot:     {self.snapshot_timestamp}")

        if self.channel_pacing:
            lines.append("")
            lines.append("  Channel Breakdown:")
            lines.append(
                f"  {'Channel':<12} {'Budget':>12} {'Spent':>12} "
                f"{'Pacing':>8} {'Imps':>12} {'eCPM':>8} {'Fill':>6}"
            )
            lines.append("  " + "-" * 72)
            for ch in self.channel_pacing:
                lines.append(
                    f"  {ch.channel:<12} "
                    f"${ch.allocated_budget:>11,.2f} "
                    f"${ch.spend:>11,.2f} "
                    f"{ch.pacing_pct:>7.1f}% "
                    f"{ch.impressions:>11,} "
                    f"${ch.effective_cpm:>7.2f} "
                    f"{ch.fill_rate * 100:>5.1f}%"
                )

        if self.alerts:
            lines.append("")
            lines.append("  Alerts:")
            for alert in self.alerts:
                icon = "[!]" if alert.severity == "warning" else "[!!]"
                lines.append(f"    {icon} {alert.message}")

        lines.append("=" * 60)
        return "\n".join(lines)

    def _to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "total_budget": self.total_budget,
            "total_spend": self.total_spend,
            "expected_spend": self.expected_spend,
            "pacing_pct": self.pacing_pct,
            "deviation_pct": self.deviation_pct,
            "channel_pacing": [ch._to_dict() for ch in self.channel_pacing],
            "alerts": [a._to_dict() for a in self.alerts],
            "snapshot_timestamp": self.snapshot_timestamp,
        }


@dataclass
class CreativeStats:
    """Per-creative asset statistics.

    Attributes:
        asset_id: Creative asset identifier.
        asset_name: Human-readable name.
        asset_type: Type of creative (video, display, audio, etc.).
        validation_status: Validation status (valid, pending, invalid).
        format_spec: Format specification dict.
        source_url: URL where the creative file is hosted.
    """

    asset_id: str
    asset_name: str
    asset_type: str
    validation_status: str
    format_spec: dict[str, Any] | None = None
    source_url: str | None = None

    def _to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "asset_name": self.asset_name,
            "asset_type": self.asset_type,
            "validation_status": self.validation_status,
            "format_spec": self.format_spec,
            "source_url": self.source_url,
        }


@dataclass
class CreativePerformanceReport:
    """Creative performance report for a campaign.

    Attributes:
        campaign_id: The campaign this report is for.
        creatives: Per-creative statistics.
        total_assets: Total number of creative assets.
        valid_assets: Number of validated assets.
        pending_assets: Number of assets pending validation.
        invalid_assets: Number of invalid assets.
    """

    campaign_id: str
    creatives: list[CreativeStats] = field(default_factory=list)
    total_assets: int = 0
    valid_assets: int = 0
    pending_assets: int = 0
    invalid_assets: int = 0

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self._to_dict(), indent=2)

    def to_text(self) -> str:
        """Render as human-readable text summary."""
        lines = [
            "=" * 60,
            "CREATIVE PERFORMANCE REPORT",
            "=" * 60,
            f"  Campaign:     {self.campaign_id}",
            f"  Total Assets: {self.total_assets}",
            f"  Valid:         {self.valid_assets}",
            f"  Pending:       {self.pending_assets}",
            f"  Invalid:       {self.invalid_assets}",
        ]

        if self.creatives:
            lines.append("")
            lines.append(f"  {'Name':<30} {'Type':<12} {'Status':<10}")
            lines.append("  " + "-" * 52)
            for c in self.creatives:
                lines.append(f"  {c.asset_name:<30} {c.asset_type:<12} {c.validation_status:<10}")

        lines.append("=" * 60)
        return "\n".join(lines)

    def _to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "creatives": [c._to_dict() for c in self.creatives],
            "total_assets": self.total_assets,
            "valid_assets": self.valid_assets,
            "pending_assets": self.pending_assets,
            "invalid_assets": self.invalid_assets,
        }


@dataclass
class DealStats:
    """Per-deal statistics.

    Attributes:
        deal_id: Deal identifier.
        allocated_budget: Budget allocated to this deal.
        spend: Total spend on this deal.
        impressions: Total impressions delivered.
        effective_cpm: Effective CPM achieved.
        fill_rate: Fill rate for this deal.
        win_rate: Win rate for this deal.
    """

    deal_id: str
    allocated_budget: float = 0.0
    spend: float = 0.0
    impressions: int = 0
    effective_cpm: float = 0.0
    fill_rate: float = 0.0
    win_rate: float = 0.0

    def _to_dict(self) -> dict[str, Any]:
        return {
            "deal_id": self.deal_id,
            "allocated_budget": self.allocated_budget,
            "spend": self.spend,
            "impressions": self.impressions,
            "effective_cpm": self.effective_cpm,
            "fill_rate": self.fill_rate,
            "win_rate": self.win_rate,
        }


@dataclass
class DealReport:
    """Deal-level reporting for a campaign.

    Attributes:
        campaign_id: The campaign this report is for.
        deals: Per-deal statistics.
        total_deals: Total number of deals.
        total_spend: Aggregate spend across all deals.
        total_impressions: Aggregate impressions across all deals.
        avg_fill_rate: Average fill rate across deals.
        avg_win_rate: Average win rate across deals.
    """

    campaign_id: str
    deals: list[DealStats] = field(default_factory=list)
    total_deals: int = 0
    total_spend: float = 0.0
    total_impressions: int = 0
    avg_fill_rate: float = 0.0
    avg_win_rate: float = 0.0

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self._to_dict(), indent=2)

    def to_text(self) -> str:
        """Render as human-readable text summary."""
        lines = [
            "=" * 60,
            "DEAL REPORT",
            "=" * 60,
            f"  Campaign:         {self.campaign_id}",
            f"  Total Deals:      {self.total_deals}",
            f"  Total Spend:      ${self.total_spend:,.2f}",
            f"  Total Impressions:{self.total_impressions:>12,}",
            f"  Avg Fill Rate:    {self.avg_fill_rate * 100:.1f}%",
            f"  Avg Win Rate:     {self.avg_win_rate * 100:.1f}%",
        ]

        if self.deals:
            lines.append("")
            lines.append(
                f"  {'Deal ID':<16} {'Spend':>10} {'Imps':>10} "
                f"{'eCPM':>8} {'Fill Rate':>10} {'Win Rate':>10}"
            )
            lines.append("  " + "-" * 66)
            for d in self.deals:
                lines.append(
                    f"  {d.deal_id:<16} "
                    f"${d.spend:>9,.2f} "
                    f"{d.impressions:>9,} "
                    f"${d.effective_cpm:>7.2f} "
                    f"{d.fill_rate * 100:>9.1f}% "
                    f"{d.win_rate * 100:>9.1f}%"
                )

        lines.append("=" * 60)
        return "\n".join(lines)

    def _to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "deals": [d._to_dict() for d in self.deals],
            "total_deals": self.total_deals,
            "total_spend": self.total_spend,
            "total_impressions": self.total_impressions,
            "avg_fill_rate": self.avg_fill_rate,
            "avg_win_rate": self.avg_win_rate,
        }


@dataclass
class CampaignReport:
    """Combined campaign report with all sections.

    Attributes:
        campaign_id: The campaign this report covers.
        status_summary: Campaign status summary.
        pacing_dashboard: Pacing dashboard data.
        creative_performance: Creative performance report.
        deal_report: Deal-level reporting.
    """

    campaign_id: str
    status_summary: CampaignStatusSummary
    pacing_dashboard: PacingDashboard
    creative_performance: CreativePerformanceReport
    deal_report: DealReport

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(
            {
                "campaign_id": self.campaign_id,
                "status_summary": self.status_summary._to_dict(),
                "pacing_dashboard": self.pacing_dashboard._to_dict(),
                "creative_performance": self.creative_performance._to_dict(),
                "deal_report": self.deal_report._to_dict(),
            },
            indent=2,
        )

    def to_text(self) -> str:
        """Render all sections as human-readable text."""
        sections = [
            self.status_summary.to_text(),
            "",
            self.pacing_dashboard.to_text(),
            "",
            self.creative_performance.to_text(),
            "",
            self.deal_report.to_text(),
        ]
        return "\n".join(sections)


# ---------------------------------------------------------------------------
# Campaign Reporter — main reporting engine
# ---------------------------------------------------------------------------

# Deviation thresholds for alert severity classification.
# Over-pacing by more than 20% from expected is "critical"; otherwise "warning".
_CRITICAL_DEVIATION_THRESHOLD = 20.0


class CampaignReporter:
    """Reporting engine that generates campaign reports from store data.

    Reads campaign, pacing, creative, and deal data from the backing
    stores and produces structured report objects that can be serialized
    to JSON or rendered as human-readable text.

    Args:
        campaign_store: CampaignStore for campaign + creative data.
        pacing_store: PacingStore for pacing snapshot data.
    """

    def __init__(
        self,
        campaign_store: CampaignStore,
        pacing_store: PacingStore,
    ) -> None:
        self._campaign_store = campaign_store
        self._pacing_store = pacing_store

    # ------------------------------------------------------------------
    # Campaign Status Summary
    # ------------------------------------------------------------------

    def campaign_status_summary(self, campaign_id: str) -> CampaignStatusSummary:
        """Generate a campaign status summary.

        Args:
            campaign_id: The campaign to report on.

        Returns:
            CampaignStatusSummary with current state and delivery metrics.

        Raises:
            KeyError: If the campaign does not exist.
        """
        campaign = self._campaign_store.get_campaign(campaign_id)
        if campaign is None:
            raise KeyError(f"Campaign not found: {campaign_id}")

        # Get latest pacing snapshot for spend/pacing data
        latest = self._pacing_store.get_latest_pacing_snapshot(campaign_id)

        total_spend = latest.total_spend if latest else 0.0
        pacing_pct = latest.pacing_pct if latest else 0.0
        total_budget = campaign["total_budget"]

        # Calculate delivery percentage
        if total_budget > 0:
            delivery_pct = (total_spend / total_budget) * 100.0
        else:
            delivery_pct = 0.0

        # Parse channels JSON if present
        channels_raw = campaign.get("channels")
        channels = []
        if channels_raw:
            try:
                channels = json.loads(channels_raw)
            except (json.JSONDecodeError, TypeError):
                channels = []

        return CampaignStatusSummary(
            campaign_id=campaign_id,
            campaign_name=campaign["campaign_name"],
            advertiser_id=campaign["advertiser_id"],
            status=campaign["status"],
            total_budget=total_budget,
            currency=campaign.get("currency", "USD"),
            total_spend=total_spend,
            delivery_pct=delivery_pct,
            pacing_pct=pacing_pct,
            flight_start=campaign["flight_start"],
            flight_end=campaign["flight_end"],
            channels=channels,
        )

    # ------------------------------------------------------------------
    # Pacing Dashboard
    # ------------------------------------------------------------------

    def pacing_dashboard(
        self,
        campaign_id: str,
        deviation_threshold: float = 10.0,
    ) -> PacingDashboard:
        """Generate a pacing dashboard for a campaign.

        Shows expected vs actual delivery with optional deviation alerts.

        Args:
            campaign_id: The campaign to report on.
            deviation_threshold: Minimum deviation percentage to trigger
                an alert. Defaults to 10%.

        Returns:
            PacingDashboard with pacing data and alerts.
        """
        latest = self._pacing_store.get_latest_pacing_snapshot(campaign_id)

        if latest is None:
            campaign = self._campaign_store.get_campaign(campaign_id)
            total_budget = campaign["total_budget"] if campaign else 0.0
            return PacingDashboard(
                campaign_id=campaign_id,
                total_budget=total_budget,
                total_spend=0.0,
                expected_spend=0.0,
                pacing_pct=0.0,
                deviation_pct=0.0,
            )

        # Build per-channel pacing data
        channel_pacing = []
        for ch in latest.channel_snapshots:
            channel_pacing.append(
                ChannelPacingData(
                    channel=ch.channel,
                    allocated_budget=ch.allocated_budget,
                    spend=ch.spend,
                    pacing_pct=ch.pacing_pct,
                    impressions=ch.impressions,
                    effective_cpm=ch.effective_cpm,
                    fill_rate=ch.fill_rate,
                )
            )

        # Generate alerts for channels exceeding deviation threshold
        alerts = self._generate_pacing_alerts(latest, deviation_threshold)

        # Format snapshot timestamp
        snapshot_ts = None
        if latest.timestamp:
            snapshot_ts = latest.timestamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        return PacingDashboard(
            campaign_id=campaign_id,
            total_budget=latest.total_budget,
            total_spend=latest.total_spend,
            expected_spend=latest.expected_spend,
            pacing_pct=latest.pacing_pct,
            deviation_pct=latest.deviation_pct,
            channel_pacing=channel_pacing,
            alerts=alerts,
            snapshot_timestamp=snapshot_ts,
        )

    def _generate_pacing_alerts(
        self,
        snapshot: Any,
        threshold: float,
    ) -> list[PacingAlert]:
        """Generate pacing deviation alerts from a snapshot.

        Args:
            snapshot: PacingSnapshot with channel data.
            threshold: Minimum deviation to trigger an alert.

        Returns:
            List of PacingAlert objects for channels exceeding the threshold.
        """
        alerts: list[PacingAlert] = []

        # Overall campaign deviation
        overall_dev = abs(snapshot.deviation_pct)
        if overall_dev >= threshold:
            direction = "over" if snapshot.deviation_pct > 0 else "under"
            severity = "critical" if overall_dev >= _CRITICAL_DEVIATION_THRESHOLD else "warning"
            alerts.append(
                PacingAlert(
                    severity=severity,
                    message=(f"Campaign is {direction}-pacing by {overall_dev:.1f}%"),
                    deviation_pct=snapshot.deviation_pct,
                )
            )

        # Per-channel deviation
        for ch in snapshot.channel_snapshots:
            # Channel deviation = channel pacing % - 100
            ch_dev = abs(ch.pacing_pct - 100.0)
            if ch_dev >= threshold:
                direction = "over" if ch.pacing_pct > 100 else "under"
                severity = "critical" if ch_dev >= _CRITICAL_DEVIATION_THRESHOLD else "warning"
                alerts.append(
                    PacingAlert(
                        severity=severity,
                        message=(f"{ch.channel} is {direction}-pacing by {ch_dev:.1f}%"),
                        channel=ch.channel,
                        deviation_pct=ch.pacing_pct - 100.0,
                    )
                )

        return alerts

    # ------------------------------------------------------------------
    # Creative Performance Report
    # ------------------------------------------------------------------

    def creative_performance_report(self, campaign_id: str) -> CreativePerformanceReport:
        """Generate a creative performance report for a campaign.

        Lists all creative assets with their type and validation status,
        plus a validation summary (counts of valid, pending, invalid).

        Args:
            campaign_id: The campaign to report on.

        Returns:
            CreativePerformanceReport with per-creative stats.
        """
        assets = self._campaign_store.list_creative_assets(campaign_id=campaign_id)

        creatives = []
        valid_count = 0
        pending_count = 0
        invalid_count = 0

        for asset in assets:
            # Parse format_spec if it's a JSON string
            format_spec = asset.get("format_spec")
            if isinstance(format_spec, str):
                try:
                    format_spec = json.loads(format_spec)
                except (json.JSONDecodeError, TypeError):
                    format_spec = None

            validation_status = asset.get("validation_status", "pending")

            creatives.append(
                CreativeStats(
                    asset_id=asset["asset_id"],
                    asset_name=asset["asset_name"],
                    asset_type=asset["asset_type"],
                    validation_status=validation_status,
                    format_spec=format_spec,
                    source_url=asset.get("source_url"),
                )
            )

            if validation_status == "valid":
                valid_count += 1
            elif validation_status == "pending":
                pending_count += 1
            elif validation_status == "invalid":
                invalid_count += 1

        return CreativePerformanceReport(
            campaign_id=campaign_id,
            creatives=creatives,
            total_assets=len(creatives),
            valid_assets=valid_count,
            pending_assets=pending_count,
            invalid_assets=invalid_count,
        )

    # ------------------------------------------------------------------
    # Deal-Level Reporting
    # ------------------------------------------------------------------

    def deal_report(self, campaign_id: str) -> DealReport:
        """Generate a deal-level report for a campaign.

        Uses the latest pacing snapshot's deal_snapshots to provide
        per-deal fill rate, win rate, spend, and impression data.

        Args:
            campaign_id: The campaign to report on.

        Returns:
            DealReport with per-deal metrics and aggregates.
        """
        latest = self._pacing_store.get_latest_pacing_snapshot(campaign_id)

        if latest is None or not latest.deal_snapshots:
            return DealReport(campaign_id=campaign_id)

        deals = []
        total_spend = 0.0
        total_impressions = 0
        fill_rates = []
        win_rates = []

        for ds in latest.deal_snapshots:
            deals.append(
                DealStats(
                    deal_id=ds.deal_id,
                    allocated_budget=ds.allocated_budget,
                    spend=ds.spend,
                    impressions=ds.impressions,
                    effective_cpm=ds.effective_cpm,
                    fill_rate=ds.fill_rate,
                    win_rate=ds.win_rate,
                )
            )
            total_spend += ds.spend
            total_impressions += ds.impressions
            fill_rates.append(ds.fill_rate)
            win_rates.append(ds.win_rate)

        avg_fill = sum(fill_rates) / len(fill_rates) if fill_rates else 0.0
        avg_win = sum(win_rates) / len(win_rates) if win_rates else 0.0

        return DealReport(
            campaign_id=campaign_id,
            deals=deals,
            total_deals=len(deals),
            total_spend=total_spend,
            total_impressions=total_impressions,
            avg_fill_rate=avg_fill,
            avg_win_rate=avg_win,
        )

    # ------------------------------------------------------------------
    # Full Campaign Report
    # ------------------------------------------------------------------

    def full_report(
        self,
        campaign_id: str,
        deviation_threshold: float = 10.0,
    ) -> CampaignReport:
        """Generate a comprehensive campaign report with all sections.

        Combines status summary, pacing dashboard, creative performance,
        and deal reporting into a single report object.

        Args:
            campaign_id: The campaign to report on.
            deviation_threshold: Pacing deviation threshold for alerts.

        Returns:
            CampaignReport containing all sub-reports.

        Raises:
            KeyError: If the campaign does not exist.
        """
        return CampaignReport(
            campaign_id=campaign_id,
            status_summary=self.campaign_status_summary(campaign_id),
            pacing_dashboard=self.pacing_dashboard(campaign_id, deviation_threshold),
            creative_performance=self.creative_performance_report(campaign_id),
            deal_report=self.deal_report(campaign_id),
        )
