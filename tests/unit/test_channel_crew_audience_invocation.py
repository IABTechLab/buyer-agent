# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for typed `AudiencePlan` flowing through the channel-crew path.

Bead ar-5y8v / proposal §5.3 / §6 row 19 -- the third deal-finding entry
point: direct channel-crew invocation. Used by tests, demos, and any
caller that bypasses CampaignPipeline (Path A) and BuyerDealFlow (Path B).

Verifies:

1. `create_*_crew(audience_plan=<typed AudiencePlan>)` accepts the typed
   model and renders the new four-role markdown (primary + constraints +
   extensions + exclusions + rationale) into the research task.
2. Backward-compat: `create_*_crew(audience_plan=<legacy dict>)` still
   accepts the pre-§19 dict shape (used by `deal_booking_flow.py`).
3. The `_format_audience_context` helper dispatches correctly on input
   type and renders the correct shape with type tags.
4. The `kickoff_channel_crew_with_audience` convenience wrapper builds
   the right crew for each channel and threads the plan through.
5. All 4 channel crews (branding/mobile/ctv/performance) accept the
   typed plan uniformly.

Reference: AUDIENCE_PLANNER_3TYPE_EXTENSION_2026-04-25.md §5.2, §5.3, §6.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock

# Stub the Anthropic key at module-load time -- CrewAI Agent factories
# instantiate an LLM eagerly in __init__ and we never make a network
# call in unit tests.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

import pytest

from ad_buyer.crews.channel_crews import (
    _format_audience_context,
    _format_audience_ref,
    _format_legacy_audience_dict,
    _format_typed_audience_plan,
    create_branding_crew,
    create_ctv_crew,
    create_mobile_crew,
    create_performance_crew,
    kickoff_channel_crew_with_audience,
)
from ad_buyer.models.audience_plan import (
    AudiencePlan,
    AudienceRef,
    ComplianceContext,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def opendirect_client() -> MagicMock:
    """Crews don't dispatch network calls at construction time."""

    return MagicMock()


@pytest.fixture
def channel_brief() -> dict[str, Any]:
    return {
        "budget": 50_000,
        "start_date": "2026-05-01",
        "end_date": "2026-05-31",
        "target_audience": {"age": "25-54"},
        "objectives": ["AWARENESS"],
        "kpis": {"viewability": 70},
    }


@pytest.fixture
def typed_plan() -> AudiencePlan:
    """A fully-populated typed plan exercising all four roles + agentic.

    Standard primary + Contextual constraint + Agentic extension +
    Standard exclusion. The agentic ref carries a compliance context.
    """

    return AudiencePlan(
        primary=AudienceRef(
            type="standard",
            identifier="3-7",
            taxonomy="iab-audience",
            version="1.1",
            source="explicit",
        ),
        constraints=[
            AudienceRef(
                type="contextual",
                identifier="IAB1-2",
                taxonomy="iab-content",
                version="3.1",
                source="resolved",
                confidence=0.92,
            ),
        ],
        extensions=[
            AudienceRef(
                type="agentic",
                identifier="emb://buyer.example.com/audiences/auto-converters-q1",
                taxonomy="agentic-audiences",
                version="draft-2026-01",
                source="explicit",
                compliance_context=ComplianceContext(
                    jurisdiction="US",
                    consent_framework="IAB-TCFv2",
                    consent_string_ref="tcf:CPxxxx...",
                ),
            ),
        ],
        exclusions=[
            AudienceRef(
                type="standard",
                identifier="3-12",
                taxonomy="iab-audience",
                version="1.1",
                source="explicit",
            ),
        ],
        rationale=(
            "Auto Intenders (Standard primary), narrowed to Automotive "
            "content (Contextual constraint), extended by Q1 converter "
            "lookalikes (Agentic extension); existing customers excluded."
        ),
    )


@pytest.fixture
def legacy_dict_plan() -> dict[str, Any]:
    """Pre-§19 dict shape used by `deal_booking_flow.py`."""

    return {
        "plan_id": "plan_legacy01",
        "target_demographics": {"age": "25-54", "gender": "all"},
        "target_interests": ["automotive", "luxury"],
        "target_behaviors": ["online shoppers"],
        "requested_signal_types": ["identity", "contextual"],
        "exclusions": ["competitor audiences"],
    }


# ---------------------------------------------------------------------------
# 1. _format_audience_context dispatches on input type
# ---------------------------------------------------------------------------


class TestFormatAudienceContextDispatch:
    """The single entry point routes typed vs. legacy vs. None correctly."""

    def test_none_returns_empty(self) -> None:
        assert _format_audience_context(None) == ""

    def test_empty_dict_returns_empty(self) -> None:
        # Pre-existing behavior the wider test suite relies on.
        assert _format_audience_context({}) == ""

    def test_typed_plan_uses_typed_renderer(self, typed_plan: AudiencePlan) -> None:
        result = _format_audience_context(typed_plan)
        assert "typed AudiencePlan" in result
        assert "Plan ID:" in result
        assert "Primary:" in result

    def test_legacy_dict_uses_legacy_renderer(
        self, legacy_dict_plan: dict[str, Any]
    ) -> None:
        result = _format_audience_context(legacy_dict_plan)
        # Legacy renderer header (no "typed" qualifier).
        assert "Audience Plan Context:" in result
        assert "typed AudiencePlan" not in result
        assert "Demographics" in result
        assert "Interests" in result

    def test_unrecognized_type_returns_empty(self) -> None:
        # Defensive: weird shapes don't crash crew construction.
        assert _format_audience_context("not a plan") == ""  # type: ignore[arg-type]
        assert _format_audience_context(42) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2. Typed AudiencePlan renders correct markdown with type tags
# ---------------------------------------------------------------------------


class TestTypedAudiencePlanRendering:
    """The typed renderer surfaces all four roles + rationale + type tags."""

    def test_renders_all_four_roles(self, typed_plan: AudiencePlan) -> None:
        result = _format_typed_audience_plan(typed_plan)
        assert "Primary:" in result
        assert "Constraints" in result
        assert "Extensions" in result
        assert "Exclusions" in result

    def test_includes_rationale(self, typed_plan: AudiencePlan) -> None:
        result = _format_typed_audience_plan(typed_plan)
        assert "Rationale:" in result
        assert "Auto Intenders" in result

    def test_includes_plan_id(self, typed_plan: AudiencePlan) -> None:
        result = _format_typed_audience_plan(typed_plan)
        # `audience_plan_id` is auto-computed; the prefix "sha256:" is stable.
        assert "sha256:" in result
        assert typed_plan.audience_plan_id in result

    def test_primary_carries_type_tag(self, typed_plan: AudiencePlan) -> None:
        result = _format_typed_audience_plan(typed_plan)
        # Primary is the standard 3-7 ref.
        assert "[standard]" in result
        assert "3-7" in result
        assert "iab-audience" in result
        assert "version=1.1" in result

    def test_contextual_constraint_carries_type_tag(
        self, typed_plan: AudiencePlan
    ) -> None:
        result = _format_typed_audience_plan(typed_plan)
        assert "[contextual]" in result
        assert "IAB1-2" in result
        assert "iab-content" in result
        assert "version=3.1" in result
        # Resolved ref -> confidence rendered.
        assert "confidence=0.92" in result

    def test_agentic_extension_carries_compliance(
        self, typed_plan: AudiencePlan
    ) -> None:
        result = _format_typed_audience_plan(typed_plan)
        assert "[agentic]" in result
        assert "emb://buyer.example.com/audiences/auto-converters-q1" in result
        assert "agentic-audiences" in result
        assert "draft-2026-01" in result
        # Compliance context fields -- proposal §5.2 mandates them.
        assert "jurisdiction=US" in result
        assert "consent=IAB-TCFv2" in result

    def test_exclusion_renders(self, typed_plan: AudiencePlan) -> None:
        result = _format_typed_audience_plan(typed_plan)
        assert "Exclusions" in result
        assert "3-12" in result

    def test_minimal_plan_omits_empty_role_sections(self) -> None:
        """A primary-only plan should not render constraint/extension/exclusion sections."""

        plan = AudiencePlan(
            primary=AudienceRef(
                type="standard",
                identifier="3-7",
                taxonomy="iab-audience",
                version="1.1",
                source="explicit",
            ),
        )
        result = _format_typed_audience_plan(plan)
        assert "Primary:" in result
        # Empty role lists should NOT emit their bullet headers.
        assert "Constraints (intersect" not in result
        assert "Extensions (union" not in result
        assert "Exclusions (subtract" not in result


# ---------------------------------------------------------------------------
# 3. _format_audience_ref helper
# ---------------------------------------------------------------------------


class TestFormatAudienceRef:
    """Single-ref renderer used by every role section."""

    def test_explicit_standard_ref(self) -> None:
        ref = AudienceRef(
            type="standard",
            identifier="3-7",
            taxonomy="iab-audience",
            version="1.1",
            source="explicit",
        )
        result = _format_audience_ref(ref)
        assert "[standard]" in result
        assert "3-7" in result
        assert "taxonomy=iab-audience" in result
        assert "version=1.1" in result
        assert "source=explicit" in result
        # Explicit ref -- no confidence rendered.
        assert "confidence=" not in result

    def test_resolved_contextual_with_confidence(self) -> None:
        ref = AudienceRef(
            type="contextual",
            identifier="IAB1-2",
            taxonomy="iab-content",
            version="3.1",
            source="resolved",
            confidence=0.85,
        )
        result = _format_audience_ref(ref)
        assert "[contextual]" in result
        assert "source=resolved" in result
        assert "confidence=0.85" in result

    def test_agentic_with_compliance(self) -> None:
        ref = AudienceRef(
            type="agentic",
            identifier="emb://example/x",
            taxonomy="agentic-audiences",
            version="draft-2026-01",
            source="explicit",
            compliance_context=ComplianceContext(
                jurisdiction="EU",
                consent_framework="GPP",
            ),
        )
        result = _format_audience_ref(ref)
        assert "[agentic]" in result
        assert "jurisdiction=EU" in result
        assert "consent=GPP" in result


# ---------------------------------------------------------------------------
# 4. Backward compat: legacy dict shape preserved
# ---------------------------------------------------------------------------


class TestLegacyDictBackwardCompat:
    """Pre-§19 dict input still produces the pre-§19 markdown."""

    def test_demographics_rendered(self, legacy_dict_plan: dict[str, Any]) -> None:
        result = _format_legacy_audience_dict(legacy_dict_plan)
        assert "Demographics" in result
        assert "25-54" in result

    def test_interests_rendered(self, legacy_dict_plan: dict[str, Any]) -> None:
        result = _format_legacy_audience_dict(legacy_dict_plan)
        assert "Interests" in result
        assert "automotive" in result

    def test_behaviors_rendered(self, legacy_dict_plan: dict[str, Any]) -> None:
        result = _format_legacy_audience_dict(legacy_dict_plan)
        assert "Behaviors" in result
        assert "online shoppers" in result

    def test_signal_types_rendered(self, legacy_dict_plan: dict[str, Any]) -> None:
        result = _format_legacy_audience_dict(legacy_dict_plan)
        assert "Required Signals" in result
        assert "identity" in result

    def test_exclusions_rendered(self, legacy_dict_plan: dict[str, Any]) -> None:
        result = _format_legacy_audience_dict(legacy_dict_plan)
        assert "Exclusions" in result
        assert "competitor audiences" in result

    def test_ucp_footer_present(self, legacy_dict_plan: dict[str, Any]) -> None:
        result = _format_legacy_audience_dict(legacy_dict_plan)
        assert "UCP-compatible" in result


# ---------------------------------------------------------------------------
# 5. All four channel crews accept typed AudiencePlan
# ---------------------------------------------------------------------------


def _research_task_description(crew: Any) -> str:
    """Pull the research task description out of a hierarchical crew.

    The research task is the first task in every channel crew; it carries
    the audience-context block injected by `_format_audience_context`.
    """

    return crew.tasks[0].description


class TestAllChannelCrewsAcceptTypedPlan:
    """The typed AudiencePlan flows into every channel crew uniformly."""

    @pytest.mark.parametrize(
        "factory",
        [
            create_branding_crew,
            create_mobile_crew,
            create_ctv_crew,
            create_performance_crew,
        ],
        ids=["branding", "mobile", "ctv", "performance"],
    )
    def test_typed_plan_injected_into_research_task(
        self,
        factory: Any,
        opendirect_client: MagicMock,
        channel_brief: dict[str, Any],
        typed_plan: AudiencePlan,
    ) -> None:
        crew = factory(opendirect_client, channel_brief, audience_plan=typed_plan)
        desc = _research_task_description(crew)
        # Typed-plan markers.
        assert "typed AudiencePlan" in desc
        assert "[standard]" in desc
        assert "[contextual]" in desc
        assert "[agentic]" in desc
        # Plan ID is part of the audit chain -- must surface to the agent.
        assert typed_plan.audience_plan_id in desc

    @pytest.mark.parametrize(
        "factory",
        [
            create_branding_crew,
            create_mobile_crew,
            create_ctv_crew,
            create_performance_crew,
        ],
        ids=["branding", "mobile", "ctv", "performance"],
    )
    def test_legacy_dict_injected_into_research_task(
        self,
        factory: Any,
        opendirect_client: MagicMock,
        channel_brief: dict[str, Any],
        legacy_dict_plan: dict[str, Any],
    ) -> None:
        crew = factory(opendirect_client, channel_brief, audience_plan=legacy_dict_plan)
        desc = _research_task_description(crew)
        # Legacy markers.
        assert "Audience Plan Context:" in desc
        # Should NOT carry the typed-plan marker -- backward compat path.
        assert "typed AudiencePlan" not in desc
        assert "Demographics" in desc
        assert "Interests" in desc

    @pytest.mark.parametrize(
        "factory",
        [
            create_branding_crew,
            create_mobile_crew,
            create_ctv_crew,
            create_performance_crew,
        ],
        ids=["branding", "mobile", "ctv", "performance"],
    )
    def test_none_plan_omits_audience_block(
        self,
        factory: Any,
        opendirect_client: MagicMock,
        channel_brief: dict[str, Any],
    ) -> None:
        crew = factory(opendirect_client, channel_brief, audience_plan=None)
        desc = _research_task_description(crew)
        # No audience block should be rendered.
        assert "Audience Plan Context" not in desc


# ---------------------------------------------------------------------------
# 6. kickoff_channel_crew_with_audience convenience wrapper
# ---------------------------------------------------------------------------


class TestConvenienceWrapper:
    """The direct-invocation wrapper routes by channel + threads the plan."""

    # Each channel maps to a Level-2 manager agent whose `role` string is
    # human-readable (e.g. "Connected TV Specialist"). We assert the role
    # contains a channel-distinguishing substring rather than an exact
    # match, so role copy can evolve without churning this test.
    @pytest.mark.parametrize(
        "channel,expected_manager_role_substr",
        [
            ("branding", "branding"),
            ("mobile", "mobile"),
            ("mobile_app", "mobile"),
            ("ctv", "connected tv"),
            ("performance", "performance"),
        ],
    )
    def test_routes_to_correct_factory(
        self,
        channel: str,
        expected_manager_role_substr: str,
        opendirect_client: MagicMock,
        channel_brief: dict[str, Any],
        typed_plan: AudiencePlan,
    ) -> None:
        crew = kickoff_channel_crew_with_audience(
            opendirect_client,
            channel,
            channel_brief,
            audience_plan=typed_plan,
        )
        assert crew.manager_agent is not None
        # Route correctness check: manager-agent role contains the expected
        # channel substring (e.g. "Branding Specialist", "Connected TV
        # Specialist"). Comparison is case-insensitive.
        assert expected_manager_role_substr in crew.manager_agent.role.lower()

    def test_unknown_channel_raises(
        self,
        opendirect_client: MagicMock,
        channel_brief: dict[str, Any],
    ) -> None:
        with pytest.raises(ValueError) as excinfo:
            kickoff_channel_crew_with_audience(
                opendirect_client,
                "linear_tv",  # not a channel-crew factory
                channel_brief,
            )
        assert "Unknown channel" in str(excinfo.value)

    def test_typed_plan_threaded_through(
        self,
        opendirect_client: MagicMock,
        channel_brief: dict[str, Any],
        typed_plan: AudiencePlan,
    ) -> None:
        crew = kickoff_channel_crew_with_audience(
            opendirect_client,
            "branding",
            channel_brief,
            audience_plan=typed_plan,
        )
        desc = _research_task_description(crew)
        assert "typed AudiencePlan" in desc
        assert typed_plan.audience_plan_id in desc

    def test_legacy_dict_threaded_through(
        self,
        opendirect_client: MagicMock,
        channel_brief: dict[str, Any],
        legacy_dict_plan: dict[str, Any],
    ) -> None:
        """The wrapper accepts legacy dict input and routes it unchanged."""

        crew = kickoff_channel_crew_with_audience(
            opendirect_client,
            "ctv",
            channel_brief,
            audience_plan=legacy_dict_plan,
        )
        desc = _research_task_description(crew)
        # Legacy markers preserved.
        assert "Audience Plan Context:" in desc
        assert "typed AudiencePlan" not in desc

    def test_no_plan_no_audience_block(
        self,
        opendirect_client: MagicMock,
        channel_brief: dict[str, Any],
    ) -> None:
        """No plan and no brief -> no audience block in the task."""

        crew = kickoff_channel_crew_with_audience(
            opendirect_client,
            "performance",
            channel_brief,
        )
        desc = _research_task_description(crew)
        assert "Audience Plan Context" not in desc

    def test_explicit_plan_wins_over_brief(
        self,
        opendirect_client: MagicMock,
        channel_brief: dict[str, Any],
        typed_plan: AudiencePlan,
    ) -> None:
        """When both `brief` and `audience_plan` are supplied, the explicit
        plan is used and the planner step is NOT invoked.

        Implementation detail: we don't expose the planner agent argument
        with a marker, but we can assert the typed plan in the output --
        the planner would have produced a different plan from a freshly-
        constructed brief, so seeing OUR plan_id in the task description
        proves the wrapper short-circuited.
        """

        # `brief` is a sentinel here -- if the wrapper invoked the planner,
        # it would crash trying to call `.advertiser_id` on a MagicMock.
        # We're deliberately passing a non-CampaignBrief sentinel that
        # would crash the planner if reached, so the test fails loudly if
        # the precedence rule is violated.
        sentinel_brief = object()
        crew = kickoff_channel_crew_with_audience(
            opendirect_client,
            "branding",
            channel_brief,
            brief=sentinel_brief,  # would crash planner if reached
            audience_plan=typed_plan,
        )
        desc = _research_task_description(crew)
        assert typed_plan.audience_plan_id in desc
