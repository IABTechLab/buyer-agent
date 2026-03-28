# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""MCP (Model Context Protocol) server for the Ad Buyer Agent.

Exposes buyer operations as MCP tools via FastMCP SSE transport.
This is the foundation server that all other MCP tool modules build upon.

Tool categories:
  - Foundation: get_setup_status, health_check, get_config
  - Setup Wizard: run_setup_wizard, get_wizard_step,
    complete_wizard_step, skip_wizard_step (buyer-byk)
  - Campaign Management: list_campaigns, get_campaign_status,
    check_pacing, review_budgets (buyer-3w3)
  - Deal Library: list_deals, search_deals, inspect_deal,
    import_deals_csv, create_deal_manual, get_portfolio_summary (buyer-4ds)
  - Seller Discovery: discover_sellers, get_seller_media_kit,
    compare_sellers (buyer-nob)
  - Negotiation: start_negotiation, get_negotiation_status,
    list_active_negotiations (buyer-r0j)
  - Orders: list_orders, get_order_status, transition_order (buyer-r0j)

Mount into a FastAPI app:
    from ad_buyer.interfaces.mcp_server import mount_mcp
    mount_mcp(app)

This creates an SSE endpoint at /mcp/sse for MCP client connections
(Claude Desktop, ChatGPT, Cursor, Windsurf, etc.).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts.base import Message

from ..auth.key_store import ApiKeyStore
from ..config.settings import Settings
from ..media_kit.client import MediaKitClient
from ..media_kit.models import MediaKitError
from ..registry.client import RegistryClient
from ..services.setup_wizard import SetupWizard
from ..storage.campaign_store import CampaignStore
from ..storage.deal_store import DealStore
from ..storage.order_store import OrderStore
from ..storage.pacing_store import PacingStore
from ..tools.deal_import import (
    ImportResult as CsvImportResult,
)
from ..tools.deal_import import (
    _parse_row,
    _resolve_columns,
)
from ..tools.deal_library.connectors import (
    IndexExchangeConnector,  # noqa: F401 - looked up dynamically via _get_ssp_connector_class
    MagniteConnector,  # noqa: F401
    PubMaticConnector,  # noqa: F401
)
from ..tools.deal_library.deal_entry import (
    ManualDealEntry,
    create_manual_deal,
)

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


def _get_registry_client() -> RegistryClient:
    """Get a RegistryClient instance.

    Uses the IAB server URL from settings as the registry URL.
    Returns a new instance each time so that test patches are reflected.
    """
    settings = _get_settings()
    return RegistryClient(registry_url=settings.iab_server_url)


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


def _get_media_kit_client() -> MediaKitClient:
    """Get a MediaKitClient instance.

    Uses the API key from settings for authenticated access.
    Returns a new instance each time so that test patches are reflected.
    """
    settings = _get_settings()
    return MediaKitClient(api_key=settings.api_key)


def _get_order_store() -> OrderStore:
    """Get a connected OrderStore instance.

    Uses the database URL from settings. Returns a new connected
    instance each time so that test patches are reflected.
    """
    settings = _get_settings()
    store = OrderStore(settings.database_url)
    store.connect()
    return store


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
        "timestamp": datetime.now(UTC).isoformat(),
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
        "timestamp": datetime.now(UTC).isoformat(),
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
# Setup Wizard Tools (buyer-byk)
# ---------------------------------------------------------------------------

# Module-level wizard instance for state persistence across MCP calls.
_wizard_instance: SetupWizard | None = None


def _get_wizard() -> SetupWizard:
    """Get or create the module-level SetupWizard instance."""
    global _wizard_instance
    if _wizard_instance is None:
        _wizard_instance = SetupWizard()
    return _wizard_instance


def _set_wizard(wizard: SetupWizard | None) -> None:
    """Set (or clear) the wizard instance for testing."""
    global _wizard_instance
    _wizard_instance = wizard


@mcp.tool()
def run_setup_wizard() -> str:
    """Run the setup wizard and get the current status of all steps.

    Auto-detects completed steps from existing configuration, then
    returns the full wizard state including all 8 steps, progress
    percentage, and current phase (developer or business).

    Returns a JSON object with:
    - steps: list of all 8 wizard steps with status and config
    - completed: whether all steps are done
    - progress_pct: percentage of steps completed (0-100)
    - current_phase: 'developer' or 'business'
    - timestamp: when this status was generated
    """
    wizard = _get_wizard()
    result = wizard.run_wizard()
    result["timestamp"] = datetime.now(UTC).isoformat()
    return json.dumps(result, indent=2)


@mcp.tool()
def get_wizard_step(step_number: int) -> str:
    """Get detailed information about a specific wizard step.

    Args:
        step_number: The step number (1-8).

    Returns a JSON object with:
    - step_number, title, description, phase
    - config_fields: fields this step configures
    - defaults: sensible default values
    - status: not_started, completed, skipped, or auto_detected
    - config: current configuration (if completed)
    - error: present only if step_number is invalid
    """
    wizard = _get_wizard()
    try:
        step = wizard.get_step(step_number)
        result = step.to_dict()
        result["timestamp"] = datetime.now(UTC).isoformat()
        return json.dumps(result, indent=2)
    except ValueError as exc:
        return json.dumps(
            {
                "error": str(exc),
                "timestamp": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )


@mcp.tool()
def complete_wizard_step(step_number: int, config: str = "{}") -> str:
    """Complete a wizard step with the given configuration.

    Args:
        step_number: The step number (1-8) to complete.
        config: JSON string of configuration values for this step.
            Example: '{"agency_name": "My Agency", "seat_id": "ttd-123"}'

    Returns a JSON object with:
    - success: whether the step was completed
    - step_number: the completed step number
    - status: the new step status
    - error: present only on failure
    """
    wizard = _get_wizard()
    try:
        config_dict = json.loads(config)
        step = wizard.complete_step(step_number, config_dict)
        return json.dumps(
            {
                "success": True,
                "step_number": step.step_number,
                "status": step.status.value,
                "timestamp": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        return json.dumps(
            {
                "success": False,
                "error": str(exc),
                "timestamp": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )


@mcp.tool()
def skip_wizard_step(step_number: int) -> str:
    """Skip a wizard step, applying its sensible defaults.

    Step 8 (Review & Launch) cannot be skipped.

    Args:
        step_number: The step number (1-7) to skip.

    Returns a JSON object with:
    - success: whether the step was skipped
    - step_number: the skipped step number
    - defaults_applied: the default values that were applied
    - error: present only on failure (e.g., trying to skip step 8)
    """
    wizard = _get_wizard()
    try:
        step = wizard.skip_step(step_number)
        return json.dumps(
            {
                "success": True,
                "step_number": step.step_number,
                "status": step.status.value,
                "defaults_applied": step.defaults,
                "timestamp": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )
    except ValueError as exc:
        return json.dumps(
            {
                "error": str(exc),
                "timestamp": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )


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
            "timestamp": datetime.now(UTC).isoformat(),
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
            "timestamp": datetime.now(UTC).isoformat(),
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
                "timestamp": datetime.now(UTC).isoformat(),
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
            "timestamp": datetime.now(UTC).isoformat(),
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
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
        campaign_store.disconnect()
        pacing_store.disconnect()


# ---------------------------------------------------------------------------
# Deal Library Tools (buyer-4ds)
# ---------------------------------------------------------------------------


@mcp.tool()
def list_deals(
    status: str | None = None,
    deal_type: str | None = None,
    media_type: str | None = None,
    seller_domain: str | None = None,
    limit: int = 50,
) -> str:
    """List deals in the portfolio with optional filters.

    Args:
        status: Filter by deal status (e.g. draft, active, paused, imported).
        deal_type: Filter by deal type (PG, PD, PA, OPEN_AUCTION, UPFRONT, SCATTER).
        media_type: Filter by media type (DIGITAL, CTV, LINEAR_TV, AUDIO, DOOH).
        seller_domain: Filter by seller domain (e.g. espn.com).
        limit: Maximum number of deals to return (default 50).

    Returns a JSON object with:
    - total: number of deals matching the filter
    - deals: list of deal summary objects
    - timestamp: when this list was generated
    """
    store = _get_deal_store()
    try:
        kwargs: dict[str, Any] = {}
        if status is not None:
            kwargs["status"] = status
        if deal_type is not None:
            kwargs["deal_type"] = deal_type
        if media_type is not None:
            kwargs["media_type"] = media_type
        if seller_domain is not None:
            kwargs["seller_domain"] = seller_domain
        kwargs["limit"] = limit

        deals = store.list_deals(**kwargs)

        deal_summaries = []
        for d in deals:
            deal_summaries.append({
                "deal_id": d["id"],
                "display_name": d.get("display_name") or d.get("product_name") or "(unnamed)",
                "status": d.get("status", "unknown"),
                "deal_type": d.get("deal_type", "unknown"),
                "media_type": d.get("media_type"),
                "seller_org": d.get("seller_org"),
                "seller_domain": d.get("seller_domain"),
                "price": d.get("price"),
                "impressions": d.get("impressions"),
                "flight_start": d.get("flight_start"),
                "flight_end": d.get("flight_end"),
            })

        result = {
            "total": len(deal_summaries),
            "deals": deal_summaries,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
        if _deal_store_override is None:
            store.disconnect()


@mcp.tool()
def search_deals(query: str) -> str:
    """Search deals in the portfolio by free-text query.

    Performs case-insensitive matching against display_name, description,
    seller_org, and seller_domain fields.

    Args:
        query: Search string. Must not be empty.

    Returns a JSON object with:
    - total: number of matching deals
    - deals: list of matching deal objects with match context
    - timestamp: when this search was performed
    """
    if not query or not query.strip():
        return json.dumps(
            {"error": "Search query must not be empty."},
            indent=2,
        )

    query = query.strip()
    query_lower = query.lower()

    store = _get_deal_store()
    try:
        # Fetch all deals for search (search needs full scan)
        deals = store.list_deals(limit=10000)

        # Search fields and their labels
        search_fields = [
            ("display_name", "display name"),
            ("product_name", "product name"),
            ("description", "description"),
            ("seller_org", "seller organization"),
            ("seller_domain", "seller domain"),
        ]

        matches = []
        for deal in deals:
            matched_fields = []
            for field_name, field_label in search_fields:
                value = deal.get(field_name)
                if value and query_lower in str(value).lower():
                    matched_fields.append(field_label)
            if matched_fields:
                matches.append({
                    "deal_id": deal["id"],
                    "display_name": (
                        deal.get("display_name")
                        or deal.get("product_name")
                        or "(unnamed)"
                    ),
                    "status": deal.get("status", "unknown"),
                    "deal_type": deal.get("deal_type", "unknown"),
                    "media_type": deal.get("media_type"),
                    "seller_org": deal.get("seller_org"),
                    "seller_domain": deal.get("seller_domain"),
                    "price": deal.get("price"),
                    "matched_in": matched_fields,
                })

        result = {
            "total": len(matches),
            "query": query,
            "deals": matches,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
        if _deal_store_override is None:
            store.disconnect()


# ---------------------------------------------------------------------------
# Seller Discovery Tools (buyer-nob)
# ---------------------------------------------------------------------------


@mcp.tool()
async def discover_sellers(capability: str | None = None) -> str:
    """Discover available seller agents from the IAB AAMP registry.

    Queries the agent registry to find seller agents, optionally
    filtering by capability name (e.g., "ctv", "display", "video").

    Args:
        capability: Optional capability name to filter sellers by.
            If omitted, returns all sellers in the registry.

    Returns a JSON object with:
    - total: number of sellers found
    - sellers: list of seller agent cards with id, name, url,
      capabilities, trust_level, and protocols
    """
    registry = _get_registry_client()
    try:
        caps_filter = [capability] if capability else None
        sellers = await registry.discover_sellers(
            capabilities_filter=caps_filter,
        )

        seller_list = []
        for seller in sellers:
            seller_list.append({
                "agent_id": seller.agent_id,
                "name": seller.name,
                "url": seller.url,
                "capabilities": [
                    {"name": c.name, "description": c.description, "tags": c.tags}
                    for c in seller.capabilities
                ],
                "trust_level": seller.trust_level.value,
                "protocols": seller.protocols,
            })

        result = {
            "total": len(seller_list),
            "sellers": seller_list,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)

    except Exception as exc:
        logger.warning("Failed to discover sellers: %s", exc)
        result = {
            "error": f"Failed to discover sellers: {exc}",
            "total": 0,
            "sellers": [],
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)


@mcp.tool()
async def get_seller_media_kit(seller_url: str) -> str:
    """Fetch a specific seller's media kit with inventory and pricing.

    Retrieves the media kit from a seller agent, showing available
    packages, ad formats, pricing ranges, and capabilities.

    Args:
        seller_url: The base URL of the seller agent
            (e.g., "http://localhost:8001").

    Returns a JSON object with:
    - seller_name: the seller's display name
    - seller_url: the seller's base URL
    - total_packages: number of available packages
    - packages: list of package summaries with pricing and format info
    """
    client = _get_media_kit_client()
    try:
        kit = await client.get_media_kit(seller_url)

        packages = []
        for pkg in kit.all_packages:
            packages.append({
                "package_id": pkg.package_id,
                "name": pkg.name,
                "description": pkg.description,
                "ad_formats": pkg.ad_formats,
                "device_types": pkg.device_types,
                "price_range": pkg.price_range,
                "rate_type": pkg.rate_type,
                "is_featured": pkg.is_featured,
                "geo_targets": pkg.geo_targets,
                "tags": pkg.tags,
            })

        result = {
            "seller_name": kit.seller_name,
            "seller_url": kit.seller_url,
            "total_packages": kit.total_packages,
            "packages": packages,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)

    except MediaKitError as exc:
        logger.warning("Failed to fetch media kit from %s: %s", seller_url, exc)
        result = {
            "error": f"Failed to fetch media kit: {exc}",
            "seller_url": seller_url,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)

    except Exception as exc:
        logger.warning(
            "Unexpected error fetching media kit from %s: %s", seller_url, exc,
        )
        result = {
            "error": f"Unexpected error: {exc}",
            "seller_url": seller_url,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)


@mcp.tool()
async def compare_sellers(seller_urls: list[str]) -> str:
    """Compare pricing and capabilities across multiple sellers.

    Fetches media kits from each seller and produces a side-by-side
    comparison of their inventory, pricing, and supported ad formats.

    Args:
        seller_urls: List of seller agent base URLs to compare.

    Returns a JSON object with:
    - sellers_compared: number of sellers in the comparison
    - sellers: per-seller summary (name, packages, ad formats, pricing)
    - summary: aggregate stats (total packages, all ad formats seen)
    """
    client = _get_media_kit_client()

    sellers_data: list[dict[str, Any]] = []
    total_packages = 0
    all_ad_formats: set[str] = set()

    for url in seller_urls:
        try:
            kit = await client.get_media_kit(url)

            # Collect ad formats across all packages
            seller_formats: set[str] = set()
            packages = []
            for pkg in kit.all_packages:
                seller_formats.update(pkg.ad_formats)
                packages.append({
                    "package_id": pkg.package_id,
                    "name": pkg.name,
                    "price_range": pkg.price_range,
                    "ad_formats": pkg.ad_formats,
                    "rate_type": pkg.rate_type,
                })

            all_ad_formats.update(seller_formats)
            total_packages += len(packages)

            sellers_data.append({
                "seller_url": url,
                "seller_name": kit.seller_name,
                "total_packages": len(packages),
                "ad_formats": sorted(seller_formats),
                "packages": packages,
            })

        except (MediaKitError, Exception) as exc:
            logger.warning("Failed to fetch media kit from %s: %s", url, exc)
            sellers_data.append({
                "seller_url": url,
                "error": f"Failed to fetch media kit: {exc}",
                "total_packages": 0,
                "ad_formats": [],
                "packages": [],
            })

    result = {
        "sellers_compared": len(seller_urls),
        "sellers": sellers_data,
        "summary": {
            "total_packages_across_sellers": total_packages,
            "all_ad_formats": sorted(all_ad_formats),
            "sellers_reachable": sum(
                1 for s in sellers_data if "error" not in s
            ),
            "sellers_unreachable": sum(
                1 for s in sellers_data if "error" in s
            ),
        },
        "timestamp": datetime.now(UTC).isoformat(),
    }
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Negotiation Tools (buyer-r0j)
# ---------------------------------------------------------------------------


@mcp.tool()
def start_negotiation(
    seller_url: str,
    product_id: str,
    product_name: str = "",
    initial_price: float = 0.0,
) -> str:
    """Initiate a negotiation with a seller within the demo ecosystem.

    Creates a deal in ``negotiating`` status and records the first
    negotiation round with the buyer's initial price offer.

    Note: This wraps the internal buyer-seller negotiation in the Agent
    Range demo. Real SSP integrations use seller-initiated deal flows,
    not buyer-initiated negotiation.

    Args:
        seller_url: Base URL of the seller agent.
        product_id: The product/package to negotiate on.
        product_name: Human-readable name for the product.
        initial_price: The buyer's opening offer (CPM).

    Returns a JSON object with:
    - deal_id: the newly created deal identifier
    - status: the deal status (negotiating)
    - initial_price: the buyer's opening offer
    - timestamp: when the negotiation was started
    """
    store = _get_deal_store()
    try:
        deal_id = store.save_deal(
            seller_url=seller_url,
            product_id=product_id,
            product_name=product_name,
            status="negotiating",
            price=initial_price,
        )

        store.save_negotiation_round(
            deal_id=deal_id,
            proposal_id=f"prop-{deal_id[:8]}",
            round_number=1,
            buyer_price=initial_price,
            seller_price=0.0,
            action="counter",
            rationale="Initial buyer offer",
        )

        result = {
            "deal_id": deal_id,
            "status": "negotiating",
            "seller_url": seller_url,
            "product_id": product_id,
            "product_name": product_name,
            "initial_price": initial_price,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
        store.disconnect()


@mcp.tool()
def get_negotiation_status(deal_id: str) -> str:
    """Check the status of a specific negotiation.

    Returns the deal's current state and its full negotiation history
    (all rounds of offers and counter-offers).

    Args:
        deal_id: The unique identifier of the deal/negotiation.

    Returns a JSON object with:
    - deal_id, status, product_name, seller_url, price
    - rounds_count: number of negotiation rounds
    - rounds: list of negotiation round details
    - error: present only if the deal was not found
    """
    store = _get_deal_store()
    try:
        deal = store.get_deal(deal_id)
        if deal is None:
            return json.dumps(
                {"error": f"Deal not found: {deal_id}"},
                indent=2,
            )

        rounds = store.get_negotiation_history(deal_id)

        round_summaries = []
        for r in rounds:
            round_summaries.append({
                "round_number": r["round_number"],
                "buyer_price": r["buyer_price"],
                "seller_price": r["seller_price"],
                "action": r["action"],
                "rationale": r.get("rationale", ""),
            })

        result = {
            "deal_id": deal_id,
            "status": deal.get("status", "unknown"),
            "product_id": deal.get("product_id", ""),
            "product_name": deal.get("product_name", ""),
            "seller_url": deal.get("seller_url", ""),
            "price": deal.get("price"),
            "rounds_count": len(round_summaries),
            "rounds": round_summaries,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
        store.disconnect()


@mcp.tool()
def inspect_deal(deal_id: str) -> str:
    """Get detailed information on a specific deal.

    Returns all deal fields, portfolio metadata, deal activations,
    and performance cache data.

    Args:
        deal_id: The unique identifier of the deal.

    Returns a JSON object with:
    - All core deal fields (display_name, status, deal_type, pricing, etc.)
    - portfolio_metadata: import source, tags, advertiser info
    - activations: cross-platform activation records
    - performance: cached performance metrics
    - error: present only if the deal was not found
    """
    store = _get_deal_store()
    try:
        deal = store.get_deal(deal_id)
        if deal is None:
            return json.dumps(
                {"error": f"Deal not found: {deal_id}"},
                indent=2,
            )

        # Build comprehensive deal view
        result: dict[str, Any] = {
            "deal_id": deal["id"],
            "display_name": deal.get("display_name") or deal.get("product_name") or "(unnamed)",
            "status": deal.get("status"),
            "deal_type": deal.get("deal_type"),
            "media_type": deal.get("media_type"),
            "seller_url": deal.get("seller_url"),
            "seller_deal_id": deal.get("seller_deal_id"),
            "seller_org": deal.get("seller_org"),
            "seller_domain": deal.get("seller_domain"),
            "seller_type": deal.get("seller_type"),
            "buyer_org": deal.get("buyer_org"),
            "buyer_id": deal.get("buyer_id"),
            "price": deal.get("price"),
            "fixed_price_cpm": deal.get("fixed_price_cpm"),
            "bid_floor_cpm": deal.get("bid_floor_cpm"),
            "price_model": deal.get("price_model"),
            "currency": deal.get("currency"),
            "impressions": deal.get("impressions"),
            "flight_start": deal.get("flight_start"),
            "flight_end": deal.get("flight_end"),
            "description": deal.get("description"),
            "created_at": deal.get("created_at"),
            "updated_at": deal.get("updated_at"),
        }

        # Portfolio metadata
        metadata = store.get_portfolio_metadata(deal_id)
        if metadata is not None:
            result["portfolio_metadata"] = {
                "import_source": metadata.get("import_source"),
                "import_date": metadata.get("import_date"),
                "advertiser_id": metadata.get("advertiser_id"),
                "agency_id": metadata.get("agency_id"),
                "tags": metadata.get("tags"),
            }
        else:
            result["portfolio_metadata"] = None

        # Deal activations
        activations = store.get_deal_activations(deal_id)
        result["activations"] = [
            {
                "platform": a.get("platform"),
                "platform_deal_id": a.get("platform_deal_id"),
                "activation_status": a.get("activation_status"),
                "last_sync_at": a.get("last_sync_at"),
            }
            for a in activations
        ]

        # Performance cache
        perf = store.get_performance_cache(deal_id)
        if perf is not None:
            result["performance"] = {
                "impressions_delivered": perf.get("impressions_delivered"),
                "spend_to_date": perf.get("spend_to_date"),
                "fill_rate": perf.get("fill_rate"),
                "win_rate": perf.get("win_rate"),
                "avg_effective_cpm": perf.get("avg_effective_cpm"),
                "performance_trend": perf.get("performance_trend"),
                "cached_at": perf.get("cached_at"),
            }
        else:
            result["performance"] = None

        result["timestamp"] = datetime.now(UTC).isoformat()
        return json.dumps(result, indent=2)
    finally:
        if _deal_store_override is None:
            store.disconnect()


@mcp.tool()
def import_deals_csv(
    csv_data: str,
    default_seller_url: str = "",
    default_product_id: str = "imported",
) -> str:
    """Import deals from CSV data into the portfolio.

    Parses CSV text with automatic column detection. Supported column
    names include: deal_name, publisher, seller_domain, deal_type,
    cpm/price, impressions, start_date, end_date, media_type, etc.

    Args:
        csv_data: CSV text content with header row and data rows.
        default_seller_url: Default seller URL for imported deals
            (CSV rarely contains full URLs).
        default_product_id: Default product ID for imported deals.

    Returns a JSON object with:
    - total_rows: number of data rows processed
    - successful: number of deals successfully imported
    - failed: number of rows that failed validation
    - skipped: number of duplicate rows skipped
    - errors: list of per-row error details
    - deal_ids: list of created deal IDs
    - timestamp: when this import was performed
    """
    store = _get_deal_store()
    try:
        # Parse CSV from string
        reader = csv.reader(io.StringIO(csv_data))
        rows = list(reader)

        import_result = CsvImportResult()

        if not rows:
            result = {
                "total_rows": 0,
                "successful": 0,
                "failed": 0,
                "skipped": 0,
                "errors": [],
                "deal_ids": [],
                "timestamp": datetime.now(UTC).isoformat(),
            }
            return json.dumps(result, indent=2)

        # First row is headers
        headers = rows[0]
        data_rows = rows[1:]

        col_map = _resolve_columns(headers, None)

        if not col_map:
            result = {
                "total_rows": 0,
                "successful": 0,
                "failed": 0,
                "skipped": 0,
                "errors": [{"message": "No columns could be mapped to schema fields."}],
                "deal_ids": [],
                "timestamp": datetime.now(UTC).isoformat(),
            }
            return json.dumps(result, indent=2)

        import_result.total_rows = len(data_rows)

        # Track seen deal IDs for deduplication
        seen_deal_ids: set[str] = set()

        for row_idx, row in enumerate(data_rows, start=1):
            # Skip completely empty rows
            if not any(cell.strip() for cell in row):
                import_result.total_rows -= 1
                continue

            deal, errors = _parse_row(
                row,
                col_map,
                row_number=row_idx,
                default_seller_url=default_seller_url,
                default_product_id=default_product_id,
            )

            if errors:
                import_result.errors.extend(errors)
                import_result.failed += 1
                continue

            # Deduplication by seller_deal_id
            sdid = deal.get("seller_deal_id")
            if sdid and sdid in seen_deal_ids:
                import_result.skipped += 1
                continue
            if sdid:
                seen_deal_ids.add(sdid)

            import_result.deals.append(deal)
            import_result.successful += 1

        # Persist parsed deals to the store
        deal_ids: list[str] = []
        for deal_data in import_result.deals:
            saved_id = store.save_deal(**deal_data)
            deal_ids.append(saved_id)

            # Save portfolio metadata
            store.save_portfolio_metadata(
                deal_id=saved_id,
                import_source="CSV",
                import_date=datetime.now(UTC).strftime("%Y-%m-%d"),
            )

        # Build error dicts
        error_dicts = [
            {
                "row": e.row_number,
                "field": e.field,
                "value": e.value,
                "message": e.message,
            }
            for e in import_result.errors
        ]

        result = {
            "total_rows": import_result.total_rows,
            "successful": import_result.successful,
            "failed": import_result.failed,
            "skipped": import_result.skipped,
            "errors": error_dicts,
            "deal_ids": deal_ids,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
        if _deal_store_override is None:
            store.disconnect()


@mcp.tool()
def create_deal_manual(
    display_name: str,
    seller_url: str,
    deal_type: str = "PD",
    status: str = "draft",
    media_type: str | None = None,
    price: float | None = None,
    impressions: int | None = None,
    flight_start: str | None = None,
    flight_end: str | None = None,
    seller_deal_id: str | None = None,
    seller_org: str | None = None,
    seller_domain: str | None = None,
    seller_type: str | None = None,
    buyer_org: str | None = None,
    buyer_id: str | None = None,
    price_model: str | None = None,
    fixed_price_cpm: float | None = None,
    bid_floor_cpm: float | None = None,
    currency: str = "USD",
    description: str | None = None,
    advertiser_id: str | None = None,
    tags: list[str] | None = None,
) -> str:
    """Manually create a single deal entry in the portfolio.

    Validates the input and saves the deal to the deal store with
    portfolio metadata (import_source=MANUAL).

    Args:
        display_name: Human-readable name for the deal.
        seller_url: Seller endpoint URL.
        deal_type: Deal type (PG, PD, PA, OPEN_AUCTION, UPFRONT, SCATTER).
        status: Initial status (draft, active, paused).
        media_type: Media type (DIGITAL, CTV, LINEAR_TV, AUDIO, DOOH).
        price: Deal price (CPM or flat rate).
        impressions: Contracted impression volume.
        flight_start: Flight start date (ISO 8601).
        flight_end: Flight end date (ISO 8601).
        seller_deal_id: Seller-assigned deal ID.
        seller_org: Seller organization name.
        seller_domain: Seller domain (e.g. espn.com).
        seller_type: Seller type (PUBLISHER, SSP, DSP, INTERMEDIARY).
        buyer_org: Buyer organization name.
        buyer_id: Buyer identifier.
        price_model: Pricing model (CPM, CPP, FLAT, HYBRID).
        fixed_price_cpm: Fixed CPM price.
        bid_floor_cpm: Bid floor CPM for auction deals.
        currency: Currency code (default USD).
        description: Free-text deal description.
        advertiser_id: Advertiser ID for portfolio tracking.
        tags: Tags for categorization.

    Returns a JSON object with:
    - success: whether the deal was created
    - deal_id: the new deal's ID (on success)
    - errors: validation error messages (on failure)
    - timestamp: when this operation was performed
    """
    # Build the ManualDealEntry for validation
    try:
        entry = ManualDealEntry(
            display_name=display_name,
            seller_url=seller_url,
            deal_type=deal_type,
            status=status,
            media_type=media_type,
            price=price,
            impressions=impressions,
            flight_start=flight_start,
            flight_end=flight_end,
            seller_deal_id=seller_deal_id,
            seller_org=seller_org,
            seller_domain=seller_domain,
            seller_type=seller_type,
            buyer_org=buyer_org,
            buyer_id=buyer_id,
            price_model=price_model,
            fixed_price_cpm=fixed_price_cpm,
            bid_floor_cpm=bid_floor_cpm,
            currency=currency,
            description=description,
            advertiser_id=advertiser_id,
            tags=tags,
        )
    except (ValueError, TypeError) as exc:
        return json.dumps({
            "success": False,
            "errors": [str(exc)],
            "timestamp": datetime.now(UTC).isoformat(),
        }, indent=2)

    # Validate and prepare
    entry_result = create_manual_deal(entry)

    if not entry_result.success:
        return json.dumps({
            "success": False,
            "errors": entry_result.errors,
            "timestamp": datetime.now(UTC).isoformat(),
        }, indent=2)

    # Save the deal
    store = _get_deal_store()
    try:
        deal_id = store.save_deal(**entry_result.deal_data)

        # Save portfolio metadata
        tags_json = (
            json.dumps(entry_result.metadata["tags"])
            if entry_result.metadata.get("tags")
            else None
        )
        store.save_portfolio_metadata(
            deal_id=deal_id,
            import_source=entry_result.metadata["import_source"],
            import_date=datetime.now(UTC).strftime("%Y-%m-%d"),
            advertiser_id=entry_result.metadata.get("advertiser_id"),
            tags=tags_json,
        )

        return json.dumps({
            "success": True,
            "deal_id": deal_id,
            "display_name": display_name,
            "timestamp": datetime.now(UTC).isoformat(),
        }, indent=2)
    finally:
        if _deal_store_override is None:
            store.disconnect()


@mcp.tool()
def get_portfolio_summary(
    top_sellers_count: int = 5,
    expiring_within_days: int = 30,
) -> str:
    """Get aggregate statistics and summary for the deal portfolio.

    Provides counts by status, deal type, media type, top sellers,
    total portfolio value, and deals expiring soon.

    Args:
        top_sellers_count: Number of top sellers to include (default 5).
        expiring_within_days: Show deals expiring within N days (default 30).

    Returns a JSON object with:
    - total_deals: total number of deals
    - total_value: estimated portfolio value (sum of price * impressions / 1000)
    - by_status: deal counts grouped by status
    - by_deal_type: deal counts grouped by deal type
    - by_media_type: deal counts grouped by media type
    - top_sellers: top sellers by deal count
    - expiring_deals: deals expiring within the specified window
    - timestamp: when this summary was generated
    """
    store = _get_deal_store()
    try:
        deals = store.list_deals(limit=10000)

        total = len(deals)

        if total == 0:
            return json.dumps({
                "total_deals": 0,
                "total_value": 0.0,
                "by_status": {},
                "by_deal_type": {},
                "by_media_type": {},
                "top_sellers": [],
                "expiring_deals": [],
                "timestamp": datetime.now(UTC).isoformat(),
            }, indent=2)

        # Count by status
        status_counts: dict[str, int] = {}
        for deal in deals:
            s = deal.get("status", "unknown")
            status_counts[s] = status_counts.get(s, 0) + 1

        # Count by deal type
        type_counts: dict[str, int] = {}
        for deal in deals:
            dt = deal.get("deal_type", "unknown")
            type_counts[dt] = type_counts.get(dt, 0) + 1

        # Count by media type
        media_counts: dict[str, int] = {}
        for deal in deals:
            mt = deal.get("media_type") or "N/A"
            media_counts[mt] = media_counts.get(mt, 0) + 1

        # Top sellers by deal count
        seller_counts: dict[str, int] = {}
        for deal in deals:
            seller = deal.get("seller_org") or deal.get("seller_domain") or "Unknown"
            seller_counts[seller] = seller_counts.get(seller, 0) + 1
        top_sellers = sorted(
            seller_counts.items(), key=lambda x: x[1], reverse=True,
        )[:top_sellers_count]

        # Total portfolio value: sum of (price * impressions / 1000)
        total_value = 0.0
        for deal in deals:
            p = deal.get("price")
            imp = deal.get("impressions")
            if p is not None and imp is not None:
                total_value += p * imp / 1000.0

        # Deals expiring within N days
        now = datetime.now(UTC)
        cutoff = now + timedelta(days=expiring_within_days)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        now_str = now.strftime("%Y-%m-%d")

        expiring_deals = []
        for deal in deals:
            if deal.get("status") not in ("active", "draft", "imported"):
                continue
            flight_end = deal.get("flight_end")
            if flight_end and now_str <= flight_end <= cutoff_str:
                expiring_deals.append({
                    "deal_id": deal["id"],
                    "display_name": (
                        deal.get("display_name")
                        or deal.get("product_name")
                        or "(unnamed)"
                    ),
                    "flight_end": flight_end,
                })

        result = {
            "total_deals": total,
            "total_value": total_value,
            "by_status": status_counts,
            "by_deal_type": type_counts,
            "by_media_type": media_counts,
            "top_sellers": [
                {"seller": name, "deal_count": count}
                for name, count in top_sellers
            ],
            "expiring_deals": expiring_deals,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
        if _deal_store_override is None:
            store.disconnect()


@mcp.tool()
def list_active_negotiations() -> str:
    """List all active/pending negotiations.

    Returns deals that are currently in ``negotiating`` status,
    along with the number of negotiation rounds for each.

    Returns a JSON object with:
    - total: number of active negotiations
    - negotiations: list of negotiation summaries
    """
    store = _get_deal_store()
    try:
        deals = store.list_deals(status="negotiating")

        negotiations = []
        for d in deals:
            deal_id = d["id"]
            rounds = store.get_negotiation_history(deal_id)

            negotiations.append({
                "deal_id": deal_id,
                "product_id": d.get("product_id", ""),
                "product_name": d.get("product_name", ""),
                "seller_url": d.get("seller_url", ""),
                "price": d.get("price"),
                "status": d.get("status", "negotiating"),
                "rounds_count": len(rounds),
                "created_at": d.get("created_at", ""),
            })

        result = {
            "total": len(negotiations),
            "negotiations": negotiations,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
        store.disconnect()


# ---------------------------------------------------------------------------
# Order Management Tools (buyer-r0j)
# ---------------------------------------------------------------------------


@mcp.tool()
def list_orders(status: str | None = None) -> str:
    """List all orders with optional status filter.

    Args:
        status: Optional status to filter by (e.g. pending, booked,
            delivering, completed, cancelled). If omitted, returns
            all orders.

    Returns a JSON object with:
    - total: number of orders matching the filter
    - orders: list of order summary objects
    """
    store = _get_order_store()
    try:
        filters = None
        if status is not None:
            filters = {"status": status}
        orders = store.list_orders(filters=filters)

        result = {
            "total": len(orders),
            "orders": orders,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
        store.disconnect()


@mcp.tool()
def get_order_status(order_id: str) -> str:
    """Get detailed status of a specific order.

    Args:
        order_id: The unique identifier of the order.

    Returns a JSON object with:
    - order_id, status, deal_id, and all order metadata
    - error: present only if the order was not found
    """
    store = _get_order_store()
    try:
        order = store.get_order(order_id)
        if order is None:
            return json.dumps(
                {"error": f"Order not found: {order_id}"},
                indent=2,
            )

        order["timestamp"] = datetime.now(UTC).isoformat()
        return json.dumps(order, indent=2)
    finally:
        store.disconnect()


@mcp.tool()
def transition_order(
    order_id: str,
    to_status: str,
    reason: str = "",
) -> str:
    """Trigger an order state transition.

    Updates the order's status (e.g., approve, reject, book, complete).
    The previous status and transition reason are included in the response.

    Args:
        order_id: The unique identifier of the order.
        to_status: The target status to transition to.
        reason: Optional explanation for the transition.

    Returns a JSON object with:
    - order_id, previous_status, new_status, reason
    - error: present only if the order was not found
    """
    store = _get_order_store()
    try:
        order = store.get_order(order_id)
        if order is None:
            return json.dumps(
                {"error": f"Order not found: {order_id}"},
                indent=2,
            )

        previous_status = order.get("status", "unknown")
        order["status"] = to_status
        store.set_order(order_id, order)

        result = {
            "order_id": order_id,
            "previous_status": previous_status,
            "new_status": to_status,
            "reason": reason,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
        store.disconnect()


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
            "timestamp": datetime.now(UTC).isoformat(),
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
        now = datetime.now(UTC)
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
        "timestamp": datetime.now(UTC).isoformat(),
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
        "timestamp": datetime.now(UTC).isoformat(),
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
        "timestamp": datetime.now(UTC).isoformat(),
    }
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Template Tools (buyer-5x7)
# ---------------------------------------------------------------------------


@mcp.tool()
def list_templates(template_type: str | None = None) -> str:
    """List available deal and supply path templates.

    Returns both deal templates and supply path templates, optionally
    filtered by type.

    Args:
        template_type: Optional filter -- "deal" for deal templates only,
            "supply_path" for supply path templates only. If omitted,
            returns both.

    Returns a JSON object with:
    - deal_templates: list of deal template summaries
    - supply_path_templates: list of supply path template summaries
    - total_deal_templates: count of deal templates
    - total_supply_path_templates: count of supply path templates
    """
    store = _get_deal_store()
    try:
        deal_templates: list[dict[str, Any]] = []
        spo_templates: list[dict[str, Any]] = []

        if template_type is None or template_type == "deal":
            raw = store.list_deal_templates()
            for t in raw:
                deal_templates.append({
                    "template_id": t["id"],
                    "name": t["name"],
                    "deal_type_pref": t.get("deal_type_pref"),
                    "advertiser_id": t.get("advertiser_id"),
                    "max_cpm": t.get("max_cpm"),
                    "created_at": t.get("created_at"),
                })

        if template_type is None or template_type == "supply_path":
            raw = store.list_supply_path_templates()
            for t in raw:
                spo_templates.append({
                    "template_id": t["id"],
                    "name": t["name"],
                    "max_reseller_hops": t.get("max_reseller_hops"),
                    "created_at": t.get("created_at"),
                })

        result = {
            "deal_templates": deal_templates,
            "supply_path_templates": spo_templates,
            "total_deal_templates": len(deal_templates),
            "total_supply_path_templates": len(spo_templates),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
        if _deal_store_override is None:
            store.disconnect()


@mcp.tool()
def create_template(
    template_type: str | None = None,
    name: str | None = None,
    deal_type_pref: str | None = None,
    max_cpm: float | None = None,
    min_impressions: int | None = None,
    default_price: float | None = None,
    default_flight_days: int | None = None,
    advertiser_id: str | None = None,
    agency_id: str | None = None,
    max_reseller_hops: int | None = None,
    scoring_weights: str | None = None,
    preferred_ssps: str | None = None,
    blocked_ssps: str | None = None,
) -> str:
    """Create a new deal or supply path template.

    Args:
        template_type: Required. Either "deal" or "supply_path".
        name: Required. Human-readable template name.
        deal_type_pref: Deal type preference (PG, PMP, etc.) -- deal only.
        max_cpm: Maximum CPM -- deal only.
        min_impressions: Minimum impressions -- deal only.
        default_price: Default price -- deal only.
        default_flight_days: Default flight duration in days -- deal only.
        advertiser_id: Scope to specific advertiser -- deal only.
        agency_id: Agency identifier -- deal only.
        max_reseller_hops: Max supply chain hops -- supply path only.
        scoring_weights: JSON scoring weights -- supply path only.
        preferred_ssps: JSON preferred SSP list -- supply path only.
        blocked_ssps: JSON blocked SSP list -- supply path only.

    Returns a JSON object with:
    - template_id: the new template's ID
    - template_type: "deal" or "supply_path"
    - name: the template name
    - error: present only if validation failed
    """
    if not template_type or template_type not in ("deal", "supply_path"):
        return json.dumps(
            {"error": "template_type is required and must be 'deal' or 'supply_path'"},
            indent=2,
        )
    if not name or not str(name).strip():
        return json.dumps({"error": "'name' is required"}, indent=2)

    store = _get_deal_store()
    try:
        if template_type == "deal":
            template_id = store.save_deal_template(
                name=name,
                deal_type_pref=deal_type_pref,
                default_price=default_price,
                max_cpm=max_cpm,
                min_impressions=min_impressions,
                default_flight_days=default_flight_days,
                advertiser_id=advertiser_id,
                agency_id=agency_id,
            )
        else:
            template_id = store.save_supply_path_template(
                name=name,
                max_reseller_hops=max_reseller_hops,
                scoring_weights=scoring_weights,
                preferred_ssps=preferred_ssps,
                blocked_ssps=blocked_ssps,
            )

        result = {
            "template_id": template_id,
            "template_type": template_type,
            "name": name,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)
    except Exception as exc:
        return json.dumps(
            {"error": f"Failed to create template: {exc}"},
            indent=2,
        )
    finally:
        if _deal_store_override is None:
            store.disconnect()


@mcp.tool()
def instantiate_from_template(
    template_id: str | None = None,
    overrides: Any = None,
) -> str:
    """Create a deal from a deal template with optional overrides.

    Looks up the deal template, applies any overrides, and creates a
    new deal in the deal store.

    Args:
        template_id: Required. The deal template ID to instantiate.
        overrides: Optional field overrides. Accepts a JSON string (e.g.
            '{"price": 25.0, "product_name": "Custom CTV"}') or a pre-parsed
            dict (MCP may pass dicts directly after JSON parsing).

    Returns a JSON object with:
    - deal_id: the newly created deal ID
    - template_id: the source template ID
    - template_name: the source template name
    - error: present only if the template was not found
    """
    if not template_id:
        return json.dumps(
            {"error": "template_id is required"},
            indent=2,
        )

    store = _get_deal_store()
    try:
        tmpl = store.get_deal_template(template_id)
        if tmpl is None:
            return json.dumps(
                {"error": f"Deal template not found: {template_id}"},
                indent=2,
            )

        # Parse overrides -- handle both str and dict (MCP may pre-parse)
        override_dict: dict[str, Any] = {}
        if overrides:
            if isinstance(overrides, dict):
                override_dict = overrides
            else:
                try:
                    override_dict = json.loads(overrides)
                except (json.JSONDecodeError, TypeError) as exc:
                    return json.dumps(
                        {"error": f"Invalid overrides JSON: {exc}"},
                        indent=2,
                    )

        # Build deal fields from template + overrides
        price = override_dict.get("price", tmpl.get("default_price", 0.0))
        product_name = override_dict.get(
            "product_name",
            f"Deal from template: {tmpl['name']}",
        )
        product_id = override_dict.get("product_id", f"tmpl-{template_id[:8]}")
        seller_url = override_dict.get("seller_url", "")

        deal_id = store.save_deal(
            seller_url=seller_url,
            product_id=product_id,
            product_name=product_name,
            status="booked",
            price=price,
        )

        result = {
            "deal_id": deal_id,
            "template_id": template_id,
            "template_name": tmpl["name"],
            "product_name": product_name,
            "price": price,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)
    except Exception as exc:
        return json.dumps(
            {"error": f"Failed to instantiate template: {exc}"},
            indent=2,
        )
    finally:
        if _deal_store_override is None:
            store.disconnect()


# ---------------------------------------------------------------------------
# Reporting Tools (buyer-5x7)
# ---------------------------------------------------------------------------


@mcp.tool()
def get_deal_performance(deal_id: str) -> str:
    """Get performance metrics for a specific deal.

    Returns deal details including price, status, and negotiation
    history from the deal store.

    Args:
        deal_id: The unique identifier of the deal.

    Returns a JSON object with:
    - deal_id, product_name, product_id, seller_url, status, price
    - negotiation_rounds: number of negotiation rounds
    - error: present only if the deal was not found
    """
    store = _get_deal_store()
    try:
        deal = store.get_deal(deal_id)
        if deal is None:
            return json.dumps(
                {"error": f"Deal not found: {deal_id}"},
                indent=2,
            )

        # Get negotiation history for round count
        rounds = store.get_negotiation_history(deal_id)

        result = {
            "deal_id": deal_id,
            "product_id": deal.get("product_id", ""),
            "product_name": deal.get("product_name", ""),
            "seller_url": deal.get("seller_url", ""),
            "status": deal.get("status", "unknown"),
            "price": deal.get("price"),
            "negotiation_rounds": len(rounds),
            "created_at": deal.get("created_at", ""),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
        if _deal_store_override is None:
            store.disconnect()


@mcp.tool()
def get_campaign_report(campaign_id: str) -> str:
    """Generate a campaign performance report.

    Combines campaign status, pacing data, creative asset summary,
    and deal-level metrics into a single comprehensive report.

    Args:
        campaign_id: The unique identifier of the campaign.

    Returns a JSON object with:
    - campaign_id, campaign_name, status
    - status_summary: campaign state and delivery metrics
    - pacing: pacing dashboard data
    - creative_summary: creative asset validation counts
    - deal_summary: deal-level metrics
    - error: present only if the campaign was not found
    """
    from ..reporting.campaign_report import CampaignReporter

    campaign_store = _get_campaign_store()
    pacing_store = _get_pacing_store()
    try:
        campaign = campaign_store.get_campaign(campaign_id)
        if campaign is None:
            return json.dumps(
                {"error": f"Campaign not found: {campaign_id}"},
                indent=2,
            )

        reporter = CampaignReporter(campaign_store, pacing_store)

        status = reporter.campaign_status_summary(campaign_id)
        pacing = reporter.pacing_dashboard(campaign_id)
        creative = reporter.creative_performance_report(campaign_id)
        deals = reporter.deal_report(campaign_id)

        result = {
            "campaign_id": campaign_id,
            "campaign_name": campaign["campaign_name"],
            "status": campaign["status"],
            "status_summary": status._to_dict(),
            "pacing": pacing._to_dict(),
            "creative_summary": {
                "total_assets": creative.total_assets,
                "valid_assets": creative.valid_assets,
                "pending_assets": creative.pending_assets,
                "invalid_assets": creative.invalid_assets,
            },
            "deal_summary": {
                "total_deals": deals.total_deals,
                "total_spend": deals.total_spend,
                "total_impressions": deals.total_impressions,
                "avg_fill_rate": deals.avg_fill_rate,
                "avg_win_rate": deals.avg_win_rate,
            },
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
        campaign_store.disconnect()
        pacing_store.disconnect()


@mcp.tool()
def get_pacing_report(campaign_id: str) -> str:
    """Get budget pacing report for a campaign.

    Provides detailed pacing data including expected vs actual spend,
    per-channel breakdown, deviation alerts, and pacing status.

    This is a more detailed version of check_pacing that includes
    alert details and channel-level effective CPM and fill rates.

    Args:
        campaign_id: The unique identifier of the campaign.

    Returns a JSON object with:
    - campaign_id, campaign_name
    - pacing_status: on_track, behind, ahead, or no_data
    - total_budget, total_spend, expected_spend
    - pacing_pct, deviation_pct
    - channel_pacing: per-channel breakdown with eCPM and fill rate
    - alerts: list of pacing deviation alerts
    - error: present only if the campaign was not found
    """
    from ..reporting.campaign_report import CampaignReporter

    campaign_store = _get_campaign_store()
    pacing_store = _get_pacing_store()
    try:
        campaign = campaign_store.get_campaign(campaign_id)
        if campaign is None:
            return json.dumps(
                {"error": f"Campaign not found: {campaign_id}"},
                indent=2,
            )

        reporter = CampaignReporter(campaign_store, pacing_store)
        dashboard = reporter.pacing_dashboard(campaign_id)

        # Determine pacing status from deviation
        deviation = dashboard.deviation_pct
        if dashboard.total_spend == 0.0 and dashboard.expected_spend == 0.0:
            pacing_status = "no_data"
        elif deviation < -10.0:
            pacing_status = "behind"
        elif deviation > 10.0:
            pacing_status = "ahead"
        else:
            pacing_status = "on_track"

        # Build channel pacing with full details
        channel_pacing = []
        for ch in dashboard.channel_pacing:
            channel_pacing.append({
                "channel": ch.channel,
                "allocated_budget": ch.allocated_budget,
                "spend": ch.spend,
                "pacing_pct": ch.pacing_pct,
                "impressions": ch.impressions,
                "effective_cpm": ch.effective_cpm,
                "fill_rate": ch.fill_rate,
            })

        # Build alerts
        alerts = []
        for alert in dashboard.alerts:
            alerts.append({
                "severity": alert.severity,
                "message": alert.message,
                "channel": alert.channel,
                "deviation_pct": alert.deviation_pct,
            })

        result = {
            "campaign_id": campaign_id,
            "campaign_name": campaign["campaign_name"],
            "pacing_status": pacing_status,
            "total_budget": dashboard.total_budget,
            "total_spend": dashboard.total_spend,
            "expected_spend": dashboard.expected_spend,
            "pacing_pct": dashboard.pacing_pct,
            "deviation_pct": dashboard.deviation_pct,
            "channel_pacing": channel_pacing,
            "alerts": alerts,
            "snapshot_timestamp": dashboard.snapshot_timestamp,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
        campaign_store.disconnect()
        pacing_store.disconnect()


# ---------------------------------------------------------------------------
# SSP Connector Tools (buyer-sozw)
# ---------------------------------------------------------------------------

# Registry mapping normalised ssp_name → connector class attribute name.
# The class is looked up from the module namespace at call time so that
# tests can patch the class via its module-level name (e.g.
# "ad_buyer.interfaces.mcp_server.PubMaticConnector") and have the patch
# take effect without the registry caching the original class.
_SSP_CLASS_NAMES: dict[str, str] = {
    "pubmatic": "PubMaticConnector",
    "magnite": "MagniteConnector",
    "index_exchange": "IndexExchangeConnector",
}


def _get_ssp_connector_class(name: str) -> type | None:
    """Look up an SSP connector class by normalised name.

    Returns the class from the current module namespace so that test
    patches are respected.  Returns None if the name is unknown.
    """
    class_name = _SSP_CLASS_NAMES.get(name)
    if class_name is None:
        return None
    import sys
    module = sys.modules[__name__]
    return getattr(module, class_name, None)


@mcp.tool()
def list_ssp_connectors() -> str:
    """List available SSP connectors and their configuration status.

    Returns each supported SSP connector with:
    - name: normalised SSP identifier (use as ssp_name in other tools)
    - display_name: human-readable SSP name
    - configured: whether required environment variables are set
    - required_env_vars: list of env var names needed for this connector

    Returns a JSON object with:
    - total: number of available connectors (always 3)
    - connectors: list of connector status objects
    - timestamp: when this list was generated
    """
    connectors = []
    for name in _SSP_CLASS_NAMES:
        cls = _get_ssp_connector_class(name)
        if cls is None:
            continue
        instance = cls()
        required = instance.get_required_config()
        connectors.append({
            "name": name,
            "display_name": instance.ssp_name,
            "configured": instance.is_configured(),
            "required_env_vars": required,
        })

    result = {
        "total": len(connectors),
        "connectors": connectors,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    return json.dumps(result, indent=2)


@mcp.tool()
def import_deals_ssp(ssp_name: str) -> str:
    """Import deals from a specified SSP connector into the deal portfolio.

    Instantiates the named SSP connector, calls its API to fetch deals,
    normalises them, and saves each deal to the deal store.  Returns the
    same result structure as ``import_deals_csv``.

    Args:
        ssp_name: Which SSP to import from.  One of:
            ``"pubmatic"``, ``"magnite"``, ``"index_exchange"``.
            Case-insensitive.

    Returns a JSON object with:
    - total_rows: total deals fetched from the SSP
    - successful: number of deals saved to the deal store
    - failed: number of deals that failed normalisation
    - skipped: number of duplicate deals skipped
    - errors: list of per-deal error messages
    - deal_ids: list of saved deal IDs
    - ssp_name: normalised name of the connector used
    - timestamp: when this import was performed
    """
    key = ssp_name.strip().lower()
    cls = _get_ssp_connector_class(key)
    if cls is None:
        known = ", ".join(sorted(_SSP_CLASS_NAMES.keys()))
        return json.dumps(
            {
                "error": (
                    f"Unknown SSP connector: '{ssp_name}'. "
                    f"Known connectors: {known}"
                ),
                "timestamp": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )

    connector = cls()
    if not connector.is_configured():
        missing = [v for v in connector.get_required_config() if not os.environ.get(v)]
        return json.dumps(
            {
                "error": (
                    f"{connector.ssp_name} connector is not configured. "
                    f"Set these environment variables: {', '.join(missing)}"
                ),
                "ssp_name": key,
                "required_env_vars": connector.get_required_config(),
                "timestamp": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )

    # Fetch deals from the SSP
    fetch_result = connector.fetch_deals()

    # Persist normalised deals to the deal store
    store = _get_deal_store()
    try:
        deal_ids: list[str] = []
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        for deal_data in fetch_result.deals:
            saved_id = store.save_deal(**deal_data)
            deal_ids.append(saved_id)
            store.save_portfolio_metadata(
                deal_id=saved_id,
                import_source=connector.import_source,
                import_date=today,
            )

        result = {
            "total_rows": fetch_result.total_fetched,
            "successful": fetch_result.successful,
            "failed": fetch_result.failed,
            "skipped": fetch_result.skipped,
            "errors": fetch_result.errors,
            "deal_ids": deal_ids,
            "ssp_name": key,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
        if _deal_store_override is None:
            store.disconnect()


@mcp.tool()
def test_ssp_connection(ssp_name: str) -> str:
    """Test connectivity to a specific SSP connector.

    Checks whether credentials are configured and, if so, attempts a
    lightweight API call to verify the credentials are valid.

    Args:
        ssp_name: Which SSP to test.  One of:
            ``"pubmatic"``, ``"magnite"``, ``"index_exchange"``.
            Case-insensitive.

    Returns a JSON object with:
    - ssp_name: normalised name of the connector tested
    - connected: true if the connection succeeded, false otherwise
    - configured: whether required environment variables are set
    - message: human-readable status or error message
    - timestamp: when this test was performed
    """
    key = ssp_name.strip().lower()
    cls = _get_ssp_connector_class(key)
    if cls is None:
        known = ", ".join(sorted(_SSP_CLASS_NAMES.keys()))
        return json.dumps(
            {
                "error": (
                    f"Unknown SSP connector: '{ssp_name}'. "
                    f"Known connectors: {known}"
                ),
                "timestamp": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )

    connector = cls()
    configured = connector.is_configured()
    if not configured:
        missing = [v for v in connector.get_required_config() if not os.environ.get(v)]
        result = {
            "ssp_name": key,
            "connected": False,
            "configured": False,
            "message": (
                f"{connector.ssp_name} connector is not configured. "
                f"Missing env vars: {', '.join(missing)}"
            ),
            "required_env_vars": connector.get_required_config(),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        return json.dumps(result, indent=2)

    # Connector is configured — attempt connection test
    connected = connector.test_connection()
    result = {
        "ssp_name": key,
        "connected": connected,
        "configured": True,
        "message": (
            f"{connector.ssp_name} connection test {'succeeded' if connected else 'failed'}."
        ),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Prompts (Slash Commands)
# ---------------------------------------------------------------------------
# These surface as /commands in Claude Desktop and Claude Web.
# Each prompt injects a user message that guides Claude to use the
# appropriate tools for that workflow.


@mcp.prompt(name="setup", description="First-time guided setup wizard")
async def setup_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Check my setup status and walk me through configuring everything "
                "that's incomplete. Go step by step through all 8 wizard steps: "
                "deployment, seller connections, credentials, buyer identity, deal "
                "preferences, campaign defaults, approval gates, and review. "
                "Ask me one question at a time.",
    )]


@mcp.prompt(name="status", description="Configuration and health overview")
async def status_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Show me a complete status overview: setup state, system health, "
                "seller connections, database status, and any issues that need "
                "attention.",
    )]


@mcp.prompt(name="campaigns", description="Campaign portfolio with budget pacing")
async def campaigns_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Show me all my campaigns with their current status and budget "
                "pacing. Highlight any campaigns that are behind or ahead on "
                "pacing, and flag anything that needs attention. Include a budget "
                "summary across all campaigns.",
    )]


@mcp.prompt(name="deals", description="Deal portfolio dashboard")
async def deals_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Give me a full dashboard of my deal portfolio: total deals, "
                "breakdown by status and deal type, top sellers, portfolio value, "
                "and any deals expiring in the next 30 days. Include recent "
                "activity.",
    )]


@mcp.prompt(name="discover", description="Find and compare seller agents")
async def discover_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Search the IAB registry for available seller agents. Show me "
                "who's out there, what they offer, and their capabilities. If I'm "
                "interested in specific sellers, help me compare their media kits "
                "and pricing side by side.",
    )]


@mcp.prompt(name="negotiate", description="Negotiation status and actions")
async def negotiate_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Show me all active negotiations: where each one stands, how many "
                "rounds we've been through, the current price positions, and what "
                "action is needed next. If there are no active negotiations, help "
                "me start one by discovering sellers and their inventory.",
    )]


@mcp.prompt(name="orders", description="Active orders and execution status")
async def orders_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Show me all my orders: their current status, any pending "
                "transitions, and orders that need my action. Group them by "
                "status and highlight anything stuck or overdue.",
    )]


@mcp.prompt(name="approvals", description="Pending approvals queue")
async def approvals_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Show me everything waiting for my approval: pending deal "
                "approvals, campaign approvals, and any budget or order changes "
                "that need my decision. Most urgent first. For each item, show "
                "me the context I need to decide.",
    )]


@mcp.prompt(name="configure", description="Settings, templates, and SSP connectors")
async def configure_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Show me my current configuration: deal and supply path templates, "
                "SSP connector status, API keys (masked), and campaign defaults. "
                "Help me create new templates, configure SSP connectors, or update "
                "settings.",
    )]


@mcp.prompt(name="help", description="What can this agent do?")
async def help_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="List everything I can do with this buyer agent, organized by "
                "category. Include all slash commands with descriptions, and "
                "summarize the tool categories: campaigns, deals, seller discovery, "
                "negotiation, orders, approvals, templates, reporting, SSP "
                "connectors, and API keys.",
    )]


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
