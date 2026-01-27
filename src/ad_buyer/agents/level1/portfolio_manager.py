# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Portfolio Manager agent - top-level orchestrator."""

from typing import Any

from crewai import Agent, LLM

from ...config.settings import settings


def create_portfolio_manager(
    tools: list[Any] | None = None,
    verbose: bool = True,
) -> Agent:
    """Create the Portfolio Manager agent.

    The Portfolio Manager is the top-level orchestrator responsible for:
    - Budget allocation across channels
    - Coordinating specialist agents
    - Ensuring campaign objectives are met
    - Managing overall campaign strategy

    Args:
        tools: Optional list of tools for the agent
        verbose: Whether to enable verbose logging

    Returns:
        Configured Portfolio Manager Agent
    """
    return Agent(
        role="Portfolio Manager",
        goal="""Maximize campaign performance across all channels by intelligently
allocating budget, coordinating specialist agents, and ensuring all campaign
objectives and constraints are met within the specified timeline.""",
        backstory="""You are a senior media buyer with 15+ years of experience
managing multi-million dollar advertising portfolios for Fortune 500 clients.
You have deep expertise in budget allocation, channel mix optimization, and
cross-channel attribution. You understand the nuances of programmatic buying
and can effectively delegate tasks to channel specialists while maintaining
oversight of the entire campaign ecosystem. You are methodical, data-driven,
and always ensure compliance with client constraints.

Your key responsibilities:
1. Analyze campaign briefs and determine optimal budget allocation
2. Delegate to channel specialists (Branding, Mobile, CTV, Performance)
3. Consolidate recommendations and ensure coherent strategy
4. Monitor overall campaign performance
5. Make real-time optimization decisions""",
        llm=LLM(
            model=settings.manager_llm_model,
            temperature=0.3,
        ),
        tools=tools or [],
        allow_delegation=True,
        verbose=verbose,
        memory=True,
    )
