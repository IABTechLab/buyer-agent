# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for MetaReportingTool's MCP routing (settings.meta_use_mcp)."""

from unittest.mock import AsyncMock, MagicMock, patch

from ad_buyer.clients.meta_ads_mcp_client import MetaAuthError as MCPAuthError
from ad_buyer.tools.reporting.meta_reporting import MetaReportingTool


def _mock_settings(**overrides):
    settings = MagicMock()
    settings.meta_access_token = "tok"
    settings.meta_ad_account_id = "act_1"
    settings.meta_page_id = "page_1"
    settings.meta_api_version = "v21.0"
    settings.meta_use_mcp = False
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


class TestUnconfigured:
    def test_missing_credentials_short_circuits_before_transport_choice(self):
        tool = MetaReportingTool()
        settings = _mock_settings(meta_access_token="", meta_ad_account_id="")
        with patch("ad_buyer.tools.reporting.meta_reporting.settings", settings):
            result = tool._run(campaign_ids=["c1"])
        assert "not configured" in result


class TestGraphApiPath:
    def test_meta_use_mcp_false_uses_graph_api_client(self):
        tool = MetaReportingTool()
        settings = _mock_settings(meta_use_mcp=False)
        client = MagicMock()
        client.get_insights.return_value = [
            {"campaign_name": "Camp 1", "spend": 10.0, "impressions": 100}
        ]
        with (
            patch("ad_buyer.tools.reporting.meta_reporting.settings", settings),
            patch(
                "ad_buyer.tools.reporting.meta_reporting.MetaAdsClient", return_value=client
            ) as client_cls,
        ):
            result = tool._run(campaign_ids=["c1"])

        client_cls.assert_called_once()
        client.get_insights.assert_called_once_with("c1", date_preset="last_30d")
        assert "Camp 1" in result

    def test_missing_page_id_on_graph_path_returns_message(self):
        tool = MetaReportingTool()
        settings = _mock_settings(meta_use_mcp=False, meta_page_id="")
        with patch("ad_buyer.tools.reporting.meta_reporting.settings", settings):
            result = tool._run(campaign_ids=["c1"])
        assert "META_PAGE_ID" in result


class TestMcpPath:
    def test_meta_use_mcp_true_uses_mcp_client(self):
        tool = MetaReportingTool()
        settings = _mock_settings(meta_use_mcp=True)

        mcp_client = AsyncMock()
        mcp_client.get_insights.return_value = [
            {"campaign_name": "Camp MCP", "spend": 5.0, "impressions": 50}
        ]
        mcp_client.__aenter__.return_value = mcp_client
        mcp_client.__aexit__.return_value = False

        with (
            patch("ad_buyer.tools.reporting.meta_reporting.settings", settings),
            patch(
                "ad_buyer.tools.reporting.meta_reporting.MetaAdsMCPClient",
                return_value=mcp_client,
            ) as client_cls,
        ):
            result = tool._run(campaign_ids=["c1"], date_preset="last_7d")

        client_cls.assert_called_once_with(
            access_token="tok", ad_account_id="act_1", page_id="page_1"
        )
        mcp_client.get_insights.assert_called_once_with("c1", date_preset="last_7d")
        assert "Camp MCP" in result

    def test_mcp_path_does_not_require_page_id(self):
        """Unlike the Graph API path, MCP has no separate page-id precheck."""
        tool = MetaReportingTool()
        settings = _mock_settings(meta_use_mcp=True, meta_page_id="")

        mcp_client = AsyncMock()
        mcp_client.get_insights.return_value = []
        mcp_client.__aenter__.return_value = mcp_client
        mcp_client.__aexit__.return_value = False

        with (
            patch("ad_buyer.tools.reporting.meta_reporting.settings", settings),
            patch(
                "ad_buyer.tools.reporting.meta_reporting.MetaAdsMCPClient",
                return_value=mcp_client,
            ),
        ):
            result = tool._run(campaign_ids=["c1"])
        assert "META_PAGE_ID" not in result

    def test_mcp_auth_error_is_reported_per_campaign(self):
        tool = MetaReportingTool()
        settings = _mock_settings(meta_use_mcp=True)

        mcp_client = AsyncMock()
        mcp_client.get_insights.side_effect = MCPAuthError("bad token")
        mcp_client.__aenter__.return_value = mcp_client
        mcp_client.__aexit__.return_value = False

        with (
            patch("ad_buyer.tools.reporting.meta_reporting.settings", settings),
            patch(
                "ad_buyer.tools.reporting.meta_reporting.MetaAdsMCPClient",
                return_value=mcp_client,
            ),
        ):
            result = tool._run(campaign_ids=["c1"])
        assert "Auth error" in result

    def test_one_campaign_failure_does_not_abort_the_others(self):
        tool = MetaReportingTool()
        settings = _mock_settings(meta_use_mcp=True)

        mcp_client = AsyncMock()
        mcp_client.get_insights.side_effect = [
            RuntimeError("boom"),
            [{"campaign_name": "Camp 2", "spend": 1.0, "impressions": 1}],
        ]
        mcp_client.__aenter__.return_value = mcp_client
        mcp_client.__aexit__.return_value = False

        with (
            patch("ad_buyer.tools.reporting.meta_reporting.settings", settings),
            patch(
                "ad_buyer.tools.reporting.meta_reporting.MetaAdsMCPClient",
                return_value=mcp_client,
            ),
        ):
            result = tool._run(campaign_ids=["c1", "c2"])

        assert "c1: Error" in result
        assert "Camp 2" in result


class TestFormatRows:
    def test_format_rows_matches_expected_layout(self):
        tool = MetaReportingTool()
        out = tool._format_rows(
            "c1",
            [
                {
                    "campaign_name": "Camp 1",
                    "spend": "12.5",
                    "impressions": "1000",
                    "reach": "800",
                    "frequency": "1.25",
                    "ctr": "2.5",
                    "cpm": "15.6",
                }
            ],
        )
        assert "Camp 1" in out
        assert "$12.50" in out
        assert "1,000" in out
