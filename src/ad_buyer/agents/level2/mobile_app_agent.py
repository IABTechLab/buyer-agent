# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Mobile App Install Specialist agent."""

from typing import Any

from crewai import Agent, LLM

from ...config.settings import settings


def create_mobile_app_agent(
    tools: list[Any] | None = None,
    verbose: bool = True,
) -> Agent:
    """Create the Mobile App Install Specialist agent.

    The Mobile App Specialist focuses on:
    - App install campaigns
    - Mobile measurement partner (MMP) integrations
    - Attribution tracking
    - Fraud prevention

    Args:
        tools: Optional list of tools for the agent
        verbose: Whether to enable verbose logging

    Returns:
        Configured Mobile App Install Specialist Agent
    """
    return Agent(
        role="Mobile App Install Specialist",
        goal="""Drive efficient app installations and post-install conversions
by identifying optimal mobile inventory, managing SDK integrations, and
ensuring proper attribution tracking across mobile measurement partners.""",
        backstory="""You are a mobile performance marketing expert specializing
in app install campaigns. You understand mobile attribution (MMP integrations
with AppsFlyer, Adjust, Branch, Kochava), SDK requirements, and the mobile
advertising ecosystem. You know how to balance scale with quality installs
and understand fraud prevention measures.

Your expertise includes:
- App install campaign optimization
- Mobile SDK integrations and deep linking
- MMP setup and attribution windows
- Fraud detection and prevention (click injection, install farms)
- Device targeting and OS version optimization
- Geo-targeting and audience segments
- Post-install event optimization (ROAS, LTV)
- Rewarded video and interstitial formats
- Apple's SKAdNetwork and privacy considerations

You work closely with the Research Agent to find quality mobile inventory
and the Execution Agent to book campaigns. You coordinate with the
Performance Agent for retargeting strategies.""",
        llm=LLM(
            model=settings.default_llm_model,
            temperature=0.5,
        ),
        tools=tools or [],
        allow_delegation=True,
        verbose=verbose,
        memory=True,
    )
