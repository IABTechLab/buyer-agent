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


class TestMetaReportingToolOverTransport:
    """MetaReportingTool through the real MCP client and the live
    ads_get_ad_entities response shape (rows as a JSON string, spend as
    amount_spent, null ctr/cpm for undelivered campaigns)."""

    @staticmethod
    def _transport(result):
        import json

        import httpx

        import ad_buyer.clients.meta_ads_mcp_client as mcp_module

        def handler(request):
            body = json.loads(request.content)
            payload = {"tools": []} if body["method"] == "tools/list" else result
            sse = "data: " + json.dumps({"jsonrpc": "2.0", "id": 1, "result": payload})
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

        real_client = httpx.AsyncClient
        return patch.object(
            mcp_module.httpx,
            "AsyncClient",
            lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
        )

    @staticmethod
    def _entities(rows):
        import json

        text = json.dumps({"ad_entities": json.dumps(rows), "client_conversation_id": "c"})
        return {"content": [{"type": "text", "text": text}], "isError": False}

    def test_formats_live_shaped_rows(self):
        row = {
            "name": "Spring Promo",
            "id": "camp_1",
            "amount_spent": {"value": "12.50", "unit": "USD"},
            "impressions": "1000",
            "reach": "800",
            "frequency": "1.25",
            "clicks": "20",
            "ctr": "2.0",
            "cpm": "12.5",
        }
        with _mcp_configured(), self._transport(self._entities([row])):
            result = MetaReportingTool()._run(["camp_1"])

        assert "Campaign: Spring Promo" in result
        assert "$12.50" in result
        assert "1,000" in result
        assert "Error" not in result

    def test_undelivered_campaign_with_null_metrics_reports_zeros(self):
        row = {
            "name": "Paused",
            "id": "camp_1",
            "amount_spent": {"value": "0", "unit": "USD"},
            "impressions": "0",
            "reach": "0",
            "frequency": "0",
            "clicks": "0",
            "ctr": None,
            "cpm": None,
        }
        with _mcp_configured(), self._transport(self._entities([row])):
            result = MetaReportingTool()._run(["camp_1"])

        assert "Campaign: Paused" in result
        assert "CPM:         $0.00" in result
        assert "Error" not in result

    def test_meta_tool_error_reported_per_campaign(self):
        error = {
            "content": [{"type": "text", "text": "Tool is not available on this server"}],
            "isError": True,
        }
        with _mcp_configured(), self._transport(error):
            result = MetaReportingTool()._run(["camp_1"])

        assert "Campaign camp_1: Error — ads_get_ad_entities: Tool is not available" in result
