# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Channel Specialist Crews for inventory research and booking.

This module defines the four channel-specialist crews (branding, mobile,
CTV, performance). Each crew factory accepts an optional audience plan,
which is rendered into the research task description so the channel
agents can target inventory accordingly.

Audience plan input shapes (proposal §5.3, bead ar-5y8v / §19):

  - Typed `AudiencePlan` (preferred): the new shape produced by the
    Audience Planner's reasoning loop. Carries primary + constraints +
    extensions + exclusions, each as `AudienceRef` with type tag,
    taxonomy, version, source, and (for agentic) compliance context.
  - Legacy dict: the older shape produced by `deal_booking_flow.py`'s
    `_create_audience_plan()` helper -- free-text demographics, interest
    lists, signal-type strings, etc. Accepted for backward compatibility
    with callers that have not yet migrated to the typed model.
  - None: no audience targeting; the audience-context block is omitted.

The single `_format_audience_context` entry point dispatches on input
type and renders the appropriate markdown.
"""

from typing import Any

from crewai import Crew, Process, Task

from ..agents.level2.branding_agent import create_branding_agent
from ..agents.level2.ctv_agent import create_ctv_agent
from ..agents.level2.mobile_app_agent import create_mobile_app_agent
from ..agents.level2.performance_agent import create_performance_agent
from ..agents.level3.execution_agent import create_execution_agent
from ..agents.level3.research_agent import create_research_agent
from ..clients.opendirect_client import OpenDirectClient
from ..config.settings import settings
from ..models.audience_plan import AudiencePlan, AudienceRef
from ..tools.audience import AudienceDiscoveryTool, AudienceMatchingTool, CoverageEstimationTool
from ..tools.execution.line_management import BookLineTool, CreateLineTool, ReserveLineTool
from ..tools.execution.order_management import CreateOrderTool
from ..tools.research.avails_check import AvailsCheckTool
from ..tools.research.product_search import ProductSearchTool


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
    """Create the three UCP audience planning tools.

    NOTE: As of proposal §5.3 / bead ar-fgyq, these tools are owned by the
    Audience Planner agent (`agents/level3/audience_planner_agent.py`), not
    by the Research Agent. The Research Agent operates on inventory; the
    Audience Planner owns audience composition, discovery, matching, and
    coverage estimation. This helper is kept here so the planner factory
    in `pipelines/campaign_pipeline.py` can build the same three-tool
    bundle, and so existing tests that assert "the bundle is these three
    classes" continue to pass at the bundle level (just no longer attached
    to the Research Agent's `tools` list).
    """
    return [
        AudienceDiscoveryTool(),
        AudienceMatchingTool(),
        CoverageEstimationTool(),
    ]


def _format_audience_ref(ref: AudienceRef) -> str:
    """Render a single typed AudienceRef as a one-line markdown bullet.

    Format: `[<type>] <identifier> (taxonomy=..., version=..., source=...)`
    Example: `[standard] 3-7 (taxonomy=iab-audience, version=1.1, source=explicit)`

    Confidence is appended when present (resolved/inferred refs); compliance
    jurisdiction is appended for agentic refs so the agent sees the consent
    regime in the same context block.
    """

    parts = [
        f"[{ref.type}] {ref.identifier}",
        f"(taxonomy={ref.taxonomy}, version={ref.version}, source={ref.source}",
    ]
    if ref.confidence is not None:
        parts.append(f", confidence={ref.confidence:.2f}")
    if ref.compliance_context is not None:
        parts.append(
            f", jurisdiction={ref.compliance_context.jurisdiction}"
            f", consent={ref.compliance_context.consent_framework}"
        )
    return "".join(parts) + ")"


def _format_typed_audience_plan(plan: AudiencePlan) -> str:
    """Format a typed `AudiencePlan` as research-task context markdown.

    Renders all four roles (primary, constraints, extensions, exclusions)
    with their type tags, taxonomies, versions, and sources -- giving the
    research agent the full overlay model defined in proposal §5.2. The
    rationale (planner's narrative) is included verbatim so the agent can
    cite it when justifying inventory recommendations.
    """

    parts = [
        "\n\nAudience Plan Context (typed AudiencePlan):",
        f"- Plan ID: {plan.audience_plan_id}",
        f"- Primary: {_format_audience_ref(plan.primary)}",
    ]

    if plan.constraints:
        parts.append("- Constraints (intersect with primary -- precision):")
        for ref in plan.constraints:
            parts.append(f"  * {_format_audience_ref(ref)}")

    if plan.extensions:
        parts.append("- Extensions (union with primary -- reach):")
        for ref in plan.extensions:
            parts.append(f"  * {_format_audience_ref(ref)}")

    if plan.exclusions:
        parts.append("- Exclusions (subtract from assembled set -- negative audiences):")
        for ref in plan.exclusions:
            parts.append(f"  * {_format_audience_ref(ref)}")

    if plan.rationale:
        parts.append(f"- Rationale: {plan.rationale}")

    parts.append(
        "\nPrioritize inventory whose audience_capabilities cover the primary "
        "ref, then evaluate constraint/extension overlap. Agentic refs require "
        "UCP/Agentic-Audiences-compatible seller capability."
    )

    return "\n".join(parts)


def _format_legacy_audience_dict(audience_plan: dict[str, Any]) -> str:
    """Render the legacy dict audience plan as research-task context markdown.

    Preserves the pre-§19 surface used by `deal_booking_flow.py`'s
    `_create_audience_plan()` helper: free-text demographics, interest
    lists, signal-type strings. Kept for backward compatibility with
    callers that have not yet migrated to the typed AudiencePlan.
    """

    context_parts = ["\n\nAudience Plan Context:"]

    if audience_plan.get("target_demographics"):
        context_parts.append(f"- Demographics: {audience_plan['target_demographics']}")

    if audience_plan.get("target_interests"):
        context_parts.append(f"- Interests: {', '.join(audience_plan['target_interests'])}")

    if audience_plan.get("target_behaviors"):
        context_parts.append(f"- Behaviors: {', '.join(audience_plan['target_behaviors'])}")

    if audience_plan.get("requested_signal_types"):
        context_parts.append(
            f"- Required Signals: {', '.join(audience_plan['requested_signal_types'])}"
        )

    if audience_plan.get("exclusions"):
        context_parts.append(f"- Exclusions: {', '.join(audience_plan['exclusions'])}")

    context_parts.append("\nPrioritize inventory with UCP-compatible audience capabilities.")

    return "\n".join(context_parts)


def _format_audience_context(
    audience_plan: AudiencePlan | dict[str, Any] | None,
) -> str:
    """Format an audience plan as research-task context markdown.

    Accepts either:
      - typed `AudiencePlan` (preferred, post-§19) -- rendered with full
        primary/constraints/extensions/exclusions + rationale shape
      - legacy dict (pre-§19) -- rendered with the pre-§19 free-text shape
        for backward compatibility with `deal_booking_flow.py` and any
        other caller that has not yet migrated
      - None -- returns an empty string (no audience targeting block)

    The dispatch is on Python type so callers cannot accidentally hit the
    wrong renderer by passing the wrong shape: a typed plan goes through
    `_format_typed_audience_plan`; a dict goes through
    `_format_legacy_audience_dict`. Empty containers return "" (matches
    the pre-existing behavior the wider test suite relies on).
    """

    if audience_plan is None:
        return ""
    if isinstance(audience_plan, AudiencePlan):
        return _format_typed_audience_plan(audience_plan)
    if isinstance(audience_plan, dict):
        if not audience_plan:
            return ""
        return _format_legacy_audience_dict(audience_plan)
    # Defensive: unrecognized shape -- behave as if no audience was supplied
    # rather than crash the crew construction. The audit trail can pick up
    # the type mismatch separately; we want crew kickoff to remain robust.
    return ""


def create_branding_crew(
    client: OpenDirectClient,
    channel_brief: dict[str, Any],
    audience_plan: AudiencePlan | dict[str, Any] | None = None,
) -> Crew:
    """Create the Branding Specialist crew.

    Args:
        client: OpenDirect API client
        channel_brief: Channel-specific brief with budget, dates, etc.
        audience_plan: Optional audience plan. Accepts either the typed
            `AudiencePlan` produced by the Audience Planner agent's
            reasoning loop (preferred, per proposal §5.3) or the legacy
            dict shape used by `deal_booking_flow.py` (backward compat).
            None disables the audience-context block in the research task.

    Returns:
        Configured Branding Crew
    """
    # Create tools
    # NOTE (ar-fgyq / proposal §5.3): audience tools moved off the
    # Research Agent and onto the Audience Planner upstream in
    # CampaignPipeline. Research Agent now operates on inventory only.
    research_tools = _create_research_tools(client)
    execution_tools = _create_execution_tools(client)

    # Create agents with tools
    branding_agent = create_branding_agent()
    research_agent = create_research_agent(tools=research_tools)
    execution_agent = create_execution_agent(tools=execution_tools)

    # Format audience context
    audience_context = _format_audience_context(audience_plan)

    # Define research task
    research_task = Task(
        description=f"""
Research premium display and video inventory for a branding campaign:

Budget: ${channel_brief.get("budget", 0):,.2f}
Flight: {channel_brief.get("start_date")} to {channel_brief.get("end_date")}
Target Audience: {channel_brief.get("target_audience", {})}
Objectives: {channel_brief.get("objectives", [])}
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
        agents=[research_agent, execution_agent],
        tasks=[research_task, recommendation_task],
        process=Process.hierarchical,
        manager_agent=branding_agent,
        memory=settings.crew_memory_enabled,
        verbose=settings.crew_verbose,
    )


def create_mobile_crew(
    client: OpenDirectClient,
    channel_brief: dict[str, Any],
    audience_plan: AudiencePlan | dict[str, Any] | None = None,
) -> Crew:
    """Create the Mobile App Install Specialist crew.

    Args:
        client: OpenDirect API client
        channel_brief: Channel-specific brief with budget, dates, etc.
        audience_plan: Optional audience plan. Accepts either the typed
            `AudiencePlan` produced by the Audience Planner agent's
            reasoning loop (preferred, per proposal §5.3) or the legacy
            dict shape used by `deal_booking_flow.py` (backward compat).
            None disables the audience-context block in the research task.

    Returns:
        Configured Mobile App Crew
    """
    # Create tools
    # NOTE (ar-fgyq / proposal §5.3): audience tools moved to the
    # Audience Planner upstream in CampaignPipeline.
    research_tools = _create_research_tools(client)
    execution_tools = _create_execution_tools(client)

    # Create agents with tools
    mobile_agent = create_mobile_app_agent()
    research_agent = create_research_agent(tools=research_tools)
    execution_agent = create_execution_agent(tools=execution_tools)

    # Format audience context
    audience_context = _format_audience_context(audience_plan)

    # Define research task
    research_task = Task(
        description=f"""
Research mobile app install inventory:

Budget: ${channel_brief.get("budget", 0):,.2f}
Flight: {channel_brief.get("start_date")} to {channel_brief.get("end_date")}
Target Audience: {channel_brief.get("target_audience", {})}
Objectives: {channel_brief.get("objectives", [])}
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
        agents=[research_agent, execution_agent],
        tasks=[research_task, recommendation_task],
        process=Process.hierarchical,
        manager_agent=mobile_agent,
        memory=settings.crew_memory_enabled,
        verbose=settings.crew_verbose,
    )


def create_ctv_crew(
    client: OpenDirectClient,
    channel_brief: dict[str, Any],
    audience_plan: AudiencePlan | dict[str, Any] | None = None,
) -> Crew:
    """Create the CTV Specialist crew.

    Args:
        client: OpenDirect API client
        channel_brief: Channel-specific brief with budget, dates, etc.
        audience_plan: Optional audience plan. Accepts either the typed
            `AudiencePlan` produced by the Audience Planner agent's
            reasoning loop (preferred, per proposal §5.3) or the legacy
            dict shape used by `deal_booking_flow.py` (backward compat).
            None disables the audience-context block in the research task.

    Returns:
        Configured CTV Crew
    """
    # Create tools
    # NOTE (ar-fgyq / proposal §5.3): audience tools moved to the
    # Audience Planner upstream in CampaignPipeline.
    research_tools = _create_research_tools(client)
    execution_tools = _create_execution_tools(client)

    # Create agents with tools
    ctv_agent = create_ctv_agent()
    research_agent = create_research_agent(tools=research_tools)
    execution_agent = create_execution_agent(tools=execution_tools)

    # Format audience context
    audience_context = _format_audience_context(audience_plan)

    # Define research task
    research_task = Task(
        description=f"""
Research Connected TV and streaming inventory:

Budget: ${channel_brief.get("budget", 0):,.2f}
Flight: {channel_brief.get("start_date")} to {channel_brief.get("end_date")}
Target Audience: {channel_brief.get("target_audience", {})}
Objectives: {channel_brief.get("objectives", [])}
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
        agents=[research_agent, execution_agent],
        tasks=[research_task, recommendation_task],
        process=Process.hierarchical,
        manager_agent=ctv_agent,
        memory=settings.crew_memory_enabled,
        verbose=settings.crew_verbose,
    )


def create_performance_crew(
    client: OpenDirectClient,
    channel_brief: dict[str, Any],
    audience_plan: AudiencePlan | dict[str, Any] | None = None,
) -> Crew:
    """Create the Performance/Remarketing Specialist crew.

    Args:
        client: OpenDirect API client
        channel_brief: Channel-specific brief with budget, dates, etc.
        audience_plan: Optional audience plan. Accepts either the typed
            `AudiencePlan` produced by the Audience Planner agent's
            reasoning loop (preferred, per proposal §5.3) or the legacy
            dict shape used by `deal_booking_flow.py` (backward compat).
            None disables the audience-context block in the research task.

    Returns:
        Configured Performance Crew
    """
    # Create tools
    # NOTE (ar-fgyq / proposal §5.3): audience tools moved to the
    # Audience Planner upstream in CampaignPipeline.
    research_tools = _create_research_tools(client)
    execution_tools = _create_execution_tools(client)

    # Create agents with tools
    performance_agent = create_performance_agent()
    research_agent = create_research_agent(tools=research_tools)
    execution_agent = create_execution_agent(tools=execution_tools)

    # Format audience context
    audience_context = _format_audience_context(audience_plan)

    # Define research task
    research_task = Task(
        description=f"""
Research performance and remarketing inventory:

Budget: ${channel_brief.get("budget", 0):,.2f}
Flight: {channel_brief.get("start_date")} to {channel_brief.get("end_date")}
Target Audience: {channel_brief.get("target_audience", {})}
Objectives: {channel_brief.get("objectives", [])}
KPIs: {channel_brief.get("kpis", {})}
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
        agents=[research_agent, execution_agent],
        tasks=[research_task, recommendation_task],
        process=Process.hierarchical,
        manager_agent=performance_agent,
        memory=settings.crew_memory_enabled,
        verbose=settings.crew_verbose,
    )


# ---------------------------------------------------------------------------
# Direct-invocation convenience wrapper (proposal §5.3 / bead ar-5y8v)
# ---------------------------------------------------------------------------


# Map channel-string keys to crew factories so callers can route by channel
# without an `if/elif` chain. Tests and demos that drive a channel crew
# directly use this map via `kickoff_channel_crew_with_audience()` below.
_CHANNEL_FACTORIES = {
    "branding": create_branding_crew,
    "ctv": create_ctv_crew,
    "mobile": create_mobile_crew,
    "mobile_app": create_mobile_crew,  # alias used by deal_booking_flow
    "performance": create_performance_crew,
}


def kickoff_channel_crew_with_audience(
    client: OpenDirectClient,
    channel: str,
    channel_brief: dict[str, Any],
    *,
    brief: Any = None,
    audience_plan: AudiencePlan | dict[str, Any] | None = None,
    planner_agent: Any = None,
) -> Crew:
    """Build a channel crew with an `AudiencePlan` attached (direct path).

    Convenience wrapper for the third deal-finding entry point identified
    in proposal §5.3 -- the "direct channel-crew invocation path" used by
    tests and demos that don't go through `CampaignPipeline` (Path A) or
    `BuyerDealFlow` (Path B). Either pass an explicit `audience_plan`, or
    pass a `CampaignBrief` and let the planner produce one in place.

    Args:
        client: OpenDirect API client.
        channel: One of "branding" / "ctv" / "mobile" / "mobile_app" /
            "performance" (case-sensitive). Unknown channels raise
            `ValueError`.
        channel_brief: Channel-specific brief dict (budget, dates, etc.).
        brief: Optional `CampaignBrief`. When supplied alongside
            `audience_plan=None`, the function runs the audience-planner
            step and uses the resulting plan. Mutually exclusive with
            an explicit `audience_plan` -- if both are supplied, the
            explicit `audience_plan` wins (callers can pre-build a plan
            and skip the planner).
        audience_plan: Optional pre-built typed `AudiencePlan` or legacy
            dict. When supplied, the planner is not invoked.
        planner_agent: Optional pre-built planner agent (forwarded to
            `run_audience_planner_step`). Lets callers re-use one agent
            across multiple channel-crew invocations in a test.

    Returns:
        Configured `Crew` ready for `.kickoff()`.

    Raises:
        ValueError: when `channel` is not recognized.
    """

    factory = _CHANNEL_FACTORIES.get(channel)
    if factory is None:
        valid = sorted(_CHANNEL_FACTORIES.keys())
        raise ValueError(
            f"Unknown channel {channel!r}; expected one of {valid}"
        )

    # If the caller passed a CampaignBrief but no plan, run the planner
    # step inline. The import is local because the planner module pulls
    # in CrewAI eagerly and we want the channel-crews module to remain
    # importable without that cost when only legacy dict input is used.
    if audience_plan is None and brief is not None:
        from ..pipelines.audience_planner_step import run_audience_planner_step

        result = run_audience_planner_step(brief, agent=planner_agent)
        audience_plan = result.plan  # may be None when reasoning failed

    return factory(client, channel_brief, audience_plan=audience_plan)
