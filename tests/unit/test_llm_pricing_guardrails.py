# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for CPM hallucination fix -- Layer 3: LLM prompt guardrails.

Bead: ar-8opr (child of epic ar-rrgw)

These tests verify that all agent backstories contain pricing discipline
language preventing LLM hallucination of CPM values, and that the
channel_crews expected_output template allows null CPM.
"""

import os

# Set a dummy API key for tests (agents validate on creation)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

import pytest

from ad_buyer.agents.level1.portfolio_manager import create_portfolio_manager
from ad_buyer.agents.level2.branding_agent import create_branding_agent
from ad_buyer.agents.level2.ctv_agent import create_ctv_agent
from ad_buyer.agents.level2.deal_library_agent import create_deal_library_agent
from ad_buyer.agents.level2.dsp_agent import create_dsp_agent
from ad_buyer.agents.level2.linear_tv_agent import create_linear_tv_agent
from ad_buyer.agents.level2.mobile_app_agent import create_mobile_app_agent
from ad_buyer.agents.level2.performance_agent import create_performance_agent
from ad_buyer.agents.level3.audience_planner_agent import create_audience_planner_agent
from ad_buyer.agents.level3.execution_agent import create_execution_agent
from ad_buyer.agents.level3.reporting_agent import create_reporting_agent
from ad_buyer.agents.level3.research_agent import create_research_agent


# ---------------------------------------------------------------------------
# All agent factory functions, for parametrized testing
# ---------------------------------------------------------------------------

ALL_AGENT_FACTORIES = [
    ("portfolio_manager", create_portfolio_manager),
    ("branding_agent", create_branding_agent),
    ("ctv_agent", create_ctv_agent),
    ("deal_library_agent", create_deal_library_agent),
    ("dsp_agent", create_dsp_agent),
    ("linear_tv_agent", create_linear_tv_agent),
    ("mobile_app_agent", create_mobile_app_agent),
    ("performance_agent", create_performance_agent),
    ("audience_planner_agent", create_audience_planner_agent),
    ("execution_agent", create_execution_agent),
    ("reporting_agent", create_reporting_agent),
    ("research_agent", create_research_agent),
]


# ---------------------------------------------------------------------------
# 1. Agent backstory pricing discipline tests
# ---------------------------------------------------------------------------


class TestAgentPricingDiscipline:
    """Verify all agent backstories contain pricing discipline guardrails.

    Every agent in the buyer system must include explicit instructions
    to never fabricate CPM pricing. This prevents the LLM from filling
    in pricing values from its training data when sellers have not
    provided them.
    """

    @pytest.mark.parametrize(
        "agent_name,factory",
        ALL_AGENT_FACTORIES,
        ids=[name for name, _ in ALL_AGENT_FACTORIES],
    )
    def test_backstory_contains_never_fabricate(self, agent_name, factory):
        """Each agent backstory must contain 'NEVER' and 'fabricate' keywords."""
        agent = factory(verbose=False)
        backstory = agent.backstory.lower()

        assert "never" in backstory, (
            f"{agent_name} backstory missing 'NEVER' pricing discipline keyword"
        )
        assert "fabricate" in backstory, (
            f"{agent_name} backstory missing 'fabricate' pricing discipline keyword"
        )

    @pytest.mark.parametrize(
        "agent_name,factory",
        ALL_AGENT_FACTORIES,
        ids=[name for name, _ in ALL_AGENT_FACTORIES],
    )
    def test_backstory_contains_pricing_negotiation_fallback(self, agent_name, factory):
        """Each agent backstory must instruct to state pricing requires negotiation."""
        agent = factory(verbose=False)
        backstory = agent.backstory.lower()

        assert "negotiation" in backstory, (
            f"{agent_name} backstory missing negotiation fallback instruction"
        )

    @pytest.mark.parametrize(
        "agent_name,factory",
        ALL_AGENT_FACTORIES,
        ids=[name for name, _ in ALL_AGENT_FACTORIES],
    )
    def test_backstory_prohibits_market_knowledge_pricing(self, agent_name, factory):
        """Each agent backstory must prohibit using market knowledge for pricing."""
        agent = factory(verbose=False)
        backstory = agent.backstory.lower()

        assert "training data" in backstory, (
            f"{agent_name} backstory missing prohibition on using training data for pricing"
        )


# ---------------------------------------------------------------------------
# 2. channel_crews expected_output template tests
# ---------------------------------------------------------------------------


class TestChannelCrewsExpectedOutput:
    """Verify the channel_crews expected_output template allows null CPM."""

    def test_branding_research_task_allows_null_cpm(self):
        """The branding research task expected_output must allow null cpm."""
        from unittest.mock import MagicMock

        from ad_buyer.crews.channel_crews import create_branding_crew

        client = MagicMock()
        brief = {
            "budget": 10000,
            "start_date": "2025-03-01",
            "end_date": "2025-03-31",
            "target_audience": {"age": "25-54"},
            "objectives": ["awareness"],
        }

        crew = create_branding_crew(client, brief)

        # Find the research task (first task)
        research_task = crew.tasks[0]
        expected = research_task.expected_output.lower()

        # Must allow null cpm
        assert "null" in expected, (
            "Branding research task expected_output must show cpm can be null"
        )
        # Must contain the NEVER estimate instruction
        assert "never" in expected, (
            "Branding research task expected_output must say NEVER estimate CPM"
        )
