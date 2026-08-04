"""End-to-end check for MetaAdsMCPClient (src/ad_buyer/clients/meta_ads_mcp_client.py)
against the real Meta Ads MCP server, plus the two crewAI tools that call it
(MetaReportingTool, MetaInventoryTool).

Usage:
    export META_MCP_TOKEN="<user access token>"   # or META_ACCESS_TOKEN
    export META_AD_ACCOUNT_ID="act_4509330366054796"
    python3 scripts/test_meta_ads_mcp_client.py

    # Also exercise the write path (create_campaign/create_adset/activate/
    # pause, cleaned up with delete() at the end). Opt-in since it mutates
    # real state, even though it's a PAUSED test campaign with no spend risk
    # on an account with no payment method:
    export META_MCP_TEST_WRITES=true

Known, confirmed-not-a-bug limitations (checked 2026-08-04, re-confirmed
across two separate live runs on two different days):
  - get_insights() hits Meta's own "ads_entity_schedule_report is still
    gradually rolling out" gate for this ad account.
  - get_reach_estimate() / list_campaigns() raise immediately -- no
    equivalent tool exists in the real 97-tool catalog.
  - create_creative()/create_ad() can't be exercised: this ad account/token
    has zero linked Facebook Pages (ads_get_ad_account_pages and
    ads_get_user_pages both return empty).
  - delete() only ever force-pauses on Meta's side, never hard-deletes.
"""

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

# Load meta_ads_mcp_client.py directly by file path rather than importing the
# ad_buyer package, which would pull in the full project's dependency tree
# (crewai, mcp, etc.) just to exercise the client. The module itself has no
# relative imports, so this is safe.
_MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "ad_buyer"
    / "clients"
    / "meta_ads_mcp_client.py"
)
_spec = importlib.util.spec_from_file_location("meta_ads_mcp_client", _MODULE_PATH)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
MetaAdsMCPClient = _module.MetaAdsMCPClient
MetaAPIError = _module.MetaAPIError
MetaAuthError = _module.MetaAuthError

TOKEN = os.environ.get("META_MCP_TOKEN") or os.environ.get("META_ACCESS_TOKEN", "")
AD_ACCOUNT_ID = os.environ.get("META_AD_ACCOUNT_ID", "")
PAGE_ID = os.environ.get("META_PAGE_ID", "")
TEST_WRITES = os.environ.get("META_MCP_TEST_WRITES", "").lower() == "true"


async def check_connection(client: MetaAdsMCPClient) -> None:
    print("=" * 64)
    print("[1] Connection / tool discovery / ads_get_ad_accounts")
    print("=" * 64)
    print(f"tools discovered: {len(client.available_tools)}")
    result = await client._call_tool("ads_get_ad_accounts", {})
    accounts = [a["ad_account_id"] for a in result.get("ad_accounts", [])]
    print(f"accounts reachable: {accounts}")


async def check_write_path(client: MetaAdsMCPClient) -> str | None:
    print("\n" + "=" * 64)
    print("[2] Write path: create_campaign -> create_adset -> activate/pause")
    print("=" * 64)

    campaign = await client.create_campaign(
        name="claude-e2e-test", objective="OUTCOME_TRAFFIC", daily_budget_cents=100
    )
    campaign_id = campaign.get("campaign_id")
    if not campaign_id:
        print(f"create_campaign -> FAILED: {campaign}")
        return None
    print(f"create_campaign -> {campaign_id} ({campaign.get('status')})")

    adset = await client.create_adset(
        campaign_id=campaign_id,
        name="claude-e2e-test-adset",
        optimization_goal="IMPRESSIONS",
        billing_event="IMPRESSIONS",
        targeting_countries=["US"],
    )
    adset_id = adset.get("ad_set_id")
    if not adset_id:
        print(f"create_adset -> FAILED: {adset}")
    else:
        print(f"create_adset -> {adset_id} ({adset.get('status')})")

    r = await client.activate(campaign_id)
    print(f"activate(campaign) -> success={r.get('success')} status={r.get('status')}")

    r = await client.pause(campaign_id)
    print(f"pause(campaign) -> success={r.get('success')}")

    if adset_id:
        r = await client._update_entity_status(adset_id, "ad_set", "PAUSED")
        print(f"pause(ad_set) -> success={r.get('success')}")

    return campaign_id


async def check_read_paths(client: MetaAdsMCPClient, campaign_id: str | None) -> None:
    print("\n" + "=" * 64)
    print("[3] Read/unsupported paths: get_insights, list_campaigns, get_reach_estimate")
    print("=" * 64)

    print("\nget_insights() ...")
    try:
        rows = await client.get_insights(campaign_id or "0")
        print(f"  success: {json.dumps(rows, indent=2)[:500]}")
    except (MetaAuthError, MetaAPIError) as e:
        print(f"  {type(e).__name__}: {e}")

    print("\nlist_campaigns() ...")
    try:
        await client.list_campaigns()
        print("  UNEXPECTED: did not raise")
    except (MetaAuthError, MetaAPIError) as e:
        print(f"  raised as expected: {e}")

    print("\nget_reach_estimate() ...")
    try:
        await client.get_reach_estimate(
            targeting={"geo_locations": {"countries": ["US"]}}, daily_budget=10.0
        )
        print("  UNEXPECTED: did not raise")
    except (MetaAuthError, MetaAPIError) as e:
        print(f"  raised as expected: {e}")


async def check_pages(client: MetaAdsMCPClient) -> None:
    print("\n" + "=" * 64)
    print("[4] Pages available (create_creative/create_ad testability signal)")
    print("=" * 64)
    pages = await client._call_tool(
        "ads_get_ad_account_pages", {"ad_account_id": AD_ACCOUNT_ID.removeprefix("act_")}
    )
    print(f"ad_account_pages: {pages}")
    user_pages = await client._call_tool("ads_get_user_pages", {})
    print(f"user_pages: {user_pages}")


async def check_crewai_flow() -> None:
    print("\n" + "=" * 64)
    print("[5] crewAI flow: MetaReportingTool / MetaInventoryTool via .run()")
    print("=" * 64)
    try:
        from ad_buyer.config.settings import settings
        from ad_buyer.tools.reporting.meta_reporting import MetaReportingTool
        from ad_buyer.tools.research.meta_inventory import MetaInventoryTool
    except ImportError:
        print(
            "  skipped -- run inside the project's full venv "
            "(uv sync --python 3.12 --extra dev) to exercise this section."
        )
        return

    if not settings.meta_use_mcp:
        print("  skipped -- set META_USE_MCP=true to exercise the MCP path")
        return

    print("\nMetaInventoryTool.run() (-> get_reach_estimate) ...")
    print(" ", MetaInventoryTool().run(channel="social", budget=1500, geo_locations=["US"])[:200])

    print("\nMetaReportingTool.run() (-> get_insights) ...")
    print(" ", MetaReportingTool().run(campaign_ids=["0"], date_preset="last_30d"))


async def main() -> None:
    if not TOKEN:
        sys.exit("Set META_MCP_TOKEN (or META_ACCESS_TOKEN) in your environment first.")

    async with MetaAdsMCPClient(
        access_token=TOKEN, ad_account_id=AD_ACCOUNT_ID, page_id=PAGE_ID
    ) as client:
        await check_connection(client)

        campaign_id = None
        if TEST_WRITES:
            campaign_id = await check_write_path(client)
        else:
            print("\n(skipping write path -- set META_MCP_TEST_WRITES=true to exercise it)")

        await check_read_paths(client, campaign_id)
        await check_pages(client)

        if TEST_WRITES and campaign_id:
            print("\n" + "=" * 64)
            print("[6] Cleanup: delete(campaign)")
            print("=" * 64)
            r = await client.delete(campaign_id)
            forced = r.get("status_forced_to_paused")
            print(f"delete -> success={r.get('success')} forced_paused={forced}")

    await check_crewai_flow()


if __name__ == "__main__":
    asyncio.run(main())
