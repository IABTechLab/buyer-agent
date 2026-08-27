# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for MetaAdsMCPClient (Meta's official mcp.facebook.com/ads server).

These verify the client's own request-building, response-parsing, and
error-handling logic against a mocked transport — not the live server's
current tool catalog or argument schemas, which Meta does not publish in
full (see the module docstring in meta_ads_mcp_client.py for what is and
isn't confirmed from official docs as of 2026-08-27).
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ad_buyer.clients.meta_ads_mcp_client import (
    MetaAdsMCPClient,
    MetaAPIError,
    MetaAuthError,
)


def _response(status_code: int = 200, json_body: dict | None = None, text: str | None = None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text if text is not None else json.dumps(json_body if json_body is not None else {})
    return resp


def _client(**overrides) -> MetaAdsMCPClient:
    kwargs = {"access_token": "tok", "ad_account_id": "1"}
    kwargs.update(overrides)
    return MetaAdsMCPClient(**kwargs)


class TestInit:
    def test_strips_act_prefix_from_account_id(self):
        client = _client(ad_account_id="act_4509330366054796")
        assert client._ad_account_id == "4509330366054796"

    def test_leaves_bare_account_id_unchanged(self):
        client = _client(ad_account_id="4509330366054796")
        assert client._ad_account_id == "4509330366054796"

    def test_available_tools_empty_before_discovery(self):
        assert _client().available_tools == []


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_aenter_sets_bearer_auth_header(self):
        client = _client(access_token="secret-tok")
        with patch.object(client, "_discover_tools", new=AsyncMock()):
            async with client as c:
                assert c._client.headers["Authorization"] == "Bearer secret-tok"

    @pytest.mark.asyncio
    async def test_aexit_closes_and_clears_transport(self):
        client = _client()
        with patch.object(client, "_discover_tools", new=AsyncMock()):
            async with client:
                pass
        assert client._client is None

    @pytest.mark.asyncio
    async def test_aenter_populates_available_tools(self):
        client = _client()
        with patch.object(
            client,
            "_call",
            new=AsyncMock(
                return_value={
                    "tools": [{"name": "ads_get_ad_accounts"}, {"name": "ads_create_campaign"}]
                }
            ),
        ):
            async with client as c:
                assert c.available_tools == ["ads_get_ad_accounts", "ads_create_campaign"]

    @pytest.mark.asyncio
    async def test_discovery_failure_does_not_raise(self):
        client = _client()
        with patch.object(client, "_call", new=AsyncMock(side_effect=MetaAPIError("server down"))):
            async with client as c:
                assert c.available_tools == []


class TestCall:
    @pytest.mark.asyncio
    async def test_requires_context_manager(self):
        client = _client()
        with pytest.raises(AssertionError):
            await client._call("tools/list", {})

    @pytest.mark.asyncio
    async def test_sends_jsonrpc_payload_with_params(self):
        client = _client()
        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.post.return_value = _response(json_body={"result": {"ok": True}})

        result = await client._call("tools/call", {"name": "x"})

        assert result == {"ok": True}
        call = client._client.post.call_args
        assert call.args[0] == "https://mcp.facebook.com/ads"
        assert call.kwargs["json"] == {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {"name": "x"},
        }

    @pytest.mark.asyncio
    async def test_omits_params_key_when_empty(self):
        client = _client()
        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.post.return_value = _response(json_body={"result": {}})

        await client._call("tools/list", {})

        assert "params" not in client._client.post.call_args.kwargs["json"]

    @pytest.mark.asyncio
    async def test_result_falls_back_to_full_body_when_no_result_key(self):
        client = _client()
        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.post.return_value = _response(json_body={"tools": []})

        result = await client._call("tools/list", {})

        assert result == {"tools": []}

    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self):
        client = _client()
        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.post.return_value = _response(status_code=401, text="nope")

        with pytest.raises(MetaAuthError, match="ads_mcp_management"):
            await client._call("tools/list", {})

    @pytest.mark.asyncio
    async def test_4xx_raises_api_error(self):
        client = _client()
        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.post.return_value = _response(status_code=400, text="bad request")

        with pytest.raises(MetaAPIError, match="HTTP 400"):
            await client._call("tools/list", {})

    @pytest.mark.asyncio
    async def test_jsonrpc_error_field_raises_api_error(self):
        client = _client()
        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.post.return_value = _response(
            json_body={"error": {"code": -32601, "message": "Method not found"}}
        )

        with pytest.raises(MetaAPIError, match="-32601"):
            await client._call("tools/list", {})

    @pytest.mark.asyncio
    async def test_parses_sse_framed_body(self):
        client = _client()
        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.post.return_value = _response(
            text='event: message\ndata: {"result": {"ok": true}}\n\n'
        )

        result = await client._call("tools/list", {})

        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_timeout_raises_api_error(self):
        client = _client()
        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.post.side_effect = httpx.TimeoutException("slow")

        with pytest.raises(MetaAPIError, match="timed out"):
            await client._call("tools/list", {})


class TestCallTool:
    @pytest.mark.asyncio
    async def test_parses_json_encoded_text_content(self):
        client = _client()
        with patch.object(
            client,
            "_call",
            new=AsyncMock(
                return_value={"content": [{"type": "text", "text": json.dumps({"id": "c1"})}]}
            ),
        ):
            result = await client._call_tool("ads_create_campaign", {})
        assert result == {"id": "c1"}

    @pytest.mark.asyncio
    async def test_returns_raw_text_when_not_json(self):
        client = _client()
        with patch.object(
            client,
            "_call",
            new=AsyncMock(return_value={"content": [{"type": "text", "text": "plain text"}]}),
        ):
            result = await client._call_tool("ads_create_campaign", {})
        assert result == "plain text"

    @pytest.mark.asyncio
    async def test_falls_back_to_full_result_without_content(self):
        client = _client()
        with patch.object(client, "_call", new=AsyncMock(return_value={"foo": "bar"})):
            result = await client._call_tool("ads_create_campaign", {})
        assert result == {"foo": "bar"}

    @pytest.mark.asyncio
    async def test_unknown_tool_rejected_before_network_call_when_catalog_known(self):
        """Pre-flight check against the discovered catalog fails fast and lists
        the real tool names — the earlier iteration of this client had no such
        check, so a renamed/removed tool surfaced only as a generic JSON-RPC
        -32601 from the server (hard to diagnose)."""
        client = _client()
        client._tools = {"ads_create_campaign": {}}
        mock_call = AsyncMock()
        with patch.object(client, "_call", new=mock_call):
            with pytest.raises(MetaAPIError, match="ads_create_campaign"):
                await client._call_tool("ads_entity_schedule_report", {})
        mock_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_known_tool_passes_the_precheck(self):
        client = _client()
        client._tools = {"ads_create_campaign": {}}
        with patch.object(
            client, "_call", new=AsyncMock(return_value={"content": []})
        ) as mock_call:
            await client._call_tool("ads_create_campaign", {"x": 1})
        mock_call.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_catalog_does_not_gate_calls(self):
        """When discovery found nothing (e.g. it failed), don't block every
        call on an empty catalog — let the server itself be the source of
        truth in that degraded case."""
        client = _client()
        assert client._tools == {}
        with patch.object(
            client, "_call", new=AsyncMock(return_value={"content": []})
        ) as mock_call:
            await client._call_tool("ads_create_campaign", {})
        mock_call.assert_called_once()


class TestCampaignAndEntityMethods:
    @pytest.mark.asyncio
    async def test_create_campaign_builds_expected_args(self):
        client = _client(ad_account_id="act_99")
        mock_tool = AsyncMock(return_value={"campaign_id": "c1"})
        with patch.object(client, "_call_tool", new=mock_tool):
            result = await client.create_campaign("My Campaign", "OUTCOME_AWARENESS", 5000)

        assert result == {"campaign_id": "c1"}
        name, args = mock_tool.call_args.args
        assert name == "ads_create_campaign"
        assert args == {
            "ad_account_id": "99",
            "campaign_name": "My Campaign",
            "objective": "OUTCOME_AWARENESS",
            "buying_type": "AUCTION",
            "campaign_daily_budget": 5000,
        }

    @pytest.mark.asyncio
    async def test_update_campaign_active_uses_activate_entity(self):
        client = _client()
        mock_tool = AsyncMock(return_value={})
        with patch.object(client, "_call_tool", new=mock_tool):
            await client.update_campaign("c1", "ACTIVE")

        name, args = mock_tool.call_args.args
        assert name == "ads_activate_entity"
        assert args == {"ad_account_id": "1", "entity_id": "c1", "entity_type": "campaign"}

    @pytest.mark.asyncio
    async def test_update_campaign_paused_uses_update_entity_with_fields(self):
        client = _client()
        mock_tool = AsyncMock(return_value={})
        with patch.object(client, "_call_tool", new=mock_tool):
            await client.update_campaign("c1", "PAUSED")

        name, args = mock_tool.call_args.args
        assert name == "ads_update_entity"
        assert args["entity_type"] == "campaign"
        assert json.loads(args["fields"]) == {"status": "PAUSED"}

    @pytest.mark.asyncio
    async def test_update_ad_uses_ad_entity_type(self):
        client = _client()
        mock_tool = AsyncMock(return_value={})
        with patch.object(client, "_call_tool", new=mock_tool):
            await client.update_ad("ad1", "DELETED")

        name, args = mock_tool.call_args.args
        assert args["entity_type"] == "ad"
        assert args["entity_id"] == "ad1"

    @pytest.mark.asyncio
    async def test_pause_activate_delete_convenience_wrappers(self):
        client = _client()
        with patch.object(client, "update_campaign", new=AsyncMock()) as mock_update:
            await client.pause("c1")
            await client.activate("c1")
            await client.delete("c1")

        assert mock_update.call_args_list == [
            (("c1", "PAUSED"),),
            (("c1", "ACTIVE"),),
            (("c1", "DELETED"),),
        ]

    @pytest.mark.asyncio
    async def test_list_campaigns_reads_data_key(self):
        client = _client()
        with patch.object(
            client,
            "_call_tool",
            new=AsyncMock(return_value={"data": [{"id": "c1"}, {"id": "c2"}]}),
        ):
            result = await client.list_campaigns()
        assert result == [{"id": "c1"}, {"id": "c2"}]

    @pytest.mark.asyncio
    async def test_list_campaigns_reads_entities_key(self):
        client = _client()
        with patch.object(
            client, "_call_tool", new=AsyncMock(return_value={"entities": [{"id": "c1"}]})
        ):
            result = await client.list_campaigns()
        assert result == [{"id": "c1"}]

    @pytest.mark.asyncio
    async def test_list_campaigns_accepts_bare_list(self):
        client = _client()
        with patch.object(client, "_call_tool", new=AsyncMock(return_value=[{"id": "c1"}])):
            result = await client.list_campaigns()
        assert result == [{"id": "c1"}]

    @pytest.mark.asyncio
    async def test_list_campaigns_unrecognized_shape_returns_empty(self):
        client = _client()
        with patch.object(client, "_call_tool", new=AsyncMock(return_value="unexpected")):
            result = await client.list_campaigns()
        assert result == []


class TestAdSet:
    @pytest.mark.asyncio
    async def test_create_adset_has_no_bid_amount_param(self):
        """CBO campaigns reject ad-set-level bid fields; the method signature
        deliberately has no bid_amount parameter to make that impossible to
        pass by mistake."""
        client = _client()
        mock_tool = AsyncMock(return_value={"ad_set_id": "as1"})
        with patch.object(client, "_call_tool", new=mock_tool):
            await client.create_adset(
                campaign_id="c1",
                name="AS1",
                optimization_goal="REACH",
                billing_event="IMPRESSIONS",
                targeting_countries=["US", "CA"],
            )

        name, args = mock_tool.call_args.args
        assert name == "ads_create_ad_set"
        assert "bid_amount" not in args
        targeting = json.loads(args["targeting"])
        assert targeting["geo_locations"]["countries"] == ["US", "CA"]
        assert targeting["publisher_platforms"] == ["facebook", "instagram"]


class TestCreativeAndAd:
    @pytest.mark.asyncio
    async def test_create_creative_omits_optional_fields_when_unset(self):
        client = _client(page_id="")
        mock_tool = AsyncMock(return_value={})
        with patch.object(client, "_call_tool", new=mock_tool):
            await client.create_creative("name", "body", "title", "https://x")

        _, args = mock_tool.call_args.args
        assert "page_id" not in args
        assert "image_url" not in args

    @pytest.mark.asyncio
    async def test_create_creative_includes_page_id_and_image(self):
        client = _client(page_id="page_1")
        mock_tool = AsyncMock(return_value={})
        with patch.object(client, "_call_tool", new=mock_tool):
            await client.create_creative(
                "name", "body", "title", "https://x", image_path="https://img"
            )

        _, args = mock_tool.call_args.args
        assert args["page_id"] == "page_1"
        assert args["image_url"] == "https://img"

    @pytest.mark.asyncio
    async def test_create_ad_serializes_creative_id(self):
        client = _client()
        mock_tool = AsyncMock(return_value={"ad_id": "a1"})
        with patch.object(client, "_call_tool", new=mock_tool):
            await client.create_ad("as1", "Ad 1", "cr1")

        name, args = mock_tool.call_args.args
        assert name == "ads_create_ad"
        assert json.loads(args["creative"]) == {"creative_id": "cr1"}


class TestInsights:
    @pytest.mark.asyncio
    async def test_get_insights_uses_ads_get_ad_entities(self):
        client = _client()
        mock_tool = AsyncMock(
            return_value={"data": [{"name": "Camp 1", "spend": "10.5", "impressions": "100"}]}
        )
        with patch.object(client, "_call_tool", new=mock_tool):
            rows = await client.get_insights("c1", date_preset="last_7d")

        name, args = mock_tool.call_args.args
        assert name == "ads_get_ad_entities"
        assert args["entity_type"] == "campaign"
        assert args["entity_ids"] == ["c1"]
        assert args["date_preset"] == "last_7d"
        assert rows[0]["campaign_name"] == "Camp 1"

    @pytest.mark.asyncio
    async def test_get_insights_default_fields_when_unset(self):
        client = _client()
        mock_tool = AsyncMock(return_value={"data": []})
        with patch.object(client, "_call_tool", new=mock_tool):
            await client.get_insights("c1")

        _, args = mock_tool.call_args.args
        assert "spend" in args["fields"]
        assert "impressions" in args["fields"]

    @pytest.mark.asyncio
    async def test_get_insights_uses_campaign_id_as_name_fallback(self):
        client = _client()
        with patch.object(
            client, "_call_tool", new=AsyncMock(return_value={"data": [{"spend": "1"}]})
        ):
            rows = await client.get_insights("c1")
        assert rows[0]["campaign_name"] == "c1"

    @pytest.mark.asyncio
    async def test_get_insights_reads_entities_key(self):
        client = _client()
        with patch.object(
            client, "_call_tool", new=AsyncMock(return_value={"entities": [{"name": "x"}]})
        ):
            rows = await client.get_insights("c1")
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_get_insights_raises_on_unexpected_shape(self):
        client = _client()
        with patch.object(client, "_call_tool", new=AsyncMock(return_value="not a dict or list")):
            with pytest.raises(MetaAPIError, match="unexpected response shape"):
                await client.get_insights("c1")


class TestReachEstimate:
    @pytest.mark.asyncio
    async def test_always_raises_no_mcp_tool(self):
        client = _client()
        with pytest.raises(MetaAPIError, match="no supported Meta Ads MCP tool"):
            await client.get_reach_estimate(targeting={}, daily_budget=10.0)
