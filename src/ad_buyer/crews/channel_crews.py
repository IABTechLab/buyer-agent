# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Channel Specialist Crews for inventory research and booking.

Each crew uses Process.sequential with a single task so kickoff_async()
returns the JSON array that _parse_recommendations() expects directly.
The former hierarchical process caused manager-agent delegation (ask_question_to_coworker)
which corrupted message history and produced prose instead of JSON.
"""

from typing import Any, Optional

from crewai import Crew, Process, Task

from ..agents.level2.branding_agent import create_branding_agent
from ..agents.level2.ctv_agent import create_ctv_agent
from ..agents.level2.mobile_app_agent import create_mobile_app_agent
from ..agents.level2.performance_agent import create_performance_agent
from ..clients.opendirect_client import OpenDirectClient
from ..config.settings import settings
from ..tools.research.avails_check import AvailsCheckTool
from ..tools.research.product_search import ProductSearchTool
from ..tools.audience import AudienceDiscoveryTool, AudienceMatchingTool, CoverageEstimationTool

# JSON array format that _parse_recommendations() expects
_JSON_ARRAY_FORMAT = """[
    {
        "product_id": "...",
        "product_name": "...",
        "publisher": "...",
        "format": "video|display|native",
        "impressions": 1000000,
        "cpm": 12.50,
        "cost": 12500.00,
        "rationale": "Why this product was selected"
    }
]"""


def _create_research_tools(client: OpenDirectClient) -> list[Any]:
    """Create research tools with the OpenDirect client."""
    return [
        ProductSearchTool(client),
        AvailsCheckTool(client),
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
    """Create the Branding Specialist crew (sequential, single task)."""
    tools = _create_research_tools(client) + _create_audience_tools()
    agent = create_branding_agent(tools=tools)

    audience_context = _format_audience_context(audience_plan)

    task = Task(
        description=f"""
Research premium display and video inventory for a branding campaign and
return a ranked list of the top 3-5 products as a JSON array.

Budget: ${channel_brief.get('budget', 0):,.2f}
Flight: {channel_brief.get('start_date')} to {channel_brief.get('end_date')}
Target Audience: {channel_brief.get('target_audience', {})}
Objectives: {channel_brief.get('objectives', [])}
Quality Requirements: Viewability > 70%, Brand Safety verified
{audience_context}

Use search_advertising_products to find premium display/video placements.
Select the best options within budget. Your ENTIRE response must be ONLY
the JSON array below — no prose, no markdown, no explanation before or after.
""",
        expected_output=_JSON_ARRAY_FORMAT,
        agent=agent,
    )

    return Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        memory=settings.crew_memory_enabled,
        verbose=settings.crew_verbose,
    )


def create_mobile_crew(
    client: OpenDirectClient,
    channel_brief: dict[str, Any],
    audience_plan: Optional[dict[str, Any]] = None,
) -> Crew:
    """Create the Mobile App Install Specialist crew (sequential, single task)."""
    tools = _create_research_tools(client) + _create_audience_tools()
    agent = create_mobile_app_agent(tools=tools)

    audience_context = _format_audience_context(audience_plan)

    task = Task(
        description=f"""
Research mobile app install inventory and return the top 3-5 products as a JSON array.

Budget: ${channel_brief.get('budget', 0):,.2f}
Flight: {channel_brief.get('start_date')} to {channel_brief.get('end_date')}
Target Audience: {channel_brief.get('target_audience', {})}
Objectives: {channel_brief.get('objectives', [])}
{audience_context}

Use search_advertising_products to find in-app, rewarded video, and mobile web placements.
Prioritize low fraud rates and MMP integrations for attribution.
Your ENTIRE response must be ONLY the JSON array below — no prose, no markdown.
""",
        expected_output=_JSON_ARRAY_FORMAT,
        agent=agent,
    )

    return Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        memory=settings.crew_memory_enabled,
        verbose=settings.crew_verbose,
    )


def create_ctv_crew(
    client: OpenDirectClient,
    channel_brief: dict[str, Any],
    audience_plan: Optional[dict[str, Any]] = None,
) -> Crew:
    """Create the CTV Specialist crew (sequential, single task)."""
    tools = _create_research_tools(client) + _create_audience_tools()
    agent = create_ctv_agent(tools=tools)

    audience_context = _format_audience_context(audience_plan)

    task = Task(
        description=f"""
Research Connected TV and streaming inventory and return the top 3-5 products as a JSON array.

Budget: ${channel_brief.get('budget', 0):,.2f}
Flight: {channel_brief.get('start_date')} to {channel_brief.get('end_date')}
Target Audience: {channel_brief.get('target_audience', {})}
Objectives: {channel_brief.get('objectives', [])}
{audience_context}

Use search_advertising_products with channel="ctv" to find premium streaming inventory.
Prioritize brand-safe premium content, household targeting, and cross-device reach.
Your ENTIRE response must be ONLY the JSON array below — no prose, no markdown.
""",
        expected_output=_JSON_ARRAY_FORMAT,
        agent=agent,
    )

    return Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        memory=settings.crew_memory_enabled,
        verbose=settings.crew_verbose,
    )


def create_performance_crew(
    client: OpenDirectClient,
    channel_brief: dict[str, Any],
    audience_plan: Optional[dict[str, Any]] = None,
) -> Crew:
    """Create the Performance/Remarketing Specialist crew (sequential, single task)."""
    tools = _create_research_tools(client) + _create_audience_tools()
    agent = create_performance_agent(tools=tools)

    audience_context = _format_audience_context(audience_plan)

    task = Task(
        description=f"""
Research performance and remarketing inventory and return the top 3-5 products as a JSON array.

Budget: ${channel_brief.get('budget', 0):,.2f}
Flight: {channel_brief.get('start_date')} to {channel_brief.get('end_date')}
Target Audience: {channel_brief.get('target_audience', {})}
Objectives: {channel_brief.get('objectives', [])}
KPIs: {channel_brief.get('kpis', {})}
{audience_context}

Use search_advertising_products to find retargeting and conversion-focused placements.
Prioritize inventory with strong conversion histories and dynamic creative support.
Your ENTIRE response must be ONLY the JSON array below — no prose, no markdown.
""",
        expected_output=_JSON_ARRAY_FORMAT,
        agent=agent,
    )

    return Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        memory=settings.crew_memory_enabled,
        verbose=settings.crew_verbose,
    )
