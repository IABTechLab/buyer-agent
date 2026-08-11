# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for MetaReportingTool's MCP-backed path (META_USE_MCP=true).

The legacy (Graph API) path already has separate coverage; these tests
target the branch added on this branch: settings.meta_use_mcp routes
_run() through MetaAdsMCPClient instead of MetaAdsClient, and does not
require META_PAGE_ID (unlike the legacy path).
"""

from unittest.mock import AsyncMock, patch

from ad_buyer.clients.meta_ads_mcp_client import MetaAdsMCPClient
from ad_buyer.clients.meta_ads_mcp_client import MetaAuthError as MCPAuthError
from ad_buyer.config.settings import settings
from ad_buyer.tools.reporting.meta_reporting import MetaReportingTool


class _FakeMCPClient:
    def __init__(self, rows=None, error=None):
        self._rows = rows or []
        self._error = error

    async def get_insights(self, campaign_id, date_preset="last_30d"):
        if self._error:
            raise self._error
        return self._rows


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


class TestMetaReportingToolMCP:
    def test_unconfigured_short_circuits_before_touching_mcp(self):
        with patch.multiple(settings, meta_access_token="", meta_ad_account_id=""):
            result = MetaReportingTool()._run(["camp_1"])
        assert "Meta not configured" in result

    def test_success_formats_rows_returned_by_mcp_client(self):
        rows = [
            {
                "campaign_name": "C1",
                "spend": "12.5",
                "impressions": "100",
                "reach": "80",
                "frequency": "1.2",
                "ctr": "0.01",
                "cpm": "5.0",
            }
        ]
        p1, p2 = _patched_transport(_FakeMCPClient(rows=rows))
        with _mcp_configured(), p1, p2:
            result = MetaReportingTool()._run(["camp_1"])

        assert "Campaign: C1" in result
        assert "$12.50" in result

    def test_auth_error_reported_per_campaign_not_raised(self):
        p1, p2 = _patched_transport(_FakeMCPClient(error=MCPAuthError("bad token")))
        with _mcp_configured(), p1, p2:
            result = MetaReportingTool()._run(["camp_1"])

        assert "Campaign camp_1: Auth error" in result

    def test_generic_error_reported_per_campaign_not_raised(self):
        p1, p2 = _patched_transport(_FakeMCPClient(error=RuntimeError("network blip")))
        with _mcp_configured(), p1, p2:
            result = MetaReportingTool()._run(["camp_1"])

        assert "Campaign camp_1: Error" in result

    def test_mcp_path_does_not_require_page_id(self):
        """META_PAGE_ID is required by the legacy Graph API path but the MCP
        path must not gate on it -- MetaAdsMCPClient takes page_id optionally."""
        p1, p2 = _patched_transport(_FakeMCPClient(rows=[{"campaign_name": "C1"}]))
        with _mcp_configured(meta_page_id=""), p1, p2:
            result = MetaReportingTool()._run(["camp_1"])

        assert "META_PAGE_ID not set" not in result
        assert "Campaign: C1" in result

    def test_multiple_campaign_ids_each_reported(self):
        p1, p2 = _patched_transport(_FakeMCPClient(rows=[{"campaign_name": "C"}]))
        with _mcp_configured(), p1, p2:
            result = MetaReportingTool()._run(["camp_1", "camp_2"])

        assert result.count("Campaign: C") == 2
