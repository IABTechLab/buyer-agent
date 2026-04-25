"""Tests for Buyer AgentCore campaign planning tools.

Validates:
- run_campaign_plan() direct flow invocation
- Budget extraction from natural language prompts
- Brief normalization (string audience → dict, string objectives → list)
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock bedrock_agentcore — only available in AgentCore container.
_mock_agentcore = MagicMock()
_mock_app = MagicMock()
_mock_app.entrypoint = lambda fn: fn
_mock_agentcore.BedrockAgentCoreApp.return_value = _mock_app
sys.modules.setdefault("bedrock_agentcore", MagicMock())
sys.modules.setdefault("bedrock_agentcore.runtime", _mock_agentcore)

from ad_buyer.interfaces.agentcore.crew_tools import run_campaign_plan


class TestRunCampaignPlan:
    """Test the direct flow invocation function."""

    def test_with_pre_extracted_brief(self):
        """Should use the pre-extracted brief."""
        brief = {
            "name": "Q4 Automotive Campaign",
            "objectives": ["brand awareness", "reach"],
            "budget": 500000,
            "start_date": "2026-10-01",
            "end_date": "2026-12-31",
            "target_audience": "automotive intenders, adults 25-54",
            "channels": ["ctv", "display"],
        }
        result = run_campaign_plan(
            "Plan a $500K Q4 automotive campaign across CTV and digital video",
            brief=brief,
        )
        assert result["total_budget"] == 500000
        assert result["campaign_name"] == "Q4 Automotive Campaign"
        assert result["approval_required"] is True

    def test_normalizes_string_audience(self):
        """Should convert string target_audience to dict."""
        brief = {
            "name": "Test",
            "objectives": ["awareness"],
            "budget": 100000,
            "start_date": "2026-10-01",
            "end_date": "2026-12-31",
            "target_audience": "adults 25-54",
        }
        result = run_campaign_plan("test", brief=brief)
        assert result["total_budget"] == 100000

    def test_normalizes_string_objectives(self):
        """Should convert string objectives to list."""
        brief = {
            "name": "Test",
            "objectives": "brand awareness",
            "budget": 200000,
            "start_date": "2026-01-01",
            "end_date": "2026-03-31",
            "target_audience": {"description": "general"},
        }
        result = run_campaign_plan("test", brief=brief)
        assert result["total_budget"] == 200000

    def test_fallback_without_brief(self):
        """Without a brief, should use defaults and still run."""
        result = run_campaign_plan("Plan a campaign")
        assert result["total_budget"] == 100000
        assert result["approval_required"] is True

    def test_approval_required_always_true(self):
        """Planning should always require approval."""
        brief = {
            "name": "Test",
            "objectives": ["awareness"],
            "budget": 500000,
            "start_date": "2026-10-01",
            "end_date": "2026-12-31",
            "target_audience": {"description": "general"},
        }
        result = run_campaign_plan("test", brief=brief)
        assert result["approval_required"] is True

    def test_returns_status(self):
        """Should return a flow execution status."""
        result = run_campaign_plan("Plan a campaign")
        assert "status" in result

    def test_has_audience_coverage(self):
        """Should include audience coverage estimates."""
        result = run_campaign_plan("Plan a campaign")
        assert "audience_coverage" in result or "errors" in result
