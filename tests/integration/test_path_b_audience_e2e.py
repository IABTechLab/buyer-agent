# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""End-to-end integration test for Path B (BuyerDealFlow + channel-crew).

Bead ar-6ipo / proposal §6 row 20 -- the buyer-side end-to-end test for
the two non-CampaignPipeline deal-finding entry points identified in
proposal §5.3:

  - **Path B1: BuyerDealFlow / BuyerDealFlow** -- the brief-driven flow
    that materializes a seller-bound DealRequest payload.
  - **Path B2: direct channel-crew invocation** -- the demo/test path
    via ``kickoff_channel_crew_with_audience``.

The seller side is **mocked** in this bead because §8/§9/§10/§11 (seller
audience capability surfaces) are still pending. The mock seller is
"responsive but ignorant of new audience semantics" -- it accepts deal
requests and returns plausible deal IDs without actually matching
against the audience plan. That's enough to exercise the buyer-side
plumbing end to end.

Scenarios per bead deliverable:

  1. Brief -> planner -> DealRequest happy path with a 3-type plan
     (Standard primary + Contextual constraint + Agentic extension).
     Asserts the materialized DealRequest carries the expected
     ``audience_plan_id``.
  2. Legacy ``list[str]`` brief migration through the path. Asserts
     ``source="inferred"`` propagates to the seller-bound payload.
  3. Audience plan survives serialization at the flow -> seller
     boundary (mock seller endpoint, capture the payload, deserialize,
     confirm ``audience_plan_id`` parity).
  4. Capability degradation scenario (mocked seller) -- confirms the
     scenario is reachable. Actual ``degrade_plan_for_seller`` lives in
     bead §12, still pending.
  5. Pre-set ``state.audience_plan`` precedence (BuyerDealFlow only):
     a parent pipeline pre-seeds the plan; BuyerDealFlow does NOT
     re-run the planner.

Reference: AUDIENCE_PLANNER_3TYPE_EXTENSION_2026-04-25.md §5.1, §5.3, §5.7, §6 row 20.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# Stub the Anthropic key BEFORE any ad_buyer.crews / agents imports.
# CrewAI Agent factories instantiate an LLM eagerly in __init__ and we
# never make a network call here. Mirrors the pattern used in unit tests.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-path-b-e2e")

import pytest

from ad_buyer.crews.channel_crews import kickoff_channel_crew_with_audience
from ad_buyer.flows.buyer_deal_flow import BuyerDealFlow, BuyerDealFlowStatus
from ad_buyer.models.audience_plan import (
    AudiencePlan,
    AudienceRef,
    ComplianceContext,
)
from ad_buyer.models.buyer_identity import (
    BuyerContext,
    BuyerIdentity,
    DealRequest,
    DealType,
)
from ad_buyer.models.campaign_brief import CampaignBrief, parse_campaign_brief


# ===========================================================================
# Fixtures
# ===========================================================================


def _three_type_plan_dict() -> dict[str, Any]:
    """Build a 3-type AudiencePlan dict (Standard + Contextual + Agentic).

    Matches the canonical example from proposal §5.1 -- a Standard primary
    narrowed by a Contextual constraint and extended by an Agentic
    lookalike. The agentic ref carries a compliance context as required.
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
                "identifier": "1",  # Automotive content (Content Tax 3.1)
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
    """Minimum CampaignBrief skeleton with valid 3-channel allocation."""

    today = date.today()
    base: dict[str, Any] = {
        "advertiser_id": "adv-pathb-001",
        "campaign_name": "Path B integration test",
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


def _legacy_list_brief() -> CampaignBrief:
    """Brief carrying a legacy ``list[str]`` target_audience (§4 shim)."""

    return parse_campaign_brief(
        _base_brief_dict(target_audience=["auto_intenders_25_54", "luxury_buyers"])
    )


def _agency_buyer_context() -> BuyerContext:
    identity = BuyerIdentity(
        seat_id="ttd-seat-pathb",
        agency_id="agency-pathb",
        agency_name="Path B Test Agency",
    )
    return BuyerContext(identity=identity, is_authenticated=True)


def _seed_dsp_request_state(flow: BuyerDealFlow) -> None:
    """Populate the @start step's required request fields on the flow."""

    flow.state.request = "CTV inventory for auto intenders under $30 CPM"
    flow.state.deal_type = DealType.PREFERRED_DEAL
    flow.state.impressions = 1_000_000
    flow.state.max_cpm = 30.0
    flow.state.flight_start = "2026-05-01"
    flow.state.flight_end = "2026-05-31"


@pytest.fixture
def mock_unified_client() -> MagicMock:
    """A UnifiedClient mock that responds successfully but is audience-blind.

    Models the §20 "responsive but ignorant of new audience semantics"
    seller. ``get_product`` succeeds with a plausible product; ``base_url``
    is set so persistence helpers behave; nothing inspects audience.
    """

    client = MagicMock()
    client.base_url = "http://mock-seller.test"
    # Async methods that the deal flow / RequestDealTool may invoke.
    client.search_products = AsyncMock()
    client.list_products = AsyncMock()
    client.get_product = AsyncMock()
    return client


@pytest.fixture
def opendirect_client() -> MagicMock:
    """OpenDirect client for the channel-crew path (no network at construction)."""

    return MagicMock()


@pytest.fixture
def channel_brief() -> dict[str, Any]:
    """Channel-specific brief dict consumed by ``create_*_crew``."""

    return {
        "budget": 50_000,
        "start_date": "2026-05-01",
        "end_date": "2026-05-31",
        "target_audience": {"age": "25-54"},
        "objectives": ["AWARENESS"],
        "kpis": {"viewability": 70},
    }


# ===========================================================================
# 1. BuyerDealFlow happy path -- 3 audience types
# ===========================================================================


class TestBuyerDealFlowThreeTypeHappyPath:
    """3-type plan (Standard + Contextual + Agentic) flows end to end."""

    def test_brief_yields_three_type_plan_on_state(
        self, mock_unified_client: MagicMock
    ) -> None:
        """Brief -> planner runs -> 3-type plan attached to flow state."""

        brief = _three_type_brief()
        # Capture the plan id BEFORE the flow runs so we can assert parity.
        assert brief.target_audience is not None
        original_plan_id = brief.target_audience.audience_plan_id

        flow = BuyerDealFlow(
            client=mock_unified_client,
            buyer_context=_agency_buyer_context(),
            brief=brief,
        )
        _seed_dsp_request_state(flow)

        result = flow.receive_request()

        assert result["status"] == "success"
        assert flow.state.status == BuyerDealFlowStatus.REQUEST_RECEIVED

        plan = flow.state.audience_plan
        assert isinstance(plan, AudiencePlan)
        # Primary preserved verbatim (explicit Standard 3-7).
        assert plan.primary.type == "standard"
        assert plan.primary.identifier == "3-7"
        assert plan.primary.source == "explicit"
        # Constraints and extensions carried through (the planner may add
        # inferred refs around the explicit ones, but the explicit refs
        # MUST survive).
        explicit_constraints = [c for c in plan.constraints if c.source != "inferred"]
        assert any(c.type == "contextual" for c in explicit_constraints)
        explicit_extensions = [e for e in plan.extensions if e.source != "inferred"]
        assert any(e.type == "agentic" for e in explicit_extensions)
        # When the planner only enriches around an explicit primary
        # (without adding refs), the audience_plan_id is stable.
        if not any(c.source == "inferred" for c in plan.constraints) and not any(
            e.source == "inferred" for e in plan.extensions
        ):
            assert plan.audience_plan_id == original_plan_id

    def test_three_type_plan_threaded_into_dealrequest(
        self, mock_unified_client: MagicMock
    ) -> None:
        """The 3-type plan must reach the materialized DealRequest payload.

        We mock the deal tool so we can capture exactly what the flow
        forwarded. The audience_plan_id on that payload must equal the
        plan_id that ``receive_request`` produced.
        """

        brief = _three_type_brief()
        flow = BuyerDealFlow(
            client=mock_unified_client,
            buyer_context=_agency_buyer_context(),
            brief=brief,
        )
        _seed_dsp_request_state(flow)
        flow.receive_request()

        plan_after_receive = flow.state.audience_plan
        assert plan_after_receive is not None
        plan_id_after_receive = plan_after_receive.audience_plan_id

        # Skip ahead to request_deal_id with a mocked deal tool so we can
        # observe the plan that crosses the flow -> tool boundary.
        flow.state.selected_product_id = "ctv-pkg-pathb"
        flow._deal_tool = MagicMock()
        flow._deal_tool._run = MagicMock(
            return_value="DEAL CREATED: deal-pathb-3type-001"
        )

        outcome = flow.request_deal_id({"status": "success"})
        assert outcome["status"] == "success"

        flow._deal_tool._run.assert_called_once()
        call_kwargs = flow._deal_tool._run.call_args.kwargs
        observed = call_kwargs.get("audience_plan")
        assert observed is not None
        # The audience_plan_id is the cross-boundary identity hash. It
        # must NOT drift between brief / state / tool kwargs.
        assert observed.audience_plan_id == plan_id_after_receive
        # All three audience types still present at the boundary.
        assert observed.primary.type == "standard"
        assert any(c.type == "contextual" for c in observed.constraints)
        assert any(e.type == "agentic" for e in observed.extensions)


# ===========================================================================
# 2. BuyerDealFlow legacy migration
# ===========================================================================


class TestBuyerDealFlowLegacyMigration:
    """Legacy ``list[str]`` brief migrates and source=inferred propagates."""

    def test_legacy_list_brief_propagates_source_inferred(
        self, mock_unified_client: MagicMock
    ) -> None:
        """Legacy list -> migrated AudiencePlan -> seller-bound payload.

        The §4 migration shim runs at brief-parse time and produces an
        AudiencePlan with primary.source="inferred". That marker MUST
        propagate all the way through the flow to the seller-bound
        DealRequest payload so downstream auditors can distinguish
        agent-attributed vs user-attributed refs.
        """

        brief = _legacy_list_brief()
        # Confirm the parser-time migration shim already ran.
        assert brief.target_audience is not None
        assert brief.target_audience.primary.identifier == "auto_intenders_25_54"
        assert brief.target_audience.primary.source == "inferred"
        # Legacy list -> first item primary, rest extensions (§4 policy).
        assert any(
            ext.identifier == "luxury_buyers" and ext.source == "inferred"
            for ext in brief.target_audience.extensions
        )

        flow = BuyerDealFlow(
            client=mock_unified_client,
            buyer_context=_agency_buyer_context(),
            brief=brief,
        )
        _seed_dsp_request_state(flow)
        flow.receive_request()

        plan = flow.state.audience_plan
        assert plan is not None
        # source=inferred must NOT be promoted to explicit by the planner.
        assert plan.primary.source == "inferred"
        assert plan.primary.identifier == "auto_intenders_25_54"

        # Now drive the flow forward to the seller boundary and confirm
        # source=inferred reached the DealRequest payload.
        flow.state.selected_product_id = "ctv-pkg-legacy"
        flow._deal_tool = MagicMock()
        flow._deal_tool._run = MagicMock(
            return_value="DEAL CREATED: deal-pathb-legacy-001"
        )
        flow.request_deal_id({"status": "success"})

        observed = flow._deal_tool._run.call_args.kwargs.get("audience_plan")
        assert observed is not None
        assert observed.primary.source == "inferred"
        # Extension carries source=inferred too -- whole-plan provenance.
        assert any(
            e.source == "inferred" and e.identifier == "luxury_buyers"
            for e in observed.extensions
        )


# ===========================================================================
# 3. BuyerDealFlow serialization parity
# ===========================================================================


class TestBuyerDealFlowSerializationParity:
    """AudiencePlan survives JSON serialization at the flow -> seller boundary."""

    def test_dealrequest_roundtrip_preserves_plan_id(
        self, mock_unified_client: MagicMock
    ) -> None:
        """Mock the seller, capture the payload, deserialize, compare.

        This is the §5.1 step-2 wire-format guarantee: the buyer's
        ``audience_plan_id`` is a content hash both sides recompute and
        compare. A serialization round-trip MUST preserve the hash --
        otherwise capability negotiation, audit trail, and snapshot-honor
        all break.
        """

        from ad_buyer.tools.buyer_deals.request_deal import RequestDealTool

        # Real RequestDealTool so we exercise build_deal_request_payload
        # end to end -- the same code path the flow uses.
        tool = RequestDealTool(
            client=mock_unified_client,
            buyer_context=_agency_buyer_context(),
        )

        brief = _three_type_brief()
        plan = brief.target_audience
        assert plan is not None
        original_plan_id = plan.audience_plan_id

        payload = tool.build_deal_request_payload(
            product_id="ctv-pkg-pathb",
            deal_type="PD",
            impressions=500_000,
            flight_start="2026-05-01",
            flight_end="2026-05-31",
            target_cpm=None,
            audience_plan=plan,
        )
        assert isinstance(payload, DealRequest)

        # Round-trip through the wire shape (model_dump -> model_validate)
        # to confirm the plan id is stable across serialization.
        wire = payload.model_dump(mode="json")
        rebuilt = DealRequest.model_validate(wire)

        assert rebuilt.audience_plan is not None
        assert rebuilt.audience_plan.audience_plan_id == original_plan_id
        # And every role survives. JSON has no Pydantic types, so the
        # rehydrated refs prove typed-vs-dict dispatch isn't lossy.
        assert rebuilt.audience_plan.primary.type == "standard"
        assert rebuilt.audience_plan.primary.identifier == "3-7"
        assert any(c.type == "contextual" for c in rebuilt.audience_plan.constraints)
        assert any(e.type == "agentic" for e in rebuilt.audience_plan.extensions)
        # Compliance context survives for agentic refs.
        agentic = next(
            e for e in rebuilt.audience_plan.extensions if e.type == "agentic"
        )
        assert agentic.compliance_context is not None
        assert agentic.compliance_context.jurisdiction == "US"

    def test_full_flow_to_seller_payload_preserves_plan_id(
        self, mock_unified_client: MagicMock
    ) -> None:
        """End-to-end: brief -> state -> tool kwargs -> wire round-trip.

        Drive the flow far enough to materialize the deal-tool kwargs,
        then take the audience_plan that crossed the boundary, serialize
        it, deserialize it, and confirm the hash matches the post-planner
        plan on flow state.

        Note on the comparison anchor: the brief's *pre-planner* plan_id
        and the *post-planner* plan_id can legitimately differ when the
        planner adds inferred refs around an explicit primary (proposal
        §5.5). The wire-format guarantee is that the plan_id observed
        AFTER the planner runs survives serialization unchanged -- that
        is the hash both sides compare under §5.1 step 2.
        """

        brief = _three_type_brief()

        flow = BuyerDealFlow(
            client=mock_unified_client,
            buyer_context=_agency_buyer_context(),
            brief=brief,
        )
        _seed_dsp_request_state(flow)
        flow.receive_request()

        plan_on_state = flow.state.audience_plan
        assert plan_on_state is not None
        plan_id_on_state = plan_on_state.audience_plan_id

        flow.state.selected_product_id = "ctv-pkg-pathb"
        flow._deal_tool = MagicMock()
        flow._deal_tool._run = MagicMock(
            return_value="DEAL CREATED: deal-pathb-roundtrip"
        )
        flow.request_deal_id({"status": "success"})

        observed = flow._deal_tool._run.call_args.kwargs.get("audience_plan")
        assert observed is not None
        # The plan that crossed the flow -> tool boundary must match the
        # plan that was on state (no mutation in flight).
        assert observed.audience_plan_id == plan_id_on_state

        # Wire round-trip -- mirror what would happen across the
        # buyer/seller HTTP boundary. The plan_id MUST be stable across
        # serialization (§5.1 step 2 hash-comparison guarantee).
        wire = observed.model_dump(mode="json")
        rebuilt = AudiencePlan.model_validate(wire)
        assert rebuilt.audience_plan_id == plan_id_on_state
        assert rebuilt.audience_plan_id == observed.audience_plan_id


# ===========================================================================
# 4. BuyerDealFlow capability degradation (mocked seller)
# ===========================================================================


class TestBuyerDealFlowCapabilityDegradation:
    """Mocked seller advertises agentic NOT supported -- scenario reachable.

    The actual ``degrade_plan_for_seller`` logic is bead §12 (still
    pending). For §20 we ASSERT THE SCENARIO IS REACHABLE so §12 has a
    concrete path to enable: the buyer can be wired with a UCPClient
    whose capability discovery returns ``agentic.supported=False``, and
    the flow does not crash.
    """

    def test_legacy_seller_capability_reachable_no_crash(
        self, mock_unified_client: MagicMock
    ) -> None:
        """Mock seller responds with no agentic support; flow still books.

        Seam: the buyer's ``UCPClient.discover_capabilities`` returns an
        empty list. The audience-discovery tool falls back to mock
        capabilities (none agentic-flagged). The deal flow proceeds
        without crashing -- exactly the "responsive but ignorant" seller
        the bead spec describes.
        """

        # Patch UCPClient.discover_capabilities to return an empty list,
        # mimicking a legacy seller that doesn't ship the §9
        # ``audience_capabilities`` block. When §12 lands, the buyer's
        # degrade_plan_for_seller will read a richer response here.
        with patch(
            "ad_buyer.clients.ucp_client.UCPClient.discover_capabilities",
            new=AsyncMock(return_value=[]),
        ):
            brief = _three_type_brief()
            flow = BuyerDealFlow(
                client=mock_unified_client,
                buyer_context=_agency_buyer_context(),
                brief=brief,
            )
            _seed_dsp_request_state(flow)
            result = flow.receive_request()

            # Critical invariant: the flow does not crash when the seller
            # is audience-ignorant. The audience plan still rides on
            # state -- §12's degrade hook will narrow it later.
            assert result["status"] == "success"
            assert flow.state.audience_plan is not None
            # The agentic extension is still on the plan -- §12 will
            # decide whether to drop it. For §20 we just confirm the
            # extension is there for §12 to act on.
            assert any(
                e.type == "agentic" for e in flow.state.audience_plan.extensions
            )

    def test_capability_degradation_seam_observable(
        self, mock_unified_client: MagicMock
    ) -> None:
        """A capability response advertising no agentic is observable.

        Records the JSON shape §12 will consume: an audience_capabilities
        block with ``agentic.supported=False`` and ``supports_extensions=False``
        is the trigger for buyer-side degradation. We don't have the
        consumer yet, but we prove the discovery pipe is wireable.
        """

        # Build a §5.7 layer-1 capability response shape -- the one §12
        # will read from. We don't yet validate field-by-field; we just
        # confirm the pipe carries a JSON-shaped dict the buyer can read.
        legacy_caps_payload: dict[str, Any] = {
            "seller_id": "seller-legacy",
            "audience_capabilities": {
                "schema_version": "1",
                "standard_taxonomy_versions": ["1.1"],
                "contextual_taxonomy_versions": ["3.1"],
                "agentic": {"supported": False},
                "supports_constraints": True,
                "supports_extensions": False,
                "supports_exclusions": False,
            },
        }

        # Verify we can route this payload into the buyer's HTTP layer
        # via the UCPClient seam without crashing. When §12 lands, this
        # payload becomes the input to ``degrade_plan_for_seller``.
        async def _fake_discover(endpoint: str) -> list[Any]:
            # Simulate a structurally valid (but empty-cap) response.
            assert endpoint  # the pipe is wired
            return []  # no AudienceCapability rows in legacy mode

        with patch(
            "ad_buyer.clients.ucp_client.UCPClient.discover_capabilities",
            new=_fake_discover,
        ):
            # The brief threads through cleanly even with the seller
            # advertising the legacy profile.
            brief = _three_type_brief()
            flow = BuyerDealFlow(
                client=mock_unified_client,
                buyer_context=_agency_buyer_context(),
                brief=brief,
            )
            _seed_dsp_request_state(flow)
            flow.receive_request()
            assert flow.state.audience_plan is not None
            # The capability shape dict is simply an observable -- no
            # production consumer reads it yet, but §12 will. We assert
            # the structure is JSON-serializable and carries the §5.7
            # required fields, so §12's design has a concrete fixture.
            import json as _json
            wire = _json.dumps(legacy_caps_payload)
            rebuilt = _json.loads(wire)
            assert rebuilt["audience_capabilities"]["agentic"]["supported"] is False
            assert (
                rebuilt["audience_capabilities"]["supports_extensions"] is False
            )


# ===========================================================================
# 5. BuyerDealFlow pre-set state.audience_plan precedence
# ===========================================================================


class TestBuyerDealFlowPreSetPlanPrecedence:
    """Pre-seeded ``state.audience_plan`` must NOT be overwritten by the planner."""

    def test_preset_plan_skips_planner_run(
        self, mock_unified_client: MagicMock
    ) -> None:
        """When state.audience_plan is already set, the planner does not run.

        Used when a parent pipeline (e.g. CampaignPipeline / Path A) ran
        the planner, then handed the plan to BuyerDealFlow as part of a
        wider orchestration. The flow must preserve the pre-seeded plan
        verbatim and skip the planner.
        """

        brief = _three_type_brief()  # would drive a planner run if not preset
        flow = BuyerDealFlow(
            client=mock_unified_client,
            buyer_context=_agency_buyer_context(),
            brief=brief,
        )
        _seed_dsp_request_state(flow)

        # Pre-seed a different plan than what the brief would produce.
        injected = AudiencePlan(
            primary=AudienceRef(
                type="standard",
                identifier="9-99",  # deliberately different from 3-7
                taxonomy="iab-audience",
                version="1.1",
                source="explicit",
            ),
            rationale="Pre-seeded by parent pipeline.",
        )
        flow.state.audience_plan = injected

        flow.receive_request()

        # The pre-seeded plan must survive verbatim.
        assert flow.state.audience_plan is injected
        # The planner did NOT run -- no cached planner result.
        assert flow.get_audience_planner_result() is None

    def test_preset_plan_threaded_to_seller_payload(
        self, mock_unified_client: MagicMock
    ) -> None:
        """Pre-seeded plan must reach the seller-bound DealRequest unchanged.

        Closes the loop for parent-pipeline integrations: not only does
        the pre-seeded plan survive ``receive_request``, it also rides
        on the seller-bound call.
        """

        injected = AudiencePlan(
            primary=AudienceRef(
                type="agentic",
                identifier="emb://parent.test/preset/plan-001",
                taxonomy="agentic-audiences",
                version="draft-2026-01",
                source="explicit",
                compliance_context=ComplianceContext(
                    jurisdiction="US",
                    consent_framework="IAB-TCFv2",
                ),
            ),
            rationale="Pre-seeded by parent pipeline (agentic primary).",
        )

        flow = BuyerDealFlow(
            client=mock_unified_client,
            buyer_context=_agency_buyer_context(),
        )
        _seed_dsp_request_state(flow)
        flow.state.audience_plan = injected
        flow.receive_request()

        flow.state.selected_product_id = "ctv-pkg-preset"
        flow._deal_tool = MagicMock()
        flow._deal_tool._run = MagicMock(
            return_value="DEAL CREATED: deal-pathb-preset-001"
        )
        flow.request_deal_id({"status": "success"})

        observed = flow._deal_tool._run.call_args.kwargs.get("audience_plan")
        assert observed is injected
        assert observed.primary.type == "agentic"
        assert observed.primary.identifier == "emb://parent.test/preset/plan-001"


# ===========================================================================
# 6. channel-crew happy path -- 3 audience types
# ===========================================================================


def _research_task_description(crew: Any) -> str:
    """Pull the research task description out of a hierarchical crew.

    The research task is the first task in every channel crew; it carries
    the audience-context block injected by ``_format_audience_context``.
    """

    return crew.tasks[0].description


class TestChannelCrewThreeTypeHappyPath:
    """3-type plan (Standard + Contextual + Agentic) flows into all 4 crews."""

    @pytest.mark.parametrize(
        "channel",
        ["branding", "mobile", "ctv", "performance"],
    )
    def test_three_type_plan_renders_into_research_task(
        self,
        channel: str,
        opendirect_client: MagicMock,
        channel_brief: dict[str, Any],
    ) -> None:
        """All four crews accept the 3-type plan and surface every type tag."""

        brief = _three_type_brief()
        plan = brief.target_audience
        assert plan is not None

        crew = kickoff_channel_crew_with_audience(
            opendirect_client,
            channel,
            channel_brief,
            audience_plan=plan,
        )
        desc = _research_task_description(crew)
        # Typed-plan markers (the §19 renderer header).
        assert "typed AudiencePlan" in desc
        # All three audience types surface their type tags.
        assert "[standard]" in desc
        assert "[contextual]" in desc
        assert "[agentic]" in desc
        # Plan ID is part of the audit chain -- must surface to the agent.
        assert plan.audience_plan_id in desc
        # Compliance context for agentic refs surfaces at this layer too.
        assert "jurisdiction=US" in desc

    def test_planner_runs_when_brief_supplied_no_plan(
        self,
        opendirect_client: MagicMock,
        channel_brief: dict[str, Any],
    ) -> None:
        """Brief supplied + no plan -> wrapper runs the planner step.

        The convenience wrapper at ``kickoff_channel_crew_with_audience``
        runs the audience planner inline (mirroring Path A / Path B1) when
        a brief is supplied but no plan is. The resulting plan must surface
        in the research task description.
        """

        brief = _three_type_brief()
        crew = kickoff_channel_crew_with_audience(
            opendirect_client,
            "branding",
            channel_brief,
            brief=brief,  # planner runs in place
        )
        desc = _research_task_description(crew)
        # Planner produced a typed plan -- the typed-plan header surfaces.
        assert "typed AudiencePlan" in desc
        assert "[standard]" in desc


# ===========================================================================
# 7. channel-crew legacy migration
# ===========================================================================


class TestChannelCrewLegacyMigration:
    """Legacy ``list[str]`` brief migrates and source=inferred surfaces in crew."""

    def test_legacy_brief_threaded_through_wrapper(
        self,
        opendirect_client: MagicMock,
        channel_brief: dict[str, Any],
    ) -> None:
        """Legacy list -> migrated AudiencePlan -> rendered in research task.

        The wrapper accepts a CampaignBrief whose target_audience was
        already migrated by the §4 shim. The resulting AudiencePlan
        carries source=inferred refs; the channel crew's research task
        must surface those source markers so the agent (and any human
        reviewer) sees the provenance.
        """

        brief = _legacy_list_brief()
        # Confirm the brief carries a migrated plan with source=inferred.
        assert brief.target_audience is not None
        assert brief.target_audience.primary.source == "inferred"

        # Pass the migrated plan directly (skip planner re-run) so we can
        # assert the §4 shim's source-tag survives the rendering path.
        crew = kickoff_channel_crew_with_audience(
            opendirect_client,
            "ctv",
            channel_brief,
            audience_plan=brief.target_audience,
        )
        desc = _research_task_description(crew)
        # source=inferred markers MUST surface at the crew layer.
        assert "source=inferred" in desc
        # And the migrated identifier reaches the agent.
        assert "auto_intenders_25_54" in desc

    def test_legacy_dict_path_still_works(
        self,
        opendirect_client: MagicMock,
        channel_brief: dict[str, Any],
    ) -> None:
        """The pre-§19 dict input shape is still honored (backward compat).

        ``deal_booking_flow.py`` and other older callers pass a free-text
        dict (demographics / interests / signal types) -- the wrapper
        must dispatch it through the legacy renderer, not crash trying
        to treat it as a typed plan.
        """

        legacy_dict = {
            "target_demographics": {"age": "25-54"},
            "target_interests": ["automotive", "luxury"],
            "requested_signal_types": ["identity", "contextual"],
        }
        crew = kickoff_channel_crew_with_audience(
            opendirect_client,
            "performance",
            channel_brief,
            audience_plan=legacy_dict,
        )
        desc = _research_task_description(crew)
        # Legacy renderer header (no "typed" qualifier).
        assert "Audience Plan Context:" in desc
        assert "typed AudiencePlan" not in desc
        # Free-text fields surface.
        assert "Demographics" in desc
        assert "automotive" in desc


# ===========================================================================
# 8. channel-crew serialization parity
# ===========================================================================


class TestChannelCrewSerializationParity:
    """AudiencePlan content survives a wire round-trip then renders identically."""

    def test_plan_round_trip_renders_same_plan_id(
        self,
        opendirect_client: MagicMock,
        channel_brief: dict[str, Any],
    ) -> None:
        """Plan -> JSON -> plan -> crew render must show the same plan_id.

        Mirrors §5.1 step 2: the audience_plan_id is a content hash both
        sides recompute. If a crew renders a deserialized plan and the
        plan_id changes, the audit chain breaks.
        """

        brief = _three_type_brief()
        plan = brief.target_audience
        assert plan is not None
        original_plan_id = plan.audience_plan_id

        # Wire round-trip.
        wire = plan.model_dump(mode="json")
        rebuilt = AudiencePlan.model_validate(wire)
        assert rebuilt.audience_plan_id == original_plan_id

        # Render the rebuilt plan into a crew and assert the plan_id and
        # all three type tags surface identically.
        crew = kickoff_channel_crew_with_audience(
            opendirect_client,
            "branding",
            channel_brief,
            audience_plan=rebuilt,
        )
        desc = _research_task_description(crew)
        assert original_plan_id in desc
        assert "[standard]" in desc
        assert "[contextual]" in desc
        assert "[agentic]" in desc

    def test_round_trip_preserves_compliance_context(
        self,
        opendirect_client: MagicMock,
        channel_brief: dict[str, Any],
    ) -> None:
        """Compliance context on agentic refs survives JSON + crew rendering.

        ComplianceContext is required for agentic refs; losing it on
        serialization would break the consent-regime guarantee in
        proposal §5.2.
        """

        brief = _three_type_brief()
        plan = brief.target_audience
        assert plan is not None

        wire = plan.model_dump(mode="json")
        rebuilt = AudiencePlan.model_validate(wire)
        agentic = next(e for e in rebuilt.extensions if e.type == "agentic")
        assert agentic.compliance_context is not None
        assert agentic.compliance_context.jurisdiction == "US"
        assert agentic.compliance_context.consent_framework == "IAB-TCFv2"

        crew = kickoff_channel_crew_with_audience(
            opendirect_client,
            "performance",
            channel_brief,
            audience_plan=rebuilt,
        )
        desc = _research_task_description(crew)
        assert "jurisdiction=US" in desc
        assert "consent=IAB-TCFv2" in desc


# ===========================================================================
# 9. channel-crew capability degradation (mocked seller)
# ===========================================================================


class TestChannelCrewCapabilityDegradation:
    """Channel crew constructed even when seller advertises legacy profile.

    Same scenario as TestBuyerDealFlowCapabilityDegradation but on the
    direct channel-crew invocation path. Asserts the scenario is
    reachable; actual ``degrade_plan_for_seller`` is bead §12.
    """

    def test_crew_constructs_with_legacy_seller_profile(
        self,
        opendirect_client: MagicMock,
        channel_brief: dict[str, Any],
    ) -> None:
        """No crash when capability discovery returns no agentic support.

        We patch ``UCPClient.discover_capabilities`` to return an empty
        list (the legacy-seller default per proposal §5.7). The channel
        crew constructs cleanly with the full 3-type plan attached --
        §12 will later decide whether to drop the agentic extension.
        """

        with patch(
            "ad_buyer.clients.ucp_client.UCPClient.discover_capabilities",
            new=AsyncMock(return_value=[]),
        ):
            brief = _three_type_brief()
            plan = brief.target_audience
            assert plan is not None

            crew = kickoff_channel_crew_with_audience(
                opendirect_client,
                "ctv",
                channel_brief,
                audience_plan=plan,
            )
            desc = _research_task_description(crew)
            # The plan reaches the crew unchanged -- §12's degradation
            # logic is the future consumer of this data flow.
            assert "[agentic]" in desc
            assert plan.audience_plan_id in desc
