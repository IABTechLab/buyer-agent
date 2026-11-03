# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for MetaAdsMCPClient (Meta's official mcp.facebook.com/ads server).

See docs/memory on the tool-name bug found via live testing: the tool-call
mappings below (name, argument keys, account-ID format) are the contract
this client promises to the real server, even though these tests only
verify the client's own request-building/response-parsing logic against a
mocked transport, not the live server's catalog.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ad_buyer.clients.meta_ads_mcp_client import (
    _INSIGHTS_MAX_PAGES,
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
        client = _client(ad_account_id="act_123456789012345")
        assert client._ad_account_id == "123456789012345"

    def test_leaves_bare_account_id_unchanged(self):
        client = _client(ad_account_id="123456789012345")
        assert client._ad_account_id == "123456789012345"

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
        client._client.post.return_value = _response(status_code=401, text="denied")

        with pytest.raises(MetaAuthError):
            await client._call("tools/list", {})

    @pytest.mark.asyncio
    async def test_5xx_raises_api_error(self):
        client = _client()
        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.post.return_value = _response(status_code=500, text="boom")

        with pytest.raises(MetaAPIError, match="500"):
            await client._call("tools/list", {})

    @pytest.mark.asyncio
    async def test_timeout_raises_api_error(self):
        client = _client()
        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.post.side_effect = httpx.TimeoutException("slow")

        with pytest.raises(MetaAPIError, match="timed out"):
            await client._call("tools/list", {})

    @pytest.mark.asyncio
    async def test_jsonrpc_error_field_raises_api_error(self):
        client = _client()
        client._client = AsyncMock(spec=httpx.AsyncClient)
        client._client.post.return_value = _response(
            json_body={"error": {"code": -32601, "message": "No handler found"}}
        )

        with pytest.raises(MetaAPIError, match="No handler found"):
            await client._call("tools/list", {})

    @pytest.mark.asyncio
    async def test_parses_sse_formatted_response(self):
        # The parser only recognizes SSE framing when the body itself starts
        # with "data:" (no leading "event:" line) -- matches what the real
        # server actually sends for this endpoint.
        client = _client()
        client._client = AsyncMock(spec=httpx.AsyncClient)
        sse_body = 'data: {"result": {"tools": []}}\n\n'
        client._client.post.return_value = _response(text=sse_body)

        result = await client._call("tools/list", {})

        assert result == {"tools": []}


class TestCallTool:
    @pytest.mark.asyncio
    async def test_invokes_tools_call_with_name_and_arguments(self):
        client = _client()
        mock_call = AsyncMock(return_value={"content": []})
        with patch.object(client, "_call", new=mock_call):
            await client._call_tool("ads_get_ad_accounts", {"ad_account_id": "1"})
        mock_call.assert_called_once_with(
            "tools/call", {"name": "ads_get_ad_accounts", "arguments": {"ad_account_id": "1"}}
        )

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
    async def test_non_json_text_content_returned_as_raw_string(self):
        client = _client()
        with patch.object(
            client,
            "_call",
            new=AsyncMock(return_value={"content": [{"type": "text", "text": "plain text"}]}),
        ):
            result = await client._call_tool("ads_x", {})
        assert result == "plain text"

    @pytest.mark.asyncio
    async def test_no_content_returns_full_result(self):
        client = _client()
        with patch.object(client, "_call", new=AsyncMock(return_value={"content": []})):
            result = await client._call_tool("ads_x", {})
        assert result == {"content": []}

    @pytest.mark.asyncio
    async def test_is_error_result_raises_api_error_with_meta_message(self):
        client = _client()
        client._client = AsyncMock(spec=httpx.AsyncClient)
        sse_body = "data: " + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [{"type": "text", "text": "Objective OUTCOME_FOO is invalid"}],
                    "isError": True,
                },
            }
        )
        client._client.post.return_value = _response(text=sse_body)

        with pytest.raises(MetaAPIError, match="ads_create_campaign: Objective OUTCOME_FOO"):
            await client.create_campaign("n", "OUTCOME_FOO", 100)

    @pytest.mark.asyncio
    async def test_is_error_without_text_still_raises(self):
        client = _client()
        with patch.object(
            client, "_call", new=AsyncMock(return_value={"content": [], "isError": True})
        ):
            with pytest.raises(MetaAPIError, match="ads_update_entity"):
                await client._call_tool("ads_update_entity", {})

    @pytest.mark.asyncio
    async def test_failed_pause_raises_instead_of_returning_error_text(self):
        client = _client()
        error = {"content": [{"type": "text", "text": "entity not found"}], "isError": True}
        with patch.object(client, "_call", new=AsyncMock(return_value=error)):
            with pytest.raises(MetaAPIError, match="entity not found"):
                await client.pause("camp_1")

    @pytest.mark.asyncio
    async def test_is_error_false_returns_parsed_content(self):
        client = _client()
        ok = {"content": [{"type": "text", "text": json.dumps({"id": "c1"})}], "isError": False}
        with patch.object(client, "_call", new=AsyncMock(return_value=ok)):
            assert await client._call_tool("ads_x", {}) == {"id": "c1"}


class TestCampaign:
    @pytest.mark.asyncio
    async def test_create_campaign_uses_ads_create_campaign_tool(self):
        client = _client(ad_account_id="act_999")
        mock_tool = AsyncMock(return_value={"id": "camp_1"})
        with patch.object(client, "_call_tool", new=mock_tool):
            result = await client.create_campaign("My Campaign", "OUTCOME_AWARENESS", 5000)

        assert result == {"id": "camp_1"}
        mock_tool.assert_called_once_with(
            "ads_create_campaign",
            {
                "ad_account_id": "999",
                "campaign_name": "My Campaign",
                "objective": "OUTCOME_AWARENESS",
                "buying_type": "AUCTION",
                "campaign_daily_budget": 5000,
            },
        )


class TestEntityStatus:
    @pytest.mark.asyncio
    async def test_active_status_uses_activate_entity_tool(self):
        client = _client()
        mock_tool = AsyncMock(return_value={})
        with patch.object(client, "_call_tool", new=mock_tool):
            await client.update_campaign("camp_1", "ACTIVE")
        mock_tool.assert_called_once_with(
            "ads_activate_entity",
            {"ad_account_id": "1", "entity_id": "camp_1", "entity_type": "campaign"},
        )

    @pytest.mark.asyncio
    async def test_paused_status_uses_update_entity_tool(self):
        client = _client()
        mock_tool = AsyncMock(return_value={})
        with patch.object(client, "_call_tool", new=mock_tool):
            await client.pause("camp_1")
        mock_tool.assert_called_once_with(
            "ads_update_entity",
            {
                "ad_account_id": "1",
                "entity_id": "camp_1",
                "entity_type": "campaign",
                "fields": json.dumps({"status": "PAUSED"}),
            },
        )

    @pytest.mark.asyncio
    async def test_activate_helper_sets_active(self):
        client = _client()
        mock_tool = AsyncMock(return_value={})
        with patch.object(client, "_call_tool", new=mock_tool):
            await client.activate("camp_1")
        assert mock_tool.call_args.args[0] == "ads_activate_entity"

    @pytest.mark.asyncio
    async def test_delete_helper_sets_deleted_status(self):
        client = _client()
        mock_tool = AsyncMock(return_value={})
        with patch.object(client, "_call_tool", new=mock_tool):
            await client.delete("camp_1")
        assert mock_tool.call_args.args[1]["fields"] == json.dumps({"status": "DELETED"})

    @pytest.mark.asyncio
    async def test_update_ad_targets_ad_entity_type(self):
        client = _client()
        mock_tool = AsyncMock(return_value={})
        with patch.object(client, "_call_tool", new=mock_tool):
            await client.update_ad("ad_1", "PAUSED")
        assert mock_tool.call_args.args[1]["entity_type"] == "ad"
        assert mock_tool.call_args.args[1]["entity_id"] == "ad_1"


class TestAdSet:
    @pytest.mark.asyncio
    async def test_create_adset_builds_expected_targeting(self):
        client = _client()
        mock_tool = AsyncMock(return_value={"id": "adset_1"})
        with patch.object(client, "_call_tool", new=mock_tool):
            await client.create_adset("camp_1", "AdSet A", "REACH", "IMPRESSIONS", ["US", "CA"])

        tool_name, body = mock_tool.call_args.args
        assert tool_name == "ads_create_ad_set"
        assert body["campaign_id"] == "camp_1"
        assert body["optimization_goal"] == "REACH"
        assert body["billing_event"] == "IMPRESSIONS"
        assert json.loads(body["targeting"]) == {
            "age_min": 18,
            "age_max": 65,
            "geo_locations": {"countries": ["US", "CA"]},
            "publisher_platforms": ["facebook", "instagram"],
        }


class TestCreative:
    @pytest.mark.asyncio
    async def test_includes_page_id_when_configured(self):
        client = _client(page_id="page_9")
        mock_tool = AsyncMock(return_value={})
        with patch.object(client, "_call_tool", new=mock_tool):
            await client.create_creative("name", "body", "title", "https://x")
        assert mock_tool.call_args.args[1]["page_id"] == "page_9"

    @pytest.mark.asyncio
    async def test_omits_page_id_when_not_configured(self):
        client = _client()
        mock_tool = AsyncMock(return_value={})
        with patch.object(client, "_call_tool", new=mock_tool):
            await client.create_creative("name", "body", "title", "https://x")
        assert "page_id" not in mock_tool.call_args.args[1]

    @pytest.mark.asyncio
    async def test_omits_image_url_when_no_image_path(self):
        client = _client()
        mock_tool = AsyncMock(return_value={})
        with patch.object(client, "_call_tool", new=mock_tool):
            await client.create_creative("name", "body", "title", "https://x")
        assert "image_url" not in mock_tool.call_args.args[1]

    @pytest.mark.asyncio
    async def test_includes_image_url_when_given(self):
        client = _client()
        mock_tool = AsyncMock(return_value={})
        with patch.object(client, "_call_tool", new=mock_tool):
            await client.create_creative(
                "name", "body", "title", "https://x", image_path="/tmp/a.png"
            )
        assert mock_tool.call_args.args[1]["image_url"] == "/tmp/a.png"


class TestAd:
    @pytest.mark.asyncio
    async def test_create_ad_wraps_creative_id_as_json(self):
        client = _client()
        mock_tool = AsyncMock(return_value={"id": "ad_1"})
        with patch.object(client, "_call_tool", new=mock_tool):
            await client.create_ad("adset_1", "Ad A", "creative_1")

        tool_name, body = mock_tool.call_args.args
        assert tool_name == "ads_create_ad"
        assert body["ad_set_id"] == "adset_1"
        assert json.loads(body["creative"]) == {"creative_id": "creative_1"}


class TestNotImplementedTools:
    @pytest.mark.asyncio
    async def test_list_campaigns_raises_api_error(self):
        with pytest.raises(MetaAPIError, match="no supported Meta Ads MCP tool"):
            await _client().list_campaigns()

    @pytest.mark.asyncio
    async def test_get_reach_estimate_raises_api_error(self):
        with pytest.raises(MetaAPIError, match="no supported Meta Ads MCP tool"):
            await _client().get_reach_estimate(targeting={}, daily_budget=10.0)


def _entities_page(rows, next_cursor=None):
    """ads_get_ad_entities result as the live server returns it: rows are a
    JSON-encoded string, not a list."""
    page = {"ad_entities": json.dumps(rows), "client_conversation_id": "conv"}
    if next_cursor:
        page["pagination"] = {"next_cursor": next_cursor}
    return page


_LIVE_ROW = {
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


class TestInsights:
    @pytest.mark.asyncio
    async def test_calls_ads_get_ad_entities_scoped_to_campaign(self):
        client = _client(ad_account_id="act_1")
        mock_tool = AsyncMock(return_value=_entities_page([]))
        with patch.object(client, "_call_tool", new=mock_tool):
            await client.get_insights("camp_1", date_preset="last_7d")

        tool_name, body = mock_tool.call_args.args
        assert tool_name == "ads_get_ad_entities"
        assert body["ad_account_id"] == "1"
        assert body["level"] == "campaign"
        assert body["object_ids"] == ["camp_1"]
        assert body["date_preset"] == "last_7d"
        assert "amount_spent" in body["fields"]

    @pytest.mark.asyncio
    async def test_parses_json_string_entities_and_maps_amount_spent_to_spend(self):
        client = _client()
        with patch.object(
            client, "_call_tool", new=AsyncMock(return_value=_entities_page([dict(_LIVE_ROW)]))
        ):
            rows = await client.get_insights("camp_1")

        assert len(rows) == 1
        assert rows[0]["spend"] == "12.50"
        assert rows[0]["impressions"] == "1000"
        assert rows[0]["campaign_name"] == "Spring Promo"

    @pytest.mark.asyncio
    async def test_accepts_entities_already_decoded_as_list(self):
        client = _client()
        page = {"ad_entities": [dict(_LIVE_ROW)]}
        with patch.object(client, "_call_tool", new=AsyncMock(return_value=page)):
            rows = await client.get_insights("camp_1")
        assert rows[0]["spend"] == "12.50"

    @pytest.mark.asyncio
    async def test_null_metrics_for_undelivered_campaign_become_zero(self):
        client = _client()
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
        with patch.object(client, "_call_tool", new=AsyncMock(return_value=_entities_page([row]))):
            rows = await client.get_insights("camp_1")
        assert rows[0]["ctr"] == 0
        assert rows[0]["cpm"] == 0

    @pytest.mark.asyncio
    async def test_unknown_campaign_returns_no_rows(self):
        client = _client()
        with patch.object(client, "_call_tool", new=AsyncMock(return_value=_entities_page([]))):
            assert await client.get_insights("camp_x") == []

    @pytest.mark.asyncio
    async def test_raises_when_response_not_a_dict(self):
        client = _client()
        with patch.object(client, "_call_tool", new=AsyncMock(return_value="not-a-dict")):
            with pytest.raises(MetaAPIError, match="ads_get_ad_entities"):
                await client.get_insights("camp_1")

    @pytest.mark.asyncio
    async def test_paginates_across_cursor_and_stops_when_exhausted(self):
        client = _client()
        page1 = _entities_page([{"name": "c1", "spend": 1}], next_cursor="p2")
        page2 = _entities_page([{"name": "c1", "spend": 2}])
        mock_tool = AsyncMock(side_effect=[page1, page2])

        with patch.object(client, "_call_tool", new=mock_tool):
            rows = await client.get_insights("camp_1")

        assert [r["spend"] for r in rows] == [1, 2]
        assert "cursor" not in mock_tool.call_args_list[0].args[1]
        assert mock_tool.call_args_list[1].args[1]["cursor"] == "p2"

    @pytest.mark.asyncio
    async def test_stops_after_max_pages_even_if_cursor_keeps_coming(self):
        client = _client()
        page = _entities_page([{"name": "c"}], next_cursor="keep-going")
        mock_tool = AsyncMock(side_effect=[page] * (_INSIGHTS_MAX_PAGES + 5))

        with patch.object(client, "_call_tool", new=mock_tool):
            rows = await client.get_insights("camp_1")

        assert len(rows) == _INSIGHTS_MAX_PAGES
        assert mock_tool.call_count == _INSIGHTS_MAX_PAGES

    @pytest.mark.asyncio
    async def test_defaults_campaign_name_when_row_has_no_name(self):
        client = _client()
        with patch.object(
            client, "_call_tool", new=AsyncMock(return_value=_entities_page([{"spend": 1}]))
        ):
            rows = await client.get_insights("camp_1")
        assert rows[0]["campaign_name"] == "camp_1"

    @pytest.mark.asyncio
    async def test_tool_error_propagates_as_api_error(self):
        client = _client()
        error = {
            "content": [{"type": "text", "text": "Tool is not available on this server"}],
            "isError": True,
        }
        with patch.object(client, "_call", new=AsyncMock(return_value=error)):
            with pytest.raises(MetaAPIError, match="ads_get_ad_entities: Tool is not available"):
                await client.get_insights("camp_1")


class TestAvailableTools:
    def test_reflects_discovered_tool_names(self):
        client = _client()
        client._tools = {"ads_a": {}, "ads_b": {}}
        assert client.available_tools == ["ads_a", "ads_b"]
