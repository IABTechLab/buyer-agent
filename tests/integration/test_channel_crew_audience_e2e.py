# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""End-to-end integration test: audience plan on the channel-crew path.

Bead ar-6ipo / proposal §6 row 20, adapted to the ONE canonical pipeline
(bead ar-j2nw): the orphan BuyerDealFlow (former "Path B1") was deleted;
its brief-driven audience scenarios now run through the canonical
DealBookingFlow handoff in tests/integration/test_canonical_audience_e2e.py.

This file keeps the **direct channel-crew invocation** path
(``kickoff_channel_crew_with_audience``) -- the demo/test entry point
that bypasses the flow. The seller side is **mocked**: responsive but
ignorant of new audience semantics.

Scenarios:

  6. Channel-crew happy path with a 3-type plan (Standard primary +
     Contextual constraint + Agentic extension) across all 4 crews.
  7. Legacy ``list[str]`` brief migration through the crew path.
  8. Audience plan serialization parity at the crew boundary.
  9. Capability degradation scenario reachable with a legacy seller
     profile (actual degradation lives in the orchestrator, tested in
     tests/unit/test_buyer_preflight.py and the canonical e2e).

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
from ad_buyer.models.audience_plan import (
    AudiencePlan,
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
                "identifier": ("emb://buyer.example.com/audiences/auto-converters-q1"),
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

    return parse_campaign_brief(_base_brief_dict(target_audience=_three_type_plan_dict()))


def _legacy_list_brief() -> CampaignBrief:
    """Brief carrying a legacy ``list[str]`` target_audience (§4 shim)."""

    return parse_campaign_brief(
        _base_brief_dict(target_audience=["auto_intenders_25_54", "luxury_buyers"])
    )


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

    Exercises the legacy-seller capability scenario on the direct
    channel-crew invocation path. Asserts the scenario is
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
