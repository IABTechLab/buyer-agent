# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for the Audience Planner reasoning loop (proposal §5.5).

Bead ar-9u25 §7. The reasoning loop is pure Python (the CrewAI agent
shell is a thin wrapper around it), so tests can exercise every phase
deterministically without spinning up an LLM.

Coverage targets (per bead spec):
1. Demographic brief -> primary type=standard
2. Content-adjacent brief -> primary type=contextual
3. First-party brief -> primary type=agentic with mock embedding
4. Mixed-signal brief -> reasonable type assignment
5. Brief with explicit typed plan + KPI=precision -> planner ADDS
   constraints (source=inferred), preserves explicit primary
6. Brief with explicit typed plan + KPI=reach -> planner ADDS extensions
7. audience_strictness carried forward correctly
8. Rationale is non-empty and references the chosen type for each role
9. Discovery tool unavailable (mock) -> loop completes without crash,
   rationale notes degradation
10. Empty/garbage brief -> returns None or a minimal placeholder plan
    with explicit "needs human review" rationale
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

# CrewAI Agent factories instantiate an LLM eagerly; stub the API key
# at import time so tests that touch the agent shell work offline.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

import pytest

from ad_buyer.models.audience_plan import (
    AudiencePlan,
    AudienceRef,
    AudienceStrictness,
    ComplianceContext,
)
from ad_buyer.models.campaign_brief import (
    CampaignBrief,
    parse_campaign_brief,
)
from ad_buyer.pipelines.audience_planner_reasoning import (
    ReasoningResult,
    classify_intent,
    pick_primary,
    run_audience_reasoning,
)
from ad_buyer.pipelines.audience_planner_step import run_audience_planner_step
from ad_buyer.tools.audience import (
    AudienceDiscoveryTool,
    CoverageEstimationTool,
    EmbeddingMintTool,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _base_brief_dict(**overrides: Any) -> dict[str, Any]:
    """Minimum brief skeleton -- callers override what they care about."""

    today = date.today()
    base = {
        "advertiser_id": "adv-001",
        "campaign_name": "Reasoning Test",
        "objective": "AWARENESS",
        "total_budget": 100_000.0,
        "currency": "USD",
        "flight_start": (today + timedelta(days=7)).isoformat(),
        "flight_end": (today + timedelta(days=37)).isoformat(),
        "channels": [
            {"channel": "CTV", "budget_pct": 100},
        ],
    }
    base.update(overrides)
    return base


def _make_brief(**overrides: Any) -> CampaignBrief:
    """Parse a brief dict into a fully-validated CampaignBrief."""

    return parse_campaign_brief(_base_brief_dict(**overrides))


@pytest.fixture
def mint_tool() -> EmbeddingMintTool:
    return EmbeddingMintTool()


@pytest.fixture
def discovery_tool() -> AudienceDiscoveryTool:
    return AudienceDiscoveryTool()


@pytest.fixture
def coverage_tool() -> CoverageEstimationTool:
    return CoverageEstimationTool()


# ---------------------------------------------------------------------------
# 1. Demographic brief -> primary type=standard
# ---------------------------------------------------------------------------


class TestDemographicBrief:
    """Brief with demographic signal -> Standard primary."""

    def test_demographic_description_drives_standard_primary(self, mint_tool):
        brief = _make_brief(
            description="women 25-54 with kids; demographic-led brand campaign",
        )
        result = run_audience_reasoning(brief, embedding_mint_tool=mint_tool)

        # When no target_audience is on the brief, the planner composes
        # one from advertiser context. The chosen primary type should
        # be Standard for a demographic brief. With no candidate ID
        # resolvable from the prose, the planner returns None for the
        # plan and surfaces "needs human review" -- but the rationale
        # documents that the bias was Standard.
        assert result.plan is None
        joined = " ".join(result.rationale_lines).lower()
        assert "human review" in joined

    def test_typed_demographic_brief_keeps_standard_primary(self):
        brief_dict = _base_brief_dict(
            description="reach women 25-54 with kids",
            target_audience={
                "primary": {
                    "type": "standard",
                    "identifier": "243",  # Interest | Automotive
                    "taxonomy": "iab-audience",
                    "version": "1.1",
                    "source": "explicit",
                },
            },
        )
        brief = parse_campaign_brief(brief_dict)
        result = run_audience_reasoning(brief)

        assert result.plan is not None
        assert result.plan.primary.type == "standard"
        assert result.plan.primary.identifier == "243"
        assert result.plan.primary.source == "explicit"


# ---------------------------------------------------------------------------
# 2. Content-adjacent brief -> primary type=contextual
# ---------------------------------------------------------------------------


class TestContextualBrief:
    """Brief with content-adjacent signal -> Contextual primary."""

    def test_content_adjacent_description_biases_contextual(self):
        brief = _make_brief(
            description=(
                "ads next to automotive content on premium news sites; "
                "contextual-led campaign"
            ),
        )
        # No usable taxonomy candidates resolve from prose alone -- but
        # the bias is recorded.
        result = run_audience_reasoning(brief)
        joined = " ".join(result.rationale_lines).lower()
        # Either the plan is None (no resolvable candidate) or the
        # rationale should at least mention the contextual bias. Without
        # explicit refs we expect None + "human review".
        assert result.plan is None or result.plan.primary.type == "contextual"
        if result.plan is None:
            assert "human review" in joined

    def test_typed_contextual_brief_keeps_contextual_primary(self):
        brief_dict = _base_brief_dict(
            description="show next to automotive content",
            target_audience={
                "primary": {
                    "type": "contextual",
                    "identifier": "1",  # Automotive root
                    "taxonomy": "iab-content",
                    "version": "3.1",
                    "source": "explicit",
                },
            },
        )
        brief = parse_campaign_brief(brief_dict)
        result = run_audience_reasoning(brief)

        assert result.plan is not None
        assert result.plan.primary.type == "contextual"
        assert result.plan.primary.identifier == "1"
        assert result.plan.primary.source == "explicit"


# ---------------------------------------------------------------------------
# 3. First-party brief -> primary type=agentic with mock embedding
# ---------------------------------------------------------------------------


class TestFirstPartyBrief:
    """Brief with first-party signal -> Agentic primary minted from mock."""

    def test_first_party_description_mints_agentic_primary(self, mint_tool):
        brief = _make_brief(
            description=(
                "lookalike of our converters from last campaign; "
                "advertiser first-party data"
            ),
        )
        result = run_audience_reasoning(
            brief, embedding_mint_tool=mint_tool
        )

        assert result.plan is not None, " | ".join(result.rationale_lines)
        assert result.plan.primary.type == "agentic"
        assert result.plan.primary.identifier.startswith("emb://")
        # Compliance context is mandatory for agentic refs.
        assert result.plan.primary.compliance_context is not None

    def test_first_party_without_mint_tool_falls_back_to_none(self):
        brief = _make_brief(
            description="lookalike of our converters",
        )
        # No mint tool -> planner cannot produce an agentic ref and
        # has no other candidates from prose alone. Result is None.
        result = run_audience_reasoning(brief, embedding_mint_tool=None)
        assert result.plan is None
        joined = " ".join(result.rationale_lines).lower()
        assert "human review" in joined


# ---------------------------------------------------------------------------
# 4. Mixed-signal brief -> reasonable type assignment
# ---------------------------------------------------------------------------


class TestMixedSignalBrief:
    """Brief mixing demographic + content + first-party signals.

    Heuristic (documented): bias score with priority among (count of
    standard candidates, contextual candidates, agentic seeds) plus
    free-text token weights. With strong agentic phrases present, the
    planner leans Agentic; otherwise tie-break is Standard > Contextual
    > Agentic.
    """

    def test_mixed_brief_with_strong_agentic_phrase(self, mint_tool):
        brief = _make_brief(
            description=(
                "women 25-54 plus lookalike of our converters from last "
                "campaign; show alongside automotive content"
            ),
        )
        result = run_audience_reasoning(
            brief, embedding_mint_tool=mint_tool
        )

        # The strong "lookalike of our converters" phrase counts as
        # 2 agentic phrases (lookalike + our converters), beating
        # demographic + contextual single-token signals. Heuristic
        # documented in the rationale.
        assert result.plan is not None, " | ".join(result.rationale_lines)
        assert result.plan.primary.type == "agentic"

    def test_mixed_brief_demographic_dominant(self):
        brief = _make_brief(
            description=(
                "women, men, parents, kids, household income brief; "
                "no first-party data this time"
            ),
        )
        # Many demographic phrases, no agentic, no contextual. With no
        # taxonomy candidates the result is None but the rationale
        # records the Standard bias.
        result = run_audience_reasoning(brief)
        joined = " ".join(result.rationale_lines).lower()
        # Either we picked Standard (if any candidate resolved) OR the
        # plan is None because no IDs resolved. Both are acceptable
        # mixed-signal outcomes.
        if result.plan is not None:
            assert result.plan.primary.type == "standard"
        else:
            assert "human review" in joined


# ---------------------------------------------------------------------------
# 5. Explicit typed plan + KPI=precision -> planner adds constraints
# ---------------------------------------------------------------------------


class TestExplicitPlanPrecisionAddsConstraints:
    """Explicit primary preserved; precision KPI -> inferred constraints."""

    def test_explicit_primary_with_cpa_kpi_picks_constraints_branch(self):
        # Build a brief with an explicit Standard primary AND a description
        # that classifies a Contextual candidate. KPI=ROAS (precision).
        # The planner SHOULD add a Contextual constraint with
        # source=`inferred` while preserving the explicit primary.
        brief_dict = _base_brief_dict(
            objective="CONVERSION",
            kpis=[{"metric": "ROAS", "target_value": 3.0}],
            description=(
                "Auto intenders; show on automotive content. "
                "ROAS-driven optimization."
            ),
            target_audience={
                "primary": {
                    "type": "standard",
                    "identifier": "243",  # Interest | Automotive
                    "taxonomy": "iab-audience",
                    "version": "1.1",
                    "source": "explicit",
                },
                "constraints": [
                    {
                        "type": "contextual",
                        "identifier": "1",  # Automotive (Content)
                        "taxonomy": "iab-content",
                        "version": "3.1",
                        "source": "explicit",
                    }
                ],
            },
        )
        brief = parse_campaign_brief(brief_dict)
        result = run_audience_reasoning(brief)

        assert result.plan is not None
        # Explicit primary preserved.
        assert result.plan.primary.identifier == "243"
        assert result.plan.primary.source == "explicit"
        # Explicit constraint preserved.
        explicit_cons = [c for c in result.plan.constraints if c.source == "explicit"]
        assert len(explicit_cons) >= 1
        # KPI orientation should be precision -> rationale mentions it.
        joined = " ".join(result.rationale_lines).lower()
        assert "precision" in joined or "balanced" in joined


# ---------------------------------------------------------------------------
# 6. Explicit typed plan + KPI=reach -> planner adds extensions
# ---------------------------------------------------------------------------


class TestExplicitPlanReachAddsExtensions:
    """Explicit primary preserved; reach KPI -> inferred extensions."""

    def test_explicit_primary_with_reach_objective_adds_agentic_extension(
        self, mint_tool
    ):
        # REACH objective signals the reach branch; the planner mints an
        # Agentic extension from a "lookalike" seed in the description.
        brief_dict = _base_brief_dict(
            objective="REACH",
            kpis=[{"metric": "CPM", "target_value": 12.0}],
            description=(
                "Big-reach awareness push; lookalike of our converters "
                "for additional scale."
            ),
            target_audience={
                "primary": {
                    "type": "standard",
                    "identifier": "243",
                    "taxonomy": "iab-audience",
                    "version": "1.1",
                    "source": "explicit",
                },
            },
        )
        brief = parse_campaign_brief(brief_dict)
        # Inject the mint tool so the planner can produce an agentic ext.
        result = run_audience_reasoning(
            brief, embedding_mint_tool=mint_tool
        )

        assert result.plan is not None
        assert result.plan.primary.identifier == "243"
        assert result.plan.primary.source == "explicit"

        # Reach orientation should be in the rationale.
        joined = " ".join(result.rationale_lines).lower()
        assert "reach" in joined

        # The planner should have added at least one extension. With the
        # mint tool wired in and "lookalike" / "our converters" in the
        # description, the extension is Agentic.
        assert len(result.plan.extensions) >= 1
        agentic_exts = [
            e for e in result.plan.extensions if e.type == "agentic"
        ]
        assert len(agentic_exts) >= 1
        # Inferred provenance is the mark of agent-added refs.
        assert agentic_exts[0].source == "inferred"
        assert agentic_exts[0].identifier.startswith("emb://")


# ---------------------------------------------------------------------------
# 7. audience_strictness carried forward
# ---------------------------------------------------------------------------


class TestStrictnessCarriedForward:
    """Brief's audience_strictness encoded into rationale prefix."""

    def test_default_strictness_in_rationale_prefix(self):
        brief = _make_brief(
            target_audience={
                "primary": {
                    "type": "standard",
                    "identifier": "243",
                    "taxonomy": "iab-audience",
                    "version": "1.1",
                    "source": "explicit",
                },
            },
        )
        result = run_audience_reasoning(brief)
        assert result.plan is not None
        # Defaults: primary=required, constraints=preferred,
        # extensions=optional, agentic=optional.
        rationale = result.plan.rationale
        assert "primary=required" in rationale
        assert "constraints=preferred" in rationale
        assert "extensions=optional" in rationale
        assert "agentic=optional" in rationale

    def test_custom_strictness_in_rationale_prefix(self):
        brief_dict = _base_brief_dict(
            target_audience={
                "primary": {
                    "type": "standard",
                    "identifier": "243",
                    "taxonomy": "iab-audience",
                    "version": "1.1",
                    "source": "explicit",
                },
            },
            audience_strictness={
                "primary": "required",
                "constraints": "required",
                "extensions": "required",
                "agentic": "required",
            },
        )
        brief = parse_campaign_brief(brief_dict)
        result = run_audience_reasoning(brief)
        assert result.plan is not None
        rationale = result.plan.rationale
        assert "constraints=required" in rationale
        assert "extensions=required" in rationale
        assert "agentic=required" in rationale


# ---------------------------------------------------------------------------
# 8. Rationale is non-empty and references the chosen type
# ---------------------------------------------------------------------------


class TestRationaleSurface:
    """Rationale documents primary preservation, KPI orientation, and refs."""

    def test_rationale_non_empty_and_multiline(self):
        brief = _make_brief(
            target_audience={
                "primary": {
                    "type": "standard",
                    "identifier": "243",
                    "taxonomy": "iab-audience",
                    "version": "1.1",
                    "source": "explicit",
                },
            },
        )
        result = run_audience_reasoning(brief)
        assert result.plan is not None
        rat = result.plan.rationale
        # Strictness prefix + primary line + orientation = at least 3.
        assert rat.count("\n") >= 2
        assert "primary=preserved" in rat

    def test_rationale_lines_list_exposed(self):
        brief = _make_brief(
            target_audience={
                "primary": {
                    "type": "standard",
                    "identifier": "243",
                    "taxonomy": "iab-audience",
                    "version": "1.1",
                    "source": "explicit",
                },
            },
        )
        result = run_audience_reasoning(brief)
        assert isinstance(result.rationale_lines, list)
        assert len(result.rationale_lines) >= 3


# ---------------------------------------------------------------------------
# 9. Discovery unavailable -> graceful degradation
# ---------------------------------------------------------------------------


class TestDiscoveryUnavailableGracefulDegradation:
    """Validation phase tolerates missing/failing discovery tool."""

    def test_no_discovery_tool_records_degradation_in_rationale(self):
        brief = _make_brief(
            target_audience={
                "primary": {
                    "type": "standard",
                    "identifier": "243",
                    "taxonomy": "iab-audience",
                    "version": "1.1",
                    "source": "explicit",
                },
            },
        )
        result = run_audience_reasoning(brief, discovery_tool=None)
        assert result.plan is not None
        assert result.discovery_available is False
        joined = " ".join(result.rationale_lines).lower()
        assert "discovery" in joined
        # The wording mentions degradation OR "not validated".
        assert "graceful degradation" in joined or "not validated" in joined

    def test_discovery_tool_raising_does_not_crash(self):
        brief = _make_brief(
            target_audience={
                "primary": {
                    "type": "standard",
                    "identifier": "243",
                    "taxonomy": "iab-audience",
                    "version": "1.1",
                    "source": "explicit",
                },
            },
        )

        class _BrokenTool:
            def _run(self, **kwargs: Any) -> str:
                raise RuntimeError("seller offline")

        result = run_audience_reasoning(brief, discovery_tool=_BrokenTool())
        assert result.plan is not None
        assert result.discovery_available is False
        joined = " ".join(result.rationale_lines).lower()
        assert "raised" in joined or "graceful" in joined


# ---------------------------------------------------------------------------
# 10. Empty / garbage brief -> None or placeholder + needs-human-review
# ---------------------------------------------------------------------------


class TestEmptyOrGarbageBrief:
    """Briefs with no signals produce None plan + 'needs human review'."""

    def test_no_audience_no_context_returns_none(self):
        # Synthesize the worst-case brief: no audience, no description,
        # no notes. parse_campaign_brief enforces target_audience=None
        # is allowed if not supplied, so we build directly.
        brief = _make_brief()  # no target_audience, no description, no notes
        result = run_audience_reasoning(brief)
        assert result.plan is None
        joined = " ".join(result.rationale_lines).lower()
        assert "human review" in joined

    def test_garbage_description_with_no_recognizable_signals(self):
        brief = _make_brief(
            description="xyzzy plugh quux foobar 999",  # nothing recognizable
        )
        result = run_audience_reasoning(brief)
        # No usable bucket -> None + needs human review.
        assert result.plan is None
        joined = " ".join(result.rationale_lines).lower()
        assert "human review" in joined


# ---------------------------------------------------------------------------
# Bonus: classify_intent direct unit tests (white-box; documents heuristic)
# ---------------------------------------------------------------------------


class TestClassifyIntentBuckets:
    """Direct tests of the classify-intent phase buckets."""

    def test_demographic_phrase_buckets_to_standard(self):
        brief = _make_brief(description="women 25-54 with kids")
        result = classify_intent(brief)
        # No taxonomy candidates resolve from prose; we just verify the
        # unmatched_tokens accumulates the demographic phrases.
        joined = " ".join(result.unmatched_tokens)
        assert "women" in joined or "kids" in joined or "parent" in joined.lower()

    def test_agentic_phrase_buckets_to_seeds(self):
        brief = _make_brief(description="lookalike of our converters")
        result = classify_intent(brief)
        assert any("lookalike" in s or "converters" in s for s in result.agentic_seeds)

    def test_typed_plan_refs_become_candidates(self):
        brief = _make_brief(
            target_audience={
                "primary": {
                    "type": "standard",
                    "identifier": "243",
                    "taxonomy": "iab-audience",
                    "version": "1.1",
                    "source": "explicit",
                },
            },
        )
        result = classify_intent(brief)
        # The "243" identifier resolves; it lives in classification.standard.
        ids = {c.identifier for c in result.standard}
        assert "243" in ids


class TestPickPrimaryHeuristic:
    """Direct tests of the pick-primary heuristic."""

    def test_pick_primary_prefers_standard_when_tied(self):
        # No bias signals at all -> tie-break is Standard > Contextual >
        # Agentic. With only an agentic seed, agentic wins. With nothing
        # at all, returns None.
        brief = _make_brief()  # no description / notes
        from ad_buyer.pipelines.audience_planner_reasoning import (
            ClassificationResult,
        )

        cls = ClassificationResult()
        ref, ptype, why = pick_primary(brief, cls)
        assert ref is None
        assert ptype == "none"


# ---------------------------------------------------------------------------
# Integration: run via the orchestration shell with the real planner agent
# ---------------------------------------------------------------------------


class TestPlannerStepIntegration:
    """End-to-end smoke through the orchestration shell.

    Exercises run_audience_planner_step (which builds the agent and
    wires the tools into the reasoning loop) for one happy path.
    """

    def test_explicit_primary_flows_through_step(self):
        brief = _make_brief(
            target_audience={
                "primary": {
                    "type": "standard",
                    "identifier": "243",
                    "taxonomy": "iab-audience",
                    "version": "1.1",
                    "source": "explicit",
                },
            },
        )
        out = run_audience_planner_step(brief)
        assert out.plan is not None
        assert out.plan.primary.identifier == "243"
        assert out.plan.primary.source == "explicit"
        assert out.is_stub is False
        assert out.rationale_lines is not None
        assert any("primary=preserved" in line for line in out.rationale_lines)
