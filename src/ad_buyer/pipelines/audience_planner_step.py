# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Audience Planner pipeline step (full reasoning loop).

Wires the Audience Planner agent (`agents/level3/audience_planner_agent.py`)
into `CampaignPipeline` between brief ingestion and orchestrator handoff
per proposal §5.3 / bead ar-fgyq §6, and now drives the full reasoning
loop per proposal §5.5 / bead ar-9u25 §7.

Design:
- The pure-Python reasoning core lives in `audience_planner_reasoning.py`
  so it is testable without spinning up CrewAI. This module is the
  orchestration shell: it (a) builds the planner agent with its 5 tools,
  (b) wires those tools into the reasoning function, and (c) returns the
  plan + agent for downstream introspection.
- The CrewAI agent is constructed but not currently kicked off as a
  Task; the reasoning loop is deterministic Python. A future bead may
  hand the rationale prose generation to the agent, but the
  classification + role assignment stays here so tests stay
  deterministic.
- Anything the planner ADDS to a user-supplied plan carries
  `source="inferred"` so the audit trail (proposal §13a) can
  distinguish user-attributed vs. agent-attributed refs.

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
from .audience_planner_reasoning import ReasoningResult, run_audience_reasoning

logger = logging.getLogger(__name__)


@dataclass
class AudiencePlannerResult:
    """Output of the planner step.

    Attributes:
        plan: The `AudiencePlan` selected for the campaign, or None when
            the brief carried no usable audience signals (the rationale
            on the parent ReasoningResult records "needs human review"
            in that branch).
        agent: The underlying CrewAI Agent instance. Exposed for
            introspection in tests; production callers should treat this
            as opaque.
        is_stub: False once the §7 reasoning loop is in place. Retained
            on the dataclass for backward compat with the §6 wiring
            tests that asserted `is_stub` after the keystone bead landed;
            those tests were tightened in §7 to assert the new value.
        rationale_lines: List of rationale lines produced by the
            reasoning loop, in order. The plan's `rationale` is the
            joined string; this list is exposed for tests and audit
            consumers.
        discovery_available: True when the validation phase ran cleanly;
            False when discovery degraded (the rationale records why).
    """

    plan: AudiencePlan | None
    agent: Agent
    is_stub: bool = False
    rationale_lines: list[str] | None = None
    discovery_available: bool = True


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


def _extract_tools(agent: Agent) -> dict[str, Any]:
    """Pull the typed tool instances off the agent for direct invocation.

    The reasoning loop calls tools as Python objects (not via the LLM)
    so it can run deterministically. We look up by tool class so the
    lookup is stable across CrewAI's internal tool wrapping.
    """

    by_type: dict[type, Any] = {}
    for tool in agent.tools or []:
        by_type[type(tool)] = tool

    return {
        "discovery": by_type.get(AudienceDiscoveryTool),
        "matching": by_type.get(AudienceMatchingTool),
        "coverage": by_type.get(CoverageEstimationTool),
        "taxonomy": by_type.get(TaxonomyLookupTool),
        "embedding_mint": by_type.get(EmbeddingMintTool),
    }


def run_audience_planner_step(
    brief: CampaignBrief,
    *,
    agent: Agent | None = None,
) -> AudiencePlannerResult:
    """Run the Audience Planner reasoning loop over a campaign brief.

    Behavior:
      1. Build (or reuse) the planner agent with its 5 tools.
      2. Run the §5.5 reasoning loop with those tools wired in.
      3. Return the composed plan, agent, and rationale.

    The reasoning loop:
      - Preserves an explicit primary verbatim (user-attributed); the
        planner only ADDs constraints / extensions and tags them
        source=`inferred`.
      - Runs the full classify -> pick-primary -> add-constraints/
        extensions -> validate -> emit pipeline when the brief came
        from legacy migration (source=`inferred` primary) or omitted
        targeting entirely.
      - Degrades gracefully when seller-side discovery is offline
        (expected in this bead -- §8/§9/§11 activate it).

    Args:
        brief: The validated `CampaignBrief` from ingestion.
        agent: Optional pre-built agent (tests inject a verbose=False
            instance). When None, a fresh agent is built.

    Returns:
        `AudiencePlannerResult` with the resolved plan (or None) and
        the agent for downstream introspection.
    """

    planner_agent = agent if agent is not None else build_audience_planner_agent()
    tools = _extract_tools(planner_agent)

    if brief.target_audience is None:
        logger.info(
            "audience_planner_step: brief has no target_audience; "
            "running reasoning loop to compose from advertiser context"
        )

    reasoning: ReasoningResult = run_audience_reasoning(
        brief,
        discovery_tool=tools.get("discovery"),
        coverage_tool=tools.get("coverage"),
        embedding_mint_tool=tools.get("embedding_mint"),
    )

    if reasoning.plan is None:
        logger.warning(
            "audience_planner_step: reasoning produced no plan; "
            "rationale=%s",
            " | ".join(reasoning.rationale_lines),
        )
    else:
        logger.info(
            "audience_planner_step: reasoning produced plan",
            extra={
                "audience_planner": {
                    "audience_plan_id": reasoning.plan.audience_plan_id,
                    "primary_identifier": reasoning.plan.primary.identifier,
                    "primary_type": reasoning.plan.primary.type,
                    "primary_source": reasoning.plan.primary.source,
                    "constraint_count": len(reasoning.plan.constraints),
                    "extension_count": len(reasoning.plan.extensions),
                    "discovery_available": reasoning.discovery_available,
                }
            },
        )

    return AudiencePlannerResult(
        plan=reasoning.plan,
        agent=planner_agent,
        is_stub=False,
        rationale_lines=reasoning.rationale_lines,
        discovery_available=reasoning.discovery_available,
    )
