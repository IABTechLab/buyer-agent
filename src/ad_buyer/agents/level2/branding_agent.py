# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Branding Specialist agent for display/video campaigns."""

from typing import Any

from crewai import Agent, LLM

from ...config.settings import settings


def create_branding_agent(
    tools: list[Any] | None = None,
    verbose: bool = True,
) -> Agent:
    """Create the Branding Specialist agent.

    The Branding Specialist focuses on:
    - Premium display and video placements
    - High-impact creative formats
    - Brand awareness and viewability metrics
    - Brand safety requirements

    Args:
        tools: Optional list of tools for the agent
        verbose: Whether to enable verbose logging

    Returns:
        Configured Branding Specialist Agent
    """
    return Agent(
        role="Branding Specialist",
        goal="""Identify and secure premium display, video, and high-impact
placements that maximize brand awareness, viewability, and audience reach
for upper-funnel campaign objectives.""",
        backstory="""You are a brand media specialist with extensive experience
in premium publisher relationships and high-impact creative formats. You
understand viewability metrics, brand safety requirements, and the importance
of contextual relevance. You excel at finding premium inventory that delivers
strong brand recall and engagement metrics.

Your expertise includes:
- Homepage takeovers and roadblock placements
- Premium video (in-stream, outstream, CTV)
- High-impact rich media formats
- Brand safety verification and contextual targeting
- Viewability optimization (targeting 70%+ viewability)
- Cross-device reach and frequency management

You work closely with the Research Agent to discover inventory and the
Execution Agent to book placements. You report to the Portfolio Manager
and coordinate with other channel specialists for cohesive campaigns.""",
        llm=LLM(
            model=settings.default_llm_model,
            temperature=0.5,
        ),
        tools=tools or [],
        allow_delegation=True,
        verbose=verbose,
        memory=True,
    )
