# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for threading AudiencePlan through the orchestrator data classes.

Covers proposal §5.2 + §6 row 5 (bead ar-9nwu): the audience plan now lives
on InventoryRequirements, DealParams, QuoteRequest, and DealBookingRequest
with a backward-compatible None default. This bead does NOT populate the
field from the planner -- that's a follow-up bead. These tests confirm
only the field exists, defaults to None, accepts a valid AudiencePlan,
serializes round-trip, and survives an end-to-end derivation chain from
CampaignPlan -> InventoryRequirements -> DealParams -> QuoteRequest ->
DealBookingRequest.
"""

from __future__ import annotations

from dataclasses import asdict

import pytest

from ad_buyer.models.audience_plan import AudiencePlan, AudienceRef
from ad_buyer.models.deals import DealBookingRequest, QuoteRequest
from ad_buyer.orchestration.multi_seller import (
    DealParams,
    InventoryRequirements,
)
from ad_buyer.pipelines.campaign_pipeline import CampaignPlan, ChannelPlan
from ad_buyer.models.campaign_brief import ChannelType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_minimal_plan() -> AudiencePlan:
    """Build a minimal valid AudiencePlan for tests.

    One Standard primary, no constraints/extensions/exclusions. The
    audience_plan_id is auto-populated by the model validator.
    """

    return AudiencePlan(
        primary=AudienceRef(
            type="standard",
            identifier="3-7",
            taxonomy="iab-audience",
            version="1.1",
            source="explicit",
        ),
        rationale="Test plan: Auto Intenders.",
    )


# ---------------------------------------------------------------------------
# Backward compatibility: each class constructs without audience_plan
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Existing call sites that don't pass audience_plan must keep working."""

    def test_inventory_requirements_defaults_to_none(self) -> None:
        ir = InventoryRequirements(media_type="ctv", deal_types=["PD"])
        assert ir.audience_plan is None

    def test_deal_params_defaults_to_none(self) -> None:
        dp = DealParams(
            product_id="prod-1",
            deal_type="PD",
            impressions=100_000,
            flight_start="2026-05-01",
            flight_end="2026-06-30",
        )
        assert dp.audience_plan is None

    def test_quote_request_defaults_to_none(self) -> None:
        qr = QuoteRequest(product_id="prod-1")
        assert qr.audience_plan is None

    def test_deal_booking_request_defaults_to_none(self) -> None:
        dbr = DealBookingRequest(quote_id="q-1")
        assert dbr.audience_plan is None


# ---------------------------------------------------------------------------
# Each class accepts a valid AudiencePlan
# ---------------------------------------------------------------------------


class TestAcceptsAudiencePlan:
    """Each class accepts an AudiencePlan instance via the new field."""

    def test_inventory_requirements_accepts_plan(self) -> None:
        plan = _build_minimal_plan()
        ir = InventoryRequirements(
            media_type="ctv", deal_types=["PD"], audience_plan=plan
        )
        assert ir.audience_plan is plan
        assert ir.audience_plan.primary.identifier == "3-7"

    def test_deal_params_accepts_plan(self) -> None:
        plan = _build_minimal_plan()
        dp = DealParams(
            product_id="prod-1",
            deal_type="PD",
            impressions=100_000,
            flight_start="2026-05-01",
            flight_end="2026-06-30",
            audience_plan=plan,
        )
        assert dp.audience_plan is plan

    def test_quote_request_accepts_plan(self) -> None:
        plan = _build_minimal_plan()
        qr = QuoteRequest(product_id="prod-1", audience_plan=plan)
        assert qr.audience_plan is not None
        assert qr.audience_plan.audience_plan_id == plan.audience_plan_id

    def test_deal_booking_request_accepts_plan(self) -> None:
        plan = _build_minimal_plan()
        dbr = DealBookingRequest(quote_id="q-1", audience_plan=plan)
        assert dbr.audience_plan is not None
        assert dbr.audience_plan.primary.identifier == "3-7"


# ---------------------------------------------------------------------------
# Round-trip serialization
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Each class round-trips through dict serialization preserving the plan."""

    def test_quote_request_round_trips(self) -> None:
        plan = _build_minimal_plan()
        qr = QuoteRequest(product_id="prod-1", audience_plan=plan)

        data = qr.model_dump()
        rebuilt = QuoteRequest(**data)

        assert rebuilt.audience_plan is not None
        assert (
            rebuilt.audience_plan.audience_plan_id == plan.audience_plan_id
        )
        assert rebuilt.audience_plan.primary.identifier == "3-7"

    def test_deal_booking_request_round_trips(self) -> None:
        plan = _build_minimal_plan()
        dbr = DealBookingRequest(quote_id="q-1", audience_plan=plan)

        data = dbr.model_dump()
        rebuilt = DealBookingRequest(**data)

        assert rebuilt.audience_plan is not None
        assert (
            rebuilt.audience_plan.audience_plan_id == plan.audience_plan_id
        )

    def test_inventory_requirements_round_trips_via_asdict(self) -> None:
        # InventoryRequirements is a dataclass; round-trip via asdict + ctor.
        plan = _build_minimal_plan()
        ir = InventoryRequirements(
            media_type="ctv", deal_types=["PD"], audience_plan=plan
        )

        # asdict recursively converts the AudiencePlan to a dict; rebuilding
        # requires re-validating the plan dict back into an AudiencePlan.
        raw = asdict(ir)
        rebuilt_plan = AudiencePlan.model_validate(raw["audience_plan"])
        rebuilt = InventoryRequirements(
            media_type=raw["media_type"],
            deal_types=raw["deal_types"],
            content_categories=raw["content_categories"],
            excluded_sellers=raw["excluded_sellers"],
            min_impressions=raw["min_impressions"],
            max_cpm=raw["max_cpm"],
            audience_plan=rebuilt_plan,
        )

        assert rebuilt.audience_plan is not None
        assert (
            rebuilt.audience_plan.audience_plan_id == plan.audience_plan_id
        )
        assert rebuilt.audience_plan.primary.identifier == "3-7"

    def test_deal_params_round_trips_via_asdict(self) -> None:
        plan = _build_minimal_plan()
        dp = DealParams(
            product_id="prod-1",
            deal_type="PD",
            impressions=100_000,
            flight_start="2026-05-01",
            flight_end="2026-06-30",
            audience_plan=plan,
        )

        raw = asdict(dp)
        rebuilt_plan = AudiencePlan.model_validate(raw["audience_plan"])
        rebuilt = DealParams(
            product_id=raw["product_id"],
            deal_type=raw["deal_type"],
            impressions=raw["impressions"],
            flight_start=raw["flight_start"],
            flight_end=raw["flight_end"],
            target_cpm=raw["target_cpm"],
            media_type=raw["media_type"],
            audience_plan=rebuilt_plan,
        )

        assert rebuilt.audience_plan is not None
        assert (
            rebuilt.audience_plan.audience_plan_id == plan.audience_plan_id
        )


# ---------------------------------------------------------------------------
# End-to-end thread: AudiencePlan survives every derivation step
# ---------------------------------------------------------------------------


class TestEndToEndThread:
    """The audience plan survives the full pipeline data-class chain."""

    def test_plan_survives_campaign_to_booking_chain(self) -> None:
        plan = _build_minimal_plan()
        plan_id = plan.audience_plan_id

        # 1. CampaignPlan carries the audience plan from brief ingestion.
        campaign_plan = CampaignPlan(
            campaign_id="camp-1",
            channel_plans=[
                ChannelPlan(
                    channel=ChannelType.CTV,
                    budget=50_000.0,
                    budget_pct=1.0,
                    media_type="ctv",
                    deal_types=["PD"],
                )
            ],
            total_budget=50_000.0,
            flight_start="2026-05-01",
            flight_end="2026-06-30",
            target_audience=plan,
        )
        assert campaign_plan.target_audience is not None
        assert campaign_plan.target_audience.audience_plan_id == plan_id

        # 2. CampaignPlan -> InventoryRequirements (orchestrator stage 1).
        ir = InventoryRequirements(
            media_type=campaign_plan.channel_plans[0].media_type,
            deal_types=campaign_plan.channel_plans[0].deal_types,
            audience_plan=campaign_plan.target_audience,
        )
        assert ir.audience_plan is not None
        assert ir.audience_plan.audience_plan_id == plan_id

        # 3. InventoryRequirements -> DealParams (orchestrator stage 2).
        dp = DealParams(
            product_id="prod-ctv-001",
            deal_type="PD",
            impressions=500_000,
            flight_start=campaign_plan.flight_start,
            flight_end=campaign_plan.flight_end,
            audience_plan=ir.audience_plan,
        )
        assert dp.audience_plan is not None
        assert dp.audience_plan.audience_plan_id == plan_id

        # 4. DealParams -> QuoteRequest (the wire request to the seller).
        qr = QuoteRequest(
            product_id=dp.product_id,
            deal_type=dp.deal_type,
            impressions=dp.impressions,
            flight_start=dp.flight_start,
            flight_end=dp.flight_end,
            target_cpm=dp.target_cpm,
            media_type=dp.media_type,
            audience_plan=dp.audience_plan,
        )
        assert qr.audience_plan is not None
        assert qr.audience_plan.audience_plan_id == plan_id

        # 5. DealParams -> DealBookingRequest (the booking call).
        dbr = DealBookingRequest(
            quote_id="q-1",
            audience_plan=dp.audience_plan,
        )
        assert dbr.audience_plan is not None
        assert dbr.audience_plan.audience_plan_id == plan_id

        # Sanity: the plan content survived without mutation.
        assert dbr.audience_plan.primary.identifier == "3-7"
        assert dbr.audience_plan.primary.type == "standard"

    def test_plan_id_stable_across_serialization_chain(self) -> None:
        """Round-tripping each stage's serialization preserves audience_plan_id."""

        plan = _build_minimal_plan()
        plan_id = plan.audience_plan_id

        qr = QuoteRequest(product_id="prod-1", audience_plan=plan)
        qr_round = QuoteRequest(**qr.model_dump())
        assert qr_round.audience_plan is not None
        assert qr_round.audience_plan.audience_plan_id == plan_id

        dbr = DealBookingRequest(quote_id="q-1", audience_plan=plan)
        dbr_round = DealBookingRequest(**dbr.model_dump())
        assert dbr_round.audience_plan is not None
        assert dbr_round.audience_plan.audience_plan_id == plan_id


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
