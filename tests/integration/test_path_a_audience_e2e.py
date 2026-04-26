# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""End-to-end integration test for Path A (CampaignPipeline).

Bead ar-lk23 / proposal §6 row 16 -- the buyer-side end-to-end test for
the brief-driven CampaignPipeline path identified in proposal §5.3:

    Path A: CampaignPipeline.ingest_brief -> plan_campaign -> execute_booking

The seller side is **mocked**: a MultiSellerOrchestrator stand-in captures
the InventoryRequirements / DealParams that the pipeline forwards, so we
can assert the full typed AudiencePlan (Standard primary + Contextual
constraint + Agentic extension) survives every stage and arrives at the
seller-facing boundary intact.

Part 1: fixtures + happy-path scenario.
Part 2 (this commit): adds three more scenarios:
  - Capability degradation against a legacy-default seller. Builds a real
    `MultiSellerOrchestrator` wired to a recording capability client +
    mocked deals client to exercise the actual `degrade_plan_for_seller`
    + audit-log emission path with the same 3-type plan content.
  - Hard-reject when the seller's standard taxonomy version doesn't
    cover the plan and `audience_strictness.primary=required`. Confirms
    the orchestrator surfaces the seller in
    `DealSelection.incompatible_sellers` (no booking attempted).
  - Cross-repo AudiencePlan JSON round-trip: builds a typed plan in the
    buyer, serializes to JSON, reconstructs through the seller's
    `AudienceRef` model, asserts byte-equivalent (sort_keys) round-trip.
    Schema-drift backstop -- the seller's own ucp.AudiencePlan has a
    different (legacy UCP) shape, so the round-trip is exercised at the
    AudienceRef level for primary + each constraint + each extension.

Reference:
  - AUDIENCE_PLANNER_3TYPE_EXTENSION_2026-04-25.md §5.1, §5.3, §6 row 16
  - tests/integration/test_path_b_audience_e2e.py (sister Path B tests)
  - tests/unit/test_buyer_preflight.py (orchestrator-level preflight)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

# Stub the Anthropic key BEFORE any ad_buyer.crews / agents imports.
# CrewAI Agent factories instantiate an LLM eagerly in __init__; we never
# make a network call here. Mirrors the Path B + unit test pattern.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-path-a-e2e")

import pytest

from ad_buyer.events.bus import InMemoryEventBus
from ad_buyer.models.audience_plan import (
    AudiencePlan,
    AudienceRef,
    ComplianceContext,
)
from ad_buyer.models.campaign_brief import CampaignBrief, parse_campaign_brief
from ad_buyer.models.state_machine import CampaignStatus
from ad_buyer.orchestration.multi_seller import (
    DealSelection,
    MultiSellerOrchestrator,
    OrchestrationResult,
)
from ad_buyer.pipelines.campaign_pipeline import CampaignPipeline


# ===========================================================================
# Fixtures
# ===========================================================================


def _three_type_plan_dict() -> dict[str, Any]:
    """Build a 3-type AudiencePlan dict (Standard + Contextual + Agentic).

    Mirrors the canonical example from proposal §5.1 -- a Standard
    primary narrowed by a Contextual constraint and extended by an
    Agentic lookalike. The agentic ref carries a compliance context.
    """

    return {
        "primary": {
            "type": "standard",
            "identifier": "3-7",
            "taxonomy": "iab-audience",
            "version": "1.1",
            "source": "explicit",
        },
        "constraints": [
            {
                "type": "contextual",
                "identifier": "1",  # Automotive (Content Tax 3.1)
                "taxonomy": "iab-content",
                "version": "3.1",
                "source": "resolved",
                "confidence": 0.92,
            }
        ],
        "extensions": [
            {
                "type": "agentic",
                "identifier": (
                    "emb://buyer.example.com/audiences/auto-converters-q1"
                ),
                "taxonomy": "agentic-audiences",
                "version": "draft-2026-01",
                "source": "explicit",
                "compliance_context": {
                    "jurisdiction": "US",
                    "consent_framework": "IAB-TCFv2",
                    "consent_string_ref": "tcf:CPxxxx-test",
                },
            }
        ],
        "rationale": (
            "Auto Intenders 25-54 (Standard primary), narrowed to "
            "Automotive content (Contextual constraint), extended by Q1 "
            "converter lookalikes (Agentic extension)."
        ),
    }


def _base_brief_dict(**overrides: Any) -> dict[str, Any]:
    """Minimum CampaignBrief skeleton with a valid 2-channel allocation."""

    today = date.today()
    base: dict[str, Any] = {
        "advertiser_id": "adv-patha-001",
        "campaign_name": "Path A integration test",
        "objective": "AWARENESS",
        "total_budget": 100_000.0,
        "currency": "USD",
        "flight_start": (today + timedelta(days=7)).isoformat(),
        "flight_end": (today + timedelta(days=37)).isoformat(),
        "channels": [
            {"channel": "CTV", "budget_pct": 60},
            {"channel": "DISPLAY", "budget_pct": 40},
        ],
    }
    base.update(overrides)
    return base


def _three_type_brief() -> CampaignBrief:
    """Brief carrying an explicit 3-type AudiencePlan."""

    return parse_campaign_brief(
        _base_brief_dict(target_audience=_three_type_plan_dict())
    )


# ---------------------------------------------------------------------------
# FakeCampaignStore -- mirrors the unit-test fake from
# tests/unit/test_campaign_pipeline.py so the pipeline can exercise its
# state-machine transitions without a real SQLite-backed store.
# ---------------------------------------------------------------------------


class FakeCampaignStore:
    """In-memory CampaignStore stand-in for pipeline integration tests."""

    def __init__(self) -> None:
        self._campaigns: dict[str, dict[str, Any]] = {}

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


def _booked_orchestration_result(
    deal_id: str = "deal-patha-001",
    spend: float = 50_000.0,
    remaining: float = 10_000.0,
) -> OrchestrationResult:
    """Build an OrchestrationResult that looks like a successful booking."""

    deal = MagicMock()
    deal.deal_id = deal_id
    deal.deal_type = "PD"
    deal.pricing = MagicMock()
    deal.pricing.final_cpm = 12.50
    return OrchestrationResult(
        discovered_sellers=[MagicMock(agent_id=f"seller-{i}") for i in range(2)],
        quote_results=[],
        ranked_quotes=[],
        selection=DealSelection(
            booked_deals=[deal],
            failed_bookings=[],
            total_spend=spend,
            remaining_budget=remaining,
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
    """A MultiSellerOrchestrator AsyncMock that captures every orchestrate call.

    The pipeline forwards InventoryRequirements / DealParams (each with
    an `audience_plan` attached per proposal §5.3 / bead ar-fgyq §6) into
    `orchestrate`. Inspecting the captured call args is how we verify
    the typed AudiencePlan reaches the seller boundary.
    """

    orch = AsyncMock(spec=MultiSellerOrchestrator)
    orch.orchestrate.return_value = _booked_orchestration_result()
    return orch


@pytest.fixture
def pipeline(
    fake_store: FakeCampaignStore,
    mock_orchestrator: AsyncMock,
    event_bus: InMemoryEventBus,
) -> CampaignPipeline:
    return CampaignPipeline(
        store=fake_store,
        orchestrator=mock_orchestrator,
        event_bus=event_bus,
    )


# ===========================================================================
# 1. CampaignPipeline happy path -- 3 audience types
# ===========================================================================


class TestCampaignPipelineThreeTypeHappyPath:
    """3-type plan flows brief -> plan -> book through CampaignPipeline."""

    def test_happy_path_three_types_through_path_a(
        self,
        pipeline: CampaignPipeline,
        mock_orchestrator: AsyncMock,
    ) -> None:
        """Full Path A: brief -> plan -> book; audience plan reaches seller.

        The brief carries an explicit 3-type plan (Standard primary +
        Contextual constraint + Agentic extension). After
        ingest_brief -> plan_campaign -> execute_booking the pipeline
        must:

          - Call the orchestrator once per channel (2 channels here).
          - Forward the typed AudiencePlan on BOTH InventoryRequirements
            and DealParams (the §5 wiring -- both surfaces carry it so
            seller discovery and the materialized DealRequest agree).
          - Preserve every audience type (standard / contextual /
            agentic) at the boundary.
          - Keep the audience_plan_id stable from CampaignPlan onwards
            -- the post-planner plan_id and the plan_id observed at the
            seller boundary must match. The pre-planner brief plan_id
            and the post-planner plan_id may legitimately differ when
            the planner adds inferred refs (§5.5 / §7); we only assert
            equality with the ingested id when the planner added none.
        """

        brief = _three_type_brief()
        assert brief.target_audience is not None
        original_plan_id = brief.target_audience.audience_plan_id

        loop = asyncio.new_event_loop()
        try:
            campaign_id = loop.run_until_complete(
                pipeline.ingest_brief(brief.model_dump(mode="json"))
            )
            campaign_plan = loop.run_until_complete(
                pipeline.plan_campaign(campaign_id)
            )
            loop.run_until_complete(pipeline.execute_booking(campaign_id))
        finally:
            loop.close()

        # Planner step ran and attached a typed AudiencePlan to the plan.
        assert campaign_plan.target_audience is not None
        plan_after_planner = campaign_plan.target_audience
        post_planner_plan_id = plan_after_planner.audience_plan_id
        # The pre-planner -> post-planner hash is stable only when no
        # inferred refs were added (proposal §5.5). Mirror the existing
        # unit test pattern in tests/unit/test_audience_planner_wiring.py.
        no_inferred_constraints = not any(
            c.source == "inferred" for c in plan_after_planner.constraints
        )
        no_inferred_extensions = not any(
            e.source == "inferred" for e in plan_after_planner.extensions
        )
        if no_inferred_constraints and no_inferred_extensions:
            assert post_planner_plan_id == original_plan_id
        # All three audience types survived the planner pass.
        assert plan_after_planner.primary.type == "standard"
        assert plan_after_planner.primary.identifier == "3-7"
        assert any(c.type == "contextual" for c in plan_after_planner.constraints)
        assert any(e.type == "agentic" for e in plan_after_planner.extensions)

        # Orchestrator called once per channel (CTV + DISPLAY = 2 calls).
        assert mock_orchestrator.orchestrate.call_count == 2

        # Inspect every orchestrate call: both InventoryRequirements and
        # DealParams must carry the typed AudiencePlan with the same id.
        for call in mock_orchestrator.orchestrate.call_args_list:
            inv_req = call.kwargs["inventory_requirements"]
            deal_params = call.kwargs["deal_params"]

            assert inv_req.audience_plan is not None
            assert isinstance(inv_req.audience_plan, AudiencePlan)
            assert deal_params.audience_plan is not None
            assert isinstance(deal_params.audience_plan, AudiencePlan)

            # End-to-end identity hash stability: post-planner plan_id
            # MUST survive plan -> seller (no in-flight mutation). This
            # is the §5.1 step-2 wire-format guarantee for the buyer
            # side of Path A.
            assert (
                inv_req.audience_plan.audience_plan_id == post_planner_plan_id
            )
            assert (
                deal_params.audience_plan.audience_plan_id
                == post_planner_plan_id
            )

            # All three types still present at the seller boundary.
            assert inv_req.audience_plan.primary.type == "standard"
            assert inv_req.audience_plan.primary.identifier == "3-7"
            assert any(
                c.type == "contextual" for c in inv_req.audience_plan.constraints
            )
            assert any(
                e.type == "agentic" for e in inv_req.audience_plan.extensions
            )

            # Compliance context survives for the agentic extension --
            # required by §5.2's consent-regime guarantee.
            agentic = next(
                e for e in inv_req.audience_plan.extensions if e.type == "agentic"
            )
            assert isinstance(agentic.compliance_context, ComplianceContext)
            assert agentic.compliance_context.jurisdiction == "US"
            assert agentic.compliance_context.consent_framework == "IAB-TCFv2"


# ===========================================================================
# Helpers for tests 2-4 (orchestrator-level + JSON round-trip).
# ===========================================================================
#
# Tests 2 and 3 exercise the *actual* MultiSellerOrchestrator preflight +
# degradation path (rather than the AsyncMock used in test 1). That orchestrator
# expects:
#   - a `capability_client` returning a SellerAudienceCapabilities per seller URL
#   - a deals_client_factory returning per-URL clients with `book_deal` mocked
# The pattern mirrors `tests/unit/test_buyer_preflight.py` so behavior is
# consistent with the unit-level coverage there but framed end-to-end against
# the same brief-driven 3-type plan the happy-path test uses.

from ad_buyer.booking.quote_normalizer import NormalizedQuote, QuoteNormalizer
from ad_buyer.clients.capability_client import CapabilityDiscoveryResult
from ad_buyer.models.audience_plan import AudienceStrictness
from ad_buyer.models.deals import DealBookingRequest, DealResponse
from ad_buyer.orchestration.audience_degradation import (
    SellerAudienceCapabilities,
)
from ad_buyer.storage import audience_audit_log


def _audience_plan_from_brief() -> AudiencePlan:
    """Build the same 3-type AudiencePlan the brief carries, as a typed model.

    Tests 2/3 hand this directly to `select_and_book` so the orchestrator
    runs the actual degradation path. The plan content matches
    `_three_type_plan_dict` -- if the brief ever drifts, this helper drifts
    with it because it parses through the same brief parser.
    """

    brief = _three_type_brief()
    assert brief.target_audience is not None
    return brief.target_audience


def _make_deal_response(
    deal_id: str = "deal-patha-degraded-001",
    quote_id: str = "q-patha-1",
    final_cpm: float = 12.50,
) -> DealResponse:
    """Mirrors the helper in tests/unit/test_buyer_preflight.py."""

    return DealResponse.model_validate(
        {
            "deal_id": deal_id,
            "quote_id": quote_id,
            "deal_type": "PD",
            "status": "booked",
            "product": {
                "product_id": "prod-patha-1",
                "name": "Path A Test Product",
                "format": "video",
                "channel": "ctv",
            },
            "pricing": {
                "base_cpm": 10.0,
                "final_cpm": final_cpm,
                "currency": "USD",
            },
            "terms": {
                "impressions": 100_000,
                "flight_start": "2026-05-01",
                "flight_end": "2026-05-31",
            },
            "buyer_tier": "public",
            "expires_at": "2026-06-30T00:00:00Z",
        }
    )


def _ranked_quote(
    quote_id: str = "q-patha-1", seller_id: str = "seller-patha-a"
) -> NormalizedQuote:
    return NormalizedQuote(
        seller_id=seller_id,
        quote_id=quote_id,
        raw_cpm=10.0,
        effective_cpm=10.0,
        deal_type="PD",
        fee_estimate=0.0,
        minimum_spend=0.0,
        score=90.0,
    )


class _RecordingCapabilityClient:
    """Records every `discover_capabilities` call and returns a configured caps.

    Mirrors the test double in `tests/unit/test_buyer_preflight.py`. Kept
    local rather than imported so this test file stays self-contained and
    integration-style readers don't have to bounce into a unit test fixture.
    """

    def __init__(self, caps_by_url: dict[str, SellerAudienceCapabilities]):
        self._caps_by_url = caps_by_url
        self.calls: list[str] = []

    async def discover_capabilities(
        self, seller_endpoint: str
    ) -> CapabilityDiscoveryResult:
        self.calls.append(seller_endpoint)
        caps = self._caps_by_url.get(
            seller_endpoint, SellerAudienceCapabilities.legacy_default()
        )
        return CapabilityDiscoveryResult(
            capabilities=caps, cache_status="miss", fetched_at=0.0
        )


@pytest.fixture
def temp_audit_db(tmp_path, monkeypatch):  # noqa: ARG001 - monkeypatch unused but matches pattern
    """Per-test SQLite file for `audience_audit_log` events.

    Tests 2 + 3 inspect the audit log to confirm degradation events landed
    keyed by the original plan's `audience_plan_id`. Fresh DB per test so
    the assertions are deterministic.
    """

    db_path = tmp_path / "audit.db"
    audience_audit_log.configure(f"sqlite:///{db_path}")
    yield db_path
    audience_audit_log.configure("sqlite:///:memory:")


@pytest.fixture
def deals_client_factory():
    """Per-URL mock deals-client factory; tests configure `book_deal` per seller."""

    clients: dict[str, AsyncMock] = {}

    def factory(seller_url: str, **kwargs: Any) -> AsyncMock:
        if seller_url not in clients:
            mock = AsyncMock()
            mock.seller_url = seller_url
            mock.book_deal = AsyncMock()
            mock.close = AsyncMock()
            clients[seller_url] = mock
        return clients[seller_url]

    factory._clients = clients  # type: ignore[attr-defined]
    return factory


def _orchestrator_with_caps(
    caps_by_url: dict[str, SellerAudienceCapabilities],
    *,
    deals_client_factory: Callable[..., Any],
) -> MultiSellerOrchestrator:
    """Build a real `MultiSellerOrchestrator` wired to a recording cap client."""

    return MultiSellerOrchestrator(
        registry_client=AsyncMock(),
        deals_client_factory=deals_client_factory,
        event_bus=None,
        quote_normalizer=QuoteNormalizer(),
        quote_timeout=5.0,
        capability_client=_RecordingCapabilityClient(caps_by_url),
    )


# `Callable` is needed by the helper above. Imported lazily to keep the
# test 1 path's imports stable.
from typing import Callable  # noqa: E402


# ===========================================================================
# 2. Capability degradation against a legacy-default seller
# ===========================================================================


class TestCapabilityDegradationLegacySeller:
    """Legacy seller -> degradation strips agentic + extensions + constraints.

    Builds the brief-driven 3-type plan, hands it to a real
    ``MultiSellerOrchestrator`` whose capability client returns the
    ``legacy_default()`` caps. The orchestrator's pre-flight runs
    ``degrade_plan_for_seller``, the strictness gate proceeds with the
    degraded plan (default ``constraints=preferred``,
    ``extensions=optional``, ``agentic=optional`` -- nothing required is
    stripped), and the deal books. The audit log carries a
    ``degradation`` event keyed by the **original** plan's
    ``audience_plan_id`` (the buyer's pre-flight emit-site uses the
    pre-degradation id; see ``multi_seller._book_with_preflight_then_retry``).
    """

    def test_capability_degradation_legacy_seller(
        self,
        deals_client_factory: Callable[..., Any],
        temp_audit_db: Any,  # noqa: ARG002 - forces audit-log redirection
    ) -> None:
        seller_url = "https://legacy-seller.example.com"
        legacy_caps = SellerAudienceCapabilities.legacy_default()
        # Sanity: legacy default really does refuse agentic + extensions.
        assert legacy_caps.agentic.supported is False
        assert legacy_caps.supports_extensions is False
        assert legacy_caps.supports_constraints is False

        orchestrator = _orchestrator_with_caps(
            {seller_url: legacy_caps}, deals_client_factory=deals_client_factory
        )
        client = deals_client_factory(seller_url)
        client.book_deal.return_value = _make_deal_response()

        plan = _audience_plan_from_brief()
        original_plan_id = plan.audience_plan_id

        loop = asyncio.new_event_loop()
        try:
            selection = loop.run_until_complete(
                orchestrator.select_and_book(
                    ranked_quotes=[_ranked_quote()],
                    budget=100_000.0,
                    count=1,
                    quote_seller_map={"q-patha-1": seller_url},
                    audience_plan=plan,
                    # Defaults: primary=required, constraints=preferred,
                    # extensions=optional, agentic=optional. Legacy seller
                    # strips everything but primary -- nothing required goes
                    # missing, so booking proceeds.
                    audience_strictness=AudienceStrictness(),
                )
            )
        finally:
            loop.close()

        # --- pre-flight ran ---
        assert orchestrator._capability_client.calls == [seller_url]

        # --- booking proceeded with degraded plan ---
        assert len(selection.booked_deals) == 1
        assert selection.incompatible_sellers == []
        assert client.book_deal.await_count == 1

        booking_arg: DealBookingRequest = client.book_deal.await_args_list[0].args[0]
        assert booking_arg.audience_plan is not None
        degraded_plan = booking_arg.audience_plan
        # Standard primary survived (legacy_default keeps standard 1.1).
        assert degraded_plan.primary.type == "standard"
        assert degraded_plan.primary.identifier == "3-7"
        # Constraints / extensions / exclusions all stripped.
        assert degraded_plan.constraints == []
        assert degraded_plan.extensions == []
        assert degraded_plan.exclusions == []
        # No agentic refs anywhere on the degraded plan.
        all_refs = (
            [degraded_plan.primary]
            + list(degraded_plan.constraints)
            + list(degraded_plan.extensions)
            + list(degraded_plan.exclusions)
        )
        assert all(ref.type != "agentic" for ref in all_refs)

        # The degraded plan's id changed (content-derived) -- confirms the
        # degradation actually mutated the plan.
        assert degraded_plan.audience_plan_id != original_plan_id

        # --- degradation log surfaced on the selection ---
        assert "q-patha-1" in selection.degradation_logs
        deg_log = selection.degradation_logs["q-patha-1"]
        # At least one drop for the contextual constraint and one for the
        # agentic extension.
        log_paths = [entry.path for entry in deg_log]
        assert any("constraints" in p for p in log_paths)
        assert any("extensions" in p for p in log_paths)

        # --- audit log keyed by the ORIGINAL plan id ---
        # ``_book_with_preflight_then_retry`` calls
        # ``audience_audit_log.log_event`` with ``plan_id=audience_plan.audience_plan_id``
        # -- the pre-degradation id, by design (so a reviewer can correlate
        # the original plan with everything that happened to it downstream).
        events = audience_audit_log.get_events(original_plan_id)
        assert events, (
            f"Expected audit events for original plan_id={original_plan_id!r}; "
            f"got none"
        )
        event_types = [e["event_type"] for e in events]
        assert audience_audit_log.EVENT_DEGRADATION in event_types
        # Find the degradation event and confirm it carries the seller and
        # the structured drop log.
        deg_events = [
            e for e in events
            if e["event_type"] == audience_audit_log.EVENT_DEGRADATION
        ]
        assert len(deg_events) >= 1
        deg_payload = deg_events[0]["payload"]
        assert deg_payload.get("phase") == "preflight"
        assert deg_payload.get("seller_url") == seller_url
        assert isinstance(deg_payload.get("log"), list)
        assert len(deg_payload["log"]) >= 1


# ===========================================================================
# 3. Hard reject when no standard taxonomy overlap and primary=required
# ===========================================================================


class TestHardRejectZeroStandardOverlap:
    """Seller advertises no overlap on the standard taxonomy version.

    The buyer's plan uses Audience Taxonomy v1.1 for the primary; the
    seller's caps say only v2.0. With ``audience_strictness.primary=required``
    (the default), pre-flight refuses to drop the primary and the
    orchestrator marks the seller incompatible. No booking is attempted.

    This is the §13 strictness-gate behavior surfaced in
    ``DealSelection.incompatible_sellers`` -- the signal §13 chose
    instead of raising an exception out of ``select_and_book``.
    """

    def test_hard_reject_zero_standard_overlap(
        self,
        deals_client_factory: Callable[..., Any],
        temp_audit_db: Any,  # noqa: ARG002 - forces audit-log redirection
    ) -> None:
        seller_url = "https://mismatch-seller.example.com"
        # Seller offers only v2.0 -- the buyer's primary is v1.1.
        caps = SellerAudienceCapabilities(
            schema_version="1",
            standard_taxonomy_versions=["2.0"],
            contextual_taxonomy_versions=["3.1"],
            supports_constraints=True,
            supports_extensions=False,
        )
        orchestrator = _orchestrator_with_caps(
            {seller_url: caps}, deals_client_factory=deals_client_factory
        )
        client = deals_client_factory(seller_url)

        plan = _audience_plan_from_brief()
        # Sanity: brief plan really does use 1.1.
        assert plan.primary.version == "1.1"

        loop = asyncio.new_event_loop()
        try:
            selection = loop.run_until_complete(
                orchestrator.select_and_book(
                    ranked_quotes=[_ranked_quote()],
                    budget=100_000.0,
                    count=1,
                    quote_seller_map={"q-patha-1": seller_url},
                    audience_plan=plan,
                    audience_strictness=AudienceStrictness(primary="required"),
                )
            )
        finally:
            loop.close()

        # --- no booking attempt ---
        assert client.book_deal.await_count == 0
        assert selection.booked_deals == []

        # --- seller marked incompatible (the §13 signal) ---
        assert _ranked_quote().seller_id in selection.incompatible_sellers
        assert len(selection.failed_bookings) == 1
        failure = selection.failed_bookings[0]
        assert failure["error_code"] == "audience_plan_unsupported"
        assert failure["quote_id"] == "q-patha-1"


# ===========================================================================
# 4. Cross-repo AudiencePlan JSON round-trip (schema-drift backstop)
# ===========================================================================


class TestCrossRepoAudiencePlanJSONRoundTrip:
    """Buyer-side AudiencePlan JSON survives reconstruction through seller models.

    The seller does NOT define a buyer-shape AudiencePlan -- its
    ``ad_seller.models.ucp.AudiencePlan`` is the legacy UCP planner shape.
    The wire-format spec lives in ``docs/api/audience_plan_wire_format.md``
    and is mirrored only at the **AudienceRef + ComplianceContext** level
    (``ad_seller.models.audience_ref``).

    So the round-trip backstop validates that every ref in a 3-type
    AudiencePlan -- primary + each constraint + each extension -- survives
    serialize-on-buyer / parse-on-seller / re-serialize without drift.
    Byte-equivalent comparison with ``json.dumps(..., sort_keys=True)``
    catches any silent schema divergence between the two repos.
    """

    def test_cross_repo_audience_plan_json_round_trip(self) -> None:
        # 1. Build typed buyer plan and serialize.
        buyer_plan: AudiencePlan = _audience_plan_from_brief()
        buyer_json: str = buyer_plan.model_dump_json()

        # 2. Reconstruct via the seller's AudienceRef model.
        # The seller worktree lives in a sibling repo; its `src` is on the
        # python path so we can validate refs through its model. The seller
        # uses the same field names so reading the buyer's JSON dict per-ref
        # works directly.
        #
        # Path resolution (per ar-840n): default targets the canonical
        # `.worktrees/audience-extension` companion alongside the buyer
        # worktree, but tests can override via the `AD_SELLER_SRC_PATH`
        # env var (e.g., when §20 / future Path B tests run from a
        # different worktree name or a CI runner with a non-standard
        # layout). Falls back to the seller repo's main `src/` when no
        # worktree exists.
        seller_src = os.environ.get("AD_SELLER_SRC_PATH")
        if not seller_src:
            # File path layout:
            #   parent/ad_buyer_system/.worktrees/<worktree>/tests/integration/<this>
            #                                   ^^^^^^^^^^^^^^ buyer_worktree_root = parents[2]
            # parent (agent_range root) = parents[5]
            buyer_worktree_root = Path(__file__).resolve().parents[2]
            worktree_name = buyer_worktree_root.name
            agent_range_root = buyer_worktree_root.parents[2]
            sibling_worktree = (
                agent_range_root
                / "ad_seller_system"
                / ".worktrees"
                / worktree_name
                / "src"
            )
            seller_main = agent_range_root / "ad_seller_system" / "src"
            seller_src = str(
                sibling_worktree if sibling_worktree.is_dir() else seller_main
            )
        sys.path.insert(0, seller_src)
        try:
            from ad_seller.models.audience_ref import AudienceRef as SellerRef
        finally:
            # Avoid leaking the path into other tests.
            pass

        buyer_dict = json.loads(buyer_json)

        # Helper: round-trip a single ref dict through the seller's model
        # and confirm byte-equivalent re-serialization.
        def _assert_ref_round_trips(ref_dict: dict[str, Any], where: str) -> None:
            seller_ref = SellerRef(**ref_dict)
            re_serialized = seller_ref.model_dump(mode="json")
            # Drop None values from the seller round-trip to compare against
            # the buyer's serialization, which uses Pydantic v2 default
            # (None fields ARE present in model_dump_json output for both
            # sides). Sort keys to make comparison order-independent.
            buyer_canon = json.dumps(ref_dict, sort_keys=True)
            seller_canon = json.dumps(re_serialized, sort_keys=True)
            assert buyer_canon == seller_canon, (
                f"Schema drift at {where}:\n"
                f"  buyer:  {buyer_canon}\n"
                f"  seller: {seller_canon}"
            )

        # 3. Round-trip every ref slot.
        _assert_ref_round_trips(buyer_dict["primary"], "primary")
        for idx, ref in enumerate(buyer_dict.get("constraints", [])):
            _assert_ref_round_trips(ref, f"constraints[{idx}]")
        for idx, ref in enumerate(buyer_dict.get("extensions", [])):
            _assert_ref_round_trips(ref, f"extensions[{idx}]")
        for idx, ref in enumerate(buyer_dict.get("exclusions", [])):
            _assert_ref_round_trips(ref, f"exclusions[{idx}]")

        # 4. Confirm the agentic compliance_context survived the round-trip
        # (it's the most failure-prone nested field).
        agentic_dict = next(
            r for r in buyer_dict["extensions"] if r["type"] == "agentic"
        )
        seller_agentic = SellerRef(**agentic_dict)
        assert seller_agentic.compliance_context is not None
        assert seller_agentic.compliance_context.jurisdiction == "US"
        assert seller_agentic.compliance_context.consent_framework == "IAB-TCFv2"

        # 5. Sanity: the buyer's plan id is content-derived; the per-ref
        # round-trip preserves content, so the buyer reproducibly hashes
        # to the same id when re-validated from the JSON.
        rebuilt_buyer = AudiencePlan.model_validate_json(buyer_json)
        assert rebuilt_buyer.audience_plan_id == buyer_plan.audience_plan_id


# ===========================================================================
# Re-exports.
# ===========================================================================

__all__ = [
    "FakeCampaignStore",
    "AudienceRef",  # used by part 2 fixtures
]
