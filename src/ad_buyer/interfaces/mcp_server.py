# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""MCP (Model Context Protocol) server for the Ad Buyer Agent.

Exposes buyer operations as MCP tools via FastMCP SSE transport.
This is the foundation server that all other MCP tool modules build upon.

Tool categories:
  - Foundation: get_setup_status, health_check, get_config
  - Campaign Management: list_campaigns, get_campaign_status,
    check_pacing, review_budgets (buyer-3w3)
  - Templates: list_templates, create_template,
    instantiate_from_template (buyer-5x7)
  - Reporting: get_deal_performance, get_campaign_report,
    get_pacing_report (buyer-5x7)

Mount into a FastAPI app:
    from ad_buyer.interfaces.mcp_server import mount_mcp
    mount_mcp(app)

This creates an SSE endpoint at /mcp/sse for MCP client connections
(Claude Desktop, ChatGPT, Cursor, Windsurf, etc.).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

from ..auth.key_store import ApiKeyStore
from ..config.settings import Settings
from ..storage.campaign_store import CampaignStore
from ..storage.deal_store import DealStore
from ..storage.pacing_store import PacingStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MCP Server Instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="ad-buyer-agent",
    instructions=(
        "You are the IAB Tech Lab Ad Buyer Agent assistant. "
        "You help users manage advertising campaigns, deals, seller "
        "relationships, and buyer operations through the buyer agent system. "
        "Use the available tools to check system status, review configuration, "
        "and manage buyer workflows."
    ),
)


def _get_settings() -> Settings:
    """Get a fresh Settings instance.

    Returns a new instance each time so that environment changes
    (and test patches) are reflected.
    """
    return Settings()


def _get_campaign_store() -> CampaignStore:
    """Get a connected CampaignStore instance.

    Uses the database URL from settings. Returns a new connected
    instance each time so that test patches are reflected.
    """
    settings = _get_settings()
    store = CampaignStore(settings.database_url)
    store.connect()
    return store


def _get_pacing_store() -> PacingStore:
    """Get a connected PacingStore instance.

    Uses the database URL from settings. Returns a new connected
    instance each time so that test patches are reflected.
    """
    settings = _get_settings()
    store = PacingStore(settings.database_url)
    store.connect()
    return store


# Deal store with test-injection support
_deal_store_override: DealStore | None = None


def _get_deal_store() -> DealStore:
    """Get a connected DealStore instance.

    If a test override has been set via ``_set_deal_store()``, returns
    that instance.  Otherwise creates a new one from settings.
    """
    if _deal_store_override is not None:
        return _deal_store_override
    settings = _get_settings()
    store = DealStore(settings.database_url)
    store.connect()
    return store


def _set_deal_store(store: DealStore | None) -> None:
    """Set (or clear) a DealStore override for testing.

    Pass a connected in-memory DealStore to inject test data.
    Pass None to revert to the default settings-based store.
    """
    global _deal_store_override
    _deal_store_override = store


def _get_api_key_store() -> ApiKeyStore:
    """Get an ApiKeyStore instance.

    Uses the default store path (~/.ad_buyer/seller_keys.json).
    Returns a new instance each time so that test patches are reflected.
    """
    return ApiKeyStore()


def _mask_key(key: str) -> str:
    """Mask an API key for display, showing only last 4 characters."""
    if len(key) <= 4:
        return "****"
    return "*" * (len(key) - 4) + key[-4:]


# ---------------------------------------------------------------------------
# Foundation Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def get_setup_status() -> str:
    """Check the current setup and configuration state of the buyer agent.

    Returns a JSON object with:
    - setup_complete: whether all required configuration is in place
    - checks: individual status checks (seller endpoints, database, etc.)
    """
    settings = _get_settings()
    checks: dict[str, bool] = {}

    # Check seller endpoints
    seller_endpoints = settings.get_seller_endpoints()
    checks["seller_endpoints_configured"] = len(seller_endpoints) > 0

    # Check database accessibility
    db_accessible = False
    try:
        db_url = settings.database_url
        # Strip sqlite:/// prefix for direct connection test
        if db_url.startswith("sqlite:///"):
            db_path = db_url[len("sqlite:///"):]
        else:
            db_path = db_url

        # Try a lightweight connection test
        conn = sqlite3.connect(db_path)
        conn.execute("SELECT 1")
        conn.close()
        db_accessible = True
    except (sqlite3.Error, OSError):
        db_accessible = False
    checks["database_accessible"] = db_accessible

    # Check API key configuration
    checks["api_key_configured"] = bool(settings.api_key)

    # Check LLM configuration
    checks["llm_configured"] = bool(settings.anthropic_api_key)

    # Overall setup completeness
    # Minimum required: seller endpoints + database
    setup_complete = (
        checks["seller_endpoints_configured"]
        and checks["database_accessible"]
    )

    result = {
        "setup_complete": setup_complete,
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return json.dumps(result, indent=2)


@mcp.tool()
def health_check() -> str:
    """Check the health of buyer agent services.

    Returns a JSON object with:
    - status: overall health (healthy, degraded, unhealthy)
    - version: system version
    - services: individual service health details
    """
    from .. import __version__

    settings = _get_settings()
    services: dict[str, dict] = {}

    # Check database service
    try:
        db_url = settings.database_url
        if db_url.startswith("sqlite:///"):
            db_path = db_url[len("sqlite:///"):]
        else:
            db_path = db_url

        conn = sqlite3.connect(db_path)
        conn.execute("SELECT 1")
        conn.close()
        services["database"] = {"status": "healthy"}
    except (sqlite3.Error, OSError) as exc:
        services["database"] = {"status": "unhealthy", "error": str(exc)}

    # Check seller connectivity (lightweight -- just config presence)
    seller_endpoints = settings.get_seller_endpoints()
    if seller_endpoints:
        services["seller_connections"] = {
            "status": "configured",
            "endpoint_count": len(seller_endpoints),
        }
    else:
        services["seller_connections"] = {
            "status": "not_configured",
            "endpoint_count": 0,
        }

    # Check event bus availability
    services["event_bus"] = {"status": "healthy"}

    # Determine overall status
    unhealthy_count = sum(
        1 for s in services.values() if s.get("status") == "unhealthy"
    )
    if unhealthy_count == 0:
        overall_status = "healthy"
    elif unhealthy_count < len(services):
        overall_status = "degraded"
    else:
        overall_status = "unhealthy"

    result = {
        "status": overall_status,
        "version": __version__,
        "services": services,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return json.dumps(result, indent=2)


@mcp.tool()
def get_config() -> str:
    """Get the current buyer agent configuration.

    Returns non-sensitive configuration values. API keys and secrets
    are never exposed through this tool.

    Returns a JSON object with:
    - environment: current environment (development, staging, production)
    - seller_endpoints: configured seller agent URLs
    - database_url: database connection string
    - llm settings: model names, temperature, etc.
    """
    settings = _get_settings()

    result = {
        "environment": settings.environment,
        "seller_endpoints": settings.get_seller_endpoints(),
        "iab_server_url": settings.iab_server_url,
        "database_url": settings.database_url,
        "default_llm_model": settings.default_llm_model,
        "manager_llm_model": settings.manager_llm_model,
        "llm_temperature": settings.llm_temperature,
        "llm_max_tokens": settings.llm_max_tokens,
        "cors_allowed_origins": settings.get_cors_origins(),
        "log_level": settings.log_level,
        "crew_memory_enabled": settings.crew_memory_enabled,
        "crew_verbose": settings.crew_verbose,
        "crew_max_iterations": settings.crew_max_iterations,
    }
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Campaign Management Tools (buyer-3w3)
# ---------------------------------------------------------------------------


@mcp.tool()
def list_campaigns(status: str | None = None) -> str:
    """List all campaigns with optional status filter.

    Args:
        status: Optional campaign status to filter by
            (e.g. DRAFT, PLANNING, BOOKING, READY, ACTIVE, PAUSED,
            COMPLETED, CANCELED). If omitted, returns all campaigns.

    Returns a JSON object with:
    - total: number of campaigns matching the filter
    - campaigns: list of campaign summary objects
    """
    store = _get_campaign_store()
    try:
        kwargs: dict[str, Any] = {}
        if status is not None:
            kwargs["status"] = status
        campaigns = store.list_campaigns(**kwargs)

        campaign_summaries = []
        for c in campaigns:
            campaign_summaries.append({
                "campaign_id": c["campaign_id"],
                "campaign_name": c["campaign_name"],
                "advertiser_id": c["advertiser_id"],
                "status": c["status"],
                "total_budget": c["total_budget"],
                "currency": c.get("currency", "USD"),
                "flight_start": c["flight_start"],
                "flight_end": c["flight_end"],
            })

        result = {
            "total": len(campaign_summaries),
            "campaigns": campaign_summaries,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
        store.disconnect()


@mcp.tool()
def get_campaign_status(campaign_id: str) -> str:
    """Get detailed status of a specific campaign.

    Args:
        campaign_id: The unique identifier of the campaign.

    Returns a JSON object with:
    - campaign_id, campaign_name, status, budget, flight dates
    - pacing: latest pacing snapshot data (or null if no data)
    - error: present only if the campaign was not found
    """
    campaign_store = _get_campaign_store()
    pacing_store = _get_pacing_store()
    try:
        campaign = campaign_store.get_campaign(campaign_id)
        if campaign is None:
            return json.dumps(
                {"error": f"Campaign not found: {campaign_id}"},
                indent=2,
            )

        # Get latest pacing snapshot
        latest = pacing_store.get_latest_pacing_snapshot(campaign_id)

        pacing_data = None
        if latest is not None:
            pacing_data = {
                "total_spend": latest.total_spend,
                "expected_spend": latest.expected_spend,
                "pacing_pct": latest.pacing_pct,
                "deviation_pct": latest.deviation_pct,
                "snapshot_timestamp": latest.timestamp.isoformat(),
            }

        # Parse channels JSON if present
        channels_raw = campaign.get("channels")
        channels: list[dict[str, Any]] = []
        if channels_raw:
            try:
                channels = json.loads(channels_raw)
            except (json.JSONDecodeError, TypeError):
                channels = []

        result = {
            "campaign_id": campaign["campaign_id"],
            "campaign_name": campaign["campaign_name"],
            "advertiser_id": campaign["advertiser_id"],
            "status": campaign["status"],
            "total_budget": campaign["total_budget"],
            "currency": campaign.get("currency", "USD"),
            "flight_start": campaign["flight_start"],
            "flight_end": campaign["flight_end"],
            "channels": channels,
            "pacing": pacing_data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
        campaign_store.disconnect()
        pacing_store.disconnect()


@mcp.tool()
def check_pacing(campaign_id: str) -> str:
    """Check budget pacing for a campaign.

    Determines whether the campaign is on track, behind, or ahead
    of its expected spend based on the latest pacing snapshot.

    Pacing thresholds:
    - on_track: deviation within +/- 10%
    - behind: deviation below -10%
    - ahead: deviation above +10%
    - no_data: no pacing snapshots available

    Args:
        campaign_id: The unique identifier of the campaign.

    Returns a JSON object with:
    - pacing_status: on_track, behind, ahead, or no_data
    - pacing_pct: current pacing percentage
    - deviation_pct: deviation from expected pacing
    - total_budget, total_spend, expected_spend
    - channel_pacing: per-channel pacing breakdown (if available)
    - error: present only if the campaign was not found
    """
    campaign_store = _get_campaign_store()
    pacing_store = _get_pacing_store()
    try:
        campaign = campaign_store.get_campaign(campaign_id)
        if campaign is None:
            return json.dumps(
                {"error": f"Campaign not found: {campaign_id}"},
                indent=2,
            )

        latest = pacing_store.get_latest_pacing_snapshot(campaign_id)

        if latest is None:
            result = {
                "campaign_id": campaign_id,
                "campaign_name": campaign["campaign_name"],
                "pacing_status": "no_data",
                "total_budget": campaign["total_budget"],
                "total_spend": 0.0,
                "expected_spend": 0.0,
                "pacing_pct": 0.0,
                "deviation_pct": 0.0,
                "channel_pacing": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            return json.dumps(result, indent=2)

        # Determine pacing status from deviation
        deviation = latest.deviation_pct
        if deviation < -10.0:
            pacing_status = "behind"
        elif deviation > 10.0:
            pacing_status = "ahead"
        else:
            pacing_status = "on_track"

        # Build channel pacing breakdown
        channel_pacing = []
        for ch in latest.channel_snapshots:
            channel_pacing.append({
                "channel": ch.channel,
                "allocated_budget": ch.allocated_budget,
                "spend": ch.spend,
                "pacing_pct": ch.pacing_pct,
                "impressions": ch.impressions,
            })

        result = {
            "campaign_id": campaign_id,
            "campaign_name": campaign["campaign_name"],
            "pacing_status": pacing_status,
            "total_budget": latest.total_budget,
            "total_spend": latest.total_spend,
            "expected_spend": latest.expected_spend,
            "pacing_pct": latest.pacing_pct,
            "deviation_pct": latest.deviation_pct,
            "channel_pacing": channel_pacing,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
        campaign_store.disconnect()
        pacing_store.disconnect()


@mcp.tool()
def review_budgets() -> str:
    """Review budget allocation and spend across all campaigns.

    Provides an aggregate view of total budget and spend across all
    campaigns, plus per-campaign budget breakdowns with delivery
    percentages.

    Returns a JSON object with:
    - total_budget: sum of all campaign budgets
    - total_spend: sum of all campaign spend (from latest pacing)
    - campaigns: per-campaign budget and spend details
    - timestamp: when this review was generated
    """
    campaign_store = _get_campaign_store()
    pacing_store = _get_pacing_store()
    try:
        campaigns = campaign_store.list_campaigns()

        total_budget = 0.0
        total_spend = 0.0
        campaign_budgets = []

        for c in campaigns:
            budget = c["total_budget"]
            total_budget += budget

            # Get latest pacing for spend data
            latest = pacing_store.get_latest_pacing_snapshot(c["campaign_id"])
            spend = latest.total_spend if latest else 0.0
            total_spend += spend

            # Calculate delivery percentage
            delivery_pct = (spend / budget * 100.0) if budget > 0 else 0.0

            campaign_budgets.append({
                "campaign_id": c["campaign_id"],
                "campaign_name": c["campaign_name"],
                "status": c["status"],
                "total_budget": budget,
                "total_spend": spend,
                "delivery_pct": round(delivery_pct, 1),
                "currency": c.get("currency", "USD"),
            })

        result = {
            "total_budget": total_budget,
            "total_spend": total_spend,
            "overall_delivery_pct": (
                round(total_spend / total_budget * 100.0, 1)
                if total_budget > 0 else 0.0
            ),
            "campaign_count": len(campaign_budgets),
            "campaigns": campaign_budgets,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
        campaign_store.disconnect()
        pacing_store.disconnect()


# ---------------------------------------------------------------------------
# Approval Tools (buyer-j7f)
# ---------------------------------------------------------------------------


@mcp.tool()
def list_pending_approvals(campaign_id: str | None = None) -> str:
    """List approval requests that are awaiting a decision.

    Returns pending approval requests for deals, campaigns, and budget
    changes. Wraps the existing approval gate system.

    Args:
        campaign_id: Optional campaign ID to filter by. If omitted,
            returns all pending approvals.

    Returns a JSON object with:
    - total: number of pending approval requests
    - pending: list of pending approval request objects
    - timestamp: when this list was generated
    """
    store = _get_campaign_store()
    try:
        store.create_approval_requests_table()
        kwargs: dict[str, Any] = {"status": "pending"}
        if campaign_id is not None:
            kwargs["campaign_id"] = campaign_id

        rows = store.list_approval_requests(**kwargs)

        pending = []
        for row in rows:
            pending.append({
                "approval_request_id": row["approval_request_id"],
                "campaign_id": row["campaign_id"],
                "stage": row["stage"],
                "status": row["status"],
                "requested_at": row["requested_at"],
                "context": json.loads(row.get("context") or "{}"),
            })

        result = {
            "total": len(pending),
            "pending": pending,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
        store.disconnect()


@mcp.tool()
def approve_or_reject(
    approval_request_id: str,
    decision: str,
    reviewer: str,
    reason: str = "",
) -> str:
    """Approve or reject a pending approval request.

    Updates the approval request status and records the reviewer's
    decision. The decision must be either "approved" or "rejected".

    Args:
        approval_request_id: The unique ID of the approval request.
        decision: Either "approved" or "rejected".
        reviewer: Identifier of the person or system making the decision.
        reason: Optional explanation for the decision.

    Returns a JSON object with:
    - approval_request_id, previous_status, new_status, reviewer, reason
    - error: present only if the request was not found or already decided
    """
    store = _get_campaign_store()
    try:
        store.create_approval_requests_table()

        # Look up the existing request
        request = store.get_approval_request(approval_request_id)
        if request is None:
            return json.dumps(
                {"error": f"Approval request not found: {approval_request_id}"},
                indent=2,
            )

        # Check if already decided
        if request["status"] != "pending":
            return json.dumps(
                {
                    "error": (
                        f"Approval request {approval_request_id} already decided "
                        f"(status={request['status']})"
                    )
                },
                indent=2,
            )

        # Normalize decision
        new_status = decision.lower()
        if new_status not in ("approved", "rejected"):
            return json.dumps(
                {"error": f"Invalid decision: {decision}. Must be 'approved' or 'rejected'."},
                indent=2,
            )

        # Update the request
        now = datetime.now(timezone.utc)
        store.update_approval_request(
            approval_request_id,
            status=new_status,
            decided_at=now.isoformat(),
            reviewer=reviewer,
            notes=reason if reason else None,
        )

        result = {
            "approval_request_id": approval_request_id,
            "previous_status": "pending",
            "new_status": new_status,
            "reviewer": reviewer,
            "reason": reason,
            "timestamp": now.isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
        store.disconnect()


# ---------------------------------------------------------------------------
# API Key Management Tools (buyer-j7f)
# ---------------------------------------------------------------------------


@mcp.tool()
def list_api_keys() -> str:
    """List configured API keys for seller integrations.

    Returns seller URLs and masked key values. Full key values are
    never exposed through this tool for security.

    Returns a JSON object with:
    - total: number of configured API keys
    - keys: list of objects with seller_url and masked_key
    - timestamp: when this list was generated
    """
    key_store = _get_api_key_store()

    sellers = key_store.list_sellers()
    keys = []
    for seller_url in sellers:
        raw_key = key_store.get_key(seller_url)
        keys.append({
            "seller_url": seller_url,
            "masked_key": _mask_key(raw_key) if raw_key else "****",
        })

    result = {
        "total": len(keys),
        "keys": keys,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return json.dumps(result, indent=2)


@mcp.tool()
def create_api_key(seller_url: str, api_key: str) -> str:
    """Store or replace an API key for a seller integration.

    If a key already exists for the seller URL, it is replaced.
    The response confirms creation but does not expose the full key.

    Args:
        seller_url: Base URL of the seller agent.
        api_key: The API key value to store.

    Returns a JSON object with:
    - seller_url: the seller URL the key was stored for
    - created: true if the key was stored successfully
    - masked_key: masked version of the stored key
    - timestamp: when the key was created/updated
    """
    key_store = _get_api_key_store()

    key_store.add_key(seller_url, api_key)

    result = {
        "seller_url": seller_url,
        "created": True,
        "masked_key": _mask_key(api_key),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return json.dumps(result, indent=2)


@mcp.tool()
def revoke_api_key(seller_url: str) -> str:
    """Revoke (remove) an API key for a seller integration.

    Permanently removes the stored API key for the given seller URL.
    If no key exists for the URL, returns revoked=false.

    Args:
        seller_url: Base URL of the seller agent whose key to revoke.

    Returns a JSON object with:
    - seller_url: the seller URL
    - revoked: true if a key was found and removed, false otherwise
    - timestamp: when the revocation was processed
    """
    key_store = _get_api_key_store()

    removed = key_store.remove_key(seller_url)

    result = {
        "seller_url": seller_url,
        "revoked": removed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Mounting
# ---------------------------------------------------------------------------


def mount_mcp(app: FastAPI) -> None:
    """Mount the MCP SSE server onto a FastAPI application.

    Creates an SSE endpoint at /mcp/sse that MCP clients can connect to.

    Args:
        app: The FastAPI application to mount onto.
    """
    sse_app = mcp.sse_app()
    app.mount("/mcp/sse", sse_app)
    logger.info("MCP SSE server mounted at /mcp/sse")
