# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Campaign Automation step-through demo app (ar-llj4, ar-uxpw).

Interactive Flask app that lets a user step through the entire Campaign
Automation pipeline, one stage at a time, with approval at each step.

6 stages:
  1. Enter Brief      -- Submit a campaign brief, create campaign in DRAFT
  2. Review Plan      -- View channel breakdown, approve plan -> PLANNING
  3. Review Deals     -- View booked deals, approve booking -> BOOKING
  4. Review Creative  -- View creative matching, approve -> READY
  5. Campaign Ready   -- View full campaign report, activate button
  6. Active Campaign  -- Pacing dashboard, alerts, reallocations, controls

Run standalone:
    cd ad_buyer_system && source venv/bin/activate
    python -m ad_buyer.demo.campaign_demo
    # Opens on http://localhost:5055

Uses real pipeline modules:
  - CampaignPipeline (campaign_pipeline.py)
  - ApprovalGate (approval.py)
  - CampaignReporter (campaign_report.py)
  - BudgetPacingEngine (pacing/engine.py)
  - EventBus (events/)

bead: ar-llj4, ar-uxpw
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from flask import Flask, jsonify, render_template, request

from ..events.bus import EventBus, InMemoryEventBus
from ..events.models import Event, EventType
from ..models.campaign import (
    ChannelSnapshot,
    DealSnapshot,
    PacingSnapshot,
)
from ..models.campaign_brief import (
    CampaignBrief,
    ChannelAllocation,
    ChannelType,
    parse_campaign_brief,
)
from ..models.state_machine import CampaignStatus
from ..pacing.engine import BudgetPacingEngine, PacingConfig
from ..reporting.campaign_report import CampaignReporter
from ..storage.campaign_store import CampaignStore
from ..storage.pacing_store import PacingStore

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"


# ---------------------------------------------------------------------------
# Sample briefs for quick demo
# ---------------------------------------------------------------------------


def _build_sample_briefs() -> list[dict[str, Any]]:
    """Return pre-built sample campaign briefs."""
    return [
        {
            "name": "Multi-Channel CTV + Display ($500K)",
            "brief": {
                "advertiser_id": "ADV-ACME-001",
                "campaign_name": "ACME Q3 Brand Awareness",
                "objective": "AWARENESS",
                "total_budget": 500000,
                "currency": "USD",
                "flight_start": "2026-07-01",
                "flight_end": "2026-09-30",
                "channels": [
                    {"channel": "CTV", "budget_pct": 60, "format_prefs": ["video_30s", "video_15s"]},
                    {"channel": "DISPLAY", "budget_pct": 40, "format_prefs": ["300x250", "728x90", "160x600"]},
                ],
                "target_audience": ["IAB-AUD-1001", "IAB-AUD-1045"],
                "kpis": [
                    {"metric": "CPM", "target_value": 20.0},
                    {"metric": "VCR", "target_value": 75.0},
                ],
                "approval_config": {
                    "plan_review": True,
                    "booking": True,
                    "creative": True,
                    "pacing_adjustment": False,
                },
            },
        },
        {
            "name": "CTV + Audio + Native ($250K)",
            "brief": {
                "advertiser_id": "ADV-GLOBEX-002",
                "campaign_name": "Globex Summer Reach Campaign",
                "objective": "REACH",
                "total_budget": 250000,
                "currency": "USD",
                "flight_start": "2026-06-15",
                "flight_end": "2026-08-31",
                "channels": [
                    {"channel": "CTV", "budget_pct": 50, "format_prefs": ["video_15s"]},
                    {"channel": "AUDIO", "budget_pct": 30, "format_prefs": ["audio_30s"]},
                    {"channel": "NATIVE", "budget_pct": 20, "format_prefs": ["native_article"]},
                ],
                "target_audience": ["IAB-AUD-2001", "IAB-AUD-2022", "IAB-AUD-2030"],
                "approval_config": {
                    "plan_review": True,
                    "booking": True,
                    "creative": True,
                    "pacing_adjustment": False,
                },
            },
        },
        {
            "name": "Display-Only Performance ($100K)",
            "brief": {
                "advertiser_id": "ADV-INITECH-003",
                "campaign_name": "Initech Q4 Conversion Drive",
                "objective": "CONVERSION",
                "total_budget": 100000,
                "currency": "USD",
                "flight_start": "2026-10-01",
                "flight_end": "2026-12-31",
                "channels": [
                    {"channel": "DISPLAY", "budget_pct": 100, "format_prefs": ["300x250", "320x50"]},
                ],
                "target_audience": ["IAB-AUD-3001"],
                "kpis": [
                    {"metric": "CPC", "target_value": 2.50},
                    {"metric": "CTR", "target_value": 1.5},
                ],
                "approval_config": {
                    "plan_review": True,
                    "booking": True,
                    "creative": True,
                    "pacing_adjustment": False,
                },
            },
        },
    ]


# ---------------------------------------------------------------------------
# Demo pipeline helper -- simulates booking + creative without real sellers
# ---------------------------------------------------------------------------


class DemoPipelineHelper:
    """Simplified pipeline helper that simulates the real pipeline stages.

    For the demo we cannot run the full MultiSellerOrchestrator (no real
    seller endpoints), so we simulate booking results and creative matching
    with realistic mock data. State transitions use the real CampaignStore
    (with state machine validation).
    """

    def __init__(
        self,
        campaign_store: CampaignStore,
        pacing_store: PacingStore,
        event_bus: InMemoryEventBus,
    ) -> None:
        self._store = campaign_store
        self._pacing_store = pacing_store
        self._event_bus = event_bus

        # Internal caches
        self._plans: dict[str, dict] = {}
        self._booking_results: dict[str, dict] = {}
        self._creative_results: dict[str, list] = {}

    def _emit_sync(self, event_type: EventType, campaign_id: str = "",
                   payload: Optional[dict] = None) -> None:
        """Emit an event synchronously to the InMemoryEventBus."""
        event = Event(
            event_type=event_type,
            campaign_id=campaign_id,
            payload=payload or {},
        )
        self._event_bus._events.append(event)

    # -- Stage 1: Ingest brief ---------------------------------------------

    def ingest_brief(self, brief_data: dict[str, Any]) -> str:
        """Parse brief, create campaign in DRAFT.

        Returns campaign_id.
        """
        # Validate the brief using the real schema
        brief = parse_campaign_brief(brief_data)

        # Build store-compatible dict
        store_brief = {
            "advertiser_id": brief.advertiser_id,
            "campaign_name": brief.campaign_name,
            "total_budget": brief.total_budget,
            "currency": brief.currency,
            "flight_start": brief.flight_start.isoformat(),
            "flight_end": brief.flight_end.isoformat(),
            "channels": json.dumps(
                [ch.model_dump(mode="json") for ch in brief.channels]
            ),
            "target_audience": json.dumps(brief.target_audience),
        }
        if brief.kpis:
            store_brief["kpis"] = json.dumps(
                [k.model_dump(mode="json") for k in brief.kpis]
            )
        if brief.approval_config:
            store_brief["approval_config"] = json.dumps(
                brief.approval_config.model_dump(mode="json")
            )

        campaign_id = self._store.create_campaign(store_brief)

        self._emit_sync(
            EventType.CAMPAIGN_CREATED,
            campaign_id=campaign_id,
            payload={
                "campaign_name": brief.campaign_name,
                "total_budget": brief.total_budget,
                "channels": [ch.channel.value for ch in brief.channels],
            },
        )
        return campaign_id

    # -- Stage 2: Plan campaign --------------------------------------------

    def plan_campaign(self, campaign_id: str) -> dict[str, Any]:
        """Transition to PLANNING, produce channel plans.

        Returns plan dict.
        """
        campaign = self._store.get_campaign(campaign_id)
        if campaign is None:
            raise KeyError(f"Campaign not found: {campaign_id}")

        # Transition DRAFT -> PLANNING
        self._store.start_planning(campaign_id)

        # Parse channels from stored JSON
        channels_raw = campaign.get("channels", "[]")
        if isinstance(channels_raw, str):
            channels_raw = json.loads(channels_raw)

        total_budget = campaign["total_budget"]

        # Channel-to-media-type mapping
        media_type_map = {
            "CTV": "ctv", "DISPLAY": "display", "AUDIO": "audio",
            "NATIVE": "native", "DOOH": "dooh", "LINEAR_TV": "linear_tv",
        }
        deal_type_map = {
            "CTV": ["PG", "PD"], "DISPLAY": ["PD", "PA"],
            "AUDIO": ["PD", "PA"], "NATIVE": ["PD", "PA"],
            "DOOH": ["PG", "PD"], "LINEAR_TV": ["PG"],
        }

        channel_plans = []
        for ch in channels_raw:
            channel = ch.get("channel", "DISPLAY")
            budget_pct = ch.get("budget_pct", 0)
            budget = round(total_budget * budget_pct / 100.0, 2)

            channel_plans.append({
                "channel": channel,
                "budget": budget,
                "budget_pct": budget_pct,
                "media_type": media_type_map.get(channel, channel.lower()),
                "deal_types": deal_type_map.get(channel, ["PD"]),
                "format_prefs": ch.get("format_prefs", []),
            })

        plan = {
            "campaign_id": campaign_id,
            "channel_plans": channel_plans,
            "total_budget": total_budget,
            "flight_start": campaign["flight_start"],
            "flight_end": campaign["flight_end"],
        }

        self._plans[campaign_id] = plan

        self._emit_sync(
            EventType.CAMPAIGN_PLAN_GENERATED,
            campaign_id=campaign_id,
            payload={
                "channels": [
                    {"channel": cp["channel"], "budget": cp["budget"]}
                    for cp in channel_plans
                ],
                "total_budget": total_budget,
            },
        )

        return plan

    # -- Stage 3: Execute booking (simulated) ------------------------------

    def execute_booking(self, campaign_id: str) -> dict[str, Any]:
        """Transition to BOOKING, simulate deal booking per channel.

        Returns dict mapping channel name to booking results.
        """
        campaign = self._store.get_campaign(campaign_id)
        if campaign is None:
            raise KeyError(f"Campaign not found: {campaign_id}")

        # Transition PLANNING -> BOOKING
        self._store.start_booking(campaign_id)

        plan = self._plans.get(campaign_id)
        if plan is None:
            raise KeyError(f"No plan for campaign {campaign_id}")

        self._emit_sync(
            EventType.CAMPAIGN_BOOKING_STARTED,
            campaign_id=campaign_id,
        )

        # Simulate realistic booking results per channel
        demo_sellers = {
            "CTV": [
                ("Hulu", "hulu.com", "PG", 35.00),
                ("Peacock", "peacocktv.com", "PD", 28.00),
                ("Roku", "roku.com", "PD", 22.00),
            ],
            "DISPLAY": [
                ("ESPN", "espn.com", "PD", 18.50),
                ("NYT", "nytimes.com", "PG", 22.00),
                ("CNN", "cnn.com", "PA", 15.00),
            ],
            "AUDIO": [
                ("Spotify", "spotify.com", "PD", 25.00),
                ("iHeart", "iheart.com", "PA", 12.00),
            ],
            "NATIVE": [
                ("Taboola", "taboola.com", "PD", 8.50),
                ("Outbrain", "outbrain.com", "PA", 7.00),
            ],
            "DOOH": [
                ("Clear Channel", "clearchannel.com", "PG", 5.50),
            ],
            "LINEAR_TV": [
                ("NBCU", "nbcuniversal.com", "PG", 45.00),
            ],
        }

        deals_by_channel: dict[str, list] = {}
        total_deals = 0
        total_spend = 0.0

        for cp in plan["channel_plans"]:
            channel = cp["channel"]
            budget = cp["budget"]
            sellers = demo_sellers.get(channel, [("Demo Seller", "demo.com", "PD", 15.00)])

            channel_deals = []
            channel_spend = 0.0
            remaining = budget

            for seller_name, seller_domain, deal_type, cpm in sellers:
                if remaining <= 0:
                    break
                # Allocate a portion of the channel budget
                deal_budget = min(remaining, budget / len(sellers))
                impressions = int((deal_budget / cpm) * 1000) if cpm > 0 else 0
                deal_id = f"DEAL-{str(uuid.uuid4())[:8].upper()}"

                channel_deals.append({
                    "deal_id": deal_id,
                    "seller": seller_name,
                    "seller_domain": seller_domain,
                    "deal_type": deal_type,
                    "cpm": cpm,
                    "impressions": impressions,
                    "spend": round(deal_budget, 2),
                })
                channel_spend += deal_budget
                remaining -= deal_budget

            deals_by_channel[channel] = channel_deals
            total_deals += len(channel_deals)
            total_spend += channel_spend

        self._booking_results[campaign_id] = deals_by_channel

        self._emit_sync(
            EventType.CAMPAIGN_BOOKING_COMPLETED,
            campaign_id=campaign_id,
            payload={
                "total_deals": total_deals,
                "total_spend": round(total_spend, 2),
                "channels_booked": len(deals_by_channel),
            },
        )

        return deals_by_channel

    # -- Stage 4: Creative matching (simulated) ----------------------------

    def match_creatives(self, campaign_id: str) -> list[dict[str, Any]]:
        """Simulate creative-to-deal matching and save creative assets.

        Returns list of creative match results.
        """
        campaign = self._store.get_campaign(campaign_id)
        if campaign is None:
            raise KeyError(f"Campaign not found: {campaign_id}")

        booking = self._booking_results.get(campaign_id, {})
        plan = self._plans.get(campaign_id)

        # Generate simulated creative assets matched to channels
        creative_specs = {
            "CTV": [
                ("CTV Hero Spot 30s", "video", {"duration": "30s", "resolution": "1920x1080"}, "valid"),
                ("CTV Bumper 15s", "video", {"duration": "15s", "resolution": "1920x1080"}, "valid"),
            ],
            "DISPLAY": [
                ("Leaderboard 728x90", "display", {"width": 728, "height": 90}, "valid"),
                ("Medium Rectangle 300x250", "display", {"width": 300, "height": 250}, "valid"),
                ("Skyscraper 160x600", "display", {"width": 160, "height": 600}, "valid"),
            ],
            "AUDIO": [
                ("Audio Spot 30s", "audio", {"duration": "30s", "format": "mp3"}, "valid"),
            ],
            "NATIVE": [
                ("Native Article Card", "native", {"headline_max": 50, "image": "1200x627"}, "valid"),
            ],
            "DOOH": [
                ("DOOH Full Screen", "display", {"width": 1920, "height": 1080}, "valid"),
            ],
            "LINEAR_TV": [
                ("TV Spot 30s", "video", {"duration": "30s", "format": "broadcast"}, "valid"),
            ],
        }

        creatives = []

        for channel, deals in booking.items():
            specs = creative_specs.get(channel, [
                ("Generic Creative", "display", {"width": 300, "height": 250}, "valid"),
            ])

            for spec_name, asset_type, format_spec, status in specs:
                asset_id = self._store.save_creative_asset(
                    campaign_id=campaign_id,
                    asset_name=spec_name,
                    asset_type=asset_type,
                    format_spec=json.dumps(format_spec),
                    source_url=f"https://cdn.demo.com/creatives/{spec_name.lower().replace(' ', '-')}.{asset_type}",
                    validation_status=status,
                )

                # Match creative to deals in this channel
                matched_deals = [d["deal_id"] for d in deals]

                creatives.append({
                    "asset_id": asset_id,
                    "asset_name": spec_name,
                    "asset_type": asset_type,
                    "format_spec": format_spec,
                    "validation_status": status,
                    "channel": channel,
                    "matched_deals": matched_deals,
                })

                self._emit_sync(
                    EventType.CREATIVE_MATCHED,
                    campaign_id=campaign_id,
                    payload={
                        "asset_id": asset_id,
                        "asset_name": spec_name,
                        "channel": channel,
                        "deals_matched": len(matched_deals),
                    },
                )

        self._creative_results[campaign_id] = creatives
        return creatives

    # -- Stage 5: Finalize -> READY ----------------------------------------

    def finalize(self, campaign_id: str) -> None:
        """Transition campaign from BOOKING to READY."""
        campaign = self._store.get_campaign(campaign_id)
        if campaign is None:
            raise KeyError(f"Campaign not found: {campaign_id}")

        self._store.mark_ready(campaign_id)

        # Seed a pacing snapshot for the report using PacingSnapshot model
        booking = self._booking_results.get(campaign_id, {})
        total_budget = campaign["total_budget"]

        channel_snapshots = []
        deal_snapshots = []

        for channel, deals in booking.items():
            ch_budget = sum(d["spend"] for d in deals)

            channel_snapshots.append(ChannelSnapshot(
                channel=channel,
                allocated_budget=ch_budget,
                spend=0.0,
                pacing_pct=0.0,
                impressions=0,
                effective_cpm=0.0,
                fill_rate=0.0,
            ))

            for d in deals:
                deal_snapshots.append(DealSnapshot(
                    deal_id=d["deal_id"],
                    allocated_budget=d["spend"],
                    spend=0.0,
                    impressions=0,
                    effective_cpm=0.0,
                    fill_rate=0.0,
                    win_rate=0.0,
                ))

        snapshot = PacingSnapshot(
            campaign_id=campaign_id,
            timestamp=datetime.now(timezone.utc),
            total_budget=total_budget,
            total_spend=0.0,
            pacing_pct=0.0,
            expected_spend=0.0,
            deviation_pct=0.0,
            channel_snapshots=channel_snapshots,
            deal_snapshots=deal_snapshots,
        )

        self._pacing_store.save_pacing_snapshot(snapshot)

        self._emit_sync(
            EventType.CAMPAIGN_READY,
            campaign_id=campaign_id,
            payload={"campaign_id": campaign_id},
        )

    # -- Stage 6: Activate -> ACTIVE (with simulated pacing) ---------------

    def activate_campaign(self, campaign_id: str) -> PacingSnapshot:
        """Transition campaign from READY to ACTIVE, generate simulated pacing.

        Since there is no real ad serving, we simulate delivery data:
        - Some channels overpace, some underpace (triggering alerts)
        - Deal-level metrics are generated with realistic fill/win rates
        - The BudgetPacingEngine generates alerts and reallocation proposals

        Returns:
            PacingSnapshot with simulated delivery data and recommendations.
        """
        campaign = self._store.get_campaign(campaign_id)
        if campaign is None:
            raise KeyError(f"Campaign not found: {campaign_id}")

        # Transition READY -> ACTIVE
        self._store.activate_campaign(campaign_id)

        self._emit_sync(
            EventType.CAMPAIGN_ACTIVATED,
            campaign_id=campaign_id,
            payload={"campaign_id": campaign_id},
        )

        # Build simulated delivery data
        booking = self._booking_results.get(campaign_id, {})
        total_budget = campaign["total_budget"]

        # Parse flight dates for pacing calculation
        flight_start_str = campaign["flight_start"]
        flight_end_str = campaign["flight_end"]
        flight_start = datetime.fromisoformat(flight_start_str).replace(
            tzinfo=timezone.utc
        )
        flight_end = datetime.fromisoformat(flight_end_str).replace(
            tzinfo=timezone.utc
        )

        # Simulate "current time" as 35% through the flight
        flight_duration = (flight_end - flight_start).total_seconds()
        sim_elapsed = flight_duration * 0.35
        sim_now = flight_start + timedelta(seconds=sim_elapsed)

        # Pre-defined pacing multipliers per channel to create varied scenarios.
        # Values <1.0 = underpacing, >1.0 = overpacing.
        pacing_multipliers = {
            "CTV": 0.72,        # Underpacing (critical, -28%)
            "DISPLAY": 1.35,    # Overpacing (critical, +35%)
            "AUDIO": 0.88,      # Slightly underpacing (warning, -12%)
            "NATIVE": 1.15,     # Slightly overpacing (warning, +15%)
            "DOOH": 0.60,       # Heavily underpacing
            "LINEAR_TV": 1.05,  # On pace
        }

        # Build channel_data and deal_data for the pacing engine
        channel_data: dict[str, dict[str, Any]] = {}
        deal_data: list[dict[str, Any]] = []

        # Use a seeded RNG for reproducible but realistic variance
        rng = random.Random(hash(campaign_id) % (2**32))

        for channel, deals in booking.items():
            ch_budget = sum(d["spend"] for d in deals)
            multiplier = pacing_multipliers.get(channel, 1.0)

            # Expected spend for this channel at sim_now
            ch_expected = ch_budget * 0.35  # 35% through flight
            ch_spend = round(ch_expected * multiplier, 2)

            # Impressions based on spend and deal CPMs
            ch_impressions = 0
            avg_cpm = 0.0
            if deals:
                avg_cpm = sum(d["cpm"] for d in deals) / len(deals)
                ch_impressions = int((ch_spend / avg_cpm) * 1000) if avg_cpm > 0 else 0

            ch_fill_rate = round(rng.uniform(0.60, 0.95), 2)
            ch_ecpm = round(avg_cpm * rng.uniform(0.85, 1.15), 2)

            channel_data[channel] = {
                "allocated_budget": ch_budget,
                "spend": ch_spend,
                "impressions": ch_impressions,
                "effective_cpm": ch_ecpm,
                "fill_rate": ch_fill_rate,
            }

            # Per-deal metrics
            for d in deals:
                deal_budget = d["spend"]
                deal_expected = deal_budget * 0.35
                # Add per-deal variance around channel multiplier
                deal_mult = multiplier * rng.uniform(0.85, 1.15)
                deal_spend = round(deal_expected * deal_mult, 2)
                deal_imps = int((deal_spend / d["cpm"]) * 1000) if d["cpm"] > 0 else 0
                deal_fill = round(rng.uniform(0.55, 0.98), 2)
                deal_win = round(rng.uniform(0.30, 0.85), 2)
                deal_ecpm = round(d["cpm"] * rng.uniform(0.90, 1.10), 2)

                deal_data.append({
                    "deal_id": d["deal_id"],
                    "allocated_budget": deal_budget,
                    "spend": deal_spend,
                    "impressions": deal_imps,
                    "effective_cpm": deal_ecpm,
                    "fill_rate": deal_fill,
                    "win_rate": deal_win,
                })

        # Use BudgetPacingEngine to generate the official snapshot
        engine = BudgetPacingEngine(
            config=PacingConfig(),
            event_bus=self._event_bus,
        )
        snapshot = engine.generate_snapshot(
            campaign_id=campaign_id,
            total_budget=total_budget,
            flight_start=flight_start,
            flight_end=flight_end,
            current_time=sim_now,
            channel_data=channel_data,
            deal_data=deal_data,
        )

        # Persist the snapshot
        self._pacing_store.save_pacing_snapshot(snapshot)

        return snapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_alerts(snapshot: PacingSnapshot) -> list:
    """Extract pacing deviation alerts from a snapshot.

    Uses the BudgetPacingEngine to detect deviations at both the campaign
    level and per-channel level.
    """
    from ..pacing.engine import BudgetPacingEngine, PacingAlert

    engine = BudgetPacingEngine()
    alerts: list[PacingAlert] = []

    # Campaign-level alert
    campaign_alert = engine.detect_deviation(
        snapshot.total_spend, snapshot.expected_spend
    )
    if campaign_alert is not None:
        alerts.append(campaign_alert)

    # Per-channel alerts
    for ch in snapshot.channel_snapshots:
        if ch.allocated_budget <= 0 or snapshot.total_budget <= 0:
            continue
        ch_expected = snapshot.expected_spend * (
            ch.allocated_budget / snapshot.total_budget
        )
        ch_alert = engine.detect_deviation(ch.spend, ch_expected)
        if ch_alert is not None:
            # Add channel context to the message
            ch_alert.message = f"[{ch.channel}] {ch_alert.message}"
            alerts.append(ch_alert)

    return alerts


# ---------------------------------------------------------------------------
# Flask app factory
# ---------------------------------------------------------------------------


def create_campaign_demo_app(
    database_url: str = "sqlite:///campaign_demo.db",
) -> Flask:
    """Create and configure the Campaign Automation demo Flask app.

    Args:
        database_url: SQLite connection string for stores.

    Returns:
        Configured Flask application.
    """
    app = Flask(
        __name__,
        template_folder=str(_TEMPLATE_DIR),
    )

    # Delete stale DB to ensure fresh schema (demo app, not production)
    if database_url.startswith("sqlite:///") and database_url != "sqlite:///:memory:":
        db_path = database_url.replace("sqlite:///", "")
        for suffix in ("", "-wal", "-shm"):
            path = db_path + suffix
            if os.path.exists(path):
                os.remove(path)

    # Initialize stores
    campaign_store = CampaignStore(database_url)
    campaign_store.connect()

    pacing_store = PacingStore(database_url)
    pacing_store.connect()

    # Event bus
    event_bus = InMemoryEventBus()

    # Pipeline helper
    pipeline = DemoPipelineHelper(campaign_store, pacing_store, event_bus)

    # Reporter
    reporter = CampaignReporter(campaign_store, pacing_store)

    # Store references on the app
    app.config["CAMPAIGN_STORE"] = campaign_store
    app.config["PACING_STORE"] = pacing_store
    app.config["EVENT_BUS"] = event_bus
    app.config["PIPELINE"] = pipeline
    app.config["REPORTER"] = reporter

    # Register routes
    _register_routes(app, campaign_store, pacing_store, event_bus, pipeline, reporter)

    return app


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def _register_routes(
    app: Flask,
    campaign_store: CampaignStore,
    pacing_store: PacingStore,
    event_bus: InMemoryEventBus,
    pipeline: DemoPipelineHelper,
    reporter: CampaignReporter,
) -> None:
    """Register all routes on the app."""

    # -- Page ---------------------------------------------------------------

    @app.route("/")
    def index():
        return render_template("campaign_demo.html")

    # -- API: Sample briefs ------------------------------------------------

    @app.route("/api/sample-briefs")
    def api_sample_briefs():
        """Return pre-built sample briefs for the dropdown."""
        samples = _build_sample_briefs()
        return jsonify({
            "briefs": [s["brief"] for s in samples],
            "names": [s["name"] for s in samples],
        })

    # -- API: Submit brief (Stage 1) ---------------------------------------

    @app.route("/api/submit-brief", methods=["POST"])
    def api_submit_brief():
        """Parse brief and create campaign in DRAFT status."""
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"success": False, "error": "No JSON body"}), 400

        try:
            campaign_id = pipeline.ingest_brief(data)
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

        campaign = campaign_store.get_campaign(campaign_id)

        return jsonify({
            "success": True,
            "campaign_id": campaign_id,
            "status": campaign["status"].lower() if campaign else "draft",
            "campaign_name": campaign["campaign_name"] if campaign else "",
        })

    # -- API: Get campaign state -------------------------------------------

    @app.route("/api/campaign/<campaign_id>")
    def api_campaign_state(campaign_id: str):
        """Return current campaign state."""
        campaign = campaign_store.get_campaign(campaign_id)
        if campaign is None:
            return jsonify({"error": "Campaign not found"}), 404

        # Parse JSON fields for the response
        result = dict(campaign)
        for field in ("channels", "target_audience", "kpis", "approval_config",
                      "target_geo", "brand_safety"):
            if result.get(field) and isinstance(result[field], str):
                try:
                    result[field] = json.loads(result[field])
                except (json.JSONDecodeError, TypeError):
                    pass

        result["status"] = result["status"].lower()
        return jsonify(result)

    # -- API: Approve plan (Stage 2) --------------------------------------

    @app.route("/api/approve-plan", methods=["POST"])
    def api_approve_plan():
        """Generate and approve the campaign plan."""
        data = request.get_json(silent=True)
        if not data or "campaign_id" not in data:
            return jsonify({"success": False, "error": "Missing campaign_id"}), 400

        campaign_id = data["campaign_id"]

        try:
            plan = pipeline.plan_campaign(campaign_id)
        except KeyError as exc:
            return jsonify({"success": False, "error": str(exc)}), 404
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

        return jsonify({
            "success": True,
            "campaign_id": campaign_id,
            "plan": plan,
        })

    # -- API: Approve booking (Stage 3) ------------------------------------

    @app.route("/api/approve-booking", methods=["POST"])
    def api_approve_booking():
        """Execute booking and return deals per channel."""
        data = request.get_json(silent=True)
        if not data or "campaign_id" not in data:
            return jsonify({"success": False, "error": "Missing campaign_id"}), 400

        campaign_id = data["campaign_id"]

        try:
            deals = pipeline.execute_booking(campaign_id)
        except KeyError as exc:
            return jsonify({"success": False, "error": str(exc)}), 404
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

        return jsonify({
            "success": True,
            "campaign_id": campaign_id,
            "deals": deals,
        })

    # -- API: Approve creative (Stage 4) -----------------------------------

    @app.route("/api/approve-creative", methods=["POST"])
    def api_approve_creative():
        """Match creatives and finalize campaign to READY."""
        data = request.get_json(silent=True)
        if not data or "campaign_id" not in data:
            return jsonify({"success": False, "error": "Missing campaign_id"}), 400

        campaign_id = data["campaign_id"]

        try:
            creatives = pipeline.match_creatives(campaign_id)
            pipeline.finalize(campaign_id)
        except KeyError as exc:
            return jsonify({"success": False, "error": str(exc)}), 404
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

        return jsonify({
            "success": True,
            "campaign_id": campaign_id,
            "status": "ready",
            "creatives": creatives,
        })

    # -- API: Activate campaign (Stage 6) ----------------------------------

    @app.route("/api/activate-campaign", methods=["POST"])
    def api_activate_campaign():
        """Activate campaign (READY -> ACTIVE), generate simulated pacing."""
        data = request.get_json(silent=True)
        if not data or "campaign_id" not in data:
            return jsonify({"success": False, "error": "Missing campaign_id"}), 400

        campaign_id = data["campaign_id"]

        try:
            snapshot = pipeline.activate_campaign(campaign_id)
        except KeyError as exc:
            return jsonify({"success": False, "error": str(exc)}), 404
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

        # Build response with pacing data
        pacing_data = {
            "total_budget": snapshot.total_budget,
            "total_spend": snapshot.total_spend,
            "expected_spend": snapshot.expected_spend,
            "pacing_pct": snapshot.pacing_pct,
            "deviation_pct": snapshot.deviation_pct,
            "channel_snapshots": [
                ch.model_dump() for ch in snapshot.channel_snapshots
            ],
            "deal_snapshots": [
                ds.model_dump() for ds in snapshot.deal_snapshots
            ],
            "alerts": [
                {
                    "level": alert.level.value if hasattr(alert.level, "value") else alert.level,
                    "direction": alert.direction,
                    "deviation_pct": alert.deviation_pct,
                    "message": alert.message,
                }
                for alert in _extract_alerts(snapshot)
            ],
            "recommendations": [
                rec.model_dump() for rec in snapshot.recommendations
            ],
        }

        return jsonify({
            "success": True,
            "campaign_id": campaign_id,
            "status": "active",
            "pacing": pacing_data,
        })

    # -- API: Pause campaign (Stage 6 control) -----------------------------

    @app.route("/api/pause-campaign", methods=["POST"])
    def api_pause_campaign():
        """Pause an active campaign (ACTIVE -> PAUSED)."""
        data = request.get_json(silent=True)
        if not data or "campaign_id" not in data:
            return jsonify({"success": False, "error": "Missing campaign_id"}), 400

        campaign_id = data["campaign_id"]

        try:
            campaign = campaign_store.get_campaign(campaign_id)
            if campaign is None:
                raise KeyError(f"Campaign not found: {campaign_id}")
            campaign_store.pause_campaign(campaign_id)
        except KeyError as exc:
            return jsonify({"success": False, "error": str(exc)}), 404
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

        return jsonify({
            "success": True,
            "campaign_id": campaign_id,
            "status": "paused",
        })

    # -- API: Resume campaign (Stage 6 control) ----------------------------

    @app.route("/api/resume-campaign", methods=["POST"])
    def api_resume_campaign():
        """Resume a paused campaign (PAUSED -> ACTIVE)."""
        data = request.get_json(silent=True)
        if not data or "campaign_id" not in data:
            return jsonify({"success": False, "error": "Missing campaign_id"}), 400

        campaign_id = data["campaign_id"]

        try:
            campaign = campaign_store.get_campaign(campaign_id)
            if campaign is None:
                raise KeyError(f"Campaign not found: {campaign_id}")
            campaign_store.resume_campaign(campaign_id)
        except KeyError as exc:
            return jsonify({"success": False, "error": str(exc)}), 404
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

        return jsonify({
            "success": True,
            "campaign_id": campaign_id,
            "status": "active",
        })

    # -- API: Complete campaign (Stage 6 control) --------------------------

    @app.route("/api/complete-campaign", methods=["POST"])
    def api_complete_campaign():
        """Complete an active campaign (ACTIVE -> COMPLETED)."""
        data = request.get_json(silent=True)
        if not data or "campaign_id" not in data:
            return jsonify({"success": False, "error": "Missing campaign_id"}), 400

        campaign_id = data["campaign_id"]

        try:
            campaign = campaign_store.get_campaign(campaign_id)
            if campaign is None:
                raise KeyError(f"Campaign not found: {campaign_id}")
            campaign_store.complete_campaign(campaign_id)
        except KeyError as exc:
            return jsonify({"success": False, "error": str(exc)}), 404
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

        return jsonify({
            "success": True,
            "campaign_id": campaign_id,
            "status": "completed",
        })

    # -- API: Campaign report (Stage 5) ------------------------------------

    @app.route("/api/campaign/<campaign_id>/report")
    def api_campaign_report(campaign_id: str):
        """Generate full campaign report."""
        campaign = campaign_store.get_campaign(campaign_id)
        if campaign is None:
            return jsonify({"error": "Campaign not found"}), 404

        try:
            report = reporter.full_report(campaign_id)
            return jsonify({
                "campaign_id": campaign_id,
                "status_summary": report.status_summary._to_dict(),
                "pacing_dashboard": report.pacing_dashboard._to_dict(),
                "creative_performance": report.creative_performance._to_dict(),
                "deal_report": report.deal_report._to_dict(),
            })
        except Exception as exc:
            logger.warning("Report generation failed: %s", exc)
            # Fall back to basic campaign data
            return jsonify({
                "campaign_id": campaign_id,
                "status_summary": {
                    "campaign_id": campaign_id,
                    "campaign_name": campaign["campaign_name"],
                    "advertiser_id": campaign["advertiser_id"],
                    "status": campaign["status"].lower(),
                    "total_budget": campaign["total_budget"],
                    "currency": campaign.get("currency", "USD"),
                    "total_spend": 0.0,
                    "delivery_pct": 0.0,
                    "pacing_pct": 0.0,
                    "flight_start": campaign["flight_start"],
                    "flight_end": campaign["flight_end"],
                    "channels": [],
                },
                "pacing_dashboard": {
                    "campaign_id": campaign_id,
                    "total_budget": campaign["total_budget"],
                    "total_spend": 0.0,
                    "expected_spend": 0.0,
                    "pacing_pct": 0.0,
                    "deviation_pct": 0.0,
                    "channel_pacing": [],
                    "alerts": [],
                },
                "creative_performance": {
                    "campaign_id": campaign_id,
                    "creatives": [],
                    "total_assets": 0,
                    "valid_assets": 0,
                    "pending_assets": 0,
                    "invalid_assets": 0,
                },
                "deal_report": {
                    "campaign_id": campaign_id,
                    "deals": [],
                    "total_deals": 0,
                    "total_spend": 0.0,
                    "total_impressions": 0,
                    "avg_fill_rate": 0.0,
                    "avg_win_rate": 0.0,
                },
            })

    # -- API: Events -------------------------------------------------------

    @app.route("/api/events")
    def api_events():
        """Return recent events from the EventBus."""
        campaign_id = request.args.get("campaign_id")
        limit = request.args.get("limit", 100, type=int)

        events = event_bus._events[-limit:]
        if campaign_id:
            events = [e for e in events if e.campaign_id == campaign_id]

        return jsonify({
            "events": [
                {
                    "event_id": e.event_id,
                    "event_type": e.event_type.value,
                    "campaign_id": e.campaign_id,
                    "timestamp": e.timestamp.isoformat() if e.timestamp else "",
                    "payload": e.payload,
                }
                for e in events
            ],
            "count": len(events),
        })

    # -- API: List campaigns -----------------------------------------------

    @app.route("/api/campaigns")
    def api_list_campaigns():
        """List all campaigns."""
        campaigns = campaign_store.list_campaigns(limit=50)
        return jsonify({
            "campaigns": [
                {
                    "campaign_id": c["campaign_id"],
                    "campaign_name": c["campaign_name"],
                    "status": c["status"].lower(),
                    "total_budget": c["total_budget"],
                    "advertiser_id": c["advertiser_id"],
                }
                for c in campaigns
            ],
        })


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the campaign demo development server."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    db_path = os.environ.get(
        "CAMPAIGN_DEMO_DB", "sqlite:///campaign_demo.db"
    )
    app = create_campaign_demo_app(database_url=db_path)

    port = int(os.environ.get("CAMPAIGN_DEMO_PORT", "5055"))
    print(f"\n  Campaign Automation Demo running at http://localhost:{port}\n")
    print("  Stages:")
    print("    1. Enter Brief       -> Submit a campaign brief")
    print("    2. Review Plan       -> Approve channel allocation plan")
    print("    3. Review Deals      -> Approve deal booking")
    print("    4. Review Creative   -> Approve creative matching")
    print("    5. Campaign Ready    -> View full campaign report")
    print("    6. Active Campaign   -> Pacing dashboard, alerts, controls")
    print()
    app.run(host="0.0.0.0", port=port, debug=True)


if __name__ == "__main__":
    main()
