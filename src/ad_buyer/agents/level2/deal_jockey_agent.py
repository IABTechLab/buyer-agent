# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""DealJockey agent - deal portfolio management specialist.

DealJockey is an L2 agent in the buyer hierarchy that manages deal portfolios:
importing, cataloging, inspecting, organizing, migrating, and optimizing deals
across publishers, SSPs, and DSPs.

L1 Routing Heuristics
---------------------
The L1 Portfolio Manager routes requests to DealJockey vs. Campaign flow using
these heuristics (from DEAL_JOCKEY_STRATEGIC_PLAN.md Section 5.4):

  -> DealJockey:
     "portfolio", "existing deals", "my deals", "migrate", "clone",
     "deprecate", "compare prices", "import", "catalog", "gap analysis",
     "sunset"

  -> Campaign flow (channel specialists):
     "campaign", "book for campaign", "budget", "target audience",
     "pacing", "flight dates", "launch"

  -> Ambiguous (both signals or unclear intent):
     L1 asks for clarification: "Are you looking to manage your existing
     deal portfolio, or book deals for a specific campaign?"

  -> Specific deal ID referenced:
     Status check / inspection -> DealJockey
     Activate for campaign -> Campaign flow
"""

from typing import Any

from crewai import Agent, LLM

from ...config.settings import settings


def create_deal_jockey_agent(
    tools: list[Any] | None = None,
    verbose: bool = True,
) -> Agent:
    """Create the DealJockey agent.

    The DealJockey agent focuses on deal portfolio management:
    - Deal portfolio organization and cataloging
    - CSV and bulk deal import/normalization
    - Deal template creation and management
    - Supply path analysis and optimization
    - Cross-platform deal tracking (TTD, DV360, Xandr, Amazon DSP)
    - Deal migration and deprecation workflows
    - Price comparison across supply paths
    - Gap analysis for portfolio coverage

    Args:
        tools: Optional list of tools for the agent
        verbose: Whether to enable verbose logging

    Returns:
        Configured DealJockey Agent
    """
    return Agent(
        role="Deal Jockey - Portfolio Manager",
        goal="""Manage deal portfolios — import, catalog, inspect, organize,
migrate, and optimize deals across publishers, SSPs, and DSPs. Treat deals
as a managed asset class, ensuring the agency's deal inventory is current,
well-organized, and aligned with campaign needs.""",
        backstory="""You are a portfolio management specialist with deep expertise
in programmatic deal operations across the ad tech ecosystem.

Your expertise includes:
- Deal portfolio organization and cataloging
- CSV and bulk deal import/normalization
- Deal template creation and management
- Supply path analysis and optimization
- Cross-platform deal tracking (TTD, DV360, Xandr, Amazon DSP)
- Deal migration and deprecation workflows
- Price comparison across supply paths
- Gap analysis for portfolio coverage

You work alongside channel specialists (Branding, CTV, Performance, Linear TV,
DSP). You receive portfolio-related requests from the L1 Portfolio Manager. You
delegate deal booking to internal deal-booking modules — your role is portfolio
management, not campaign execution.

When a deal needs to be booked for a campaign, you hand off to the appropriate
campaign flow. When you detect underperforming deals or better supply paths, you
propose changes that the campaign flow can execute.""",
        llm=LLM(
            model=settings.default_llm_model,
            temperature=0.3,
        ),
        tools=tools or [],
        allow_delegation=True,
        verbose=verbose,
        memory=True,
    )
