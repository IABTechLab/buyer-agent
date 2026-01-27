# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Channel Specialist Crews for inventory research and booking."""

from typing import Any, Optional

from crewai import Crew, Process, Task

from ..agents.level2.branding_agent import create_branding_agent
from ..agents.level2.ctv_agent import create_ctv_agent
from ..agents.level2.mobile_app_agent import create_mobile_app_agent
from ..agents.level2.performance_agent import create_performance_agent
from ..agents.level3.execution_agent import create_execution_agent
from ..agents.level3.research_agent import create_research_agent
from ..agents.level3.audience_planner_agent import create_audience_planner_agent
from ..clients.opendirect_client import OpenDirectClient
from ..config.settings import settings
from ..tools.execution.line_management import BookLineTool, CreateLineTool, ReserveLineTool
from ..tools.execution.order_management import CreateOrderTool
from ..tools.research.avails_check import AvailsCheckTool
from ..tools.research.product_search import ProductSearchTool
from ..tools.audience import AudienceDiscoveryTool, AudienceMatchingTool, CoverageEstimationTool


def _create_research_tools(client: OpenDirectClient) -> list[Any]:
    """Create research tools with the OpenDirect client."""
    return [
        ProductSearchTool(client),
        AvailsCheckTool(client),
    ]


def _create_execution_tools(client: OpenDirectClient) -> list[Any]:
    """Create execution tools with the OpenDirect client."""
    return [
        CreateOrderTool(client),
        CreateLineTool(client),
        ReserveLineTool(client),
        BookLineTool(client),
    ]


def _create_audience_tools() -> list[Any]:
    """Create audience planning tools."""
    return [
        AudienceDiscoveryTool(),
        AudienceMatchingTool(),
        CoverageEstimationTool(),
    ]


def _format_audience_context(audience_plan: Optional[dict[str, Any]]) -> str:
    """Format audience plan as context for research tasks."""
    if not audience_plan:
        return ""

    context_parts = ["\n\nAudience Plan Context:"]

    if audience_plan.get("target_demographics"):
        context_parts.append(f"- Demographics: {audience_plan['target_demographics']}")

    if audience_plan.get("target_interests"):
        context_parts.append(f"- Interests: {', '.join(audience_plan['target_interests'])}")

    if audience_plan.get("target_behaviors"):
        context_parts.append(f"- Behaviors: {', '.join(audience_plan['target_behaviors'])}")

    if audience_plan.get("requested_signal_types"):
        context_parts.append(f"- Required Signals: {', '.join(audience_plan['requested_signal_types'])}")

    if audience_plan.get("exclusions"):
        context_parts.append(f"- Exclusions: {', '.join(audience_plan['exclusions'])}")

    context_parts.append("\nPrioritize inventory with UCP-compatible audience capabilities.")

    return "\n".join(context_parts)


def create_branding_crew(
    client: OpenDirectClient,
    channel_brief: dict[str, Any],
    audience_plan: Optional[dict[str, Any]] = None,
) -> Crew:
    """Create the Branding Specialist crew.

    Args:
        client: OpenDirect API client
        channel_brief: Channel-specific brief with budget, dates, etc.
        audience_plan: Optional audience plan from Audience Planner Agent

    Returns:
        Configured Branding Crew
    """
    # Create tools
    research_tools = _create_research_tools(client)
    execution_tools = _create_execution_tools(client)
    audience_tools = _create_audience_tools()

    # Create agents with tools
    branding_agent = create_branding_agent()
    research_agent = create_research_agent(tools=research_tools + audience_tools)
    execution_agent = create_execution_agent(tools=execution_tools)

    # Format audience context
    audience_context = _format_audience_context(audience_plan)

    # Define research task
    research_task = Task(
        description=f"""
Research premium display and video inventory for a branding campaign:

Budget: ${channel_brief.get('budget', 0):,.2f}
Flight: {channel_brief.get('start_date')} to {channel_brief.get('end_date')}
Target Audience: {channel_brief.get('target_audience', {})}
Objectives: {channel_brief.get('objectives', [])}
Quality Requirements: Viewability > 70%, Brand Safety verified
{audience_context}

Search for:
1. High-impact display placements (homepage takeovers, roadblocks)
2. Premium video placements (in-stream, outstream)
3. Cross-device reach opportunities

For the top 5 products, check availability and pricing for the flight dates.
Use audience matching tools to verify targeting compatibility.
Provide ranked recommendations with rationale.
""",
        expected_output="""List of recommended products:
[
    {
        "product_id": "...",
        "product_name": "...",
        "publisher": "...",
        "format": "...",
        "impressions": X,
        "cpm": Y,
        "cost": Z,
        "rationale": "..."
    }
]""",
        agent=research_agent,
    )

    # Define recommendation task
    recommendation_task = Task(
        description="""
Review the research findings and select the best inventory for this
branding campaign. Consider:

1. Alignment with campaign objectives
2. Budget efficiency
3. Reach and frequency
4. Quality metrics

Finalize your recommendations for approval.
""",
        expected_output="""Final recommendations with booking priority:
{
    "recommendations": [...],
    "total_impressions": X,
    "total_cost": Y,
    "summary": "..."
}""",
        agent=branding_agent,
        context=[research_task],
    )

    return Crew(
        agents=[branding_agent, research_agent, execution_agent],
        tasks=[research_task, recommendation_task],
        process=Process.hierarchical,
        manager_agent=branding_agent,
        memory=settings.crew_memory_enabled,
        verbose=settings.crew_verbose,
    )


def create_mobile_crew(
    client: OpenDirectClient,
    channel_brief: dict[str, Any],
    audience_plan: Optional[dict[str, Any]] = None,
) -> Crew:
    """Create the Mobile App Install Specialist crew.

    Args:
        client: OpenDirect API client
        channel_brief: Channel-specific brief with budget, dates, etc.
        audience_plan: Optional audience plan from Audience Planner Agent

    Returns:
        Configured Mobile App Crew
    """
    # Create tools
    research_tools = _create_research_tools(client)
    execution_tools = _create_execution_tools(client)
    audience_tools = _create_audience_tools()

    # Create agents with tools
    mobile_agent = create_mobile_app_agent()
    research_agent = create_research_agent(tools=research_tools + audience_tools)
    execution_agent = create_execution_agent(tools=execution_tools)

    # Format audience context
    audience_context = _format_audience_context(audience_plan)

    # Define research task
    research_task = Task(
        description=f"""
Research mobile app install inventory:

Budget: ${channel_brief.get('budget', 0):,.2f}
Flight: {channel_brief.get('start_date')} to {channel_brief.get('end_date')}
Target Audience: {channel_brief.get('target_audience', {})}
Objectives: {channel_brief.get('objectives', [])}
{audience_context}

Search for:
1. In-app interstitial placements
2. Rewarded video inventory
3. Mobile web placements
4. Inventory with low fraud rates

Focus on publishers with MMP integrations for proper attribution.
Use audience matching tools to verify targeting compatibility.
Provide ranked recommendations with rationale.
""",
        expected_output="""List of recommended products with fraud scores and MMP support.""",
        agent=research_agent,
    )

    recommendation_task = Task(
        description="""
Review the research findings and select the best mobile inventory.
Prioritize quality over scale - low fraud and proper attribution are critical.
""",
        expected_output="""Final recommendations with booking priority.""",
        agent=mobile_agent,
        context=[research_task],
    )

    return Crew(
        agents=[mobile_agent, research_agent, execution_agent],
        tasks=[research_task, recommendation_task],
        process=Process.hierarchical,
        manager_agent=mobile_agent,
        memory=settings.crew_memory_enabled,
        verbose=settings.crew_verbose,
    )


def create_ctv_crew(
    client: OpenDirectClient,
    channel_brief: dict[str, Any],
    audience_plan: Optional[dict[str, Any]] = None,
) -> Crew:
    """Create the CTV Specialist crew.

    Args:
        client: OpenDirect API client
        channel_brief: Channel-specific brief with budget, dates, etc.
        audience_plan: Optional audience plan from Audience Planner Agent

    Returns:
        Configured CTV Crew
    """
    # Create tools
    research_tools = _create_research_tools(client)
    execution_tools = _create_execution_tools(client)
    audience_tools = _create_audience_tools()

    # Create agents with tools
    ctv_agent = create_ctv_agent()
    research_agent = create_research_agent(tools=research_tools + audience_tools)
    execution_agent = create_execution_agent(tools=execution_tools)

    # Format audience context
    audience_context = _format_audience_context(audience_plan)

    # Define research task
    research_task = Task(
        description=f"""
Research Connected TV and streaming inventory:

Budget: ${channel_brief.get('budget', 0):,.2f}
Flight: {channel_brief.get('start_date')} to {channel_brief.get('end_date')}
Target Audience: {channel_brief.get('target_audience', {})}
Objectives: {channel_brief.get('objectives', [])}
{audience_context}

Search for:
1. Premium streaming platforms (Hulu, Peacock, etc.)
2. FAST channels (Pluto, Tubi, Freevee)
3. Device-specific inventory (Roku, Fire TV, etc.)
4. PMPs with household targeting

Prioritize brand-safe, premium content environments.
Use audience matching tools to verify targeting compatibility.
Provide ranked recommendations with rationale.
""",
        expected_output="""List of recommended CTV products with household reach estimates.""",
        agent=research_agent,
    )

    recommendation_task = Task(
        description="""
Review the research findings and select the best CTV inventory.
Balance reach with frequency management across devices.
""",
        expected_output="""Final recommendations with booking priority.""",
        agent=ctv_agent,
        context=[research_task],
    )

    return Crew(
        agents=[ctv_agent, research_agent, execution_agent],
        tasks=[research_task, recommendation_task],
        process=Process.hierarchical,
        manager_agent=ctv_agent,
        memory=settings.crew_memory_enabled,
        verbose=settings.crew_verbose,
    )


def create_performance_crew(
    client: OpenDirectClient,
    channel_brief: dict[str, Any],
    audience_plan: Optional[dict[str, Any]] = None,
) -> Crew:
    """Create the Performance/Remarketing Specialist crew.

    Args:
        client: OpenDirect API client
        channel_brief: Channel-specific brief with budget, dates, etc.
        audience_plan: Optional audience plan from Audience Planner Agent

    Returns:
        Configured Performance Crew
    """
    # Create tools
    research_tools = _create_research_tools(client)
    execution_tools = _create_execution_tools(client)
    audience_tools = _create_audience_tools()

    # Create agents with tools
    performance_agent = create_performance_agent()
    research_agent = create_research_agent(tools=research_tools + audience_tools)
    execution_agent = create_execution_agent(tools=execution_tools)

    # Format audience context
    audience_context = _format_audience_context(audience_plan)

    # Define research task
    research_task = Task(
        description=f"""
Research performance and remarketing inventory:

Budget: ${channel_brief.get('budget', 0):,.2f}
Flight: {channel_brief.get('start_date')} to {channel_brief.get('end_date')}
Target Audience: {channel_brief.get('target_audience', {})}
Objectives: {channel_brief.get('objectives', [])}
KPIs: {channel_brief.get('kpis', {})}
{audience_context}

Search for:
1. Retargeting-optimized inventory
2. Conversion-focused placements
3. Dynamic creative-enabled publishers
4. Performance-priced inventory (CPA/CPC options)

Prioritize inventory with strong conversion histories.
Use audience matching tools to verify targeting compatibility.
Provide ranked recommendations with rationale.
""",
        expected_output="""List of recommended products with conversion rate estimates.""",
        agent=research_agent,
    )

    recommendation_task = Task(
        description="""
Review the research findings and select the best performance inventory.
Optimize for ROAS and conversion efficiency.
""",
        expected_output="""Final recommendations with booking priority.""",
        agent=performance_agent,
        context=[research_task],
    )

    return Crew(
        agents=[performance_agent, research_agent, execution_agent],
        tasks=[research_task, recommendation_task],
        process=Process.hierarchical,
        manager_agent=performance_agent,
        memory=settings.crew_memory_enabled,
        verbose=settings.crew_verbose,
    )
