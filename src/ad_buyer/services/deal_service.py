# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Deal library application service.

Owns the deal-library business logic that used to live inline in the MCP
interface layer: portfolio reads (list/search/inspect/summary), CSV
import, SSP-connector import, and manual deal entry.  The interface
layer (``interfaces/mcp_server.py``) now calls these functions instead
of reaching into the ``tools`` package for private helpers such as
``_parse_row`` / ``_resolve_columns``.

Every function takes an already-connected ``DealStore`` and returns a
plain ``dict`` (JSON-serialisable) so callers stay thin: the MCP tool
just serialises the returned dict.  These functions do not manage the
store connection lifecycle -- the caller owns it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from ..storage.deal_store import DealStore
from ..tools.deal_import import parse_csv_string
from ..tools.deal_library.deal_entry import (
    ManualDealEntry,
)
from ..tools.deal_library.deal_entry import (
    create_manual_deal as validate_manual_deal,
)


def _now() -> str:
    """Current UTC timestamp as ISO 8601 (matches prior interface output)."""
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Portfolio reads
# ---------------------------------------------------------------------------


def list_deals(
    store: DealStore,
    *,
    status: str | None = None,
    deal_type: str | None = None,
    media_type: str | None = None,
    seller_domain: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List deals in the portfolio with optional filters."""
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

    deal_summaries = [
        {
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
        }
        for d in deals
    ]

    return {
        "total": len(deal_summaries),
        "deals": deal_summaries,
        "timestamp": _now(),
    }


def search_deals(store: DealStore, query: str) -> dict[str, Any]:
    """Free-text case-insensitive search across deal fields."""
    if not query or not query.strip():
        return {"error": "Search query must not be empty."}

    query = query.strip()
    query_lower = query.lower()

    deals = store.list_deals(limit=10000)

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
            matches.append(
                {
                    "deal_id": deal["id"],
                    "display_name": (
                        deal.get("display_name") or deal.get("product_name") or "(unnamed)"
                    ),
                    "status": deal.get("status", "unknown"),
                    "deal_type": deal.get("deal_type", "unknown"),
                    "media_type": deal.get("media_type"),
                    "seller_org": deal.get("seller_org"),
                    "seller_domain": deal.get("seller_domain"),
                    "price": deal.get("price"),
                    "matched_in": matched_fields,
                }
            )

    return {
        "total": len(matches),
        "query": query,
        "deals": matches,
        "timestamp": _now(),
    }


def inspect_deal(store: DealStore, deal_id: str) -> dict[str, Any]:
    """Return the full detail view for a single deal."""
    deal = store.get_deal(deal_id)
    if deal is None:
        return {"error": f"Deal not found: {deal_id}"}

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

    result["timestamp"] = _now()
    return result


def portfolio_summary(
    store: DealStore,
    *,
    top_sellers_count: int = 5,
    expiring_within_days: int = 30,
) -> dict[str, Any]:
    """Aggregate portfolio statistics (counts, value, top sellers, expiring)."""
    deals = store.list_deals(limit=10000)
    total = len(deals)

    if total == 0:
        return {
            "total_deals": 0,
            "total_value": 0.0,
            "by_status": {},
            "by_deal_type": {},
            "by_media_type": {},
            "top_sellers": [],
            "expiring_deals": [],
            "timestamp": _now(),
        }

    status_counts: dict[str, int] = {}
    for deal in deals:
        s = deal.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    type_counts: dict[str, int] = {}
    for deal in deals:
        dt = deal.get("deal_type", "unknown")
        type_counts[dt] = type_counts.get(dt, 0) + 1

    media_counts: dict[str, int] = {}
    for deal in deals:
        mt = deal.get("media_type") or "N/A"
        media_counts[mt] = media_counts.get(mt, 0) + 1

    seller_counts: dict[str, int] = {}
    for deal in deals:
        seller = deal.get("seller_org") or deal.get("seller_domain") or "Unknown"
        seller_counts[seller] = seller_counts.get(seller, 0) + 1
    top_sellers = sorted(
        seller_counts.items(),
        key=lambda x: x[1],
        reverse=True,
    )[:top_sellers_count]

    total_value = 0.0
    for deal in deals:
        p = deal.get("price")
        imp = deal.get("impressions")
        if p is not None and imp is not None:
            total_value += p * imp / 1000.0

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
            expiring_deals.append(
                {
                    "deal_id": deal["id"],
                    "display_name": (
                        deal.get("display_name") or deal.get("product_name") or "(unnamed)"
                    ),
                    "flight_end": flight_end,
                }
            )

    return {
        "total_deals": total,
        "total_value": total_value,
        "by_status": status_counts,
        "by_deal_type": type_counts,
        "by_media_type": media_counts,
        "top_sellers": [{"seller": name, "deal_count": count} for name, count in top_sellers],
        "expiring_deals": expiring_deals,
        "timestamp": _now(),
    }


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------


def import_deals_csv(
    store: DealStore,
    csv_data: str,
    *,
    default_seller_url: str = "",
    default_product_id: str = "imported",
) -> dict[str, Any]:
    """Parse CSV text and persist the resulting deals to the portfolio.

    Wraps the pure ``tools.deal_import.parse_csv_string`` parser (so the
    interface no longer imports the parser's private ``_parse_row`` /
    ``_resolve_columns`` helpers) and performs the persistence side
    effects the parser deliberately omits.
    """
    import_result = parse_csv_string(
        csv_data,
        default_seller_url=default_seller_url,
        default_product_id=default_product_id,
    )

    deal_ids: list[str] = []
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    for deal_data in import_result.deals:
        saved_id = store.save_deal(**deal_data)
        deal_ids.append(saved_id)
        store.save_portfolio_metadata(
            deal_id=saved_id,
            import_source="CSV",
            import_date=today,
        )

    error_dicts = [
        {
            "row": e.row_number,
            "field": e.field,
            "value": e.value,
            "message": e.message,
        }
        for e in import_result.errors
    ]

    return {
        "total_rows": import_result.total_rows,
        "successful": import_result.successful,
        "failed": import_result.failed,
        "skipped": import_result.skipped,
        "errors": error_dicts,
        "deal_ids": deal_ids,
        "timestamp": _now(),
    }


def import_deals_ssp(store: DealStore, connector: Any) -> dict[str, Any]:
    """Fetch deals from an already-configured SSP connector and persist them.

    The connector instance (and its configuration checks) are resolved by
    the caller; this function performs the fetch + persistence and shapes
    the result identically to ``import_deals_csv``.
    """
    fetch_result = connector.fetch_deals()

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

    return {
        "total_rows": fetch_result.total_fetched,
        "successful": fetch_result.successful,
        "failed": fetch_result.failed,
        "skipped": fetch_result.skipped,
        "errors": fetch_result.errors,
        "deal_ids": deal_ids,
        "timestamp": _now(),
    }


def create_manual_deal(store: DealStore, **fields: Any) -> dict[str, Any]:
    """Validate and persist a single manually-entered deal.

    Builds a ``ManualDealEntry`` from ``fields``, validates it via the
    deal-library ``create_manual_deal`` builder, and (on success) saves
    the deal plus its portfolio metadata.
    """
    display_name = fields.get("display_name")

    try:
        entry = ManualDealEntry(**fields)
    except (ValueError, TypeError) as exc:
        return {
            "success": False,
            "errors": [str(exc)],
            "timestamp": _now(),
        }

    entry_result = validate_manual_deal(entry)
    if not entry_result.success:
        return {
            "success": False,
            "errors": entry_result.errors,
            "timestamp": _now(),
        }

    deal_id = store.save_deal(**entry_result.deal_data)

    tags_json = (
        json.dumps(entry_result.metadata["tags"]) if entry_result.metadata.get("tags") else None
    )
    store.save_portfolio_metadata(
        deal_id=deal_id,
        import_source=entry_result.metadata["import_source"],
        import_date=datetime.now(UTC).strftime("%Y-%m-%d"),
        advertiser_id=entry_result.metadata.get("advertiser_id"),
        tags=tags_json,
    )

    return {
        "success": True,
        "deal_id": deal_id,
        "display_name": display_name,
        "timestamp": _now(),
    }
