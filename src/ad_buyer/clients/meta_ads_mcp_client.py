# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Meta Ads MCP client — calls mcp.facebook.com/ads via MCP Streamable HTTP.

Replaces direct Graph API calls (meta_ads_client.py / meta_ads_api_client.py)
when META_USE_MCP=true. Auth: user access token in Authorization: Bearer header.
All write operations create resources in PAUSED state by default.
"""

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MCP_URL = "https://mcp.facebook.com/ads"
_TOOL_TIMEOUT = 30.0
_REPORT_MAX_POLLS = 5


class MetaAuthError(Exception):
    """Raised when the Meta MCP server rejects the access token."""


class MetaAPIError(Exception):
    """Raised when a Meta MCP tool call returns an error."""


class MetaAdsMCPClient:
    """Meta Ads client via Meta's official MCP server (mcp.facebook.com/ads).

    Uses JSON-RPC 2.0 over HTTP POST with a user access token.
    Requires the app to have the 'Create & manage ads with ads MCP server'
    use case enabled and Advanced Access for ads_management.

    Auth: user access token (not system user token) in Authorization: Bearer.
    """

    def __init__(
        self,
        access_token: str,
        ad_account_id: str,
        page_id: str = "",
        mcp_url: str = MCP_URL,
        timeout: float = _TOOL_TIMEOUT,
    ) -> None:
        self._token = access_token
        # Ads MCP tools take the bare numeric account ID, no "act_" prefix.
        self._ad_account_id = ad_account_id.removeprefix("act_")
        self._page_id = page_id
        self._mcp_url = mcp_url
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._tools: dict[str, Any] = {}

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def __aenter__(self) -> "MetaAdsMCPClient":
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            timeout=self._timeout,
        )
        await self._discover_tools()
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _discover_tools(self) -> None:
        try:
            result = await self._call("tools/list", {})
            tools = result.get("tools", [])
            self._tools = {t["name"]: t for t in tools if "name" in t}
            logger.info("Meta Ads MCP: %d tools discovered", len(self._tools))
        except Exception as exc:
            logger.warning("Meta Ads MCP tool discovery failed: %s", exc)

    # ── Core JSON-RPC caller ───────────────────────────────────────────────

    async def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert self._client, "Use async with MetaAdsMCPClient(...) as client"
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": 1}
        if params:
            payload["params"] = params

        try:
            resp = await self._client.post(self._mcp_url, json=payload)
        except httpx.TimeoutException as exc:
            raise MetaAPIError(f"Meta MCP request timed out: {exc}") from exc

        if resp.status_code == 401:
            raise MetaAuthError(
                "Meta MCP auth failed — ensure token is a user access token (not system user) "
                "from an app with Advanced Access for ads_management. "
                f"Detail: {resp.text}"
            )
        if resp.status_code >= 400:
            raise MetaAPIError(f"Meta MCP HTTP {resp.status_code}: {resp.text}")

        # Response may be SSE or JSON
        body = resp.text.strip()
        if body.startswith("data:"):
            # SSE — extract first data line
            for line in body.splitlines():
                if line.startswith("data:"):
                    body = line[5:].strip()
                    break

        data = json.loads(body)
        if "error" in data:
            err = data["error"]
            raise MetaAPIError(f"Meta MCP error {err.get('code')}: {err.get('message')}")
        return data.get("result", data)

    async def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        result = await self._call("tools/call", {"name": tool_name, "arguments": arguments})
        # MCP tools/call returns content array
        content = result.get("content", [])
        for item in content:
            if item.get("type") == "text":
                try:
                    return json.loads(item["text"])
                except (json.JSONDecodeError, KeyError):
                    return item.get("text", result)
        return result

    # ── Campaign ──────────────────────────────────────────────────────────

    async def create_campaign(
        self,
        name: str,
        objective: str,
        daily_budget_cents: int,
    ) -> dict[str, Any]:
        """Create a campaign in PAUSED state. objective must be an ODAX value."""
        return await self._call_tool(
            "ads_create_campaign",
            {
                "ad_account_id": self._ad_account_id,
                "campaign_name": name,
                "objective": objective,
                "buying_type": "AUCTION",
                "campaign_daily_budget": daily_budget_cents,
            },
        )

    async def _update_entity_status(
        self, entity_id: str, entity_type: str, status: str
    ) -> dict[str, Any]:
        """Update a campaign/ad_set/ad's status: ACTIVE | PAUSED | DELETED."""
        if status == "ACTIVE":
            return await self._call_tool(
                "ads_activate_entity",
                {
                    "ad_account_id": self._ad_account_id,
                    "entity_id": entity_id,
                    "entity_type": entity_type,
                },
            )
        return await self._call_tool(
            "ads_update_entity",
            {
                "ad_account_id": self._ad_account_id,
                "entity_id": entity_id,
                "entity_type": entity_type,
                "fields": json.dumps({"status": status}),
            },
        )

    async def update_campaign(self, campaign_id: str, status: str) -> dict[str, Any]:
        """Update campaign status: ACTIVE | PAUSED | DELETED."""
        return await self._update_entity_status(campaign_id, "campaign", status)

    async def list_campaigns(self) -> list[dict[str, Any]]:
        """List campaigns for the ad account. Not implemented -- no MCP tool for this exists."""
        raise MetaAPIError(
            "list_campaigns() has no supported Meta Ads MCP tool — "
            "use MetaAdsAPIClient for campaign listing via Graph API"
        )

    async def pause(self, campaign_id: str) -> dict[str, Any]:
        return await self.update_campaign(campaign_id, "PAUSED")

    async def activate(self, campaign_id: str) -> dict[str, Any]:
        return await self.update_campaign(campaign_id, "ACTIVE")

    async def delete(self, campaign_id: str) -> dict[str, Any]:
        return await self.update_campaign(campaign_id, "DELETED")

    # ── Ad Set ────────────────────────────────────────────────────────────

    async def create_adset(
        self,
        campaign_id: str,
        name: str,
        optimization_goal: str,
        billing_event: str,
        targeting_countries: list[str],
    ) -> dict[str, Any]:
        """Create an ad set under a campaign in PAUSED state.

        create_campaign() always creates CBO (campaign-budget-optimized)
        campaigns, which reject bid_amount/bid_strategy on the ad set --
        confirmed live: Meta returns a VALIDATION error otherwise.
        """
        targeting = {
            "age_min": 18,
            "age_max": 65,
            "geo_locations": {"countries": targeting_countries},
            "publisher_platforms": ["facebook", "instagram"],
        }
        return await self._call_tool(
            "ads_create_ad_set",
            {
                "ad_account_id": self._ad_account_id,
                "campaign_id": campaign_id,
                "ad_set_name": name,
                "optimization_goal": optimization_goal,
                "billing_event": billing_event,
                "targeting": json.dumps(targeting),
            },
        )

    # ── Creative ──────────────────────────────────────────────────────────

    async def create_creative(
        self,
        name: str,
        body: str,
        title: str,
        link_url: str,
        call_to_action: str = "LEARN_MORE",
        image_path: str | None = None,
    ) -> dict[str, Any]:
        """Create an ad creative."""
        args: dict[str, Any] = {
            "ad_account_id": self._ad_account_id,
            "name": name,
            "message": body,
            "headline": title,
            "link_url": link_url,
            "call_to_action_type": call_to_action,
        }
        if self._page_id:
            args["page_id"] = self._page_id
        if image_path:
            args["image_url"] = image_path
        return await self._call_tool("ads_create_creative", args)

    # ── Ad ────────────────────────────────────────────────────────────────

    async def create_ad(self, adset_id: str, name: str, creative_id: str) -> dict[str, Any]:
        """Create an ad linking a creative to an ad set."""
        return await self._call_tool(
            "ads_create_ad",
            {
                "ad_account_id": self._ad_account_id,
                "ad_set_id": adset_id,
                "ad_name": name,
                "creative": json.dumps({"creative_id": creative_id}),
            },
        )

    async def update_ad(self, ad_id: str, status: str) -> dict[str, Any]:
        return await self._update_entity_status(ad_id, "ad", status)

    # ── Insights ──────────────────────────────────────────────────────────

    async def get_insights(
        self,
        campaign_id: str,
        date_preset: str = "last_30d",
        fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get performance insights for a campaign via the async report flow
        (ads_entity_schedule_report -> ads_entity_get_report)."""
        report_fields = fields or [
            "name",
            "spend",
            "impressions",
            "reach",
            "frequency",
            "clicks",
            "ctr",
            "cpm",
        ]
        schedule = await self._call_tool(
            "ads_entity_schedule_report",
            {
                "ad_account_id": self._ad_account_id,
                "level": "campaign",
                "fields": report_fields,
                "filtering": [
                    {"field": "campaign.id", "operator": "IN", "value": [campaign_id]},
                ],
                "date_preset": date_preset,
            },
        )
        if not isinstance(schedule, dict):
            raise MetaAPIError(f"ads_entity_schedule_report: {schedule}")
        report_run_id = schedule.get("report_run_id")
        if not report_run_id:
            raise MetaAPIError(f"ads_entity_schedule_report returned no report_run_id: {schedule}")

        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(_REPORT_MAX_POLLS):
            params: dict[str, Any] = {"report_run_id": report_run_id}
            if cursor:
                params["cursor"] = cursor
            result = await self._call_tool("ads_entity_get_report", params)
            if not isinstance(result, dict):
                raise MetaAPIError(f"ads_entity_get_report: {result}")
            if result.get("status") in ("FAILED", "ERROR"):
                raise MetaAPIError(f"Meta Ads report failed: {result}")
            rows.extend(result.get("data", []))
            cursor = (result.get("pagination") or {}).get("next_cursor")
            if not cursor:
                break

        for row in rows:
            row.setdefault("campaign_name", row.get("name", campaign_id))
        return rows

    # ── Reach Estimate ────────────────────────────────────────────────────

    async def get_reach_estimate(
        self,
        targeting: dict[str, Any],
        daily_budget: float,
        optimize_for: str = "REACH",
    ) -> dict[str, Any]:
        """Estimate reach for a targeting + daily budget. Not implemented -- no MCP tool exists."""
        raise MetaAPIError(
            "get_reach_estimate() has no supported Meta Ads MCP tool — "
            "use MetaAdsAPIClient for reach estimates via Graph API"
        )

    # ── Tools list ────────────────────────────────────────────────────────

    @property
    def available_tools(self) -> list[str]:
        return list(self._tools.keys())
