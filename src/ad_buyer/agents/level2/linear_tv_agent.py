# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Linear TV Specialist agent (Level 2).

Handles traditional linear television buying: daypart-based pricing
(CPP/CPM), DMA/local market targeting, scatter deal structures,
GRP-based audience guarantees, makegood terms, and commercial pod
positioning. Maps to seller's 1E (Linear TV Specialist Agent).

Design decisions (from LINEAR_TV_DEAL_FLOW_RESEARCH.md):
- Scatter-only for v1 (upfronts TBD, bead ar-gh6)
- Nielsen measurement currency for v1
- DMA-level granularity (all 210 DMAs)
- TIP-compatible, not TIP-native
"""

from typing import Any

from crewai import Agent, LLM

from ...config.settings import settings


def create_linear_tv_agent(
    tools: list[Any] | None = None,
    verbose: bool = True,
) -> Agent:
    """Create the Linear TV Specialist agent.

    The Linear TV Specialist focuses on:
    - National and local linear TV buying (scatter market)
    - Daypart-based pricing and inventory selection
    - GRP-based audience guarantees and CPP negotiation
    - DMA-level targeting across all 210 Nielsen markets
    - Post-booking lifecycle: makegoods and cancellations
    - Cross-media CPM/CPP comparison for unified TV planning

    Args:
        tools: Optional list of tools for the agent.
        verbose: Whether to enable verbose logging.

    Returns:
        Configured Linear TV Specialist Agent.
    """
    return Agent(
        role="Linear TV Specialist",
        goal="""Secure linear TV inventory that delivers target demographic
reach through scatter market buying, optimizing CPP across dayparts,
networks, and DMAs to meet GRP goals within budget.""",
        backstory="""You are a linear television advertising expert with deep
knowledge of the traditional TV buying landscape. You understand daypart
structures (Primetime, Daytime, Late Night, Early Morning, Early Fringe,
Prime Access, Overnight, Weekend), GRP-based audience measurement, and
CPP (Cost Per Point) pricing mechanics.

Your expertise includes:
- National and local scatter market buying
- All 210 Nielsen DMA (Designated Market Area) targeting
- CPP negotiation and rate-of-change analysis
- GRP delivery guarantees and audience deficiency units (ADU/makegoods)
- Nielsen audience measurement (C3/C7 ratings, live plus same day)
- Cross-media comparison: CPM to CPP conversion for unified TV planning
- Daypart mix optimization across broadcast and cable networks
- Commercial pod positioning (:15, :30, :60 spot lengths)
- Cancellation window management and notice period compliance
- Network group buying (broadcast: NBC, CBS, ABC, FOX; cable: ESPN, TNT, etc.)
- TIP (Television Interface Practices) compatibility for electronic orders

You work with the Research Agent to discover available linear TV inventory
and the Execution Agent to book placements. You coordinate with the CTV
Specialist for cross-screen TV strategies and with Branding for cohesive
video campaigns across linear and streaming.""",
        llm=LLM(
            model=settings.default_llm_model,
            temperature=0.5,
        ),
        tools=tools or [],
        allow_delegation=True,
        verbose=verbose,
        memory=True,
    )
