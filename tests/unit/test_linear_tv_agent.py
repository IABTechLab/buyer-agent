# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the Linear TV Specialist agent (Level 2).

Follows the same pattern as test_agents.py for consistency.
Tests written first (TDD) per bead buyer-6io.
"""

import os

# Set a dummy API key for tests (agents validate on creation)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

from ad_buyer.agents.level2.linear_tv_agent import create_linear_tv_agent


class TestLinearTVAgent:
    """Tests for the Linear TV Specialist agent."""

    def test_linear_tv_agent_creation(self):
        """Test Linear TV Specialist agent creation."""
        agent = create_linear_tv_agent(verbose=False)

        assert agent.role == "Linear TV Specialist"
        # Goal should mention linear TV concepts
        goal_lower = agent.goal.lower()
        assert "linear" in goal_lower or "tv" in goal_lower
        assert agent.allow_delegation is True

    def test_linear_tv_agent_with_no_tools(self):
        """Test Linear TV agent starts with no tools by default."""
        agent = create_linear_tv_agent(verbose=False)
        assert len(agent.tools) == 0

    def test_linear_tv_agent_with_custom_tools(self):
        """Test Linear TV agent accepts custom tools."""
        from crewai.tools import BaseTool

        class DummyTool(BaseTool):
            name: str = "dummy"
            description: str = "A dummy tool"

            def _run(self, **kwargs):
                return "ok"

        mock_tools = [DummyTool(), DummyTool()]
        agent = create_linear_tv_agent(tools=mock_tools, verbose=False)
        assert len(agent.tools) == 2

    def test_linear_tv_agent_backstory_covers_key_concepts(self):
        """Agent backstory should cover key linear TV concepts."""
        agent = create_linear_tv_agent(verbose=False)
        backstory_lower = agent.backstory.lower()

        # Should mention core linear TV concepts
        assert "daypart" in backstory_lower
        assert "grp" in backstory_lower or "gross rating point" in backstory_lower
        assert "cpp" in backstory_lower or "cost per point" in backstory_lower
        assert "scatter" in backstory_lower
        assert "makegood" in backstory_lower or "make good" in backstory_lower
        assert "dma" in backstory_lower or "designated market" in backstory_lower
        assert "nielsen" in backstory_lower

    def test_linear_tv_agent_is_level2(self):
        """Linear TV agent should be Level 2 (can delegate)."""
        agent = create_linear_tv_agent(verbose=False)
        assert agent.allow_delegation is True

    def test_linear_tv_agent_has_memory(self):
        """Linear TV agent should have memory enabled."""
        agent = create_linear_tv_agent(verbose=False)
        # crewai converts memory=True to a Memory object
        assert agent.memory is not None

    def test_linear_tv_agent_in_level2_init(self):
        """Linear TV agent should be importable from level2 package."""
        from ad_buyer.agents.level2 import create_linear_tv_agent as imported_fn

        assert imported_fn is create_linear_tv_agent
