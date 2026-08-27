# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Meta Ads MCP client — calls Meta's official Ads MCP server (mcp.facebook.com/ads).

Replaces direct Graph API calls (meta_ads_client.py / meta_ads_api_client.py) when
META_USE_MCP=true. All write operations create resources in PAUSED state by default,
matching Meta's own MCP behavior (a human must explicitly activate a resource).

Reference: https://developers.facebook.com/documentation/ads-commerce/ads-ai-connectors/ads-mcp-server/
(fetched 2026-08-27). Meta's docs publish tool names and one-line descriptions but do
not publish full JSON-RPC request/response schemas in prose — argument shapes below
follow the naming conventions Meta uses elsewhere in the Marketing API. Each mapped
tool name is verified against ``available_tools`` (populated from the server's own
``tools/list`` response) before it is called, so a renamed/removed tool fails fast
with a clear error instead of a cryptic JSON-RPC ``-32601``.

Confirmed from the official docs (2026-08-27 snapshot):
    - Transport: JSON-RPC 2.0 over HTTP POST to https://mcp.facebook.com/ads,
      authenticated via ``Authorization: Bearer <user access token>`` for
      programmatic/non-interactive clients (the OAuth-dialog flow is the
      alternative for chat clients like Claude Desktop/ChatGPT).
    - Required token scopes: ads_mcp_management, ads_read, ads_management,
      catalog_management, business_management, pages_show_list, instagram_basic.
    - Campaign-lifecycle tools: ads_create_campaign, ads_create_ad_set,
      ads_create_ad, ads_create_creative, ads_update_entity, ads_activate_entity.
    - Reporting tool: ads_get_ad_entities — lists campaigns/ad sets/ads together
      with performance metrics (spend, impressions, CTR, CPC, CPM, conversions),
      with filtering, breakdowns, sorting, and date ranges. This supersedes the
      earlier (undocumented, no-longer-listed) async report-scheduling tools an
      earlier iteration of this client assumed.
    - No MCP tool exists for reach estimation as of this snapshot — callers must
      fall back to MetaAdsAPIClient (Graph API) for that.

Not independently re-verified against a live account for this rewrite: the exact
JSON-RPC argument shapes for the write tools, and whether ``ad_account_id`` is
expected bare (no ``act_`` prefix) or with it. This client strips the ``act_``
prefix, matching a live-tested finding from 2026-07-31 (see project memory) and
Meta's own bare-ID convention elsewhere; re-verify before relying on this in
production.
"""

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MCP_URL = "https://mcp.facebook.com/ads"
_TOOL_TIMEOUT = 30.0


class MetaAuthError(Exception):
    """Raised when the Meta MCP server rejects the access token."""


class MetaAPIError(Exception):
    """Raised when a Meta MCP tool call returns an error, or the tool is unknown."""


class MetaAdsMCPClient:
    """Meta Ads client via Meta's official MCP server (mcp.facebook.com/ads).

    Uses JSON-RPC 2.0 over HTTP POST with a user access token — the programmatic
    auth path Meta's own get-started docs document via curl, as opposed to the
    OAuth-dialog flow used by interactive chat clients.

    Auth: user access token (not system user token) in Authorization: Bearer,
    scoped per the module docstring above.
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
        # Ads MCP tools take the bare numeric account ID, no "act_" prefix —
        # see the module docstring for how confident we are in this default.
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
        """Populate ``available_tools`` from the server's own catalog.

        Best-effort: a discovery failure must not block connecting, since a
        stale/unreachable tool list only degrades error messages later, it
        does not affect correctness of a subsequent successful call.
        """
        try:
            result = await self._call("tools/list", {})
            tools = result.get("tools", [])
            self._tools = {t["name"]: t for t in tools if "name" in t}
            logger.info("Meta Ads MCP: %d tools discovered", len(self._tools))
        except Exception as exc:  # noqa: BLE001 - discovery is best-effort; must not block connect
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
                "Meta MCP auth failed — ensure the token is a user access token "
                "(not a system user token) with the ads_mcp_management scope. "
                f"Detail: {resp.text}"
            )
        if resp.status_code >= 400:
            raise MetaAPIError(f"Meta MCP HTTP {resp.status_code}: {resp.text}")

        # The server may answer with a plain JSON body or an SSE-framed one
        # (SSE responses commonly carry an "event: message" line before the
        # "data:" line, so the "data:" line is looked for anywhere, not only
        # as the very first line of the body).
        body = resp.text.strip()
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
        """Call an MCP tool, failing fast if it isn't in the discovered catalog.

        A pre-flight check against ``available_tools`` turns a renamed/removed
        tool into a clear error here rather than a generic JSON-RPC -32601 from
        the server — this is what made the earlier iteration's tool-name drift
        hard to diagnose (see project memory: a bogus tool name and a real but
        wrong one returned identical opaque errors).
        """
        if self._tools and tool_name not in self._tools:
            raise MetaAPIError(
                f"'{tool_name}' is not in the Meta Ads MCP server's advertised tool "
                f"catalog. Available tools: {sorted(self._tools)}"
            )
        result = await self._call("tools/call", {"name": tool_name, "arguments": arguments})
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
        """Update a campaign/ad_set/ad's status: ACTIVE | PAUSED | DELETED.

        Per the official tool descriptions, activation is a dedicated tool
        (``ads_activate_entity``) separate from the general-purpose field
        updater (``ads_update_entity``) — activation is treated as the
        spend-starting action, everything else is a plain field update.
        """
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
        """List campaigns for the ad account via ads_get_ad_entities."""
        result = await self._call_tool(
            "ads_get_ad_entities",
            {
                "ad_account_id": self._ad_account_id,
                "entity_type": "campaign",
                "fields": ["id", "name", "status", "objective"],
            },
        )
        if isinstance(result, dict):
            return result.get("data", result.get("entities", []))
        return result if isinstance(result, list) else []

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

        No bid_amount here: create_campaign() always creates CBO
        (campaign-budget-optimized) campaigns, which reject ad-set-level bid
        fields — confirmed live against the Graph API, and the ad set/campaign
        object model is shared between the Graph API and the MCP tools.
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
        """Create a single-image link ad creative (per ads_create_creative's docs)."""
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
        """Get performance insights for a campaign via ads_get_ad_entities.

        ads_get_ad_entities returns campaigns/ad sets/ads together with
        performance metrics directly — there is no separate async
        schedule/poll report flow in the current tool catalog.
        """
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
        result = await self._call_tool(
            "ads_get_ad_entities",
            {
                "ad_account_id": self._ad_account_id,
                "entity_type": "campaign",
                "entity_ids": [campaign_id],
                "fields": report_fields,
                "date_preset": date_preset,
            },
        )
        if isinstance(result, dict):
            rows = result.get("data", result.get("entities", []))
        else:
            rows = result
        if not isinstance(rows, list):
            raise MetaAPIError(f"ads_get_ad_entities: unexpected response shape: {result}")
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
