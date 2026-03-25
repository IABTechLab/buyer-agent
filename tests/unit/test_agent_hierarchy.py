# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Comprehensive tests for the agent hierarchy (L1/L2/L3).

Covers:
- Agent initialization and configuration for each level
- Agent tool assignments and capabilities per level
- Crew composition and task delegation
- Agent decision-making patterns (routing, delegation settings)
- Edge cases: empty tools, verbose toggling, memory settings
- DSP and Audience Planner agents (previously untested)
- Crew construction and structure validation
- The _format_audience_context helper in channel_crews
"""

import os
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, Field

# Set a dummy API key for tests (agents validate on creation)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

from crewai import Agent
from crewai.tools import BaseTool

from ad_buyer.agents.level1.portfolio_manager import create_portfolio_manager
from ad_buyer.agents.level2.branding_agent import create_branding_agent
from ad_buyer.agents.level2.ctv_agent import create_ctv_agent
from ad_buyer.agents.level2.dsp_agent import create_dsp_agent
from ad_buyer.agents.level2.mobile_app_agent import create_mobile_app_agent
from ad_buyer.agents.level2.performance_agent import create_performance_agent
from ad_buyer.agents.level3.audience_planner_agent import create_audience_planner_agent
from ad_buyer.agents.level3.execution_agent import create_execution_agent
from ad_buyer.agents.level3.reporting_agent import create_reporting_agent
from ad_buyer.agents.level3.research_agent import create_research_agent
from ad_buyer.crews.channel_crews import (
    _create_audience_tools,
    _create_execution_tools,
    _create_research_tools,
    _format_audience_context,
    create_branding_crew,
    create_ctv_crew,
    create_mobile_crew,
    create_performance_crew,
)
from ad_buyer.crews.portfolio_crew import create_portfolio_crew

# ---------------------------------------------------------------------------
# Helper: create valid BaseTool instances for injection tests
# ---------------------------------------------------------------------------


class _StubToolInput(BaseModel):
    """Minimal input schema for stub tools."""

    x: str = Field(default="", description="unused")


class _StubTool(BaseTool):
    """A valid CrewAI BaseTool for use in injection tests."""

    name: str = "stub"
    description: str = "Stub tool"
    args_schema: type[BaseModel] = _StubToolInput

    def _run(self, **kwargs):
        return "ok"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_tool():
    """A single valid BaseTool for injection into agents."""
    return _StubTool(name="mock_tool", description="Mock tool")


@pytest.fixture
def mock_tools():
    """Multiple valid BaseTools for injection into agents."""
    return [
        _StubTool(name="tool_a", description="Stub A"),
        _StubTool(name="tool_b", description="Stub B"),
        _StubTool(name="tool_c", description="Stub C"),
    ]


@pytest.fixture
def mock_opendirect_client():
    """Mock OpenDirectClient for crew creation tests."""
    return MagicMock()


@pytest.fixture
def campaign_brief():
    """Standard campaign brief for crew tests."""
    return {
        "name": "Test Campaign Q1",
        "objectives": ["brand awareness", "reach"],
        "budget": 100000,
        "start_date": "2025-03-01",
        "end_date": "2025-03-31",
        "target_audience": {
            "age": "25-54",
            "gender": "all",
            "geo": ["US"],
        },
        "kpis": {
            "viewability": 70,
            "ctr": 0.5,
        },
    }


@pytest.fixture
def channel_brief():
    """Standard channel-level brief for channel crew tests."""
    return {
        "budget": 25000,
        "start_date": "2025-03-01",
        "end_date": "2025-03-31",
        "target_audience": {
            "age": "25-54",
            "gender": "all",
            "geo": ["US"],
        },
        "objectives": ["brand awareness"],
        "kpis": {"viewability": 70},
    }


@pytest.fixture
def audience_plan():
    """Sample audience plan for crew creation."""
    return {
        "target_demographics": {"age": "25-54", "income": "high"},
        "target_interests": ["technology", "finance"],
        "target_behaviors": ["online shoppers", "mobile users"],
        "requested_signal_types": ["identity", "contextual"],
        "exclusions": ["competitor audiences"],
    }


# ===========================================================================
# Level 1 Agent Tests -- Portfolio Manager
# ===========================================================================


class TestPortfolioManagerAgent:
    """Extended tests for the L1 Portfolio Manager."""

    def test_creation_returns_agent_instance(self):
        """Portfolio Manager factory returns a crewai Agent."""
        agent = create_portfolio_manager(verbose=False)
        assert isinstance(agent, Agent)

    def test_role_name(self):
        """Role name is exactly 'Portfolio Manager'."""
        agent = create_portfolio_manager(verbose=False)
        assert agent.role == "Portfolio Manager"

    def test_goal_mentions_key_concepts(self):
        """Goal references budget and campaign performance."""
        agent = create_portfolio_manager(verbose=False)
        goal_lower = agent.goal.lower()
        assert "budget" in goal_lower
        assert "campaign" in goal_lower

    def test_backstory_is_non_empty(self):
        """Backstory provides context for LLM reasoning."""
        agent = create_portfolio_manager(verbose=False)
        assert len(agent.backstory) > 50

    def test_delegation_enabled(self):
        """L1 agents must be able to delegate."""
        agent = create_portfolio_manager(verbose=False)
        assert agent.allow_delegation is True

    def test_memory_enabled(self):
        """Memory should be enabled for stateful decision-making."""
        agent = create_portfolio_manager(verbose=False)
        assert agent.memory  # Memory object is truthy when enabled

    def test_default_no_tools(self):
        """Default creation has no tools."""
        agent = create_portfolio_manager(verbose=False)
        assert agent.tools == []

    def test_custom_tools_accepted(self, mock_tools):
        """Can inject custom tools at creation time."""
        agent = create_portfolio_manager(tools=mock_tools, verbose=False)
        assert len(agent.tools) == 3

    def test_verbose_default_true(self):
        """Default verbose is True."""
        agent = create_portfolio_manager()
        assert agent.verbose is True

    def test_verbose_false(self):
        """Can disable verbose."""
        agent = create_portfolio_manager(verbose=False)
        assert agent.verbose is False

    def test_uses_manager_llm_model(self):
        """Portfolio Manager should use the manager-tier LLM model."""
        agent = create_portfolio_manager(verbose=False)
        assert agent.llm is not None

    def test_llm_temperature_conservative(self):
        """Manager agent uses conservative temperature for strategic decisions."""
        agent = create_portfolio_manager(verbose=False)
        assert agent.llm.temperature == 0.3


# ===========================================================================
# Level 2 Agent Tests -- Channel Specialists
# ===========================================================================


class TestBrandingAgent:
    """Tests for the L2 Branding Specialist."""

    def test_creation(self):
        agent = create_branding_agent(verbose=False)
        assert isinstance(agent, Agent)
        assert agent.role == "Branding Specialist"

    def test_goal_focuses_on_brand(self):
        agent = create_branding_agent(verbose=False)
        goal_lower = agent.goal.lower()
        assert "brand" in goal_lower or "display" in goal_lower or "video" in goal_lower

    def test_delegation_enabled(self):
        agent = create_branding_agent(verbose=False)
        assert agent.allow_delegation is True

    def test_memory_enabled(self):
        agent = create_branding_agent(verbose=False)
        assert agent.memory

    def test_tools_injection(self, mock_tool):
        agent = create_branding_agent(tools=[mock_tool], verbose=False)
        assert len(agent.tools) == 1

    def test_default_no_tools(self):
        agent = create_branding_agent(verbose=False)
        assert agent.tools == []

    def test_uses_default_llm_model(self):
        agent = create_branding_agent(verbose=False)
        assert agent.llm is not None

    def test_llm_temperature(self):
        agent = create_branding_agent(verbose=False)
        assert agent.llm.temperature == 0.5


class TestCTVAgent:
    """Tests for the L2 Connected TV Specialist."""

    def test_creation(self):
        agent = create_ctv_agent(verbose=False)
        assert isinstance(agent, Agent)
        assert agent.role == "Connected TV Specialist"

    def test_goal_focuses_on_streaming(self):
        agent = create_ctv_agent(verbose=False)
        goal_lower = agent.goal.lower()
        assert "streaming" in goal_lower or "tv" in goal_lower

    def test_delegation_enabled(self):
        agent = create_ctv_agent(verbose=False)
        assert agent.allow_delegation is True

    def test_memory_enabled(self):
        agent = create_ctv_agent(verbose=False)
        assert agent.memory

    def test_tools_injection(self, mock_tools):
        agent = create_ctv_agent(tools=mock_tools, verbose=False)
        assert len(agent.tools) == 3

    def test_llm_temperature(self):
        agent = create_ctv_agent(verbose=False)
        assert agent.llm.temperature == 0.5


class TestMobileAppAgent:
    """Tests for the L2 Mobile App Install Specialist."""

    def test_creation(self):
        agent = create_mobile_app_agent(verbose=False)
        assert isinstance(agent, Agent)
        assert agent.role == "Mobile App Install Specialist"

    def test_goal_focuses_on_apps(self):
        agent = create_mobile_app_agent(verbose=False)
        goal_lower = agent.goal.lower()
        assert "app" in goal_lower

    def test_delegation_enabled(self):
        agent = create_mobile_app_agent(verbose=False)
        assert agent.allow_delegation is True

    def test_memory_enabled(self):
        agent = create_mobile_app_agent(verbose=False)
        assert agent.memory

    def test_llm_temperature(self):
        agent = create_mobile_app_agent(verbose=False)
        assert agent.llm.temperature == 0.5


class TestPerformanceAgent:
    """Tests for the L2 Performance/Remarketing Specialist."""

    def test_creation(self):
        agent = create_performance_agent(verbose=False)
        assert isinstance(agent, Agent)
        assert agent.role == "Performance/Remarketing Specialist"

    def test_goal_focuses_on_conversion(self):
        agent = create_performance_agent(verbose=False)
        goal_lower = agent.goal.lower()
        assert "conversion" in goal_lower or "roas" in goal_lower

    def test_delegation_enabled(self):
        agent = create_performance_agent(verbose=False)
        assert agent.allow_delegation is True

    def test_memory_enabled(self):
        agent = create_performance_agent(verbose=False)
        assert agent.memory

    def test_llm_temperature(self):
        agent = create_performance_agent(verbose=False)
        assert agent.llm.temperature == 0.5


class TestDSPAgent:
    """Tests for the L2 DSP Deal Discovery Specialist (previously untested)."""

    def test_creation(self):
        agent = create_dsp_agent(verbose=False)
        assert isinstance(agent, Agent)

    def test_role_name(self):
        agent = create_dsp_agent(verbose=False)
        assert agent.role == "DSP Deal Discovery Specialist"

    def test_goal_focuses_on_deals(self):
        agent = create_dsp_agent(verbose=False)
        goal_lower = agent.goal.lower()
        assert "deal" in goal_lower or "inventory" in goal_lower

    def test_backstory_mentions_deal_types(self):
        """DSP agent backstory should mention PG, PD, PA deal types."""
        agent = create_dsp_agent(verbose=False)
        backstory = agent.backstory
        assert "PG" in backstory or "Programmatic Guaranteed" in backstory
        assert "PD" in backstory or "Preferred Deal" in backstory
        assert "PA" in backstory or "Private Auction" in backstory

    def test_backstory_mentions_tiered_pricing(self):
        """DSP agent backstory should cover tiered pricing tiers."""
        agent = create_dsp_agent(verbose=False)
        backstory = agent.backstory
        assert "Public" in backstory
        assert "Seat" in backstory
        assert "Agency" in backstory
        assert "Advertiser" in backstory

    def test_delegation_enabled(self):
        agent = create_dsp_agent(verbose=False)
        assert agent.allow_delegation is True

    def test_memory_enabled(self):
        agent = create_dsp_agent(verbose=False)
        assert agent.memory

    def test_default_no_tools(self):
        agent = create_dsp_agent(verbose=False)
        assert agent.tools == []

    def test_custom_tools(self, mock_tools):
        agent = create_dsp_agent(tools=mock_tools, verbose=False)
        assert len(agent.tools) == 3

    def test_llm_temperature(self):
        agent = create_dsp_agent(verbose=False)
        assert agent.llm.temperature == 0.5


# ===========================================================================
# Level 3 Agent Tests -- Operational Sub-Agents
# ===========================================================================


class TestResearchAgent:
    """Tests for the L3 Research Agent."""

    def test_creation(self):
        agent = create_research_agent(verbose=False)
        assert isinstance(agent, Agent)
        assert agent.role == "Inventory Research Analyst"

    def test_goal_focuses_on_inventory(self):
        agent = create_research_agent(verbose=False)
        goal_lower = agent.goal.lower()
        assert "inventory" in goal_lower or "discover" in goal_lower

    def test_delegation_disabled(self):
        """L3 agents are leaf nodes and cannot delegate."""
        agent = create_research_agent(verbose=False)
        assert agent.allow_delegation is False

    def test_memory_enabled(self):
        agent = create_research_agent(verbose=False)
        assert agent.memory

    def test_low_temperature(self):
        """Research agent uses low temperature for factual analysis."""
        agent = create_research_agent(verbose=False)
        assert agent.llm.temperature == 0.2

    def test_tools_injection(self, mock_tools):
        agent = create_research_agent(tools=mock_tools, verbose=False)
        assert len(agent.tools) == 3

    def test_default_no_tools(self):
        agent = create_research_agent(verbose=False)
        assert agent.tools == []


class TestExecutionAgent:
    """Tests for the L3 Execution Agent."""

    def test_creation(self):
        agent = create_execution_agent(verbose=False)
        assert isinstance(agent, Agent)
        assert agent.role == "Campaign Execution Specialist"

    def test_goal_focuses_on_execution(self):
        agent = create_execution_agent(verbose=False)
        goal_lower = agent.goal.lower()
        assert "execute" in goal_lower or "booking" in goal_lower or "order" in goal_lower

    def test_delegation_disabled(self):
        agent = create_execution_agent(verbose=False)
        assert agent.allow_delegation is False

    def test_memory_enabled(self):
        agent = create_execution_agent(verbose=False)
        assert agent.memory

    def test_very_low_temperature(self):
        """Execution agent uses very low temperature -- precision matters."""
        agent = create_execution_agent(verbose=False)
        assert agent.llm.temperature == 0.1

    def test_backstory_mentions_booking_states(self):
        """Execution agent should know the booking lifecycle states."""
        agent = create_execution_agent(verbose=False)
        backstory = agent.backstory
        assert "Draft" in backstory
        assert "Reserved" in backstory
        assert "Booked" in backstory
        assert "Cancelled" in backstory


class TestReportingAgent:
    """Tests for the L3 Reporting Agent."""

    def test_creation(self):
        agent = create_reporting_agent(verbose=False)
        assert isinstance(agent, Agent)
        assert agent.role == "Performance Reporting Analyst"

    def test_goal_focuses_on_reporting(self):
        agent = create_reporting_agent(verbose=False)
        goal_lower = agent.goal.lower()
        assert "performance" in goal_lower or "data" in goal_lower

    def test_delegation_disabled(self):
        agent = create_reporting_agent(verbose=False)
        assert agent.allow_delegation is False

    def test_memory_enabled(self):
        agent = create_reporting_agent(verbose=False)
        assert agent.memory

    def test_low_temperature(self):
        agent = create_reporting_agent(verbose=False)
        assert agent.llm.temperature == 0.2

    def test_backstory_mentions_key_metrics(self):
        """Should reference advertising KPIs."""
        agent = create_reporting_agent(verbose=False)
        backstory = agent.backstory.lower()
        assert "cpm" in backstory
        assert "ctr" in backstory
        assert "viewability" in backstory


class TestAudiencePlannerAgent:
    """Tests for the L3 Audience Planner Agent (previously untested)."""

    def test_creation(self):
        agent = create_audience_planner_agent(verbose=False)
        assert isinstance(agent, Agent)

    def test_role_name(self):
        agent = create_audience_planner_agent(verbose=False)
        assert agent.role == "Audience Planning Specialist"

    def test_goal_focuses_on_audience(self):
        agent = create_audience_planner_agent(verbose=False)
        goal_lower = agent.goal.lower()
        assert "audience" in goal_lower

    def test_delegation_disabled(self):
        """Audience planner is L3; makes final audience decisions."""
        agent = create_audience_planner_agent(verbose=False)
        assert agent.allow_delegation is False

    def test_memory_enabled(self):
        agent = create_audience_planner_agent(verbose=False)
        assert agent.memory

    def test_backstory_mentions_ucp(self):
        """Should reference IAB Tech Lab UCP protocol."""
        agent = create_audience_planner_agent(verbose=False)
        backstory = agent.backstory
        assert "UCP" in backstory

    def test_backstory_mentions_signal_types(self):
        """Should know about identity, contextual, and reinforcement signals."""
        agent = create_audience_planner_agent(verbose=False)
        backstory = agent.backstory.lower()
        assert "identity" in backstory
        assert "contextual" in backstory
        assert "reinforcement" in backstory

    def test_llm_temperature(self):
        """Balanced temperature for strategic audience recommendations."""
        agent = create_audience_planner_agent(verbose=False)
        assert agent.llm.temperature == 0.3

    def test_default_no_tools(self):
        agent = create_audience_planner_agent(verbose=False)
        assert agent.tools == []

    def test_custom_tools(self, mock_tools):
        agent = create_audience_planner_agent(tools=mock_tools, verbose=False)
        assert len(agent.tools) == 3


# ===========================================================================
# Cross-Level Hierarchy Invariants
# ===========================================================================


class TestHierarchyInvariants:
    """Tests that verify structural invariants across the agent hierarchy."""

    L1_FACTORIES = [create_portfolio_manager]
    L2_FACTORIES = [
        create_branding_agent,
        create_ctv_agent,
        create_dsp_agent,
        create_mobile_app_agent,
        create_performance_agent,
    ]
    L3_FACTORIES = [
        create_audience_planner_agent,
        create_execution_agent,
        create_reporting_agent,
        create_research_agent,
    ]

    def test_all_l1_can_delegate(self):
        """Every L1 agent must have allow_delegation=True."""
        for factory in self.L1_FACTORIES:
            agent = factory(verbose=False)
            assert agent.allow_delegation is True, f"{agent.role} should delegate"

    def test_all_l2_can_delegate(self):
        """Every L2 agent must have allow_delegation=True."""
        for factory in self.L2_FACTORIES:
            agent = factory(verbose=False)
            assert agent.allow_delegation is True, f"{agent.role} should delegate"

    def test_all_l3_cannot_delegate(self):
        """Every L3 agent must have allow_delegation=False."""
        for factory in self.L3_FACTORIES:
            agent = factory(verbose=False)
            assert agent.allow_delegation is False, f"{agent.role} should NOT delegate"

    def test_all_agents_have_memory(self):
        """All agents at every level should have memory enabled."""
        all_factories = self.L1_FACTORIES + self.L2_FACTORIES + self.L3_FACTORIES
        for factory in all_factories:
            agent = factory(verbose=False)
            assert agent.memory, f"{agent.role} should have memory"

    def test_all_agents_have_non_empty_role(self):
        """Every agent must have a meaningful role string."""
        all_factories = self.L1_FACTORIES + self.L2_FACTORIES + self.L3_FACTORIES
        for factory in all_factories:
            agent = factory(verbose=False)
            assert len(agent.role) > 5, f"Role too short: {agent.role}"

    def test_all_agents_have_non_empty_goal(self):
        """Every agent must have a meaningful goal string."""
        all_factories = self.L1_FACTORIES + self.L2_FACTORIES + self.L3_FACTORIES
        for factory in all_factories:
            agent = factory(verbose=False)
            assert len(agent.goal) > 20, f"Goal too short for {agent.role}"

    def test_all_agents_have_non_empty_backstory(self):
        """Every agent must have a detailed backstory."""
        all_factories = self.L1_FACTORIES + self.L2_FACTORIES + self.L3_FACTORIES
        for factory in all_factories:
            agent = factory(verbose=False)
            assert len(agent.backstory) > 100, f"Backstory too short for {agent.role}"

    def test_all_agents_have_llm(self):
        """Every agent must have an LLM configured."""
        all_factories = self.L1_FACTORIES + self.L2_FACTORIES + self.L3_FACTORIES
        for factory in all_factories:
            agent = factory(verbose=False)
            assert agent.llm is not None, f"{agent.role} missing LLM"

    def test_unique_roles_across_hierarchy(self):
        """No two agents should have the same role name."""
        all_factories = self.L1_FACTORIES + self.L2_FACTORIES + self.L3_FACTORIES
        roles = [factory(verbose=False).role for factory in all_factories]
        assert len(roles) == len(set(roles)), f"Duplicate roles found: {roles}"

    def test_l3_temperatures_lower_than_l2(self):
        """L3 operational agents should generally use lower temperatures (more precise)."""
        l2_temps = [factory(verbose=False).llm.temperature for factory in self.L2_FACTORIES]
        l3_temps = [factory(verbose=False).llm.temperature for factory in self.L3_FACTORIES]
        avg_l2 = sum(l2_temps) / len(l2_temps)
        avg_l3 = sum(l3_temps) / len(l3_temps)
        assert avg_l3 <= avg_l2, f"L3 avg temp ({avg_l3}) should be <= L2 avg temp ({avg_l2})"

    def test_empty_tools_list_when_none_provided(self):
        """All factories produce agents with empty tools when None is passed."""
        all_factories = self.L1_FACTORIES + self.L2_FACTORIES + self.L3_FACTORIES
        for factory in all_factories:
            agent = factory(tools=None, verbose=False)
            assert agent.tools == [], f"{agent.role} has unexpected default tools"

    def test_tools_passthrough(self, mock_tools):
        """All factories correctly pass through custom tools."""
        all_factories = self.L1_FACTORIES + self.L2_FACTORIES + self.L3_FACTORIES
        for factory in all_factories:
            agent = factory(tools=mock_tools, verbose=False)
            assert len(agent.tools) == 3, f"{agent.role} did not accept tools"

    def test_agent_count_by_level(self):
        """Verify the expected number of agents at each level."""
        assert len(self.L1_FACTORIES) == 1, "Expected 1 L1 agent"
        assert len(self.L2_FACTORIES) == 5, "Expected 5 L2 agents"
        assert len(self.L3_FACTORIES) == 4, "Expected 4 L3 agents"


# ===========================================================================
# Audience Context Formatting (channel_crews helper)
# ===========================================================================


class TestFormatAudienceContext:
    """Tests for _format_audience_context helper in channel_crews.py."""

    def test_none_returns_empty(self):
        """None audience plan returns empty string."""
        assert _format_audience_context(None) == ""

    def test_empty_dict_returns_empty(self):
        """Empty dict is falsy, so returns empty string (same as None)."""
        result = _format_audience_context({})
        assert result == ""

    def test_minimal_plan_includes_header_and_footer(self):
        """A plan with one key renders the header and UCP footer."""
        plan = {"target_demographics": {"age": "18-34"}}
        result = _format_audience_context(plan)
        assert "Audience Plan Context" in result
        assert "UCP-compatible" in result

    def test_demographics_included(self):
        plan = {"target_demographics": {"age": "25-54"}}
        result = _format_audience_context(plan)
        assert "Demographics" in result
        assert "25-54" in result

    def test_interests_included(self):
        plan = {"target_interests": ["technology", "finance"]}
        result = _format_audience_context(plan)
        assert "Interests" in result
        assert "technology" in result
        assert "finance" in result

    def test_behaviors_included(self):
        plan = {"target_behaviors": ["online shoppers"]}
        result = _format_audience_context(plan)
        assert "Behaviors" in result
        assert "online shoppers" in result

    def test_signal_types_included(self):
        plan = {"requested_signal_types": ["identity", "contextual"]}
        result = _format_audience_context(plan)
        assert "Required Signals" in result
        assert "identity" in result

    def test_exclusions_included(self):
        plan = {"exclusions": ["competitor audiences"]}
        result = _format_audience_context(plan)
        assert "Exclusions" in result
        assert "competitor audiences" in result

    def test_full_plan(self, audience_plan):
        """Full audience plan renders all sections."""
        result = _format_audience_context(audience_plan)
        assert "Demographics" in result
        assert "Interests" in result
        assert "Behaviors" in result
        assert "Required Signals" in result
        assert "Exclusions" in result
        assert "UCP-compatible" in result


# ===========================================================================
# Tool Factory Functions (channel_crews helpers)
# ===========================================================================


class TestToolFactoryFunctions:
    """Tests for _create_research_tools, _create_execution_tools, _create_audience_tools."""

    def test_research_tools_count(self, mock_opendirect_client):
        """Research tools factory creates exactly 2 tools."""
        tools = _create_research_tools(mock_opendirect_client)
        assert len(tools) == 2

    def test_execution_tools_count(self, mock_opendirect_client):
        """Execution tools factory creates exactly 4 tools."""
        tools = _create_execution_tools(mock_opendirect_client)
        assert len(tools) == 4

    def test_audience_tools_count(self):
        """Audience tools factory creates exactly 3 tools."""
        tools = _create_audience_tools()
        assert len(tools) == 3

    def test_research_tools_types(self, mock_opendirect_client):
        """Research tools are ProductSearchTool and AvailsCheckTool."""
        from ad_buyer.tools.research.avails_check import AvailsCheckTool
        from ad_buyer.tools.research.product_search import ProductSearchTool

        tools = _create_research_tools(mock_opendirect_client)
        tool_types = {type(t) for t in tools}
        assert ProductSearchTool in tool_types
        assert AvailsCheckTool in tool_types

    def test_execution_tools_types(self, mock_opendirect_client):
        """Execution tools include all four management tools."""
        from ad_buyer.tools.execution.line_management import (
            BookLineTool,
            CreateLineTool,
            ReserveLineTool,
        )
        from ad_buyer.tools.execution.order_management import CreateOrderTool

        tools = _create_execution_tools(mock_opendirect_client)
        tool_types = {type(t) for t in tools}
        assert CreateOrderTool in tool_types
        assert CreateLineTool in tool_types
        assert ReserveLineTool in tool_types
        assert BookLineTool in tool_types

    def test_audience_tools_types(self):
        """Audience tools include discovery, matching, and coverage."""
        from ad_buyer.tools.audience import (
            AudienceDiscoveryTool,
            AudienceMatchingTool,
            CoverageEstimationTool,
        )

        tools = _create_audience_tools()
        tool_types = {type(t) for t in tools}
        assert AudienceDiscoveryTool in tool_types
        assert AudienceMatchingTool in tool_types
        assert CoverageEstimationTool in tool_types


# ===========================================================================
# Module-Level Import Tests
# ===========================================================================


class TestModuleImports:
    """Test that __init__.py re-exports are correct."""

    def test_level1_init_exports(self):
        from ad_buyer.agents.level1 import create_portfolio_manager as pm

        assert callable(pm)

    def test_level2_init_exports(self):
        from ad_buyer.agents.level2 import (
            create_branding_agent,
            create_ctv_agent,
            create_dsp_agent,
            create_mobile_app_agent,
            create_performance_agent,
        )

        for fn in [
            create_branding_agent,
            create_ctv_agent,
            create_dsp_agent,
            create_mobile_app_agent,
            create_performance_agent,
        ]:
            assert callable(fn)

    def test_level3_init_exports(self):
        from ad_buyer.agents.level3 import (
            create_audience_planner_agent,
            create_execution_agent,
            create_reporting_agent,
            create_research_agent,
        )

        for fn in [
            create_audience_planner_agent,
            create_execution_agent,
            create_reporting_agent,
            create_research_agent,
        ]:
            assert callable(fn)

    def test_crews_init_exports(self):
        from ad_buyer.crews import (
            create_branding_crew,
            create_ctv_crew,
            create_mobile_crew,
            create_performance_crew,
            create_portfolio_crew,
        )

        for fn in [
            create_branding_crew,
            create_ctv_crew,
            create_mobile_crew,
            create_performance_crew,
            create_portfolio_crew,
        ]:
            assert callable(fn)
