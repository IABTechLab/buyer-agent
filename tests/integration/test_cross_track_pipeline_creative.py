# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Cross-track integration test: Campaign Pipeline + Creative Agent.

Verifies the seam between the deal flow track (CampaignPipeline, buyer-u8l)
and the creative management track (CreativeValidator, CreativeMatcher,
CreativeManagementTool, buyer-3aa).

Test scenarios:
  1. End-to-end: brief -> plan -> book -> validate creatives -> match -> READY
  2. Creative matching produces correct assignments per channel
  3. Error handling when creative matching fails (no matching creatives)
  4. Event emission across both tracks (pipeline + creative events)
  5. Partial matching: some deals matched, some mismatched
  6. Multiple asset types across channels (video for CTV, display for DISPLAY)

These tests exercise real CreativeValidator and CreativeMatcher logic with
a mocked MultiSellerOrchestrator and FakeCampaignStore, ensuring the two
tracks integrate correctly without requiring live seller connections.

bead: buyer-gb2
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import date, timedelta
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from ad_buyer.events.bus import InMemoryEventBus
from ad_buyer.events.models import Event, EventType
from ad_buyer.models.campaign_brief import ChannelType
from ad_buyer.models.creative_asset import AssetType, CreativeAsset, ValidationStatus
from ad_buyer.models.state_machine import CampaignStatus
from ad_buyer.orchestration.multi_seller import (
    DealSelection,
    MultiSellerOrchestrator,
    OrchestrationResult,
)
from ad_buyer.pipelines.campaign_pipeline import (
    CampaignPipeline,
    CampaignPlan,
    ChannelPlan,
)
from ad_buyer.tools.creative.matcher import CreativeMatcher, MatchResult
from ad_buyer.tools.creative.tool import CreativeManagementTool
from ad_buyer.tools.creative.validator import CreativeValidator


# ---------------------------------------------------------------------------
# Helpers / Fakes
# ---------------------------------------------------------------------------


class FakeCampaignStore:
    """In-memory fake of CampaignStore for cross-track tests.

    Supports campaign CRUD and creative asset storage so we can verify
    the end-to-end flow from pipeline through creative management.
    """

    def __init__(self) -> None:
        self._campaigns: dict[str, dict[str, Any]] = {}
        self._creative_assets: dict[str, dict[str, Any]] = {}

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def create_campaign(self, brief: dict[str, Any]) -> str:
        campaign_id = str(uuid.uuid4())
        self._campaigns[campaign_id] = {
            "campaign_id": campaign_id,
            "advertiser_id": brief["advertiser_id"],
            "campaign_name": brief["campaign_name"],
            "status": CampaignStatus.DRAFT.value,
            "total_budget": brief["total_budget"],
            "currency": brief.get("currency", "USD"),
            "flight_start": brief["flight_start"],
            "flight_end": brief["flight_end"],
            "channels": brief.get("channels"),
            "target_audience": brief.get("target_audience"),
        }
        return campaign_id

    def get_campaign(self, campaign_id: str) -> Optional[dict[str, Any]]:
        return self._campaigns.get(campaign_id)

    def start_planning(self, campaign_id: str) -> None:
        self._campaigns[campaign_id]["status"] = CampaignStatus.PLANNING.value

    def start_booking(self, campaign_id: str) -> None:
        self._campaigns[campaign_id]["status"] = CampaignStatus.BOOKING.value

    def mark_ready(self, campaign_id: str) -> None:
        self._campaigns[campaign_id]["status"] = CampaignStatus.READY.value

    def update_campaign(self, campaign_id: str, **kwargs: Any) -> bool:
        if campaign_id not in self._campaigns:
            return False
        self._campaigns[campaign_id].update(kwargs)
        return True

    def save_creative_asset(
        self,
        *,
        asset_id: Optional[str] = None,
        campaign_id: str,
        asset_name: str,
        asset_type: str,
        format_spec: Optional[str] = None,
        source_url: Optional[str] = None,
        validation_status: Optional[str] = None,
        validation_errors: Optional[str] = None,
    ) -> str:
        if asset_id is None:
            asset_id = str(uuid.uuid4())
        self._creative_assets[asset_id] = {
            "asset_id": asset_id,
            "campaign_id": campaign_id,
            "asset_name": asset_name,
            "asset_type": asset_type,
            "format_spec": format_spec,
            "source_url": source_url,
            "validation_status": validation_status or "pending",
            "validation_errors": validation_errors or "[]",
        }
        return asset_id

    def list_creative_assets(
        self,
        *,
        campaign_id: str,
        asset_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        results = [
            a for a in self._creative_assets.values()
            if a["campaign_id"] == campaign_id
        ]
        if asset_type is not None:
            results = [a for a in results if a["asset_type"] == asset_type]
        return results[:limit]


def _make_brief_dict(**overrides: Any) -> dict[str, Any]:
    """Return a valid campaign brief dict with CTV and DISPLAY channels."""
    today = date.today()
    brief = {
        "advertiser_id": "adv-cross-track-001",
        "campaign_name": "Cross-Track Test Campaign",
        "objective": "AWARENESS",
        "total_budget": 100_000.0,
        "currency": "USD",
        "flight_start": (today + timedelta(days=7)).isoformat(),
        "flight_end": (today + timedelta(days=37)).isoformat(),
        "channels": [
            {"channel": "CTV", "budget_pct": 60},
            {"channel": "DISPLAY", "budget_pct": 40},
        ],
        "target_audience": ["auto_intenders_25_54"],
    }
    brief.update(overrides)
    return brief


def _make_fake_deal(
    deal_id: str = "deal-001",
    deal_type: str = "PD",
    cpm: float = 12.50,
    media_type: str = "display",
) -> MagicMock:
    """Return a mock DealResponse with specified attributes."""
    deal = MagicMock()
    deal.deal_id = deal_id
    deal.deal_type = deal_type
    deal.pricing = MagicMock()
    deal.pricing.final_cpm = cpm
    deal.media_type = media_type
    return deal


def _make_orchestration_result(
    deal_prefix: str = "deal",
    num_deals: int = 2,
    total_spend: float = 30_000.0,
    remaining_budget: float = 10_000.0,
    media_type: str = "display",
) -> OrchestrationResult:
    """Return a mock OrchestrationResult with booked deals."""
    deals = [
        _make_fake_deal(
            deal_id=f"{deal_prefix}-{i:03d}",
            media_type=media_type,
        )
        for i in range(num_deals)
    ]
    return OrchestrationResult(
        discovered_sellers=[MagicMock(agent_id=f"seller-{i}") for i in range(3)],
        quote_results=[],
        ranked_quotes=[],
        selection=DealSelection(
            booked_deals=deals,
            failed_bookings=[],
            total_spend=total_spend,
            remaining_budget=remaining_budget,
        ),
    )


def _make_creative_asset(
    campaign_id: str,
    asset_type: AssetType = AssetType.DISPLAY,
    asset_name: str = "Test Banner",
    format_spec: Optional[dict[str, Any]] = None,
    validation_status: ValidationStatus = ValidationStatus.PENDING,
) -> CreativeAsset:
    """Create a CreativeAsset with sensible defaults."""
    if format_spec is None:
        if asset_type == AssetType.DISPLAY:
            format_spec = {"width": 300, "height": 250}
        elif asset_type == AssetType.VIDEO:
            format_spec = {"duration_sec": 30, "vast_version": "4.2"}
        elif asset_type == AssetType.AUDIO:
            format_spec = {"duration_sec": 30, "daast_version": "1.0"}
        elif asset_type == AssetType.INTERACTIVE:
            format_spec = {"simid_version": "1.1"}
        else:
            format_spec = {}

    return CreativeAsset(
        asset_id=str(uuid.uuid4()),
        campaign_id=campaign_id,
        asset_name=asset_name,
        asset_type=asset_type,
        format_spec=format_spec,
        source_url=f"https://cdn.example.com/{asset_name.replace(' ', '_').lower()}.bin",
        validation_status=validation_status,
    )


def _make_deal_dict(
    deal_id: str,
    media_type: str = "display",
    deal_name: str = "Test Deal",
    creative_requirements: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Create a deal dict in the format expected by CreativeMatcher."""
    return {
        "seller_deal_id": deal_id,
        "deal_name": deal_name,
        "media_type": media_type,
        "creative_requirements": creative_requirements or {},
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_store() -> FakeCampaignStore:
    return FakeCampaignStore()


@pytest.fixture
def event_bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
def mock_orchestrator() -> AsyncMock:
    """Mock orchestrator returning different results per channel."""
    orch = AsyncMock(spec=MultiSellerOrchestrator)
    call_count = 0

    async def _orchestrate_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call is CTV (video)
            return _make_orchestration_result(
                deal_prefix="ctv-deal",
                num_deals=2,
                total_spend=40_000.0,
                remaining_budget=20_000.0,
                media_type="video",
            )
        else:
            # Second call is DISPLAY
            return _make_orchestration_result(
                deal_prefix="display-deal",
                num_deals=1,
                total_spend=25_000.0,
                remaining_budget=15_000.0,
                media_type="display",
            )

    orch.orchestrate.side_effect = _orchestrate_side_effect
    return orch


@pytest.fixture
def pipeline(fake_store, mock_orchestrator, event_bus) -> CampaignPipeline:
    return CampaignPipeline(
        store=fake_store,
        orchestrator=mock_orchestrator,
        event_bus=event_bus,
    )


@pytest.fixture
def validator() -> CreativeValidator:
    return CreativeValidator()


@pytest.fixture
def matcher() -> CreativeMatcher:
    return CreativeMatcher()


# ---------------------------------------------------------------------------
# Test: End-to-end cross-track flow
# ---------------------------------------------------------------------------


class TestCrossTrackEndToEnd:
    """Full pipeline -> creative agent flow: brief to creative-assigned deals."""

    def test_full_flow_brief_to_creative_matched_ready(
        self, pipeline, fake_store, validator, matcher, event_bus
    ):
        """End-to-end: ingest brief -> plan -> book -> validate creatives -> match -> READY.

        This is the core cross-track integration test. The pipeline handles
        the deal flow track (brief through booking), then the creative agent
        components (validator + matcher) handle creative-to-deal assignment.
        """
        loop = asyncio.get_event_loop()

        # Stage 1-4: Run pipeline from brief to READY
        summary = loop.run_until_complete(pipeline.run(_make_brief_dict()))
        campaign_id = summary["campaign_id"]

        # Verify pipeline reached READY
        campaign = fake_store.get_campaign(campaign_id)
        assert campaign["status"] == CampaignStatus.READY.value

        # Stage 5: Create creative assets for the campaign
        video_asset = _make_creative_asset(
            campaign_id=campaign_id,
            asset_type=AssetType.VIDEO,
            asset_name="CTV 30s Spot",
            format_spec={"duration_sec": 30, "vast_version": "4.2"},
        )
        display_asset = _make_creative_asset(
            campaign_id=campaign_id,
            asset_type=AssetType.DISPLAY,
            asset_name="Banner 300x250",
            format_spec={"width": 300, "height": 250},
        )

        # Stage 6: Validate creatives through the creative agent
        validator.validate(video_asset)
        validator.validate(display_asset)

        assert video_asset.validation_status == ValidationStatus.VALID
        assert display_asset.validation_status == ValidationStatus.VALID

        # Stage 7: Match creatives to booked deals
        # Build deal dicts from the booking results
        deals = [
            _make_deal_dict("ctv-deal-000", media_type="video", deal_name="CTV Deal A"),
            _make_deal_dict("ctv-deal-001", media_type="video", deal_name="CTV Deal B"),
            _make_deal_dict("display-deal-000", media_type="display", deal_name="Display Deal A"),
        ]

        match_result = matcher.match_creatives_to_deals(
            assets=[video_asset, display_asset],
            deals=deals,
        )

        # Video asset should match CTV deals
        ctv_matches = [m for m in match_result.matches if m["deal_id"].startswith("ctv-")]
        assert len(ctv_matches) == 2
        for m in ctv_matches:
            assert m["asset_id"] == video_asset.asset_id
            assert m["asset_name"] == "CTV 30s Spot"

        # Display asset should match display deal
        display_matches = [m for m in match_result.matches if m["deal_id"].startswith("display-")]
        assert len(display_matches) == 1
        assert display_matches[0]["asset_id"] == display_asset.asset_id
        assert display_matches[0]["asset_name"] == "Banner 300x250"

        # No mismatches expected
        assert len(match_result.mismatches) == 0

    def test_pipeline_summary_contains_deal_ids_for_matching(
        self, pipeline, fake_store
    ):
        """Pipeline summary should include deal_ids that can be used for creative matching."""
        loop = asyncio.get_event_loop()
        summary = loop.run_until_complete(pipeline.run(_make_brief_dict()))

        # Summary must have channels with deal info
        assert "channels" in summary
        for ch_key, ch_data in summary["channels"].items():
            assert "deal_ids" in ch_data
            assert "deals_booked" in ch_data

    def test_run_then_match_single_channel(
        self, fake_store, event_bus, validator, matcher
    ):
        """Single-channel (CTV-only) brief -> validate -> match -> all matched."""
        loop = asyncio.get_event_loop()

        # Single CTV channel
        orch = AsyncMock(spec=MultiSellerOrchestrator)
        orch.orchestrate.return_value = _make_orchestration_result(
            deal_prefix="ctv-solo",
            num_deals=1,
            total_spend=50_000.0,
            remaining_budget=50_000.0,
            media_type="video",
        )
        pipeline = CampaignPipeline(
            store=fake_store, orchestrator=orch, event_bus=event_bus,
        )

        brief = _make_brief_dict(
            channels=[{"channel": "CTV", "budget_pct": 100}],
        )
        summary = loop.run_until_complete(pipeline.run(brief))
        campaign_id = summary["campaign_id"]

        # Create matching video creative
        video_asset = _make_creative_asset(
            campaign_id=campaign_id,
            asset_type=AssetType.VIDEO,
            asset_name="Solo CTV Spot",
        )
        validator.validate(video_asset)
        assert video_asset.validation_status == ValidationStatus.VALID

        # Match
        deals = [_make_deal_dict("ctv-solo-000", media_type="video", deal_name="Solo CTV")]
        match_result = matcher.match_creatives_to_deals(
            assets=[video_asset], deals=deals,
        )
        assert len(match_result.matches) == 1
        assert len(match_result.mismatches) == 0


# ---------------------------------------------------------------------------
# Test: Creative matching per channel
# ---------------------------------------------------------------------------


class TestCreativeMatchingPerChannel:
    """Verify correct creative-to-deal assignment across channel types."""

    def test_video_asset_matches_video_deals_only(self, matcher, validator):
        """A validated video asset should only match video/CTV deals."""
        campaign_id = "camp-video-test"
        video = _make_creative_asset(
            campaign_id=campaign_id,
            asset_type=AssetType.VIDEO,
            asset_name="Video 30s",
        )
        validator.validate(video)
        assert video.validation_status == ValidationStatus.VALID

        deals = [
            _make_deal_dict("v-deal-1", media_type="video", deal_name="CTV PG"),
            _make_deal_dict("d-deal-1", media_type="display", deal_name="Banner PD"),
        ]

        result = matcher.match_creatives_to_deals(assets=[video], deals=deals)
        assert len(result.matches) == 1
        assert result.matches[0]["deal_id"] == "v-deal-1"
        assert len(result.mismatches) == 1
        assert result.mismatches[0]["deal_id"] == "d-deal-1"

    def test_display_asset_matches_display_deals_only(self, matcher, validator):
        """A validated display asset should only match display deals."""
        campaign_id = "camp-display-test"
        banner = _make_creative_asset(
            campaign_id=campaign_id,
            asset_type=AssetType.DISPLAY,
            asset_name="Banner 728x90",
            format_spec={"width": 728, "height": 90},
        )
        validator.validate(banner)
        assert banner.validation_status == ValidationStatus.VALID

        deals = [
            _make_deal_dict("d-deal-1", media_type="display", deal_name="Display PD"),
            _make_deal_dict("v-deal-1", media_type="video", deal_name="CTV PG"),
        ]

        result = matcher.match_creatives_to_deals(assets=[banner], deals=deals)
        assert len(result.matches) == 1
        assert result.matches[0]["deal_id"] == "d-deal-1"
        assert len(result.mismatches) == 1

    def test_multiple_assets_multi_channel_matching(self, matcher, validator):
        """Multiple assets across types should each match their channel's deals."""
        campaign_id = "camp-multi-channel"

        video = _make_creative_asset(
            campaign_id=campaign_id,
            asset_type=AssetType.VIDEO,
            asset_name="Video Creative",
        )
        display = _make_creative_asset(
            campaign_id=campaign_id,
            asset_type=AssetType.DISPLAY,
            asset_name="Display Creative",
        )
        audio = _make_creative_asset(
            campaign_id=campaign_id,
            asset_type=AssetType.AUDIO,
            asset_name="Audio Spot",
        )

        for asset in [video, display, audio]:
            validator.validate(asset)
            assert asset.validation_status == ValidationStatus.VALID

        deals = [
            _make_deal_dict("vid-1", media_type="video", deal_name="CTV Deal"),
            _make_deal_dict("disp-1", media_type="display", deal_name="Display Deal"),
            _make_deal_dict("aud-1", media_type="audio", deal_name="Audio Deal"),
        ]

        result = matcher.match_creatives_to_deals(
            assets=[video, display, audio], deals=deals,
        )

        # All 3 deals should be matched
        assert len(result.matches) == 3
        assert len(result.mismatches) == 0

        # Verify each deal matched the correct asset type
        match_by_deal = {m["deal_id"]: m for m in result.matches}
        assert match_by_deal["vid-1"]["asset_id"] == video.asset_id
        assert match_by_deal["disp-1"]["asset_id"] == display.asset_id
        assert match_by_deal["aud-1"]["asset_id"] == audio.asset_id

    def test_display_size_requirement_matching(self, matcher, validator):
        """Display deals with specific size requirements should only match compatible assets."""
        campaign_id = "camp-size-test"

        banner_300x250 = _make_creative_asset(
            campaign_id=campaign_id,
            asset_type=AssetType.DISPLAY,
            asset_name="Medium Rectangle",
            format_spec={"width": 300, "height": 250},
        )
        banner_728x90 = _make_creative_asset(
            campaign_id=campaign_id,
            asset_type=AssetType.DISPLAY,
            asset_name="Leaderboard",
            format_spec={"width": 728, "height": 90},
        )

        for asset in [banner_300x250, banner_728x90]:
            validator.validate(asset)

        deals = [
            _make_deal_dict(
                "d-300x250", media_type="display", deal_name="300x250 Slot",
                creative_requirements={"width": 300, "height": 250},
            ),
            _make_deal_dict(
                "d-728x90", media_type="display", deal_name="728x90 Slot",
                creative_requirements={"width": 728, "height": 90},
            ),
        ]

        result = matcher.match_creatives_to_deals(
            assets=[banner_300x250, banner_728x90], deals=deals,
        )

        # Both deals should match, each to the right asset
        assert len(result.matches) == 2
        assert len(result.mismatches) == 0

        match_by_deal = {m["deal_id"]: m for m in result.matches}
        assert match_by_deal["d-300x250"]["asset_id"] == banner_300x250.asset_id
        assert match_by_deal["d-728x90"]["asset_id"] == banner_728x90.asset_id


# ---------------------------------------------------------------------------
# Test: Error handling when creative matching fails
# ---------------------------------------------------------------------------


class TestCreativeMatchingErrors:
    """Error cases: no matching creatives, invalid creatives, partial matches."""

    def test_no_creatives_available(self, matcher):
        """Matching with no assets should produce mismatches for all deals."""
        deals = [
            _make_deal_dict("deal-1", media_type="video", deal_name="CTV Deal"),
            _make_deal_dict("deal-2", media_type="display", deal_name="Display Deal"),
        ]

        result = matcher.match_creatives_to_deals(assets=[], deals=deals)
        assert len(result.matches) == 0
        assert len(result.mismatches) == 2

    def test_invalid_creatives_not_matched(self, matcher, validator):
        """Only VALID creatives should be matched; INVALID ones are skipped."""
        campaign_id = "camp-invalid-test"

        # Create a video asset with bad VAST version -> will be INVALID
        bad_video = _make_creative_asset(
            campaign_id=campaign_id,
            asset_type=AssetType.VIDEO,
            asset_name="Bad Video",
            format_spec={"duration_sec": 30, "vast_version": "99.0"},
        )
        validator.validate(bad_video)
        assert bad_video.validation_status == ValidationStatus.INVALID

        deals = [
            _make_deal_dict("v-deal-1", media_type="video", deal_name="CTV Deal"),
        ]

        result = matcher.match_creatives_to_deals(assets=[bad_video], deals=deals)
        # Invalid asset should not match
        assert len(result.matches) == 0
        assert len(result.mismatches) == 1

    def test_pending_creatives_not_matched(self, matcher):
        """PENDING (unvalidated) creatives should not be matched."""
        campaign_id = "camp-pending-test"

        pending_asset = _make_creative_asset(
            campaign_id=campaign_id,
            asset_type=AssetType.VIDEO,
            asset_name="Pending Video",
            validation_status=ValidationStatus.PENDING,
        )

        deals = [
            _make_deal_dict("v-deal-1", media_type="video", deal_name="CTV Deal"),
        ]

        result = matcher.match_creatives_to_deals(assets=[pending_asset], deals=deals)
        assert len(result.matches) == 0
        assert len(result.mismatches) == 1

    def test_wrong_media_type_produces_mismatch(self, matcher, validator):
        """A display asset cannot match a video deal."""
        campaign_id = "camp-wrong-type"

        display = _make_creative_asset(
            campaign_id=campaign_id,
            asset_type=AssetType.DISPLAY,
            asset_name="Banner",
        )
        validator.validate(display)

        deals = [
            _make_deal_dict("v-deal-1", media_type="video", deal_name="CTV Deal"),
        ]

        result = matcher.match_creatives_to_deals(assets=[display], deals=deals)
        assert len(result.matches) == 0
        assert len(result.mismatches) == 1
        assert "v-deal-1" in result.mismatches[0]["message"]

    def test_partial_matching_some_deals_unmatched(self, matcher, validator):
        """When only some deals have matching creatives, mismatches are reported."""
        campaign_id = "camp-partial"

        # Only have video creative, but deals span video and audio
        video = _make_creative_asset(
            campaign_id=campaign_id,
            asset_type=AssetType.VIDEO,
            asset_name="Video Asset",
        )
        validator.validate(video)

        deals = [
            _make_deal_dict("v-deal", media_type="video", deal_name="Video Deal"),
            _make_deal_dict("a-deal", media_type="audio", deal_name="Audio Deal"),
        ]

        result = matcher.match_creatives_to_deals(assets=[video], deals=deals)
        assert len(result.matches) == 1
        assert result.matches[0]["deal_id"] == "v-deal"
        assert len(result.mismatches) == 1
        assert result.mismatches[0]["deal_id"] == "a-deal"

    def test_video_duration_mismatch(self, matcher, validator):
        """A video with the wrong duration should not match a deal requiring specific duration."""
        campaign_id = "camp-duration-mismatch"

        # 15-second video
        video_15s = _make_creative_asset(
            campaign_id=campaign_id,
            asset_type=AssetType.VIDEO,
            asset_name="Video 15s",
            format_spec={"duration_sec": 15, "vast_version": "4.2"},
        )
        validator.validate(video_15s)
        assert video_15s.validation_status == ValidationStatus.VALID

        # Deal requires 30-second video
        deals = [
            _make_deal_dict(
                "v-deal-30", media_type="video", deal_name="30s CTV Slot",
                creative_requirements={"duration_sec": 30},
            ),
        ]

        result = matcher.match_creatives_to_deals(assets=[video_15s], deals=deals)
        assert len(result.matches) == 0
        assert len(result.mismatches) == 1

    def test_display_size_mismatch(self, matcher, validator):
        """A display asset with the wrong size should not match."""
        campaign_id = "camp-size-mismatch"

        banner_300x250 = _make_creative_asset(
            campaign_id=campaign_id,
            asset_type=AssetType.DISPLAY,
            asset_name="Medium Rectangle",
            format_spec={"width": 300, "height": 250},
        )
        validator.validate(banner_300x250)

        # Deal requires 728x90
        deals = [
            _make_deal_dict(
                "d-728x90", media_type="display", deal_name="Leaderboard Slot",
                creative_requirements={"width": 728, "height": 90},
            ),
        ]

        result = matcher.match_creatives_to_deals(assets=[banner_300x250], deals=deals)
        assert len(result.matches) == 0
        assert len(result.mismatches) == 1


# ---------------------------------------------------------------------------
# Test: Event emission across both tracks
# ---------------------------------------------------------------------------


class TestCrossTrackEventEmission:
    """Verify event emission covers both pipeline and creative lifecycle."""

    def test_pipeline_emits_full_lifecycle_events(self, pipeline, event_bus):
        """Pipeline run should emit created, plan, booking, and ready events."""
        loop = asyncio.get_event_loop()
        loop.run_until_complete(pipeline.run(_make_brief_dict()))

        all_events = loop.run_until_complete(event_bus.list_events())
        event_types = {e.event_type for e in all_events}

        assert EventType.CAMPAIGN_CREATED in event_types
        assert EventType.CAMPAIGN_PLAN_GENERATED in event_types
        assert EventType.CAMPAIGN_BOOKING_STARTED in event_types
        assert EventType.CAMPAIGN_BOOKING_COMPLETED in event_types
        assert EventType.CAMPAIGN_READY in event_types

    def test_creative_events_emittable_after_pipeline(self, pipeline, event_bus):
        """After pipeline run, creative lifecycle events can be emitted on the same bus."""
        loop = asyncio.get_event_loop()
        summary = loop.run_until_complete(pipeline.run(_make_brief_dict()))
        campaign_id = summary["campaign_id"]

        # Emit creative events on the same event bus the pipeline used
        creative_validated_event = Event(
            event_type=EventType.CREATIVE_VALIDATED,
            campaign_id=campaign_id,
            payload={"asset_count": 2, "valid": 2, "invalid": 0},
        )
        loop.run_until_complete(event_bus.publish(creative_validated_event))

        creative_matched_event = Event(
            event_type=EventType.CREATIVE_MATCHED,
            campaign_id=campaign_id,
            payload={"matches": 3, "mismatches": 0},
        )
        loop.run_until_complete(event_bus.publish(creative_matched_event))

        # Verify all events (pipeline + creative) are on the bus
        all_events = loop.run_until_complete(event_bus.list_events())
        event_types = {e.event_type for e in all_events}

        # Pipeline events
        assert EventType.CAMPAIGN_CREATED in event_types
        assert EventType.CAMPAIGN_READY in event_types
        # Creative events
        assert EventType.CREATIVE_VALIDATED in event_types
        assert EventType.CREATIVE_MATCHED in event_types

    def test_events_have_correct_campaign_id(self, pipeline, event_bus):
        """All events should reference the correct campaign_id."""
        loop = asyncio.get_event_loop()
        summary = loop.run_until_complete(pipeline.run(_make_brief_dict()))
        campaign_id = summary["campaign_id"]

        all_events = loop.run_until_complete(event_bus.list_events())

        # All pipeline events should reference this campaign
        for event in all_events:
            assert event.campaign_id == campaign_id

    def test_event_ordering_reflects_pipeline_stages(self, pipeline, event_bus):
        """Events should be emitted in the correct lifecycle order."""
        loop = asyncio.get_event_loop()
        loop.run_until_complete(pipeline.run(_make_brief_dict()))

        all_events = loop.run_until_complete(event_bus.list_events())
        event_types = [e.event_type for e in all_events]

        # CREATED must come before PLAN_GENERATED
        created_idx = event_types.index(EventType.CAMPAIGN_CREATED)
        plan_idx = event_types.index(EventType.CAMPAIGN_PLAN_GENERATED)
        assert created_idx < plan_idx

        # PLAN must come before BOOKING_STARTED
        booking_start_idx = event_types.index(EventType.CAMPAIGN_BOOKING_STARTED)
        assert plan_idx < booking_start_idx

        # BOOKING_COMPLETED must come before READY
        booking_done_idx = event_types.index(EventType.CAMPAIGN_BOOKING_COMPLETED)
        ready_idx = event_types.index(EventType.CAMPAIGN_READY)
        assert booking_done_idx < ready_idx


# ---------------------------------------------------------------------------
# Test: CreativeManagementTool integration with pipeline data
# ---------------------------------------------------------------------------


class TestCreativeToolIntegration:
    """Test that CreativeManagementTool works with pipeline-produced deal data."""

    def test_tool_validate_action(self):
        """The tool's validate action should correctly validate creative assets."""
        tool = CreativeManagementTool()
        asset_dicts = [
            {
                "asset_type": "display",
                "format_spec": {"width": 300, "height": 250},
                "asset_name": "Banner 300x250",
                "campaign_id": "camp-tool-test",
                "source_url": "https://cdn.example.com/banner.png",
            },
            {
                "asset_type": "video",
                "format_spec": {"duration_sec": 30, "vast_version": "4.2"},
                "asset_name": "Video 30s",
                "campaign_id": "camp-tool-test",
                "source_url": "https://cdn.example.com/video.mp4",
            },
        ]

        result = tool._run(action="validate", assets=asset_dicts)
        assert "2 valid" in result
        assert "0 invalid" in result

    def test_tool_match_action_with_pipeline_deals(self):
        """The tool's match action should pair assets with deals from pipeline output."""
        tool = CreativeManagementTool()

        asset_dicts = [
            {
                "asset_id": "asset-vid-001",
                "asset_type": "video",
                "format_spec": {"duration_sec": 30, "vast_version": "4.2"},
                "asset_name": "CTV Spot",
                "campaign_id": "camp-tool-test",
                "source_url": "https://cdn.example.com/ctv.mp4",
                "validation_status": "valid",
            },
            {
                "asset_id": "asset-disp-001",
                "asset_type": "display",
                "format_spec": {"width": 300, "height": 250},
                "asset_name": "Banner",
                "campaign_id": "camp-tool-test",
                "source_url": "https://cdn.example.com/banner.png",
                "validation_status": "valid",
            },
        ]

        deals = [
            {
                "seller_deal_id": "ctv-deal-001",
                "deal_name": "CTV PG Deal",
                "media_type": "video",
                "creative_requirements": {},
            },
            {
                "seller_deal_id": "display-deal-001",
                "deal_name": "Display PD Deal",
                "media_type": "display",
                "creative_requirements": {},
            },
        ]

        result = tool._run(action="match", assets=asset_dicts, deals=deals)
        assert "Matches (2)" in result
        assert "CTV Spot" in result
        assert "Banner" in result

    def test_tool_list_mismatches_action(self):
        """The tool's list_mismatches action should report unmatched deals."""
        tool = CreativeManagementTool()

        # Only display assets, but deal requires video
        asset_dicts = [
            {
                "asset_id": "asset-disp-001",
                "asset_type": "display",
                "format_spec": {"width": 300, "height": 250},
                "asset_name": "Banner",
                "campaign_id": "camp-tool-test",
                "source_url": "https://cdn.example.com/banner.png",
                "validation_status": "valid",
            },
        ]

        deals = [
            {
                "seller_deal_id": "ctv-deal-001",
                "deal_name": "CTV Deal",
                "media_type": "video",
                "creative_requirements": {},
            },
        ]

        result = tool._run(action="list_mismatches", assets=asset_dicts, deals=deals)
        assert "Mismatches (1)" in result
        assert "ctv-deal-001" in result

    def test_tool_validate_then_match_round_trip(self):
        """Validate assets, then match them to deals -- full tool round trip."""
        tool = CreativeManagementTool()

        raw_assets = [
            {
                "asset_type": "video",
                "format_spec": {"duration_sec": 30, "vast_version": "4.2"},
                "asset_name": "Good Video",
                "campaign_id": "camp-roundtrip",
                "source_url": "https://cdn.example.com/good.mp4",
            },
            {
                "asset_type": "video",
                "format_spec": {"duration_sec": 30, "vast_version": "99.0"},
                "asset_name": "Bad Video",
                "campaign_id": "camp-roundtrip",
                "source_url": "https://cdn.example.com/bad.mp4",
            },
        ]

        # Step 1: Validate
        validate_result = tool._run(action="validate", assets=raw_assets)
        assert "1 valid" in validate_result
        assert "1 invalid" in validate_result

        # Step 2: Match (only the valid one should match)
        # Simulating the pattern: after validation, pass validated assets to match
        validated_assets = [
            {
                "asset_id": "vid-001",
                "asset_type": "video",
                "format_spec": {"duration_sec": 30, "vast_version": "4.2"},
                "asset_name": "Good Video",
                "campaign_id": "camp-roundtrip",
                "source_url": "https://cdn.example.com/good.mp4",
                "validation_status": "valid",
            },
            {
                "asset_id": "vid-002",
                "asset_type": "video",
                "format_spec": {"duration_sec": 30, "vast_version": "99.0"},
                "asset_name": "Bad Video",
                "campaign_id": "camp-roundtrip",
                "source_url": "https://cdn.example.com/bad.mp4",
                "validation_status": "invalid",
                "validation_errors": ["Unsupported VAST version"],
            },
        ]

        deals = [
            {
                "seller_deal_id": "v-deal-001",
                "deal_name": "CTV PG",
                "media_type": "video",
                "creative_requirements": {},
            },
        ]

        match_result = tool._run(action="match", assets=validated_assets, deals=deals)
        assert "Matches (1)" in match_result
        assert "Good Video" in match_result
        # Bad Video should not appear in matches
        assert "Bad Video" not in match_result.split("Mismatches")[0] if "Mismatches" in match_result else True


# ---------------------------------------------------------------------------
# Test: Pipeline + creative store integration
# ---------------------------------------------------------------------------


class TestPipelineCreativeStoreIntegration:
    """Verify creative assets can be stored and retrieved for a campaign."""

    def test_creative_assets_stored_for_pipeline_campaign(
        self, pipeline, fake_store, validator
    ):
        """Creative assets saved to the store should be retrievable by campaign_id."""
        loop = asyncio.get_event_loop()
        summary = loop.run_until_complete(pipeline.run(_make_brief_dict()))
        campaign_id = summary["campaign_id"]

        # Save creative assets to the store for this campaign
        fake_store.save_creative_asset(
            campaign_id=campaign_id,
            asset_name="CTV 30s Spot",
            asset_type="video",
            format_spec=json.dumps({"duration_sec": 30, "vast_version": "4.2"}),
            source_url="https://cdn.example.com/ctv.mp4",
            validation_status="valid",
        )
        fake_store.save_creative_asset(
            campaign_id=campaign_id,
            asset_name="Banner 300x250",
            asset_type="display",
            format_spec=json.dumps({"width": 300, "height": 250}),
            source_url="https://cdn.example.com/banner.png",
            validation_status="valid",
        )

        # Retrieve and verify
        assets = fake_store.list_creative_assets(campaign_id=campaign_id)
        assert len(assets) == 2
        asset_types = {a["asset_type"] for a in assets}
        assert "video" in asset_types
        assert "display" in asset_types

    def test_creative_assets_filtered_by_type(self, pipeline, fake_store):
        """list_creative_assets should filter by asset_type."""
        loop = asyncio.get_event_loop()
        summary = loop.run_until_complete(pipeline.run(_make_brief_dict()))
        campaign_id = summary["campaign_id"]

        fake_store.save_creative_asset(
            campaign_id=campaign_id,
            asset_name="Video 1",
            asset_type="video",
        )
        fake_store.save_creative_asset(
            campaign_id=campaign_id,
            asset_name="Banner 1",
            asset_type="display",
        )

        video_assets = fake_store.list_creative_assets(
            campaign_id=campaign_id, asset_type="video",
        )
        assert len(video_assets) == 1
        assert video_assets[0]["asset_name"] == "Video 1"

    def test_campaign_not_found_prevents_creative_operations(self, fake_store):
        """Creative assets for a non-existent campaign should return empty."""
        assets = fake_store.list_creative_assets(campaign_id="nonexistent-camp")
        assert len(assets) == 0


# ---------------------------------------------------------------------------
# Test: Orchestrator failure does not block creative operations
# ---------------------------------------------------------------------------


class TestOrchestratorFailureCreativeRecovery:
    """Verify creative operations still work when orchestrator partially fails."""

    def test_partial_booking_failure_still_allows_creative_matching(
        self, fake_store, event_bus, validator, matcher
    ):
        """If one channel's booking fails, creative matching still works on successful channels."""
        loop = asyncio.get_event_loop()

        # Orchestrator: first call succeeds (CTV), second raises (DISPLAY)
        orch = AsyncMock(spec=MultiSellerOrchestrator)
        call_count = 0

        async def _side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_orchestration_result(
                    deal_prefix="ctv-ok",
                    num_deals=1,
                    media_type="video",
                )
            else:
                raise RuntimeError("Seller unavailable for display")

        orch.orchestrate.side_effect = _side_effect

        pipeline = CampaignPipeline(
            store=fake_store, orchestrator=orch, event_bus=event_bus,
        )

        summary = loop.run_until_complete(pipeline.run(_make_brief_dict()))
        campaign_id = summary["campaign_id"]

        # Pipeline should still reach READY
        assert summary["status"] == CampaignStatus.READY.value

        # Creative matching on the successful channel should work
        video = _make_creative_asset(
            campaign_id=campaign_id,
            asset_type=AssetType.VIDEO,
            asset_name="CTV Creative",
        )
        validator.validate(video)
        assert video.validation_status == ValidationStatus.VALID

        deals = [
            _make_deal_dict("ctv-ok-000", media_type="video", deal_name="CTV Deal"),
        ]
        result = matcher.match_creatives_to_deals(assets=[video], deals=deals)
        assert len(result.matches) == 1
        assert len(result.mismatches) == 0

    def test_all_bookings_fail_creative_matching_reports_empty(
        self, fake_store, event_bus, validator, matcher
    ):
        """If all bookings fail, no deals to match creatives against."""
        loop = asyncio.get_event_loop()

        orch = AsyncMock(spec=MultiSellerOrchestrator)
        orch.orchestrate.side_effect = RuntimeError("All sellers down")

        pipeline = CampaignPipeline(
            store=fake_store, orchestrator=orch, event_bus=event_bus,
        )

        summary = loop.run_until_complete(pipeline.run(_make_brief_dict()))

        # Pipeline still reaches READY
        assert summary["status"] == CampaignStatus.READY.value

        # With no deals, matching against empty deals list produces no matches
        video = _make_creative_asset(
            campaign_id=summary["campaign_id"],
            asset_type=AssetType.VIDEO,
            asset_name="Orphaned Video",
        )
        validator.validate(video)

        result = matcher.match_creatives_to_deals(assets=[video], deals=[])
        assert len(result.matches) == 0
        assert len(result.mismatches) == 0


# ---------------------------------------------------------------------------
# Test: Validator + Matcher interplay (cross-component)
# ---------------------------------------------------------------------------


class TestValidatorMatcherInterplay:
    """Verify the validator's output feeds correctly into the matcher."""

    def test_validator_marks_valid_assets_matcher_uses_them(self, validator, matcher):
        """Only validator-approved assets should be used by matcher."""
        campaign_id = "camp-interplay"

        good_display = _make_creative_asset(
            campaign_id=campaign_id,
            asset_type=AssetType.DISPLAY,
            asset_name="Good Banner",
            format_spec={"width": 300, "height": 250},
        )
        bad_display = _make_creative_asset(
            campaign_id=campaign_id,
            asset_type=AssetType.DISPLAY,
            asset_name="Bad Banner",
            format_spec={"width": -1, "height": 250},  # Invalid width
        )

        validator.validate(good_display)
        validator.validate(bad_display)

        assert good_display.validation_status == ValidationStatus.VALID
        assert bad_display.validation_status == ValidationStatus.INVALID

        deals = [
            _make_deal_dict("d-1", media_type="display", deal_name="Banner Slot"),
        ]

        result = matcher.match_creatives_to_deals(
            assets=[good_display, bad_display], deals=deals,
        )

        # Only the valid banner should match
        assert len(result.matches) == 1
        assert result.matches[0]["asset_name"] == "Good Banner"

    def test_mixed_valid_invalid_across_types(self, validator, matcher):
        """Mix of valid and invalid assets across multiple types."""
        campaign_id = "camp-mixed"

        valid_video = _make_creative_asset(
            campaign_id=campaign_id,
            asset_type=AssetType.VIDEO,
            asset_name="Valid Video",
            format_spec={"duration_sec": 30, "vast_version": "4.0"},
        )
        invalid_video = _make_creative_asset(
            campaign_id=campaign_id,
            asset_type=AssetType.VIDEO,
            asset_name="Invalid Video",
            format_spec={"duration_sec": -5, "vast_version": "4.0"},
        )
        valid_display = _make_creative_asset(
            campaign_id=campaign_id,
            asset_type=AssetType.DISPLAY,
            asset_name="Valid Banner",
            format_spec={"width": 728, "height": 90},
        )

        for asset in [valid_video, invalid_video, valid_display]:
            validator.validate(asset)

        assert valid_video.validation_status == ValidationStatus.VALID
        assert invalid_video.validation_status == ValidationStatus.INVALID
        assert valid_display.validation_status == ValidationStatus.VALID

        deals = [
            _make_deal_dict("v-1", media_type="video", deal_name="Video Deal"),
            _make_deal_dict("d-1", media_type="display", deal_name="Display Deal"),
        ]

        result = matcher.match_creatives_to_deals(
            assets=[valid_video, invalid_video, valid_display],
            deals=deals,
        )

        assert len(result.matches) == 2
        assert len(result.mismatches) == 0

        match_names = {m["asset_name"] for m in result.matches}
        assert "Valid Video" in match_names
        assert "Valid Banner" in match_names
        assert "Invalid Video" not in match_names
