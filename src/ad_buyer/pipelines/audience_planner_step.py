# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Audience Planner pipeline step (stub passthrough).

Wires the Audience Planner agent (`agents/level3/audience_planner_agent.py`)
into `CampaignPipeline` between brief ingestion and orchestrator handoff
per proposal §5.3 / bead ar-fgyq §6.

This is the keystone wiring bead. The planner agent itself is instantiated
here with its five tools (3 UCP audience tools + TaxonomyLookupTool +
EmbeddingMintTool), but its reasoning loop (proposal §5.5) is a STUB
in this bead -- it returns the brief's migrated AudiencePlan unchanged
when one is present, or `None` when the brief omitted audience targeting.
The full reasoning loop is bead ar-fgyq §7.

The CrewAI Task and Crew are constructed but not executed here -- the stub
short-circuits on the brief's already-typed plan. The agent is still
constructed (and its tool bindings introspectable) so that:
1. Tool ownership tests pass (tools live on the planner, not the research
   agent).
2. §7 can replace the stub body with `crew.kickoff()` + plan parsing
   without touching the rest of the pipeline.

Reference: AUDIENCE_PLANNER_3TYPE_EXTENSION_2026-04-25.md §5.3, §5.5, §6.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from crewai import Agent

from ..agents.level3.audience_planner_agent import create_audience_planner_agent
from ..models.audience_plan import AudiencePlan
from ..models.campaign_brief import CampaignBrief
from ..tools.audience import (
    AudienceDiscoveryTool,
    AudienceMatchingTool,
    CoverageEstimationTool,
    EmbeddingMintTool,
    TaxonomyLookupTool,
)

logger = logging.getLogger(__name__)


# Stub-passthrough rationale appended when the brief carries an explicit
# AudiencePlan. We do NOT overwrite the user's rationale -- we annotate it
# so the audit trail captures that the planner step ran (per the
# §13a audit-trail follow-up). Full reasoning loop: bead §7.
_STUB_PASSTHROUGH_NOTE = (
    "Stub passthrough -- full reasoning loop in bead ar-fgyq §7"
)


@dataclass
class AudiencePlannerResult:
    """Output of the planner step.

    Attributes:
        plan: The `AudiencePlan` selected for the campaign, or None when
            the brief omitted audience targeting and the stub had nothing
            to compose. (§7 will replace this with a real reasoning result.)
        agent: The underlying CrewAI Agent instance. Exposed for
            introspection in tests; production callers should treat this
            as opaque.
        is_stub: Always True in this bead; flips to False once §7 lands
            and the agent actually drives the plan composition.
    """

    plan: AudiencePlan | None
    agent: Agent
    is_stub: bool = True


def build_audience_planner_agent(verbose: bool = False) -> Agent:
    """Construct the Audience Planner agent with its full tool kit.

    Five tools per proposal §5.5:
      - AudienceDiscoveryTool (UCP) -- relocated from Research Agent
      - AudienceMatchingTool   (UCP) -- relocated from Research Agent
      - CoverageEstimationTool (UCP) -- relocated from Research Agent
      - TaxonomyLookupTool     -- vendored-taxonomy resolver
      - EmbeddingMintTool      -- mock agentic-ref minter (bead §22 swaps
        in a real model)

    The factory is shared across the pipeline step (here) and tests so
    we have one source of truth for "what tools the planner owns".
    """

    tools: list[Any] = [
        AudienceDiscoveryTool(),
        AudienceMatchingTool(),
        CoverageEstimationTool(),
        TaxonomyLookupTool(),
        EmbeddingMintTool(),
    ]
    return create_audience_planner_agent(tools=tools, verbose=verbose)


def run_audience_planner_step(
    brief: CampaignBrief,
    *,
    agent: Agent | None = None,
) -> AudiencePlannerResult:
    """Run the (stub) Audience Planner over a campaign brief.

    Behavior in this bead (the stub):
      1. If the brief carries a typed `AudiencePlan` (which it always does
         once the §4 migration ran -- legacy `list[str]` rows are migrated
         on ingest), pass it through unchanged. The user's rationale is
         preserved verbatim; this step does NOT mutate the plan content
         or its `audience_plan_id` content hash.
      2. If `brief.target_audience is None` (the brief omitted audience
         targeting), return None. §7 will replace this branch with actual
         reasoning that composes a default plan from advertiser context.

    The planner agent is instantiated regardless so:
      - Tool-binding tests can introspect the agent's `tools` attribute.
      - The CrewAI plumbing is in place for §7 to plug in.

    Args:
        brief: The validated `CampaignBrief` from ingestion.
        agent: Optional pre-built agent (tests inject a verbose=False
            instance). When None, a fresh agent is built.

    Returns:
        `AudiencePlannerResult` with the resolved plan (or None) and the
        agent for downstream introspection.
    """

    planner_agent = agent if agent is not None else build_audience_planner_agent()

    plan = brief.target_audience  # Already AudiencePlan | None post-§4.

    if plan is None:
        # No audience on the brief -- nothing to plan over yet. §7 will
        # fill in the reasoning that *creates* a plan from scratch in
        # this branch (using TaxonomyLookupTool + EmbeddingMintTool).
        logger.info(
            "audience_planner_step: brief has no target_audience; "
            "stub returns None (full reasoning is bead §7)"
        )
        return AudiencePlannerResult(plan=None, agent=planner_agent, is_stub=True)

    # Stub passthrough: emit a structured log noting the planner step
    # ran without touching the plan content. The user's rationale is
    # left intact -- callers that want to surface the stub-ran fact can
    # read `is_stub` on the result.
    logger.info(
        "audience_planner_step: stub passthrough on existing plan",
        extra={
            "audience_planner": {
                "audience_plan_id": plan.audience_plan_id,
                "primary_identifier": plan.primary.identifier,
                "primary_type": plan.primary.type,
                "stub": True,
                "note": _STUB_PASSTHROUGH_NOTE,
            }
        },
    )
    return AudiencePlannerResult(plan=plan, agent=planner_agent, is_stub=True)
