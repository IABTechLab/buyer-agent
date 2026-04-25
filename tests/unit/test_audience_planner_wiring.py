# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the Audience Planner wiring into CampaignPipeline.

Bead ar-fgyq §6 -- the keystone wiring bead. Verifies:

1. CampaignPipeline.plan_campaign() runs the (stub) Audience Planner step
   and populates CampaignPlan.target_audience.
2. Stub passthrough preserves the brief's user-supplied AudiencePlan
   exactly (rationale + content hash unchanged).
3. The Audience Planner agent owns 5 tools: 3 UCP + TaxonomyLookup +
   EmbeddingMint.
4. The Research Agent in channel_crews.py NO LONGER owns the 3 audience
   tools (relocated upstream).
5. EmbeddingMintTool returns an AudienceRef with type=agentic, an emb://
   identifier, and a populated compliance_context.

Reference: AUDIENCE_PLANNER_3TYPE_EXTENSION_2026-04-25.md §5.3, §5.5, §5.6.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

# Mirror the pattern in test_linear_tv_agent.py: stub the Anthropic key
# at module-load time so the CrewAI Agent factories (which instantiate an
# LLM eagerly in __init__) work in unit tests that never make a network
# call. This must run BEFORE any ad_buyer imports that touch crewai.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

import pytest

from ad_buyer.crews.channel_crews import (
    create_branding_crew,
    create_ctv_crew,
    create_mobile_crew,
    create_performance_crew,
)
from ad_buyer.events.bus import InMemoryEventBus
from ad_buyer.models.audience_plan import AudiencePlan, AudienceRef
from ad_buyer.models.state_machine import CampaignStatus
from ad_buyer.orchestration.multi_seller import (
    DealSelection,
    MultiSellerOrchestrator,
    OrchestrationResult,
)
from ad_buyer.pipelines.audience_planner_step import (
    build_audience_planner_agent,
    run_audience_planner_step,
)
from ad_buyer.pipelines.campaign_pipeline import CampaignPipeline
from ad_buyer.tools.audience import (
    AudienceDiscoveryTool,
    AudienceMatchingTool,
    CoverageEstimationTool,
    EmbeddingMintTool,
    TaxonomyLookupTool,
)


# ---------------------------------------------------------------------------
# Fixtures (mirror test_campaign_pipeline.py at minimum)
# ---------------------------------------------------------------------------


def _legacy_brief_dict(**overrides: Any) -> dict[str, Any]:
    """Brief that uses the legacy `list[str]` audience -- migrated on ingest."""

    today = date.today()
    brief = {
        "advertiser_id": "adv-001",
        "campaign_name": "Wiring Test (legacy audience)",
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


def _typed_brief_dict(**overrides: Any) -> dict[str, Any]:
    """Brief that already carries a typed AudiencePlan."""

    plan = {
        "primary": {
            "type": "standard",
            "identifier": "3-7",
            "taxonomy": "iab-audience",
            "version": "1.1",
            "source": "explicit",
        },
        "rationale": "User-supplied: focus on auto intenders aged 25-54.",
    }
    return _legacy_brief_dict(target_audience=plan, **overrides)


class _FakeStore:
    """Trimmed-down fake of CampaignStore."""

    def __init__(self) -> None:
        self._campaigns: dict[str, dict[str, Any]] = {}

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


def _make_orchestration_result(num_deals: int = 1) -> OrchestrationResult:
    deals = [MagicMock(deal_id=f"deal-{i}") for i in range(num_deals)]
    return OrchestrationResult(
        discovered_sellers=[],
        quote_results=[],
        ranked_quotes=[],
        selection=DealSelection(
            booked_deals=deals,
            failed_bookings=[],
            total_spend=10_000.0,
            remaining_budget=0.0,
        ),
    )


@pytest.fixture
def fake_store() -> _FakeStore:
    return _FakeStore()


@pytest.fixture
def event_bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
def mock_orchestrator() -> AsyncMock:
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
# 1. Pipeline produces target_audience on CampaignPlan
# ---------------------------------------------------------------------------


class TestPipelineProducesAudiencePlan:
    """plan_campaign must populate `CampaignPlan.target_audience`."""

    def test_legacy_brief_yields_migrated_plan(self, pipeline, fake_store):
        """Legacy list[str] brief -> migrated AudiencePlan threaded onto plan."""

        loop = asyncio.new_event_loop()
        try:
            campaign_id = loop.run_until_complete(
                pipeline.ingest_brief(json.dumps(_legacy_brief_dict()))
            )
            plan = loop.run_until_complete(pipeline.plan_campaign(campaign_id))
        finally:
            loop.close()

        assert plan.target_audience is not None, (
            "After ar-fgyq §6, plan_campaign MUST populate target_audience "
            "via the Audience Planner step (stub passthrough today)."
        )
        assert isinstance(plan.target_audience, AudiencePlan)
        # Legacy migration policy: first item -> primary, type=standard.
        assert plan.target_audience.primary.identifier == "auto_intenders_25_54"
        assert plan.target_audience.primary.type == "standard"

    def test_typed_brief_yields_same_typed_plan(self, pipeline, fake_store):
        """Typed AudiencePlan from brief flows through unchanged."""

        loop = asyncio.new_event_loop()
        try:
            campaign_id = loop.run_until_complete(
                pipeline.ingest_brief(json.dumps(_typed_brief_dict()))
            )
            plan = loop.run_until_complete(pipeline.plan_campaign(campaign_id))
        finally:
            loop.close()

        assert plan.target_audience is not None
        assert plan.target_audience.primary.identifier == "3-7"
        assert plan.target_audience.primary.type == "standard"


# ---------------------------------------------------------------------------
# 2. Reasoning loop preserves explicit primary, may enrich rationale
# ---------------------------------------------------------------------------


class TestPlannerPreservesExplicitPrimary:
    """The §7 reasoning loop preserves an explicit primary verbatim.

    Documented behavior (post-§7):
      - The brief's explicit primary survives intact (identifier,
        type, source=`explicit`).
      - The planner produces its own rationale that records the
        preservation, plus the strictness policy and KPI orientation.
        The user's original rationale is no longer the plan's
        rationale; the audit-trail surface (§13a) handles that.
      - audience_plan_id (the content hash) remains stable across the
        planner step when no refs are added (e.g. when classification
        finds no candidates to extend the explicit primary with).
      - The planner result is no longer a stub (is_stub=False).
    """

    def test_explicit_primary_preserved(self, pipeline):
        loop = asyncio.new_event_loop()
        try:
            campaign_id = loop.run_until_complete(
                pipeline.ingest_brief(json.dumps(_typed_brief_dict()))
            )
            plan = loop.run_until_complete(pipeline.plan_campaign(campaign_id))
        finally:
            loop.close()

        assert plan.target_audience is not None
        assert plan.target_audience.primary.identifier == "3-7"
        assert plan.target_audience.primary.type == "standard"
        assert plan.target_audience.primary.source == "explicit"

    def test_planner_rationale_records_preservation(self, pipeline):
        loop = asyncio.new_event_loop()
        try:
            campaign_id = loop.run_until_complete(
                pipeline.ingest_brief(json.dumps(_typed_brief_dict()))
            )
            plan = loop.run_until_complete(pipeline.plan_campaign(campaign_id))
        finally:
            loop.close()

        assert plan.target_audience is not None
        rationale = plan.target_audience.rationale
        # The §7 rationale is multi-line and records strictness,
        # primary preservation, and KPI orientation. We assert the
        # SHAPE of the rationale rather than the exact string to keep
        # the test robust against future wording tweaks.
        assert "primary=preserved" in rationale, rationale
        assert "explicit standard 3-7" in rationale, rationale
        assert "[strictness" in rationale, rationale

    def test_audience_plan_id_stable_when_no_refs_added(self, pipeline):
        """Hash stable across the planner when nothing was added.

        With no advertiser context on the brief and no resolvable
        classification candidates, the planner enriches with zero
        constraints/extensions; the content hash matches the ingested
        plan's hash. (rationale isn't in the hash.)
        """

        loop = asyncio.new_event_loop()
        try:
            campaign_id = loop.run_until_complete(
                pipeline.ingest_brief(json.dumps(_typed_brief_dict()))
            )
            ingested_plan = pipeline._briefs[campaign_id].target_audience
            assert ingested_plan is not None
            ingested_id = ingested_plan.audience_plan_id

            plan = loop.run_until_complete(pipeline.plan_campaign(campaign_id))
        finally:
            loop.close()

        assert plan.target_audience is not None
        # If no constraints/extensions were inferred, the hash matches.
        if not plan.target_audience.constraints and not plan.target_audience.extensions:
            assert plan.target_audience.audience_plan_id == ingested_id

    def test_planner_result_no_longer_stub(self, pipeline):
        loop = asyncio.new_event_loop()
        try:
            campaign_id = loop.run_until_complete(
                pipeline.ingest_brief(json.dumps(_typed_brief_dict()))
            )
            loop.run_until_complete(pipeline.plan_campaign(campaign_id))
        finally:
            loop.close()

        result = pipeline.get_audience_planner_result(campaign_id)
        assert result is not None
        assert result.is_stub is False
        # The §7 result also exposes the rationale lines and discovery
        # availability for downstream audit-trail consumers.
        assert result.rationale_lines is not None
        assert len(result.rationale_lines) >= 2
        # discovery_available is True or False depending on whether the
        # mock seller was reachable; both are acceptable -- the rationale
        # records the outcome.
        assert isinstance(result.discovery_available, bool)


# ---------------------------------------------------------------------------
# 3. Audience Planner agent owns the right tools
# ---------------------------------------------------------------------------


class TestPlannerToolBindings:
    """Audience Planner has 3 UCP audience tools + TaxonomyLookup + EmbeddingMint."""

    def test_planner_has_five_tools(self):
        agent = build_audience_planner_agent()
        assert len(agent.tools) == 5

    def test_planner_owns_three_ucp_tools(self):
        agent = build_audience_planner_agent()
        tool_types = {type(t) for t in agent.tools}
        assert AudienceDiscoveryTool in tool_types
        assert AudienceMatchingTool in tool_types
        assert CoverageEstimationTool in tool_types

    def test_planner_owns_taxonomy_lookup_and_embedding_mint(self):
        agent = build_audience_planner_agent()
        tool_types = {type(t) for t in agent.tools}
        assert TaxonomyLookupTool in tool_types
        assert EmbeddingMintTool in tool_types

    def test_pipeline_caches_planner_with_same_five_tools(self, pipeline):
        """The pipeline-instantiated planner has the same tool kit."""

        loop = asyncio.new_event_loop()
        try:
            campaign_id = loop.run_until_complete(
                pipeline.ingest_brief(json.dumps(_typed_brief_dict()))
            )
            loop.run_until_complete(pipeline.plan_campaign(campaign_id))
        finally:
            loop.close()

        result = pipeline.get_audience_planner_result(campaign_id)
        assert result is not None
        tool_types = {type(t) for t in result.agent.tools}
        assert tool_types == {
            AudienceDiscoveryTool,
            AudienceMatchingTool,
            CoverageEstimationTool,
            TaxonomyLookupTool,
            EmbeddingMintTool,
        }


# ---------------------------------------------------------------------------
# 4. Research Agent no longer owns the 3 audience tools
# ---------------------------------------------------------------------------


class TestResearchAgentRelocation:
    """The 3 UCP audience tools moved off the Research Agent.

    Channel crews previously bundled `research_tools + audience_tools` into
    `create_research_agent(...)`. After ar-fgyq §6 / proposal §5.3, the
    Research Agent operates on inventory only. This test introspects the
    Research Agent inside each channel crew and asserts the audience tools
    are gone.
    """

    @pytest.fixture
    def opendirect_client(self):
        # MagicMock is fine -- crews don't dispatch network calls at
        # construction time.
        return MagicMock()

    @pytest.fixture
    def channel_brief(self):
        return {
            "budget": 50_000,
            "start_date": "2026-05-01",
            "end_date": "2026-05-31",
            "target_audience": {"age": "25-54"},
            "objectives": ["AWARENESS"],
        }

    def _research_agent_tools(self, crew):
        """Find the Research Agent inside a crew and return its tool types."""

        # The Research Agent is one of the crew's `agents`; the manager
        # agent is the L2 channel specialist. Match on role to be robust
        # against ordering changes.
        from ad_buyer.agents.level3.research_agent import (  # noqa: WPS433 - localized import
            create_research_agent,
        )

        ref_agent = create_research_agent(verbose=False)
        research_role = ref_agent.role
        for agent in crew.agents:
            if agent.role == research_role:
                return {type(t) for t in agent.tools}
        raise AssertionError(
            f"Could not find Research Agent (role={research_role!r}) in crew"
        )

    def test_branding_crew_research_agent_has_no_audience_tools(
        self, opendirect_client, channel_brief
    ):
        crew = create_branding_crew(opendirect_client, channel_brief)
        types = self._research_agent_tools(crew)
        assert AudienceDiscoveryTool not in types
        assert AudienceMatchingTool not in types
        assert CoverageEstimationTool not in types

    def test_mobile_crew_research_agent_has_no_audience_tools(
        self, opendirect_client, channel_brief
    ):
        crew = create_mobile_crew(opendirect_client, channel_brief)
        types = self._research_agent_tools(crew)
        assert AudienceDiscoveryTool not in types
        assert AudienceMatchingTool not in types
        assert CoverageEstimationTool not in types

    def test_ctv_crew_research_agent_has_no_audience_tools(
        self, opendirect_client, channel_brief
    ):
        crew = create_ctv_crew(opendirect_client, channel_brief)
        types = self._research_agent_tools(crew)
        assert AudienceDiscoveryTool not in types
        assert AudienceMatchingTool not in types
        assert CoverageEstimationTool not in types

    def test_performance_crew_research_agent_has_no_audience_tools(
        self, opendirect_client, channel_brief
    ):
        crew = create_performance_crew(opendirect_client, channel_brief)
        types = self._research_agent_tools(crew)
        assert AudienceDiscoveryTool not in types
        assert AudienceMatchingTool not in types
        assert CoverageEstimationTool not in types


# ---------------------------------------------------------------------------
# 5. EmbeddingMintTool produces a well-formed agentic AudienceRef
# ---------------------------------------------------------------------------


class TestEmbeddingMintTool:
    """EmbeddingMintTool returns an agentic AudienceRef with emb:// identifier."""

    def test_mint_returns_agentic_ref(self):
        tool = EmbeddingMintTool()
        ref = tool.mint(name="last-campaign-converters")
        assert isinstance(ref, AudienceRef)
        assert ref.type == "agentic"

    def test_mint_identifier_starts_with_emb_prefix(self):
        tool = EmbeddingMintTool()
        ref = tool.mint(name="high-ltv-lookalike", description="top decile")
        assert ref.identifier.startswith("emb://"), (
            f"Expected emb:// prefix, got {ref.identifier!r}"
        )

    def test_mint_compliance_context_populated(self):
        tool = EmbeddingMintTool()
        ref = tool.mint(
            name="eu-converters",
            jurisdiction="EU",
            consent_framework="IAB-TCFv2",
        )
        assert ref.compliance_context is not None
        assert ref.compliance_context.jurisdiction == "EU"
        assert ref.compliance_context.consent_framework == "IAB-TCFv2"

    def test_mock_label_exposed_on_tool(self):
        tool = EmbeddingMintTool()
        assert "MOCK" in tool.embedding_mode_label
        assert "§22" in tool.embedding_mode_label

    def test_mint_is_deterministic_for_same_inputs(self):
        """Same name+description -> same emb:// identifier."""

        tool = EmbeddingMintTool()
        a = tool.mint(name="x", description="y")
        b = tool.mint(name="x", description="y")
        assert a.identifier == b.identifier

    def test_mint_changes_for_different_inputs(self):
        tool = EmbeddingMintTool()
        a = tool.mint(name="x", description="y")
        b = tool.mint(name="x", description="z")
        assert a.identifier != b.identifier


# ---------------------------------------------------------------------------
# 6. None-audience brief -- planner stub returns None gracefully
# ---------------------------------------------------------------------------


class TestPlannerHandlesNoAudience:
    """Brief with no audience and no advertiser context -> None (no crash).

    Post-§7 the reasoning loop has the latitude to compose a plan from
    advertiser context (description/notes) when target_audience is None.
    With NEITHER audience nor context, the loop emits None and records
    "needs human review" in the rationale lines.
    """

    def test_run_step_returns_none_when_brief_lacks_signals(self):
        # Synthesize a brief whose audience and context are all empty.
        # parse_campaign_brief would reject missing audience at ingestion,
        # so we fabricate the minimal shape the reasoning loop expects.
        brief = MagicMock()
        brief.target_audience = None
        brief.description = None
        brief.notes = None
        # Strictness must be a real AudienceStrictness object so the
        # rationale prefix can read its fields.
        from ad_buyer.models.audience_plan import AudienceStrictness

        brief.audience_strictness = AudienceStrictness()

        result = run_audience_planner_step(brief)
        assert result.plan is None
        assert result.is_stub is False
        assert result.rationale_lines is not None
        joined = " ".join(result.rationale_lines)
        assert "human review" in joined.lower()
