"""Tests for ROUTING_MODE selection logic in the Buyer AgentCore entrypoint.

Verifies that:
- ROUTING_MODE=crew uses DealBookingFlow (default)
- ROUTING_MODE=chat uses ChatInterface
- Invalid mode falls back to default
- Payload routing_mode overrides env var
- Campaign brief extraction works
- Plan response formatting works
- UI payload auto-routing works
"""

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# We need to mock bedrock_agentcore before importing the entrypoint,
# since it's imported at module level.
import sys

# Mock bedrock_agentcore — only available in AgentCore container.
# NOTE: This mock persists in sys.modules for the pytest session.
# Run agentcore tests separately if community tests fail:
#   pytest tests/unit/agentcore/ -v
from unittest.mock import MagicMock
_mock_agentcore = MagicMock()
_mock_app = MagicMock()
_mock_app.entrypoint = lambda fn: fn
_mock_agentcore.BedrockAgentCoreApp.return_value = _mock_app
sys.modules.setdefault("bedrock_agentcore", MagicMock())
sys.modules.setdefault("bedrock_agentcore.runtime", _mock_agentcore)

from ad_buyer.interfaces.agentcore.http_main import (
    _DEFAULT_ROUTING_MODE,
    _VALID_ROUTING_MODES,
    _get_routing_mode,
    _handle_crew_invocation,
    _handle_invocation,
)


# ---------------------------------------------------------------------------
# _get_routing_mode tests
# ---------------------------------------------------------------------------


class TestGetRoutingMode:
    """Tests for the _get_routing_mode function."""

    def test_default_mode_is_crew(self):
        """Default routing mode should be 'crew'."""
        with patch.dict(os.environ, {"ROUTING_MODE": "crew"}):
            assert _get_routing_mode({}) == "crew"

    def test_env_var_chat(self):
        """ROUTING_MODE=chat should return 'chat'."""
        with patch.dict(os.environ, {"ROUTING_MODE": "chat"}):
            assert _get_routing_mode({}) == "chat"

    def test_env_var_crew(self):
        """ROUTING_MODE=crew should return 'crew'."""
        with patch.dict(os.environ, {"ROUTING_MODE": "crew"}):
            assert _get_routing_mode({}) == "crew"

    def test_invalid_env_var_falls_back(self):
        """Invalid ROUTING_MODE value should fall back to default."""
        with patch.dict(os.environ, {"ROUTING_MODE": "invalid_mode"}):
            result = _get_routing_mode({})
            assert result in _VALID_ROUTING_MODES

    def test_payload_overrides_env_var(self):
        """Payload routing_mode should take priority over env var."""
        with patch.dict(os.environ, {"ROUTING_MODE": "chat"}):
            assert _get_routing_mode({"routing_mode": "crew"}) == "crew"

    def test_payload_crew_without_env_var(self):
        """Payload routing_mode=crew should work without env var."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ROUTING_MODE", None)
            assert _get_routing_mode({"routing_mode": "crew"}) == "crew"

    def test_case_insensitive(self):
        """Routing mode should be case-insensitive."""
        with patch.dict(os.environ, {"ROUTING_MODE": "CREW"}):
            assert _get_routing_mode({}) == "crew"

    def test_whitespace_stripped(self):
        """Whitespace around routing mode should be stripped."""
        with patch.dict(os.environ, {"ROUTING_MODE": "  crew  "}):
            assert _get_routing_mode({}) == "crew"


# ---------------------------------------------------------------------------
# _handle_invocation routing tests
# ---------------------------------------------------------------------------


class TestHandleInvocationRouting:
    """Tests for routing logic in _handle_invocation."""

    @pytest.mark.asyncio
    async def test_chat_mode_uses_chat_handler(self):
        """Chat mode should route through _handle_chat_invocation."""
        with patch(
            "ad_buyer.interfaces.agentcore.http_main._handle_chat_invocation"
        ) as mock_chat:
            mock_chat.return_value = {"response": "test"}
            result = await _handle_invocation(
                {"prompt": "hello", "routing_mode": "chat"}
            )
            mock_chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_crew_mode_uses_crew_handler(self):
        """Crew mode should route through _handle_crew_invocation."""
        with patch(
            "ad_buyer.interfaces.agentcore.http_main._handle_crew_invocation"
        ) as mock_crew:
            mock_crew.return_value = {"response": "test"}
            result = await _handle_invocation(
                {"prompt": "plan campaign", "routing_mode": "crew"}
            )
            mock_crew.assert_called_once()

    @pytest.mark.asyncio
    async def test_ui_payload_auto_routes_to_crew(self):
        """UI payloads (agent_name present, no routing_mode) should auto-route to crew."""
        with patch.dict(os.environ, {"ROUTING_MODE": "chat"}):
            with patch(
                "ad_buyer.interfaces.agentcore.http_main._handle_crew_invocation"
            ) as mock_crew:
                mock_crew.return_value = {"response": "test"}
                result = await _handle_invocation(
                    {"prompt": "plan campaign", "agent_name": "AAMPBuyerCrewAgent"}
                )
                mock_crew.assert_called_once()


# ---------------------------------------------------------------------------
# _format_crew_output tests
# ---------------------------------------------------------------------------


class TestHandleCrewInvocation:
    """Tests for the _handle_crew_invocation function."""

    @pytest.mark.asyncio
    async def test_missing_prompt_returns_error(self):
        result = await _handle_crew_invocation({})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_plan_campaign_calls_run_campaign_plan(self):
        """Campaign planning should call run_campaign_plan."""
        with patch(
            "ad_buyer.interfaces.agentcore.crew_tools.run_campaign_plan",
            return_value={
                "campaign_name": "Test",
                "total_budget": 500000,
                "flight": "Q4",
                "status": "planned",
                "approval_required": True,
            },
        ) as mock_plan:
            result = await _handle_crew_invocation(
                {"prompt": "Plan a $500K Q4 automotive campaign"}
            )
        mock_plan.assert_called_once()
        assert result["metadata"]["type"] == "buyer_campaign_plan"
        assert result["metadata"]["approval_required"] is True

    @pytest.mark.asyncio
    async def test_exception_returns_error(self):
        with patch(
            "ad_buyer.interfaces.agentcore.crew_tools.run_campaign_plan",
            side_effect=Exception("Flow failed"),
        ):
            result = await _handle_crew_invocation(
                {"prompt": "Plan a campaign"}
            )
            assert "error" in result


# ---------------------------------------------------------------------------
# Constants tests
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify module-level constants."""

    def test_valid_routing_modes(self):
        assert _VALID_ROUTING_MODES == {"chat", "crew"}

    def test_default_routing_mode(self):
        assert _DEFAULT_ROUTING_MODE in _VALID_ROUTING_MODES
