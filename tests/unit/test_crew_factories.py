# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for crew factory functions.

Verifies that crew factories correctly configure hierarchical crews,
specifically that manager_agent is NOT included in the agents list
(crewai rejects this with a ValidationError in newer versions).
"""

import os
import pytest
from unittest.mock import MagicMock

# Set a dummy API key for tests (agents validate on creation)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

from ad_buyer.crews.portfolio_crew import create_portfolio_crew
from ad_buyer.crews.channel_crews import (
    create_branding_crew,
    create_ctv_crew,
    create_mobile_crew,
    create_performance_crew,
)


@pytest.fixture
def mock_client():
    """Create a mock OpenDirect client."""
    return MagicMock()


@pytest.fixture
def channel_brief():
    """Minimal channel brief for crew creation tests."""
    return {
        "budget": 10000,
        "start_date": "2025-03-01",
        "end_date": "2025-03-31",
        "target_audience": {"age": "25-54"},
        "objectives": ["awareness"],
        "kpis": {"cpa": 10},
    }


@pytest.fixture
def campaign_brief():
    """Minimal campaign brief for portfolio crew tests."""
    return {
        "name": "Test Campaign",
        "objectives": ["awareness"],
        "budget": 50000,
        "start_date": "2025-03-01",
        "end_date": "2025-03-31",
        "target_audience": {"age": "25-54"},
        "kpis": {"viewability": 70},
    }


class TestCrewManagerAgentNotInAgentsList:
    """Verify manager_agent is excluded from agents list in all crews.

    crewai >= recent versions raises ValidationError if the manager_agent
    is also present in the agents=[] list. The manager_agent should only
    be passed via the manager_agent parameter.
    """

    def test_portfolio_crew_manager_not_in_agents(self, mock_client, campaign_brief):
        """Portfolio crew: portfolio_manager should not be in agents list."""
        crew = create_portfolio_crew(mock_client, campaign_brief)
        assert crew.manager_agent is not None
        assert crew.manager_agent not in crew.agents, (
            "manager_agent (portfolio_manager) must not be in crew.agents list"
        )

    def test_branding_crew_manager_not_in_agents(self, mock_client, channel_brief):
        """Branding crew: branding_agent should not be in agents list."""
        crew = create_branding_crew(mock_client, channel_brief)
        assert crew.manager_agent is not None
        assert crew.manager_agent not in crew.agents, (
            "manager_agent (branding_agent) must not be in crew.agents list"
        )

    def test_mobile_crew_manager_not_in_agents(self, mock_client, channel_brief):
        """Mobile crew: mobile_agent should not be in agents list."""
        crew = create_mobile_crew(mock_client, channel_brief)
        assert crew.manager_agent is not None
        assert crew.manager_agent not in crew.agents, (
            "manager_agent (mobile_agent) must not be in crew.agents list"
        )

    def test_ctv_crew_manager_not_in_agents(self, mock_client, channel_brief):
        """CTV crew: ctv_agent should not be in agents list."""
        crew = create_ctv_crew(mock_client, channel_brief)
        assert crew.manager_agent is not None
        assert crew.manager_agent not in crew.agents, (
            "manager_agent (ctv_agent) must not be in crew.agents list"
        )

    def test_performance_crew_manager_not_in_agents(self, mock_client, channel_brief):
        """Performance crew: performance_agent should not be in agents list."""
        crew = create_performance_crew(mock_client, channel_brief)
        assert crew.manager_agent is not None
        assert crew.manager_agent not in crew.agents, (
            "manager_agent (performance_agent) must not be in crew.agents list"
        )


class TestCrewStructure:
    """Verify basic crew structure is correct after fix."""

    def test_portfolio_crew_has_correct_agent_count(self, mock_client, campaign_brief):
        """Portfolio crew should have 4 agents (not 5) after removing manager."""
        crew = create_portfolio_crew(mock_client, campaign_brief)
        # 4 channel specialists, manager is separate
        assert len(crew.agents) == 4

    def test_branding_crew_has_correct_agent_count(self, mock_client, channel_brief):
        """Branding crew should have 2 agents (not 3) after removing manager."""
        crew = create_branding_crew(mock_client, channel_brief)
        # research_agent + execution_agent, branding_agent is manager
        assert len(crew.agents) == 2

    def test_mobile_crew_has_correct_agent_count(self, mock_client, channel_brief):
        """Mobile crew should have 2 agents after removing manager."""
        crew = create_mobile_crew(mock_client, channel_brief)
        assert len(crew.agents) == 2

    def test_ctv_crew_has_correct_agent_count(self, mock_client, channel_brief):
        """CTV crew should have 2 agents after removing manager."""
        crew = create_ctv_crew(mock_client, channel_brief)
        assert len(crew.agents) == 2

    def test_performance_crew_has_correct_agent_count(self, mock_client, channel_brief):
        """Performance crew should have 2 agents after removing manager."""
        crew = create_performance_crew(mock_client, channel_brief)
        assert len(crew.agents) == 2

    def test_all_crews_use_hierarchical_process(self, mock_client, campaign_brief, channel_brief):
        """All crews should use hierarchical process."""
        from crewai import Process

        crews = [
            create_portfolio_crew(mock_client, campaign_brief),
            create_branding_crew(mock_client, channel_brief),
            create_mobile_crew(mock_client, channel_brief),
            create_ctv_crew(mock_client, channel_brief),
            create_performance_crew(mock_client, channel_brief),
        ]
        for crew in crews:
            assert crew.process == Process.hierarchical
