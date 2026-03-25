# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""MCP (Model Context Protocol) server for the Ad Buyer Agent.

Exposes buyer operations as MCP tools via FastMCP SSE transport.
This is the foundation server that all other MCP tool modules build upon.

Tool categories:
  - Foundation: get_setup_status, health_check, get_config
  - Campaign Management: list_campaigns, get_campaign_status,
    check_pacing, review_budgets (buyer-3w3)
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

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

from ..config.settings import Settings
from ..media_kit.client import MediaKitClient
from ..media_kit.models import MediaKitError
from ..registry.client import RegistryClient
from ..storage.campaign_store import CampaignStore
from ..storage.deal_store import DealStore
from ..storage.order_store import OrderStore
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


def _get_registry_client() -> RegistryClient:
    """Get a RegistryClient instance.

    Uses the IAB server URL from settings as the registry URL.
    Returns a new instance each time so that test patches are reflected.
    """
    settings = _get_settings()
    return RegistryClient(registry_url=settings.iab_server_url)


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
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return json.dumps(result, indent=2)

    except Exception as exc:
        logger.warning("Failed to discover sellers: %s", exc)
        result = {
            "error": f"Failed to discover sellers: {exc}",
            "total": 0,
            "sellers": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
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
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return json.dumps(result, indent=2)

    except MediaKitError as exc:
        logger.warning("Failed to fetch media kit from %s: %s", seller_url, exc)
        result = {
            "error": f"Failed to fetch media kit: {exc}",
            "seller_url": seller_url,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return json.dumps(result, indent=2)

    except Exception as exc:
        logger.warning(
            "Unexpected error fetching media kit from %s: %s", seller_url, exc,
        )
        result = {
            "error": f"Unexpected error: {exc}",
            "seller_url": seller_url,
            "timestamp": datetime.now(timezone.utc).isoformat(),
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
        "timestamp": datetime.now(timezone.utc).isoformat(),
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
            "timestamp": datetime.now(timezone.utc).isoformat(),
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
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
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
            "timestamp": datetime.now(timezone.utc).isoformat(),
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
            "timestamp": datetime.now(timezone.utc).isoformat(),
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

        order["timestamp"] = datetime.now(timezone.utc).isoformat()
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
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return json.dumps(result, indent=2)
    finally:
        store.disconnect()


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
