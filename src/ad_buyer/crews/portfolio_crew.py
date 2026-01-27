# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Portfolio Crew - top-level hierarchical crew."""

from typing import Any

from crewai import Crew, Process, Task

from ..agents.level1.portfolio_manager import create_portfolio_manager
from ..agents.level2.branding_agent import create_branding_agent
from ..agents.level2.ctv_agent import create_ctv_agent
from ..agents.level2.mobile_app_agent import create_mobile_app_agent
from ..agents.level2.performance_agent import create_performance_agent
from ..clients.opendirect_client import OpenDirectClient
from ..config.settings import settings


def create_portfolio_crew(
    client: OpenDirectClient,
    campaign_brief: dict[str, Any],
) -> Crew:
    """Create the top-level portfolio management crew.

    This crew coordinates budget allocation and channel specialist
    delegation for a campaign.

    Args:
        client: OpenDirect API client
        campaign_brief: Campaign brief with objectives, budget, etc.

    Returns:
        Configured Portfolio Crew
    """
    # Create agents (tools will be added by channel crews)
    portfolio_manager = create_portfolio_manager()
    branding_agent = create_branding_agent()
    mobile_app_agent = create_mobile_app_agent()
    ctv_agent = create_ctv_agent()
    performance_agent = create_performance_agent()

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

    # Define channel coordination task
    channel_coordination_task = Task(
        description="""
Based on the budget allocation, provide high-level guidance for each
active channel specialist:

For each channel with budget > $0:
1. Key objectives for that channel
2. Targeting priorities
3. Quality requirements (viewability, brand safety, etc.)
4. Any specific constraints or preferences

This guidance will be used by channel specialists to research and
recommend specific inventory.
""",
        expected_output="""Channel guidance for each active channel:
{
    "channel_name": {
        "objectives": ["..."],
        "targeting_priorities": ["..."],
        "quality_requirements": {...},
        "constraints": ["..."]
    }
}""",
        agent=portfolio_manager,
        context=[budget_allocation_task],
    )

    return Crew(
        agents=[
            portfolio_manager,
            branding_agent,
            mobile_app_agent,
            ctv_agent,
            performance_agent,
        ],
        tasks=[budget_allocation_task, channel_coordination_task],
        process=Process.hierarchical,
        manager_agent=portfolio_manager,
        memory=settings.crew_memory_enabled,
        verbose=settings.crew_verbose,
    )
