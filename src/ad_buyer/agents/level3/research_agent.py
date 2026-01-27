# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Research Agent for inventory discovery."""

from typing import Any

from crewai import Agent, LLM

from ...config.settings import settings


def create_research_agent(
    tools: list[Any] | None = None,
    verbose: bool = True,
) -> Agent:
    """Create the Research Agent.

    The Research Agent is responsible for:
    - Discovering available inventory
    - Evaluating product quality and fit
    - Checking availability and pricing
    - Providing recommendations

    Args:
        tools: List of research tools (product_search, avails_check)
        verbose: Whether to enable verbose logging

    Returns:
        Configured Research Agent
    """
    return Agent(
        role="Inventory Research Analyst",
        goal="""Discover, evaluate, and recommend optimal advertising inventory
and deals that match campaign requirements, budget constraints, and
targeting needs.""",
        backstory="""You are a meticulous inventory analyst who excels at
finding the right advertising products across publishers. You understand
OpenDirect product specifications, can interpret availability data, and
know how to evaluate inventory quality. You provide detailed analysis of
pricing, reach, and targeting capabilities to help specialists make
informed decisions.

Your responsibilities:
1. Search for products matching campaign criteria
2. Check real-time availability and pricing
3. Evaluate inventory quality (viewability, brand safety, fraud)
4. Compare options across publishers
5. Provide ranked recommendations with rationale

You work for the channel specialists (Branding, Mobile, CTV, Performance)
and provide them with inventory options. You do not make booking decisions
yourself - that is handled by the Execution Agent after approval.

When searching for inventory, always consider:
- Budget constraints
- Targeting requirements
- Flight dates
- Quality metrics
- Publisher reputation""",
        llm=LLM(
            model=settings.default_llm_model,
            temperature=0.2,
        ),
        tools=tools or [],
        allow_delegation=False,
        verbose=verbose,
        memory=True,
    )
