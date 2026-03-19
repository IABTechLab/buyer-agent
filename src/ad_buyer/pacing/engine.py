# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Budget Pacing & Reallocation Engine.

Monitors campaign spend against plan, detects pacing deviations, and
proposes cross-channel budget reallocations. Integrates with the
PacingStore for snapshot persistence and EventBus for pacing events.

Key capabilities:
  - Linear pacing model: expected spend proportional to elapsed time
  - Pacing deviation detection: warning and critical thresholds
  - Cross-channel reallocation: shift budget from underpacing to overpacing
  - Deal-level pacing: per-deal spend tracking and alerts
  - Pacing snapshot generation: aggregate campaign pacing state
  - Event emission: PACING_SNAPSHOT_TAKEN, PACING_DEVIATION_DETECTED,
    PACING_REALLOCATION_RECOMMENDED

Reference: Campaign Automation Strategic Plan, Section 7.3
bead: buyer-9zz (2C: Budget Pacing & Reallocation)
"""

import asyncio
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..events.bus import EventBus
from ..events.models import Event, EventType
from ..models.campaign import (
    ChannelSnapshot,
    DealSnapshot,
    PacingRecommendation,
    PacingSnapshot,
    RecommendationStatus,
    RecommendationType,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class PacingConfig(BaseModel):
    """Configuration for pacing deviation thresholds and reallocation limits.

    Attributes:
        underpacing_warning_pct: Deviation percentage triggering a warning
            when the campaign is underpacing. (e.g. 10.0 = 10% below expected)
        underpacing_critical_pct: Deviation percentage triggering a critical
            alert when severely underpacing.
        overpacing_warning_pct: Deviation percentage triggering a warning
            when the campaign is overpacing.
        overpacing_critical_pct: Deviation percentage triggering a critical
            alert when severely overpacing.
        min_reallocation_amount: Minimum budget amount for a reallocation
            proposal (smaller amounts are not worth the operational cost).
        max_reallocation_pct: Maximum percentage of total budget that can
            be reallocated in a single proposal.
    """

    underpacing_warning_pct: float = 10.0
    underpacing_critical_pct: float = 25.0
    overpacing_warning_pct: float = 10.0
    overpacing_critical_pct: float = 25.0
    min_reallocation_amount: float = 100.0
    max_reallocation_pct: float = 30.0


# ---------------------------------------------------------------------------
# Alert models
# ---------------------------------------------------------------------------


class PacingAlertLevel(str, Enum):
    """Severity level for pacing deviation alerts."""

    WARNING = "warning"
    CRITICAL = "critical"


class PacingAlert(BaseModel):
    """Pacing deviation alert.

    Attributes:
        level: Severity (warning or critical).
        direction: "underpacing" or "overpacing".
        deviation_pct: Actual deviation from expected pacing.
        message: Human-readable description of the alert.
    """

    level: PacingAlertLevel
    direction: str  # "underpacing" or "overpacing"
    deviation_pct: float
    message: str = ""


# ---------------------------------------------------------------------------
# Reallocation proposal
# ---------------------------------------------------------------------------


class ReallocationProposal(BaseModel):
    """Proposal to move budget from one channel to another.

    Attributes:
        source_channel: Channel to take budget from (underpacing).
        target_channel: Channel to give budget to (overpacing/performing).
        amount: Dollar amount to reallocate.
        reason: Human-readable justification for the reallocation.
    """

    source_channel: str
    target_channel: str
    amount: float
    reason: str = ""


# ---------------------------------------------------------------------------
# Budget Pacing Engine
# ---------------------------------------------------------------------------


class BudgetPacingEngine:
    """Core pacing engine for campaign budget monitoring and reallocation.

    Provides linear pacing calculations, deviation detection, cross-channel
    reallocation proposals, deal-level pacing, and snapshot generation.

    Args:
        config: PacingConfig with deviation thresholds.
        event_bus: Optional EventBus for emitting pacing events.
    """

    def __init__(
        self,
        config: Optional[PacingConfig] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self.config = config or PacingConfig()
        self.event_bus = event_bus

    # ------------------------------------------------------------------
    # Event emission helpers
    # ------------------------------------------------------------------

    def _emit_sync(
        self,
        event_type: EventType,
        campaign_id: str = "",
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        """Emit an event to the event bus synchronously. Fail-open.

        For InMemoryEventBus: directly calls the synchronous internals
        (append to list + fire callbacks) to avoid event-loop issues.
        For other implementations: falls back to asyncio run helpers.
        """
        if self.event_bus is None:
            return
        try:
            event = Event(
                event_type=event_type,
                campaign_id=campaign_id,
                payload=payload or {},
            )

            # Fast path: for InMemoryEventBus, directly manipulate
            # the internal state. The publish() method is async in
            # signature but its body is purely synchronous: it appends
            # to a list and calls subscriber callbacks.
            from ..events.bus import InMemoryEventBus

            if isinstance(self.event_bus, InMemoryEventBus):
                bus = self.event_bus
                bus._events.append(event)
                logger.info(
                    "Event published: %s (id=%s)",
                    event.event_type,
                    event.event_id,
                )
                for cb in bus._subscribers.get(event.event_type.value, []):
                    try:
                        cb(event)
                    except Exception as e:
                        logger.error(
                            "Subscriber error for %s: %s",
                            event.event_type, e,
                        )
                for cb in bus._subscribers.get("*", []):
                    try:
                        cb(event)
                    except Exception as e:
                        logger.error("Subscriber error (wildcard): %s", e)
                return

            # Generic async path for other bus implementations
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(self.event_bus.publish(event))
                else:
                    loop.run_until_complete(self.event_bus.publish(event))
            except RuntimeError:
                asyncio.run(self.event_bus.publish(event))
        except Exception as exc:
            logger.warning("Failed to emit event %s: %s", event_type, exc)

    # ------------------------------------------------------------------
    # Spend tracking against plan
    # ------------------------------------------------------------------

    def calculate_expected_spend(
        self,
        total_budget: float,
        flight_start: datetime,
        flight_end: datetime,
        current_time: datetime,
    ) -> float:
        """Calculate expected spend at the current point in the campaign.

        Uses a linear pacing model: expected spend is proportional to
        the fraction of the flight window that has elapsed.

        Args:
            total_budget: Total campaign budget.
            flight_start: Campaign start datetime (UTC).
            flight_end: Campaign end datetime (UTC).
            current_time: Current datetime (UTC).

        Returns:
            Expected spend at current_time, capped at [0, total_budget].
        """
        if total_budget <= 0:
            return 0.0

        # Clamp current_time to the flight window
        if current_time <= flight_start:
            return 0.0
        if current_time >= flight_end:
            return total_budget

        # Total flight duration in seconds
        total_duration = (flight_end - flight_start).total_seconds()
        if total_duration <= 0:
            return total_budget

        # Elapsed time in seconds
        elapsed = (current_time - flight_start).total_seconds()

        # Linear pacing fraction
        fraction = elapsed / total_duration
        return round(total_budget * fraction, 2)

    def calculate_pacing_pct(
        self,
        actual_spend: float,
        expected_spend: float,
    ) -> float:
        """Calculate pacing percentage: (actual / expected) * 100.

        Args:
            actual_spend: Actual spend to date.
            expected_spend: Expected spend to date.

        Returns:
            Pacing percentage. 100% = on pace, <100% = underpacing,
            >100% = overpacing. Returns 0.0 if expected_spend is 0.
        """
        if expected_spend <= 0:
            return 0.0
        return round((actual_spend / expected_spend) * 100.0, 2)

    def calculate_deviation_pct(
        self,
        actual_spend: float,
        expected_spend: float,
    ) -> float:
        """Calculate deviation from expected pacing.

        Args:
            actual_spend: Actual spend to date.
            expected_spend: Expected spend to date.

        Returns:
            Deviation percentage. Negative = underpacing, positive = overpacing.
            Returns 0.0 if expected_spend is 0.
        """
        pacing_pct = self.calculate_pacing_pct(actual_spend, expected_spend)
        if pacing_pct == 0.0 and expected_spend <= 0:
            return 0.0
        return round(pacing_pct - 100.0, 2)

    # ------------------------------------------------------------------
    # Pacing deviation detection
    # ------------------------------------------------------------------

    def detect_deviation(
        self,
        actual_spend: float,
        expected_spend: float,
    ) -> Optional[PacingAlert]:
        """Detect pacing deviation and return an alert if thresholds are exceeded.

        Thresholds are exclusive: the deviation must strictly exceed the
        threshold to trigger an alert.

        Args:
            actual_spend: Actual spend to date.
            expected_spend: Expected spend to date.

        Returns:
            PacingAlert if deviation exceeds a threshold, None otherwise.
        """
        if expected_spend <= 0:
            return None

        deviation = self.calculate_deviation_pct(actual_spend, expected_spend)
        abs_deviation = abs(deviation)

        if deviation < 0:
            # Underpacing
            if abs_deviation > self.config.underpacing_critical_pct:
                return PacingAlert(
                    level=PacingAlertLevel.CRITICAL,
                    direction="underpacing",
                    deviation_pct=deviation,
                    message=(
                        f"Critical underpacing: {deviation:.1f}% deviation "
                        f"(threshold: -{self.config.underpacing_critical_pct}%)"
                    ),
                )
            elif abs_deviation > self.config.underpacing_warning_pct:
                return PacingAlert(
                    level=PacingAlertLevel.WARNING,
                    direction="underpacing",
                    deviation_pct=deviation,
                    message=(
                        f"Warning underpacing: {deviation:.1f}% deviation "
                        f"(threshold: -{self.config.underpacing_warning_pct}%)"
                    ),
                )
        elif deviation > 0:
            # Overpacing
            if abs_deviation > self.config.overpacing_critical_pct:
                return PacingAlert(
                    level=PacingAlertLevel.CRITICAL,
                    direction="overpacing",
                    deviation_pct=deviation,
                    message=(
                        f"Critical overpacing: +{deviation:.1f}% deviation "
                        f"(threshold: +{self.config.overpacing_critical_pct}%)"
                    ),
                )
            elif abs_deviation > self.config.overpacing_warning_pct:
                return PacingAlert(
                    level=PacingAlertLevel.WARNING,
                    direction="overpacing",
                    deviation_pct=deviation,
                    message=(
                        f"Warning overpacing: +{deviation:.1f}% deviation "
                        f"(threshold: +{self.config.overpacing_warning_pct}%)"
                    ),
                )

        return None

    # ------------------------------------------------------------------
    # Cross-channel budget reallocation
    # ------------------------------------------------------------------

    def propose_reallocations(
        self,
        channel_snapshots: list[ChannelSnapshot],
        total_budget: float,
        expected_spend: float,
    ) -> list[ReallocationProposal]:
        """Propose budget reallocations between channels.

        Identifies underpacing channels as budget sources and overpacing
        channels as budget targets. Respects min/max reallocation constraints.

        Args:
            channel_snapshots: Current pacing state per channel.
            total_budget: Total campaign budget.
            expected_spend: Expected total spend at current time.

        Returns:
            List of ReallocationProposal objects. Empty if no reallocation needed.
        """
        if not channel_snapshots or total_budget <= 0 or expected_spend <= 0:
            return []

        max_amount = total_budget * (self.config.max_reallocation_pct / 100.0)

        # Classify channels by pacing state
        underpacing_channels: list[tuple[ChannelSnapshot, float]] = []
        overpacing_channels: list[tuple[ChannelSnapshot, float]] = []

        for ch in channel_snapshots:
            if ch.allocated_budget <= 0:
                continue

            # Calculate expected spend for this channel proportionally
            ch_expected = expected_spend * (ch.allocated_budget / total_budget)
            if ch_expected <= 0:
                continue

            ch_deviation = self.calculate_deviation_pct(ch.spend, ch_expected)

            if ch_deviation < -self.config.underpacing_warning_pct:
                # Underpacing: potential source of budget
                underspend = ch_expected - ch.spend
                underpacing_channels.append((ch, underspend))
            elif ch_deviation > self.config.overpacing_warning_pct:
                # Overpacing: potential target for budget
                overspend = ch.spend - ch_expected
                overpacing_channels.append((ch, overspend))

        if not underpacing_channels or not overpacing_channels:
            return []

        # Generate proposals: pair underpacing sources with overpacing targets
        proposals: list[ReallocationProposal] = []

        for source, underspend in underpacing_channels:
            for target, overspend in overpacing_channels:
                # Reallocation amount: take the smaller of the two imbalances,
                # capped by max_reallocation_pct
                amount = min(underspend, overspend, max_amount)
                amount = round(amount, 2)

                if amount < self.config.min_reallocation_amount:
                    continue

                proposals.append(ReallocationProposal(
                    source_channel=source.channel,
                    target_channel=target.channel,
                    amount=amount,
                    reason=(
                        f"{source.channel} underpacing "
                        f"(spend: ${source.spend:,.0f} vs "
                        f"expected: ${expected_spend * (source.allocated_budget / total_budget):,.0f}), "
                        f"{target.channel} overpacing "
                        f"(spend: ${target.spend:,.0f} vs "
                        f"expected: ${expected_spend * (target.allocated_budget / total_budget):,.0f})"
                    ),
                ))

        return proposals

    # ------------------------------------------------------------------
    # Deal-level spend tracking
    # ------------------------------------------------------------------

    def calculate_deal_pacing(
        self,
        deal_snapshot: DealSnapshot,
        flight_start: datetime,
        flight_end: datetime,
        current_time: datetime,
    ) -> dict[str, Any]:
        """Calculate pacing metrics for an individual deal.

        Args:
            deal_snapshot: Current deal spend state.
            flight_start: Deal flight start.
            flight_end: Deal flight end.
            current_time: Current datetime.

        Returns:
            Dict with expected_spend, pacing_pct, deviation_pct, and
            optional alert.
        """
        expected = self.calculate_expected_spend(
            total_budget=deal_snapshot.allocated_budget,
            flight_start=flight_start,
            flight_end=flight_end,
            current_time=current_time,
        )

        pacing_pct = self.calculate_pacing_pct(deal_snapshot.spend, expected)
        deviation_pct = self.calculate_deviation_pct(deal_snapshot.spend, expected)
        alert = self.detect_deviation(deal_snapshot.spend, expected)

        return {
            "deal_id": deal_snapshot.deal_id,
            "allocated_budget": deal_snapshot.allocated_budget,
            "actual_spend": deal_snapshot.spend,
            "expected_spend": expected,
            "pacing_pct": pacing_pct,
            "deviation_pct": deviation_pct,
            "alert": alert,
        }

    # ------------------------------------------------------------------
    # Full pacing snapshot generation
    # ------------------------------------------------------------------

    def generate_snapshot(
        self,
        campaign_id: str,
        total_budget: float,
        flight_start: datetime,
        flight_end: datetime,
        current_time: datetime,
        channel_data: dict[str, dict[str, Any]],
        deal_data: list[dict[str, Any]],
    ) -> PacingSnapshot:
        """Generate a complete pacing snapshot for a campaign.

        Computes campaign-level, channel-level, and deal-level pacing,
        and generates reallocation recommendations if warranted.

        Args:
            campaign_id: Campaign identifier.
            total_budget: Total campaign budget.
            flight_start: Campaign flight start.
            flight_end: Campaign flight end.
            current_time: Current time for pacing calculation.
            channel_data: Dict keyed by channel name, values are dicts with
                'allocated_budget', 'spend', and optionally 'impressions',
                'effective_cpm', 'fill_rate'.
            deal_data: List of dicts with 'deal_id', 'allocated_budget',
                'spend', and optionally 'impressions', 'effective_cpm',
                'fill_rate', 'win_rate'.

        Returns:
            PacingSnapshot model with all pacing data and recommendations.
        """
        # Calculate expected spend
        expected_spend = self.calculate_expected_spend(
            total_budget=total_budget,
            flight_start=flight_start,
            flight_end=flight_end,
            current_time=current_time,
        )

        # Build channel snapshots
        channel_snapshots: list[ChannelSnapshot] = []
        total_spend = 0.0

        for ch_name, ch_info in channel_data.items():
            ch_budget = ch_info.get("allocated_budget", 0.0)
            ch_spend = ch_info.get("spend", 0.0)
            total_spend += ch_spend

            # Calculate channel-level pacing
            ch_expected = (
                expected_spend * (ch_budget / total_budget)
                if total_budget > 0
                else 0.0
            )
            ch_pacing_pct = self.calculate_pacing_pct(ch_spend, ch_expected)

            channel_snapshots.append(ChannelSnapshot(
                channel=ch_name,
                allocated_budget=ch_budget,
                spend=ch_spend,
                pacing_pct=ch_pacing_pct,
                impressions=ch_info.get("impressions", 0),
                effective_cpm=ch_info.get("effective_cpm", 0.0),
                fill_rate=ch_info.get("fill_rate", 0.0),
            ))

        # Build deal snapshots
        deal_snapshots: list[DealSnapshot] = []
        for deal_info in deal_data:
            deal_snapshots.append(DealSnapshot(
                deal_id=deal_info["deal_id"],
                allocated_budget=deal_info.get("allocated_budget", 0.0),
                spend=deal_info.get("spend", 0.0),
                impressions=deal_info.get("impressions", 0),
                effective_cpm=deal_info.get("effective_cpm", 0.0),
                fill_rate=deal_info.get("fill_rate", 0.0),
                win_rate=deal_info.get("win_rate", 0.0),
            ))

        # Calculate campaign-level pacing
        campaign_pacing_pct = self.calculate_pacing_pct(total_spend, expected_spend)
        campaign_deviation_pct = self.calculate_deviation_pct(total_spend, expected_spend)

        # Generate reallocation recommendations
        recommendations: list[PacingRecommendation] = []
        proposals = self.propose_reallocations(
            channel_snapshots=channel_snapshots,
            total_budget=total_budget,
            expected_spend=expected_spend,
        )

        for proposal in proposals:
            recommendations.append(PacingRecommendation(
                type=RecommendationType.REALLOCATE,
                source_channel=proposal.source_channel,
                target_channel=proposal.target_channel,
                amount=proposal.amount,
                reason=proposal.reason,
                status=RecommendationStatus.PENDING,
            ))

        # Build the snapshot
        snapshot = PacingSnapshot(
            campaign_id=campaign_id,
            timestamp=current_time,
            total_budget=total_budget,
            total_spend=total_spend,
            pacing_pct=campaign_pacing_pct,
            expected_spend=expected_spend,
            deviation_pct=campaign_deviation_pct,
            channel_snapshots=channel_snapshots,
            deal_snapshots=deal_snapshots,
            recommendations=recommendations,
        )

        # Emit events
        self._emit_snapshot_taken(campaign_id, snapshot)
        self._emit_deviation_if_needed(campaign_id, total_spend, expected_spend)
        self._emit_reallocation_if_needed(campaign_id, proposals)

        return snapshot

    # ------------------------------------------------------------------
    # Event emission for pacing lifecycle
    # ------------------------------------------------------------------

    def _emit_snapshot_taken(
        self, campaign_id: str, snapshot: PacingSnapshot
    ) -> None:
        """Emit PACING_SNAPSHOT_TAKEN event."""
        self._emit_sync(
            EventType.PACING_SNAPSHOT_TAKEN,
            campaign_id=campaign_id,
            payload={
                "snapshot_id": snapshot.snapshot_id,
                "total_budget": snapshot.total_budget,
                "total_spend": snapshot.total_spend,
                "pacing_pct": snapshot.pacing_pct,
                "deviation_pct": snapshot.deviation_pct,
                "channels": len(snapshot.channel_snapshots),
                "deals": len(snapshot.deal_snapshots),
            },
        )

    def _emit_deviation_if_needed(
        self,
        campaign_id: str,
        actual_spend: float,
        expected_spend: float,
    ) -> None:
        """Emit PACING_DEVIATION_DETECTED if deviation exceeds threshold."""
        alert = self.detect_deviation(actual_spend, expected_spend)
        if alert is not None:
            self._emit_sync(
                EventType.PACING_DEVIATION_DETECTED,
                campaign_id=campaign_id,
                payload={
                    "alert_level": alert.level.value,
                    "direction": alert.direction,
                    "deviation_pct": alert.deviation_pct,
                    "message": alert.message,
                },
            )

    def _emit_reallocation_if_needed(
        self,
        campaign_id: str,
        proposals: list[ReallocationProposal],
    ) -> None:
        """Emit PACING_REALLOCATION_RECOMMENDED for each proposal."""
        for proposal in proposals:
            self._emit_sync(
                EventType.PACING_REALLOCATION_RECOMMENDED,
                campaign_id=campaign_id,
                payload={
                    "source_channel": proposal.source_channel,
                    "target_channel": proposal.target_channel,
                    "amount": proposal.amount,
                    "reason": proposal.reason,
                },
            )
