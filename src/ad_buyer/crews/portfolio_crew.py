# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Portfolio Crew - budget allocation crew (sequential)."""

from typing import Any

from crewai import Crew, Process, Task

from ..agents.level1.portfolio_manager import create_portfolio_manager
from ..clients.opendirect_client import OpenDirectClient
from ..config.settings import settings


def create_portfolio_crew(
    client: OpenDirectClient,
    campaign_brief: dict[str, Any],
) -> Crew:
    """Create the portfolio management crew.

    Runs sequentially so the Portfolio Manager completes budget allocation
    and channel guidance without delegating via tool calls (which caused
    malformed message history under the hierarchical process).

    Args:
        client: OpenDirect API client
        campaign_brief: Campaign brief with objectives, budget, etc.

    Returns:
        Configured Portfolio Crew
    """
    portfolio_manager = create_portfolio_manager()

    # Define budget allocation task
    budget_allocation_task = Task(
        description=f"""
Analyze the campaign brief and allocate budget across channels:

Campaign Name: {campaign_brief.get('name', 'Unnamed Campaign')}
Campaign Objectives: {campaign_brief.get('objectives', [])}
Total Budget: ${campaign_brief.get('budget', 0):,.2f}
Flight Dates: {campaign_brief.get('start_date')} to {campaign_brief.get('end_date')}
Target Audience: {campaign_brief.get('target_audience', {})}
KPIs: {campaign_brief.get('kpis', {})}

Determine the optimal budget split across:
1. Branding (display/video) - for awareness objectives
2. Mobile App Install - if app promotion is needed
3. CTV (Connected TV) - for premium video reach
4. Performance/Remarketing - for conversion objectives

Consider the campaign objectives and provide channel allocations with rationale.
Not all channels may be needed - allocate $0 to channels that don't fit the objectives.
""",
        expected_output="""A JSON object with channel allocations:
{
    "branding": {"budget": X, "percentage": Y, "rationale": "..."},
    "mobile_app": {"budget": X, "percentage": Y, "rationale": "..."},
    "ctv": {"budget": X, "percentage": Y, "rationale": "..."},
    "performance": {"budget": X, "percentage": Y, "rationale": "..."}
}""",
        agent=portfolio_manager,
    )

    return Crew(
        agents=[portfolio_manager],
        tasks=[budget_allocation_task],
        process=Process.sequential,
        memory=settings.crew_memory_enabled,
        verbose=settings.crew_verbose,
    )
