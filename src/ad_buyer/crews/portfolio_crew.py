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

    # Build channel constraint section from brief
    requested_channels = [c.lower() for c in campaign_brief.get("channels", []) if c.strip()]
    total_budget = campaign_brief.get("budget", 0)

    if requested_channels:
        channel_instruction = (
            f"IMPORTANT: The campaign brief specifies these channels ONLY: "
            f"{requested_channels}.\n"
            f"You MUST allocate the full budget of ${total_budget:,.2f} "
            f"across ONLY these channels.\n"
            f"Do NOT include any other channels in your response."
        )
        channel_keys = requested_channels
    else:
        channel_instruction = (
            "Determine the optimal budget split across available channels:\n"
            "1. branding (display/video) - for awareness objectives\n"
            "2. mobile_app - if app promotion is needed\n"
            "3. ctv (Connected TV) - for premium video reach\n"
            "4. performance - for conversion objectives\n"
            "5. social - for Facebook/Instagram campaigns\n"
            "Not all channels may be needed - allocate $0 to channels that don't fit."
        )
        channel_keys = ["branding", "mobile_app", "ctv", "performance", "social"]

    expected_output_example = "\n".join(
        f'    "{ch}": {{"budget": X, "percentage": Y, "rationale": "..."}}' for ch in channel_keys
    )

    # Define budget allocation task
    budget_allocation_task = Task(
        description=f"""
Analyze the campaign brief and allocate budget across channels:

Campaign Name: {campaign_brief.get("name", "Unnamed Campaign")}
Campaign Objectives: {campaign_brief.get("objectives", [])}
Total Budget: ${total_budget:,.2f}
Flight Dates: {campaign_brief.get("start_date")} to {campaign_brief.get("end_date")}
Target Audience: {campaign_brief.get("target_audience", {})}
KPIs: {campaign_brief.get("kpis", {})}

{channel_instruction}

Allocate budget based on campaign objectives and audience fit. Provide a rationale
for each channel's allocation. Percentages must sum to 100.
""",
        expected_output=f"""A JSON object with channel allocations:
{{
{expected_output_example}
}}""",
        agent=portfolio_manager,
    )

    # Legacy: channel_coordination_task was the final task, so kickoff() returned
    # guidance JSON (no "budget" field). _parse_allocations() found nothing and
    # budget_allocations stayed empty, skipping all channel research crews.
    # Commented out so budget_allocation_task is the sole output of kickoff().
    #
    # channel_coordination_task = Task(
    #     description="""
    # Based on the budget allocation, provide high-level guidance for each
    # active channel specialist:
    #
    # For each channel with budget > $0:
    # 1. Key objectives for that channel
    # 2. Targeting priorities
    # 3. Quality requirements (viewability, brand safety, etc.)
    # 4. Any specific constraints or preferences
    #
    # This guidance will be used by channel specialists to research and
    # recommend specific inventory.
    # """,
    #     expected_output="""Channel guidance for each active channel:
    # {
    #     "channel_name": {
    #         "objectives": ["..."],
    #         "targeting_priorities": ["..."],
    #         "quality_requirements": {...},
    #         "constraints": ["..."]
    #     }
    # }""",
    #     agent=portfolio_manager,
    #     context=[budget_allocation_task],
    # )

    return Crew(
        agents=[
            branding_agent,
            mobile_app_agent,
            ctv_agent,
            performance_agent,
        ],
        tasks=[budget_allocation_task],
        process=Process.hierarchical,
        manager_agent=portfolio_manager,
        memory=settings.crew_memory_enabled,
        verbose=settings.crew_verbose,
    )
