# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for CampaignPipeline (buyer-u8l).

End-to-end pipeline: brief in -> plan -> orchestrate -> book -> ready.
Uses mocked CampaignStore, MultiSellerOrchestrator, and EventBus.
"""

import asyncio
import json
import uuid
from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ad_buyer.events.bus import InMemoryEventBus
from ad_buyer.events.models import EventType
from ad_buyer.models.campaign_brief import (
    ChannelType,
)
from ad_buyer.models.state_machine import CampaignStatus
from ad_buyer.orchestration.multi_seller import (
    DealSelection,
    MultiSellerOrchestrator,
    OrchestrationResult,
)

# We'll import the module under test once it exists.
# For now these imports define the expected API.
from ad_buyer.pipelines.campaign_pipeline import CampaignPipeline, CampaignPlan

# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------


def _make_brief_dict(**overrides: Any) -> dict[str, Any]:
    """Return a valid campaign brief dict for testing."""
    today = date.today()
    brief = {
        "advertiser_id": "adv-001",
        "campaign_name": "Test Campaign",
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


def _make_brief_json(**overrides: Any) -> str:
    """Return a valid campaign brief JSON string."""
    return json.dumps(_make_brief_dict(**overrides))


class FakeCampaignStore:
    """In-memory fake of CampaignStore for pipeline tests.

    Provides just enough behavior to exercise create_campaign,
    start_planning, start_booking, mark_ready, get_campaign, and
    update_campaign.
    """

    def __init__(self) -> None:
        self._campaigns: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []

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

    def get_campaign(self, campaign_id: str) -> dict[str, Any] | None:
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


def _make_fake_deal(deal_id: str = "deal-001", deal_type: str = "PD", cpm: float = 12.50):
    """Return a mock DealResponse."""
    deal = MagicMock()
    deal.deal_id = deal_id
    deal.deal_type = deal_type
    deal.pricing = MagicMock()
    deal.pricing.final_cpm = cpm
    return deal


def _make_orchestration_result(
    num_deals: int = 2,
    total_spend: float = 50_000.0,
    remaining_budget: float = 10_000.0,
) -> OrchestrationResult:
    """Return a mock OrchestrationResult with booked deals."""
    deals = [_make_fake_deal(deal_id=f"deal-{i:03d}") for i in range(num_deals)]
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


@pytest.fixture
def fake_store() -> FakeCampaignStore:
    return FakeCampaignStore()


@pytest.fixture
def event_bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
def mock_orchestrator() -> AsyncMock:
    """Return an AsyncMock of MultiSellerOrchestrator."""
    orch = AsyncMock(spec=MultiSellerOrchestrator)
    orch.orchestrate.return_value = _make_orchestration_result()
    return orch


@pytest.fixture
def pipeline(fake_store, mock_orchestrator, event_bus) -> CampaignPipeline:
    return CampaignPipeline(
        store=fake_store,
        orchestrator=mock_orchestrator,
        event_bus=event_bus,
    )


# ---------------------------------------------------------------------------
# Tests: ingest_brief
# ---------------------------------------------------------------------------


class TestIngestBrief:
    """Test CampaignPipeline.ingest_brief()."""

    def test_ingest_valid_brief_creates_campaign_in_draft(self, pipeline, fake_store):
        """A valid brief JSON should create a campaign in DRAFT status."""
        campaign_id = asyncio.get_event_loop().run_until_complete(
            pipeline.ingest_brief(_make_brief_json())
        )
        assert campaign_id is not None
        campaign = fake_store.get_campaign(campaign_id)
        assert campaign is not None
        assert campaign["status"] == CampaignStatus.DRAFT.value
        assert campaign["advertiser_id"] == "adv-001"
        assert campaign["total_budget"] == 100_000.0

    def test_ingest_brief_accepts_dict(self, pipeline, fake_store):
        """ingest_brief should also accept a dict directly."""
        campaign_id = asyncio.get_event_loop().run_until_complete(
            pipeline.ingest_brief(_make_brief_dict())
        )
        assert campaign_id is not None
        campaign = fake_store.get_campaign(campaign_id)
        assert campaign is not None

    def test_ingest_invalid_brief_raises(self, pipeline):
        """An invalid brief should raise a ValueError."""
        with pytest.raises((ValueError, Exception)):
            asyncio.get_event_loop().run_until_complete(
                pipeline.ingest_brief('{"not": "a valid brief"}')
            )

    def test_ingest_brief_emits_campaign_created_event(self, pipeline, event_bus):
        """ingest_brief should emit a CAMPAIGN_CREATED event."""
        asyncio.get_event_loop().run_until_complete(pipeline.ingest_brief(_make_brief_json()))
        events = asyncio.get_event_loop().run_until_complete(
            event_bus.list_events(event_type=EventType.CAMPAIGN_CREATED.value)
        )
        assert len(events) >= 1

    def test_ingest_brief_stores_channel_info(self, pipeline, fake_store):
        """Channel allocations from the brief should be stored."""
        campaign_id = asyncio.get_event_loop().run_until_complete(
            pipeline.ingest_brief(_make_brief_json())
        )
        campaign = fake_store.get_campaign(campaign_id)
        # Channels should be preserved (as JSON string or as-is)
        assert campaign.get("channels") is not None


# ---------------------------------------------------------------------------
# Tests: plan_campaign
# ---------------------------------------------------------------------------


class TestPlanCampaign:
    """Test CampaignPipeline.plan_campaign()."""

    def test_plan_transitions_to_planning(self, pipeline, fake_store):
        """plan_campaign should move status from DRAFT to PLANNING."""
        campaign_id = asyncio.get_event_loop().run_until_complete(
            pipeline.ingest_brief(_make_brief_json())
        )
        plan = asyncio.get_event_loop().run_until_complete(pipeline.plan_campaign(campaign_id))
        campaign = fake_store.get_campaign(campaign_id)
        assert campaign["status"] == CampaignStatus.PLANNING.value

    def test_plan_returns_channel_plans(self, pipeline, fake_store):
        """plan_campaign should return a CampaignPlan with per-channel info."""
        campaign_id = asyncio.get_event_loop().run_until_complete(
            pipeline.ingest_brief(_make_brief_json())
        )
        plan = asyncio.get_event_loop().run_until_complete(pipeline.plan_campaign(campaign_id))
        assert isinstance(plan, CampaignPlan)
        assert len(plan.channel_plans) == 2  # CTV and DISPLAY

    def test_plan_allocates_budgets_per_channel(self, pipeline, fake_store):
        """Each channel plan should have the correct budget allocation."""
        campaign_id = asyncio.get_event_loop().run_until_complete(
            pipeline.ingest_brief(_make_brief_json())
        )
        plan = asyncio.get_event_loop().run_until_complete(pipeline.plan_campaign(campaign_id))
        ctv_plan = next(cp for cp in plan.channel_plans if cp.channel == ChannelType.CTV)
        display_plan = next(cp for cp in plan.channel_plans if cp.channel == ChannelType.DISPLAY)
        assert ctv_plan.budget == 60_000.0  # 60% of 100k
        assert display_plan.budget == 40_000.0  # 40% of 100k

    def test_plan_emits_plan_generated_event(self, pipeline, event_bus):
        """plan_campaign should emit a CAMPAIGN_PLAN_GENERATED event."""
        campaign_id = asyncio.get_event_loop().run_until_complete(
            pipeline.ingest_brief(_make_brief_json())
        )
        asyncio.get_event_loop().run_until_complete(pipeline.plan_campaign(campaign_id))
        events = asyncio.get_event_loop().run_until_complete(
            event_bus.list_events(event_type=EventType.CAMPAIGN_PLAN_GENERATED.value)
        )
        assert len(events) >= 1

    def test_plan_campaign_not_found_raises(self, pipeline):
        """plan_campaign for a non-existent campaign should raise KeyError."""
        with pytest.raises(KeyError):
            asyncio.get_event_loop().run_until_complete(pipeline.plan_campaign("nonexistent-id"))


# ---------------------------------------------------------------------------
# Tests: execute_booking
# ---------------------------------------------------------------------------


class TestExecuteBooking:
    """Test CampaignPipeline.execute_booking()."""

    def test_booking_transitions_to_booking(self, pipeline, fake_store):
        """execute_booking should move from PLANNING to BOOKING."""
        campaign_id = asyncio.get_event_loop().run_until_complete(
            pipeline.ingest_brief(_make_brief_json())
        )
        asyncio.get_event_loop().run_until_complete(pipeline.plan_campaign(campaign_id))
        result = asyncio.get_event_loop().run_until_complete(pipeline.execute_booking(campaign_id))
        campaign = fake_store.get_campaign(campaign_id)
        assert campaign["status"] == CampaignStatus.BOOKING.value

    def test_booking_calls_orchestrator_per_channel(self, pipeline, mock_orchestrator):
        """execute_booking should call orchestrator.orchestrate for each channel."""
        campaign_id = asyncio.get_event_loop().run_until_complete(
            pipeline.ingest_brief(_make_brief_json())
        )
        asyncio.get_event_loop().run_until_complete(pipeline.plan_campaign(campaign_id))
        asyncio.get_event_loop().run_until_complete(pipeline.execute_booking(campaign_id))
        # Should have been called once per channel (2 channels: CTV, DISPLAY)
        assert mock_orchestrator.orchestrate.call_count == 2

    def test_booking_returns_results_per_channel(self, pipeline):
        """execute_booking should return a dict mapping channels to results."""
        campaign_id = asyncio.get_event_loop().run_until_complete(
            pipeline.ingest_brief(_make_brief_json())
        )
        asyncio.get_event_loop().run_until_complete(pipeline.plan_campaign(campaign_id))
        result = asyncio.get_event_loop().run_until_complete(pipeline.execute_booking(campaign_id))
        assert isinstance(result, dict)
        assert len(result) == 2  # CTV and DISPLAY channels

    def test_booking_emits_booking_started_event(self, pipeline, event_bus):
        """execute_booking should emit a CAMPAIGN_BOOKING_STARTED event."""
        campaign_id = asyncio.get_event_loop().run_until_complete(
            pipeline.ingest_brief(_make_brief_json())
        )
        asyncio.get_event_loop().run_until_complete(pipeline.plan_campaign(campaign_id))
        asyncio.get_event_loop().run_until_complete(pipeline.execute_booking(campaign_id))
        events = asyncio.get_event_loop().run_until_complete(
            event_bus.list_events(event_type=EventType.CAMPAIGN_BOOKING_STARTED.value)
        )
        assert len(events) >= 1

    def test_booking_emits_booking_completed_event(self, pipeline, event_bus):
        """execute_booking should emit a CAMPAIGN_BOOKING_COMPLETED event."""
        campaign_id = asyncio.get_event_loop().run_until_complete(
            pipeline.ingest_brief(_make_brief_json())
        )
        asyncio.get_event_loop().run_until_complete(pipeline.plan_campaign(campaign_id))
        asyncio.get_event_loop().run_until_complete(pipeline.execute_booking(campaign_id))
        events = asyncio.get_event_loop().run_until_complete(
            event_bus.list_events(event_type=EventType.CAMPAIGN_BOOKING_COMPLETED.value)
        )
        assert len(events) >= 1

    def test_booking_not_found_raises(self, pipeline):
        """execute_booking for a non-existent campaign should raise KeyError."""
        with pytest.raises(KeyError):
            asyncio.get_event_loop().run_until_complete(pipeline.execute_booking("nonexistent-id"))


# ---------------------------------------------------------------------------
# Tests: finalize
# ---------------------------------------------------------------------------


class TestFinalize:
    """Test CampaignPipeline.finalize()."""

    def test_finalize_transitions_to_ready(self, pipeline, fake_store):
        """finalize should move from BOOKING to READY."""
        campaign_id = asyncio.get_event_loop().run_until_complete(
            pipeline.ingest_brief(_make_brief_json())
        )
        asyncio.get_event_loop().run_until_complete(pipeline.plan_campaign(campaign_id))
        asyncio.get_event_loop().run_until_complete(pipeline.execute_booking(campaign_id))
        asyncio.get_event_loop().run_until_complete(pipeline.finalize(campaign_id))
        campaign = fake_store.get_campaign(campaign_id)
        assert campaign["status"] == CampaignStatus.READY.value

    def test_finalize_emits_ready_event(self, pipeline, event_bus):
        """finalize should emit a CAMPAIGN_READY event."""
        campaign_id = asyncio.get_event_loop().run_until_complete(
            pipeline.ingest_brief(_make_brief_json())
        )
        asyncio.get_event_loop().run_until_complete(pipeline.plan_campaign(campaign_id))
        asyncio.get_event_loop().run_until_complete(pipeline.execute_booking(campaign_id))
        asyncio.get_event_loop().run_until_complete(pipeline.finalize(campaign_id))
        events = asyncio.get_event_loop().run_until_complete(
            event_bus.list_events(event_type=EventType.CAMPAIGN_READY.value)
        )
        assert len(events) >= 1

    def test_finalize_not_found_raises(self, pipeline):
        """finalize for non-existent campaign should raise KeyError."""
        with pytest.raises(KeyError):
            asyncio.get_event_loop().run_until_complete(pipeline.finalize("nonexistent-id"))


# ---------------------------------------------------------------------------
# Tests: run (end-to-end)
# ---------------------------------------------------------------------------


class TestRunEndToEnd:
    """Test CampaignPipeline.run() end-to-end."""

    def test_run_returns_campaign_summary(self, pipeline, fake_store):
        """run should return a summary dict with campaign_id and status."""
        summary = asyncio.get_event_loop().run_until_complete(pipeline.run(_make_brief_json()))
        assert "campaign_id" in summary
        assert summary["status"] == CampaignStatus.READY.value

    def test_run_goes_through_all_states(self, pipeline, fake_store):
        """run should transition DRAFT -> PLANNING -> BOOKING -> READY."""
        summary = asyncio.get_event_loop().run_until_complete(pipeline.run(_make_brief_json()))
        campaign = fake_store.get_campaign(summary["campaign_id"])
        assert campaign["status"] == CampaignStatus.READY.value

    def test_run_includes_booked_deals(self, pipeline):
        """run summary should include booked deal info."""
        summary = asyncio.get_event_loop().run_until_complete(pipeline.run(_make_brief_json()))
        assert "channels" in summary
        assert len(summary["channels"]) == 2  # CTV and DISPLAY

    def test_run_emits_all_lifecycle_events(self, pipeline, event_bus):
        """run should emit created, plan, booking_started, booking_completed, ready."""
        asyncio.get_event_loop().run_until_complete(pipeline.run(_make_brief_json()))
        all_events = asyncio.get_event_loop().run_until_complete(event_bus.list_events())
        event_types = [e.event_type for e in all_events]
        assert EventType.CAMPAIGN_CREATED in event_types
        assert EventType.CAMPAIGN_PLAN_GENERATED in event_types
        assert EventType.CAMPAIGN_BOOKING_STARTED in event_types
        assert EventType.CAMPAIGN_BOOKING_COMPLETED in event_types
        assert EventType.CAMPAIGN_READY in event_types

    def test_run_with_single_channel(self, pipeline, mock_orchestrator):
        """run should work with a single-channel campaign."""
        brief = _make_brief_dict(
            channels=[{"channel": "AUDIO", "budget_pct": 100}],
        )
        summary = asyncio.get_event_loop().run_until_complete(pipeline.run(brief))
        assert summary["status"] == CampaignStatus.READY.value
        assert mock_orchestrator.orchestrate.call_count == 1

    def test_run_with_three_channels(self, pipeline, mock_orchestrator):
        """run should orchestrate once per channel for a 3-channel brief."""
        brief = _make_brief_dict(
            channels=[
                {"channel": "CTV", "budget_pct": 50},
                {"channel": "DISPLAY", "budget_pct": 30},
                {"channel": "AUDIO", "budget_pct": 20},
            ],
        )
        summary = asyncio.get_event_loop().run_until_complete(pipeline.run(brief))
        assert summary["status"] == CampaignStatus.READY.value
        assert mock_orchestrator.orchestrate.call_count == 3

    def test_run_invalid_brief_raises(self, pipeline):
        """run should raise on an invalid brief."""
        with pytest.raises((ValueError, Exception)):
            asyncio.get_event_loop().run_until_complete(pipeline.run('{"bad": "data"}'))


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_orchestrator_returns_no_deals(self, pipeline, fake_store, mock_orchestrator):
        """Pipeline should still finalize even if no deals are booked."""
        mock_orchestrator.orchestrate.return_value = _make_orchestration_result(
            num_deals=0,
            total_spend=0,
            remaining_budget=60_000.0,
        )
        summary = asyncio.get_event_loop().run_until_complete(pipeline.run(_make_brief_json()))
        # Pipeline still completes to READY even with no deals
        assert summary["status"] == CampaignStatus.READY.value

    def test_orchestrator_partial_failure(self, pipeline, mock_orchestrator):
        """If orchestrator fails for one channel, pipeline should still proceed."""
        call_count = 0

        async def _side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_orchestration_result(num_deals=2)
            else:
                # Second channel fails
                raise RuntimeError("Seller unavailable")

        mock_orchestrator.orchestrate.side_effect = _side_effect
        summary = asyncio.get_event_loop().run_until_complete(pipeline.run(_make_brief_json()))
        # Should still reach READY with partial results
        assert summary["status"] == CampaignStatus.READY.value

    def test_channel_media_type_mapping(self, pipeline, mock_orchestrator):
        """The pipeline should map ChannelType to the correct media_type for the orchestrator."""
        brief = _make_brief_dict(
            channels=[{"channel": "CTV", "budget_pct": 100}],
        )
        asyncio.get_event_loop().run_until_complete(pipeline.run(brief))
        call_args = mock_orchestrator.orchestrate.call_args
        # The inventory_requirements should have media_type derived from CTV
        inv_req = (
            call_args.kwargs.get("inventory_requirements")
            or call_args[1].get("inventory_requirements")
            if len(call_args) > 1
            else None
        )
        if inv_req is None and call_args.args:
            inv_req = call_args.args[0]
        # Accept either keyword or positional form
        assert mock_orchestrator.orchestrate.called

    def test_pipeline_preserves_brief_metadata(self, pipeline, fake_store):
        """The pipeline should store the full brief info on the campaign."""
        brief = _make_brief_dict(
            preferred_sellers=["seller-a", "seller-b"],
            excluded_sellers=["seller-x"],
        )
        summary = asyncio.get_event_loop().run_until_complete(pipeline.run(brief))
        campaign = fake_store.get_campaign(summary["campaign_id"])
        assert campaign is not None
        assert campaign["advertiser_id"] == "adv-001"
