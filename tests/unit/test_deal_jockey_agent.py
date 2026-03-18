# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for DealJockey L2 agent creation."""

import os
import pytest
from unittest.mock import MagicMock

from crewai.tools import BaseTool
from pydantic import Field

# Set a dummy API key for tests (agents validate on creation)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

from ad_buyer.agents.level2.deal_jockey_agent import create_deal_jockey_agent
from ad_buyer.agents.level2 import create_deal_jockey_agent as imported_from_package


class _MockTool(BaseTool):
    """A minimal mock tool for testing."""

    name: str = "mock_tool"
    description: str = "A mock tool for testing"

    def _run(self, **kwargs):
        return "mock result"


class TestDealJockeyAgent:
    """Tests for the DealJockey L2 agent."""

    def test_deal_jockey_agent_creation(self):
        """Test DealJockey agent can be created with default args."""
        agent = create_deal_jockey_agent(verbose=False)

        assert agent is not None
        assert agent.role == "Deal Jockey - Portfolio Manager"

    def test_deal_jockey_agent_goal_keywords(self):
        """Test DealJockey agent goal contains portfolio management keywords."""
        agent = create_deal_jockey_agent(verbose=False)
        goal_lower = agent.goal.lower()

        assert "portfolio" in goal_lower or "deal" in goal_lower
        assert "import" in goal_lower or "catalog" in goal_lower or "manage" in goal_lower

    def test_deal_jockey_agent_backstory_keywords(self):
        """Test DealJockey agent backstory contains expected expertise areas."""
        agent = create_deal_jockey_agent(verbose=False)
        backstory_lower = agent.backstory.lower()

        assert "portfolio" in backstory_lower
        assert "csv" in backstory_lower or "import" in backstory_lower
        assert "migration" in backstory_lower or "migrate" in backstory_lower

    def test_deal_jockey_agent_can_delegate(self):
        """Test DealJockey agent can delegate (L2 agents delegate to L3)."""
        agent = create_deal_jockey_agent(verbose=False)
        assert agent.allow_delegation is True

    def test_deal_jockey_agent_has_memory(self):
        """Test DealJockey agent has memory enabled."""
        agent = create_deal_jockey_agent(verbose=False)
        # CrewAI converts memory=True into a Memory object
        assert agent.memory is not None

    def test_deal_jockey_agent_with_no_tools(self):
        """Test DealJockey agent starts with no tools by default."""
        agent = create_deal_jockey_agent(verbose=False)
        assert len(agent.tools) == 0

    def test_deal_jockey_agent_with_custom_tools(self):
        """Test DealJockey agent can be created with custom tools."""
        mock_tools = [_MockTool(), _MockTool(name="mock_tool_2")]
        agent = create_deal_jockey_agent(tools=mock_tools, verbose=False)
        assert len(agent.tools) == 2

    def test_deal_jockey_importable_from_level2_package(self):
        """Test DealJockey agent factory is importable from level2 package."""
        assert imported_from_package is create_deal_jockey_agent
