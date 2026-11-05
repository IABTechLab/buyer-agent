# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for MetaInventoryTool reach estimates with META_USE_MCP=true.

Meta's Ads MCP server has no reach-estimate tool, so with META_USE_MCP on
this tool must keep using the Graph API (MetaAdsAPIClient) for real reach
data instead of degrading to the budget-based heuristic.
"""

import re
from unittest.mock import MagicMock, patch

import pytest

from ad_buyer.clients.meta_ads_mcp_client import MetaAdsMCPClient
from ad_buyer.config.settings import settings
from ad_buyer.tools.research.meta_inventory import MetaInventoryTool


def _mcp_configured(**overrides):
    kwargs = dict(
        meta_access_token="tok",
        meta_ad_account_id="act_1",
        meta_page_id="page_1",
        meta_use_mcp=True,
    )
    kwargs.update(overrides)
    return patch.multiple(settings, **kwargs)


def _graph_client(reach_data=None, error=None):
    client = MagicMock()
    if error:
        client.get_reach_estimate.side_effect = error
    else:
        client.get_reach_estimate.return_value = reach_data
    return client


class TestMetaInventoryToolMCP:
    def test_unconfigured_short_circuits_before_touching_meta(self):
        with patch.multiple(settings, meta_access_token="", meta_ad_account_id=""):
            result = MetaInventoryTool()._run(channel="social", budget=1000)
        assert "Meta not configured" in result

    def test_mcp_mode_uses_graph_api_reach_estimate(self):
        graph = _graph_client(reach_data={"users_lower_bound": 1000, "users_upper_bound": 3000})
        with (
            _mcp_configured(),
            patch(
                "ad_buyer.tools.research.meta_inventory.MetaAdsAPIClient", return_value=graph
            ) as graph_cls,
            patch.object(MetaAdsMCPClient, "__aenter__") as mcp_enter,
        ):
            result = MetaInventoryTool()._run(channel="branding", budget=1000)

        graph_cls.assert_called_once()
        graph.get_reach_estimate.assert_called_once()
        mcp_enter.assert_not_called()
        assert "estimated — API error" not in result
        assert "Found 2 Meta placements for branding" in result

    def test_graph_error_in_mcp_mode_degrades_to_heuristic(self):
        graph = _graph_client(error=RuntimeError("graph down"))
        with (
            _mcp_configured(),
            patch("ad_buyer.tools.research.meta_inventory.MetaAdsAPIClient", return_value=graph),
        ):
            result = MetaInventoryTool()._run(channel="social", budget=3000)

        assert "estimated — API error: graph down" in result
        assert "Found 4 Meta placements for social" in result

    def test_zero_reach_bounds_fall_back_to_budget_heuristic(self):
        """Zero bounds mean no usable audience size, so reach comes from the
        budget at the benchmark CPM instead of being reported as zero."""
        graph = _graph_client(reach_data={"users_lower_bound": 0, "users_upper_bound": 0})
        with (
            _mcp_configured(),
            patch("ad_buyer.tools.research.meta_inventory.MetaAdsAPIClient", return_value=graph),
        ):
            result = MetaInventoryTool()._run(channel="social", budget=10)

        assert "estimated — API error" not in result
        assert "Estimated Reach: 250 users" in result  # $10 / $4 CPM / 2.5 freq / 4 placements


def _cpms(result):
    return [float(m) for m in re.findall(r"Estimated CPM: \$([\d.]+)", result)]


def _run_with_bounds(lower, upper, budget, channel="social"):
    graph = _graph_client(reach_data={"users_lower_bound": lower, "users_upper_bound": upper})
    with (
        _mcp_configured(),
        patch("ad_buyer.tools.research.meta_inventory.MetaAdsAPIClient", return_value=graph),
    ):
        return MetaInventoryTool()._run(channel=channel, budget=budget)


class TestMetaInventoryEstimates:
    def test_realistic_audience_keeps_cpm_at_benchmark(self):
        result = _run_with_bounds(255_200_000, 300_200_000, budget=1500)

        assert "Audience Size: 277,700,000 users" in result
        assert _cpms(result) == [4.0, 4.0, 4.0, 4.0]
        assert "Estimated Impressions: 93,750" in result
        assert "Estimated Reach: 37,500 users" in result

    @pytest.mark.parametrize("budget", [10, 1500, 50_000, 2_000_000])
    def test_cpm_stays_plausible_across_budgets_at_1e8_scale(self, budget):
        result = _run_with_bounds(100_000_000, 400_000_000, budget=budget)

        assert all(1.0 <= cpm <= 50.0 for cpm in _cpms(result))

    def test_small_audience_caps_delivery_not_cpm(self):
        result = _run_with_bounds(1000, 3000, budget=1500)

        assert "Audience Size: 2,000 users" in result
        assert "Estimated Reach: 500 users" in result
        assert "Estimated Impressions: 1,250" in result
        assert "Estimated Cost: $5.00" in result
        assert _cpms(result) == [4.0, 4.0, 4.0, 4.0]

    def test_graph_error_matches_previous_placeholder_numbers(self):
        graph = _graph_client(error=RuntimeError("graph down"))
        with (
            _mcp_configured(),
            patch("ad_buyer.tools.research.meta_inventory.MetaAdsAPIClient", return_value=graph),
        ):
            result = MetaInventoryTool()._run(channel="social", budget=1500)

        assert "Audience Size" not in result
        assert "Estimated Reach: 37,500 users" in result
        assert "Estimated Impressions: 93,750" in result
        assert "Estimated Cost: $375.00" in result
        assert _cpms(result) == [4.0, 4.0, 4.0, 4.0]

    def test_zero_budget_reports_zero_delivery(self):
        result = _run_with_bounds(255_200_000, 300_200_000, budget=0)

        assert "Estimated Impressions: 0" in result
        assert _cpms(result) == [4.0, 4.0, 4.0, 4.0]
