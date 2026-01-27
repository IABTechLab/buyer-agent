# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for agent creation."""

import os
import pytest
from unittest.mock import patch, MagicMock

# Set a dummy API key for tests (agents validate on creation)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

from ad_buyer.agents.level1.portfolio_manager import create_portfolio_manager
from ad_buyer.agents.level2.branding_agent import create_branding_agent
from ad_buyer.agents.level2.mobile_app_agent import create_mobile_app_agent
from ad_buyer.agents.level2.ctv_agent import create_ctv_agent
from ad_buyer.agents.level2.performance_agent import create_performance_agent
from ad_buyer.agents.level3.research_agent import create_research_agent
from ad_buyer.agents.level3.execution_agent import create_execution_agent
from ad_buyer.agents.level3.reporting_agent import create_reporting_agent


class TestLevel1Agents:
    """Tests for Level 1 (orchestrator) agents."""

    def test_portfolio_manager_creation(self):
        """Test Portfolio Manager agent creation."""
        agent = create_portfolio_manager(verbose=False)

        assert agent.role == "Portfolio Manager"
        assert "budget" in agent.goal.lower()
        assert agent.allow_delegation is True

    def test_portfolio_manager_with_no_tools(self):
        """Test Portfolio Manager starts with no tools by default."""
        agent = create_portfolio_manager(verbose=False)
        assert len(agent.tools) == 0


class TestLevel2Agents:
    """Tests for Level 2 (channel specialist) agents."""

    def test_branding_agent_creation(self):
        """Test Branding Specialist agent creation."""
        agent = create_branding_agent(verbose=False)

        assert agent.role == "Branding Specialist"
        assert "display" in agent.goal.lower() or "video" in agent.goal.lower()
        assert agent.allow_delegation is True

    def test_mobile_app_agent_creation(self):
        """Test Mobile App Install Specialist agent creation."""
        agent = create_mobile_app_agent(verbose=False)

        assert agent.role == "Mobile App Install Specialist"
        assert "app" in agent.goal.lower()
        assert agent.allow_delegation is True

    def test_ctv_agent_creation(self):
        """Test CTV Specialist agent creation."""
        agent = create_ctv_agent(verbose=False)

        assert agent.role == "Connected TV Specialist"
        assert "streaming" in agent.goal.lower() or "tv" in agent.goal.lower()
        assert agent.allow_delegation is True

    def test_performance_agent_creation(self):
        """Test Performance/Remarketing Specialist agent creation."""
        agent = create_performance_agent(verbose=False)

        assert agent.role == "Performance/Remarketing Specialist"
        assert "conversion" in agent.goal.lower() or "roas" in agent.goal.lower()
        assert agent.allow_delegation is True


class TestLevel3Agents:
    """Tests for Level 3 (operational) agents."""

    def test_research_agent_creation(self):
        """Test Research Agent creation."""
        agent = create_research_agent(verbose=False)

        assert agent.role == "Inventory Research Analyst"
        assert "discover" in agent.goal.lower() or "inventory" in agent.goal.lower()
        assert agent.allow_delegation is False  # Leaf agent

    def test_execution_agent_creation(self):
        """Test Execution Agent creation."""
        agent = create_execution_agent(verbose=False)

        assert agent.role == "Campaign Execution Specialist"
        assert "execute" in agent.goal.lower() or "booking" in agent.goal.lower()
        assert agent.allow_delegation is False  # Leaf agent

    def test_reporting_agent_creation(self):
        """Test Reporting Agent creation."""
        agent = create_reporting_agent(verbose=False)

        assert agent.role == "Performance Reporting Analyst"
        assert "performance" in agent.goal.lower() or "data" in agent.goal.lower()
        assert agent.allow_delegation is False  # Leaf agent

    def test_research_agent_with_no_tools(self):
        """Test Research Agent starts with no tools by default."""
        agent = create_research_agent(verbose=False)
        assert len(agent.tools) == 0


class TestAgentHierarchy:
    """Tests for agent hierarchy and delegation settings."""

    def test_level1_can_delegate(self):
        """Test Level 1 agents can delegate."""
        agent = create_portfolio_manager(verbose=False)
        assert agent.allow_delegation is True

    def test_level2_can_delegate(self):
        """Test Level 2 agents can delegate."""
        agents = [
            create_branding_agent(verbose=False),
            create_mobile_app_agent(verbose=False),
            create_ctv_agent(verbose=False),
            create_performance_agent(verbose=False),
        ]
        for agent in agents:
            assert agent.allow_delegation is True

    def test_level3_cannot_delegate(self):
        """Test Level 3 agents cannot delegate (leaf nodes)."""
        agents = [
            create_research_agent(verbose=False),
            create_execution_agent(verbose=False),
            create_reporting_agent(verbose=False),
        ]
        for agent in agents:
            assert agent.allow_delegation is False
