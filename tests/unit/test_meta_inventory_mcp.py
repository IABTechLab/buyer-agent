# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for MetaInventoryTool's MCP-backed reach-estimate path (META_USE_MCP=true).

Per docs/memory on this branch's MCP client, get_reach_estimate() has no
real MCP tool equivalent and always raises MetaAPIError -- so with
META_USE_MCP on, this tool must degrade to its budget-based heuristic
rather than crash. That degrade path is the main behavior under test here.
"""

from unittest.mock import AsyncMock, patch

from ad_buyer.clients.meta_ads_mcp_client import MetaAdsMCPClient, MetaAPIError
from ad_buyer.config.settings import settings
from ad_buyer.tools.research.meta_inventory import MetaInventoryTool


class _FakeMCPClient:
    def __init__(self, reach_data=None, error=None):
        self._reach_data = reach_data
        self._error = error

    async def get_reach_estimate(self, targeting, daily_budget, optimize_for="REACH"):
        if self._error:
            raise self._error
        return self._reach_data


def _patched_transport(fake_client):
    return (
        patch.object(MetaAdsMCPClient, "__aenter__", new=AsyncMock(return_value=fake_client)),
        patch.object(MetaAdsMCPClient, "__aexit__", new=AsyncMock(return_value=False)),
    )


def _mcp_configured(**overrides):
    kwargs = dict(
        meta_access_token="tok",
        meta_ad_account_id="act_1",
        meta_page_id="page_1",
        meta_use_mcp=True,
    )
    kwargs.update(overrides)
    return patch.multiple(settings, **kwargs)


class TestMetaInventoryToolMCP:
    def test_unconfigured_short_circuits_before_touching_mcp(self):
        with patch.multiple(settings, meta_access_token="", meta_ad_account_id=""):
            result = MetaInventoryTool()._run(channel="social", budget=1000)
        assert "Meta not configured" in result

    def test_unimplemented_reach_estimate_degrades_to_heuristic(self):
        p1, p2 = _patched_transport(
            _FakeMCPClient(error=MetaAPIError("no supported Meta Ads MCP tool"))
        )
        with _mcp_configured(), p1, p2:
            result = MetaInventoryTool()._run(channel="social", budget=3000)

        assert "estimated — API error" in result
        assert "Found 4 Meta placements for social" in result

    def test_success_path_uses_reach_bounds_from_mcp_and_skips_fallback_note(self):
        p1, p2 = _patched_transport(
            _FakeMCPClient(reach_data={"users_lower_bound": 1000, "users_upper_bound": 3000})
        )
        with _mcp_configured(), p1, p2:
            result = MetaInventoryTool()._run(channel="branding", budget=1000)

        assert "estimated — API error" not in result
        assert "Found 2 Meta placements for branding" in result

    def test_zero_reach_bounds_fall_back_to_budget_heuristic(self):
        """(lower + upper) // 2 == 0 is falsy, so estimated_reach must fall
        back to int(budget * 100), not report zero reach."""
        p1, p2 = _patched_transport(
            _FakeMCPClient(reach_data={"users_lower_bound": 0, "users_upper_bound": 0})
        )
        with _mcp_configured(), p1, p2:
            result = MetaInventoryTool()._run(channel="social", budget=10)

        assert "estimated — API error" not in result
        assert "Estimated Reach: 250 users" in result  # (10*100) // 4 placements
