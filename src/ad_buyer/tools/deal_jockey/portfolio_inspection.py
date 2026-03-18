# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Portfolio inspection tools for DealJockey.

CrewAI tools that let DealJockey list, filter, search, and aggregate
deal portfolio views.  Unlike the CSV parser and manual-entry tools
(which are pure functions), these tools interact directly with
DealStore to query live portfolio data.

Usage:
    store = DealStore("sqlite:///./ad_buyer.db")
    store.connect()

    list_tool = ListPortfolioTool(deal_store=store)
    result = list_tool._run(filters_json='{"status": "active"}')

    search_tool = SearchPortfolioTool(deal_store=store)
    result = search_tool._run(query="ESPN")

    summary_tool = PortfolioSummaryTool(deal_store=store)
    result = summary_tool._run(options_json='{}')

    inspect_tool = InspectDealTool(deal_store=store)
    result = inspect_tool._run(deal_id="deal-001")

Note: The caller should emit EventType.PORTFOLIO_INSPECTED after
using ListPortfolioTool or SearchPortfolioTool to track portfolio
access in the event bus audit trail.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...storage.deal_store import DealStore

logger = logging.getLogger(__name__)


# -- Input schemas -----------------------------------------------------------


class ListPortfolioInput(BaseModel):
    """Input schema for ListPortfolioTool."""

    filters_json: str = Field(
        ...,
        description=(
            "JSON string with filter/sort/pagination parameters. "
            "Filters: status, media_type, seller_domain, deal_type, "
            "advertiser_id, seller_type. "
            "Pagination: limit (default 50), offset (default 0). "
            "Sorting: sort_by (created_at, price, display_name), "
            "sort_order (asc, desc; default desc)."
        ),
    )


class SearchPortfolioInput(BaseModel):
    """Input schema for SearchPortfolioTool."""

    query: str = Field(
        ...,
        description=(
            "Free-text search string. Matches case-insensitively "
            "against display_name, description, seller_org, and "
            "seller_domain."
        ),
    )


class PortfolioSummaryInput(BaseModel):
    """Input schema for PortfolioSummaryTool."""

    options_json: str = Field(
        ...,
        description=(
            "JSON string with summary options. "
            "Optional: top_sellers_count (default 5), "
            "expiring_within_days (default 30)."
        ),
    )


class InspectDealInput(BaseModel):
    """Input schema for InspectDealTool."""

    deal_id: str = Field(
        ...,
        description="The deal ID to inspect.",
    )


# -- Formatting helpers ------------------------------------------------------


def _format_deal_summary(deal: dict[str, Any]) -> str:
    """Format a single deal as a compact, human-readable summary line block."""
    name = deal.get("display_name") or deal.get("product_name") or "(unnamed)"
    deal_id = deal.get("id", "?")
    status = deal.get("status", "?")
    deal_type = deal.get("deal_type", "?")
    media_type = deal.get("media_type") or "N/A"
    seller = deal.get("seller_org") or deal.get("seller_domain") or "N/A"
    price = deal.get("price")
    impressions = deal.get("impressions")

    lines = [
        f"  [{deal_id}] {name}",
        f"    Status: {status} | Type: {deal_type} | Media: {media_type}",
        f"    Seller: {seller}",
    ]

    price_parts = []
    if price is not None:
        price_parts.append(f"Price: ${price:,.2f}")
    if impressions is not None:
        price_parts.append(f"Impressions: {impressions:,}")
    if price_parts:
        lines.append(f"    {' | '.join(price_parts)}")

    flight_start = deal.get("flight_start")
    flight_end = deal.get("flight_end")
    if flight_start or flight_end:
        flight = f"    Flight: {flight_start or '?'} to {flight_end or '?'}"
        lines.append(flight)

    return "\n".join(lines)


def _format_number(n: int | float) -> str:
    """Format a number with comma separators."""
    if isinstance(n, float):
        return f"{n:,.2f}"
    return f"{n:,}"


# -- ListPortfolioTool -------------------------------------------------------


class ListPortfolioTool(BaseTool):
    """List deals in the portfolio with rich filtering, sorting, and pagination.

    Returns formatted deal summaries (not raw dicts) for agent readability.
    The caller should emit EventType.PORTFOLIO_INSPECTED after use.
    """

    name: str = "list_portfolio"
    description: str = (
        "List deals in the deal portfolio with filtering, sorting, and "
        "pagination. Accepts a JSON string with filter params (status, "
        "media_type, seller_domain, deal_type, advertiser_id, seller_type), "
        "pagination (limit, offset), and sorting (sort_by, sort_order)."
    )
    args_schema: type[BaseModel] = ListPortfolioInput
    deal_store: Any = Field(exclude=True)

    def _run(self, filters_json: str) -> str:
        """List deals with the specified filters.

        Args:
            filters_json: JSON string with filter, sort, and pagination params.

        Returns:
            Human-readable formatted list of matching deals.
        """
        # Parse input
        try:
            params = json.loads(filters_json)
        except (json.JSONDecodeError, TypeError) as exc:
            return f"Error: Invalid JSON input -- {exc}"

        # Extract pagination
        limit = params.pop("limit", 50)
        offset = params.pop("offset", 0)

        # Extract sorting
        sort_by = params.pop("sort_by", "created_at")
        sort_order = params.pop("sort_order", "desc")

        # Validate sort_by
        valid_sort_fields = {"created_at", "price", "display_name"}
        if sort_by not in valid_sort_fields:
            sort_by = "created_at"
        if sort_order not in ("asc", "desc"):
            sort_order = "desc"

        # Extract seller_type filter (handled in Python since DealStore
        # list_deals does not support it natively)
        seller_type_filter = params.pop("seller_type", None)

        # Build DealStore filters (only pass supported ones)
        store_filters: dict[str, Any] = {}
        for key in ("status", "media_type", "seller_domain", "deal_type", "advertiser_id"):
            if key in params:
                store_filters[key] = params[key]

        # Fetch from store -- get more than we need for post-filtering + sorting
        fetch_limit = limit + offset + 200  # over-fetch for post-filtering
        deals = self.deal_store.list_deals(limit=fetch_limit, **store_filters)

        # Post-filter by seller_type if specified
        if seller_type_filter:
            deals = [d for d in deals if d.get("seller_type") == seller_type_filter]

        # Sort
        if sort_by == "price":
            deals.sort(
                key=lambda d: d.get("price") or 0,
                reverse=(sort_order == "desc"),
            )
        elif sort_by == "display_name":
            deals.sort(
                key=lambda d: (d.get("display_name") or d.get("product_name") or "").lower(),
                reverse=(sort_order == "desc"),
            )
        else:
            # created_at -- DealStore already sorts by created_at DESC,
            # but we may need ASC
            if sort_order == "asc":
                deals.sort(key=lambda d: d.get("created_at") or "")

        # Apply pagination
        total_count = len(deals)
        deals = deals[offset: offset + limit]

        # Format output
        if not deals:
            if total_count == 0:
                return "No deals found matching the specified filters."
            return f"No deals found at offset {offset} (total matching: {total_count})."

        lines = [f"Portfolio: {len(deals)} of {total_count} deals shown"]
        if offset > 0:
            lines[0] += f" (offset: {offset})"
        lines.append("")

        for deal in deals:
            lines.append(_format_deal_summary(deal))
            lines.append("")

        return "\n".join(lines)


# -- SearchPortfolioTool -----------------------------------------------------


class SearchPortfolioTool(BaseTool):
    """Search the deal portfolio with free-text matching.

    Performs case-insensitive LIKE matching across deal fields:
    display_name, description, seller_org, seller_domain.
    Returns matching deals with relevance context (which field matched).

    The caller should emit EventType.PORTFOLIO_INSPECTED after use.
    """

    name: str = "search_portfolio"
    description: str = (
        "Free-text search across deal portfolio. Searches display_name, "
        "description, seller_org, and seller_domain with case-insensitive "
        "matching. Returns deals with context about which field matched."
    )
    args_schema: type[BaseModel] = SearchPortfolioInput
    deal_store: Any = Field(exclude=True)

    # Fields to search and their human-readable labels
    _SEARCH_FIELDS = [
        ("display_name", "display name"),
        ("description", "description"),
        ("seller_org", "seller organization"),
        ("seller_domain", "seller domain"),
    ]

    def _run(self, query: str) -> str:
        """Search deals by free-text query.

        Args:
            query: Search string (case-insensitive).

        Returns:
            Human-readable list of matching deals with match context.
        """
        if not query or not query.strip():
            return "Error: Search query must not be empty."

        query = query.strip()
        query_lower = query.lower()

        # Fetch all deals (search needs full scan)
        deals = self.deal_store.list_deals(limit=10000)

        # Match against search fields
        matches: list[tuple[dict[str, Any], list[str]]] = []
        for deal in deals:
            matched_fields = []
            for field_name, field_label in self._SEARCH_FIELDS:
                value = deal.get(field_name)
                if value and query_lower in str(value).lower():
                    matched_fields.append(field_label)
            if matched_fields:
                matches.append((deal, matched_fields))

        if not matches:
            return f'No deals found matching "{query}".'

        lines = [f'Search results for "{query}": {len(matches)} deal(s) found']
        lines.append("")

        for deal, matched_fields in matches:
            lines.append(_format_deal_summary(deal))
            lines.append(f"    Matched in: {', '.join(matched_fields)}")
            lines.append("")

        return "\n".join(lines)


# -- PortfolioSummaryTool ----------------------------------------------------


class PortfolioSummaryTool(BaseTool):
    """Generate aggregate statistics for the deal portfolio.

    Produces a structured summary with counts by status, media type,
    deal type, top sellers, total portfolio value, and expiring deals.
    """

    name: str = "portfolio_summary"
    description: str = (
        "Generate aggregate statistics for the deal portfolio: total "
        "deals by status, media type, deal type; top sellers; total "
        "portfolio value; and active deals expiring within N days."
    )
    args_schema: type[BaseModel] = PortfolioSummaryInput
    deal_store: Any = Field(exclude=True)

    def _run(self, options_json: str) -> str:
        """Generate a portfolio summary.

        Args:
            options_json: JSON string with options.
                top_sellers_count: Number of top sellers to show (default 5).
                expiring_within_days: Show deals expiring within N days (default 30).

        Returns:
            Human-readable portfolio summary string.
        """
        # Parse options
        try:
            options = json.loads(options_json)
        except (json.JSONDecodeError, TypeError) as exc:
            return f"Error: Invalid JSON input -- {exc}"

        top_sellers_count = options.get("top_sellers_count", 5)
        expiring_within_days = options.get("expiring_within_days", 30)

        # Fetch all deals
        deals = self.deal_store.list_deals(limit=10000)

        if not deals:
            return (
                "Portfolio Summary\n"
                "=================\n"
                "Total deals: 0\n"
                "\nThe portfolio is empty."
            )

        total = len(deals)

        # Count by status
        status_counts: dict[str, int] = {}
        for deal in deals:
            s = deal.get("status", "unknown")
            status_counts[s] = status_counts.get(s, 0) + 1

        # Count by media type
        media_counts: dict[str, int] = {}
        for deal in deals:
            mt = deal.get("media_type") or "N/A"
            media_counts[mt] = media_counts.get(mt, 0) + 1

        # Count by deal type
        type_counts: dict[str, int] = {}
        for deal in deals:
            dt = deal.get("deal_type", "unknown")
            type_counts[dt] = type_counts.get(dt, 0) + 1

        # Top sellers by deal count
        seller_counts: dict[str, int] = {}
        for deal in deals:
            seller = deal.get("seller_org") or deal.get("seller_domain") or "Unknown"
            seller_counts[seller] = seller_counts.get(seller, 0) + 1
        top_sellers = sorted(
            seller_counts.items(), key=lambda x: x[1], reverse=True
        )[:top_sellers_count]

        # Total portfolio value: sum of (price * impressions / 1000) for CPM
        total_value = 0.0
        for deal in deals:
            price = deal.get("price")
            impressions = deal.get("impressions")
            if price is not None and impressions is not None:
                total_value += price * impressions / 1000.0

        # Deals expiring within N days
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=expiring_within_days)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        now_str = now.strftime("%Y-%m-%d")

        expiring_deals = []
        for deal in deals:
            if deal.get("status") not in ("active", "draft"):
                continue
            flight_end = deal.get("flight_end")
            if flight_end and now_str <= flight_end <= cutoff_str:
                expiring_deals.append(deal)

        # Build output
        lines = [
            "Portfolio Summary",
            "=================",
            f"Total deals: {total}",
            f"Total portfolio value: ${_format_number(total_value)}",
            "",
            "By Status:",
        ]
        for status, count in sorted(status_counts.items()):
            lines.append(f"  {status}: {count}")

        lines.append("")
        lines.append("By Media Type:")
        for mt, count in sorted(media_counts.items()):
            lines.append(f"  {mt}: {count}")

        lines.append("")
        lines.append("By Deal Type:")
        for dt, count in sorted(type_counts.items()):
            lines.append(f"  {dt}: {count}")

        lines.append("")
        lines.append(f"Top Sellers (by deal count):")
        for seller, count in top_sellers:
            lines.append(f"  {seller}: {count} deal(s)")

        lines.append("")
        if expiring_deals:
            lines.append(
                f"Deals expiring within {expiring_within_days} days: "
                f"{len(expiring_deals)}"
            )
            for deal in expiring_deals:
                name = deal.get("display_name") or deal.get("product_name") or "?"
                lines.append(
                    f"  [{deal.get('id')}] {name} -- expires {deal.get('flight_end')}"
                )
        else:
            lines.append(
                f"No active/draft deals expiring within {expiring_within_days} days."
            )

        return "\n".join(lines)


# -- InspectDealTool ---------------------------------------------------------


class InspectDealTool(BaseTool):
    """Deep inspection of a single deal by ID.

    Returns all deal fields plus portfolio_metadata, deal_activations,
    and performance_cache, formatted for agent readability.
    """

    name: str = "inspect_deal"
    description: str = (
        "Deep-inspect a single deal by ID. Returns all deal fields, "
        "portfolio metadata, cross-platform activations, and cached "
        "performance metrics in a formatted view."
    )
    args_schema: type[BaseModel] = InspectDealInput
    deal_store: Any = Field(exclude=True)

    def _run(self, deal_id: str) -> str:
        """Inspect a deal in full detail.

        Args:
            deal_id: The deal's primary key.

        Returns:
            Human-readable deep inspection of the deal.
        """
        deal = self.deal_store.get_deal(deal_id)
        if deal is None:
            return f"Deal not found: {deal_id}"

        lines = self._format_deal_section(deal)

        # Portfolio metadata
        metadata = self.deal_store.get_portfolio_metadata(deal_id)
        lines.extend(self._format_metadata_section(metadata))

        # Deal activations
        activations = self.deal_store.get_deal_activations(deal_id)
        lines.extend(self._format_activations_section(activations))

        # Performance cache
        perf = self.deal_store.get_performance_cache(deal_id)
        lines.extend(self._format_performance_section(perf))

        return "\n".join(lines)

    def _format_deal_section(self, deal: dict[str, Any]) -> list[str]:
        """Format the core deal fields."""
        name = deal.get("display_name") or deal.get("product_name") or "(unnamed)"
        lines = [
            f"Deal Inspection: {name}",
            "=" * (len(f"Deal Inspection: {name}")),
            "",
            "Core Fields:",
            f"  ID: {deal.get('id')}",
            f"  Display Name: {name}",
            f"  Status: {deal.get('status')}",
            f"  Deal Type: {deal.get('deal_type')}",
            f"  Media Type: {deal.get('media_type') or 'N/A'}",
        ]

        # Seller info
        lines.append("")
        lines.append("Seller:")
        for field, label in [
            ("seller_url", "URL"),
            ("seller_org", "Organization"),
            ("seller_domain", "Domain"),
            ("seller_type", "Type"),
            ("seller_deal_id", "Deal ID"),
        ]:
            val = deal.get(field)
            if val:
                lines.append(f"  {label}: {val}")

        # Buyer info
        buyer_org = deal.get("buyer_org")
        buyer_id = deal.get("buyer_id")
        if buyer_org or buyer_id:
            lines.append("")
            lines.append("Buyer:")
            if buyer_org:
                lines.append(f"  Organization: {buyer_org}")
            if buyer_id:
                lines.append(f"  ID: {buyer_id}")

        # Pricing
        lines.append("")
        lines.append("Pricing:")
        for field, label in [
            ("price", "Price (CPM)"),
            ("original_price", "Original Price"),
            ("fixed_price_cpm", "Fixed CPM"),
            ("bid_floor_cpm", "Bid Floor CPM"),
            ("price_model", "Price Model"),
            ("currency", "Currency"),
            ("cpp", "CPP"),
            ("guaranteed_grps", "Guaranteed GRPs"),
            ("fee_transparency", "Fee Transparency"),
        ]:
            val = deal.get(field)
            if val is not None:
                if isinstance(val, float):
                    lines.append(f"  {label}: ${val:,.2f}")
                else:
                    lines.append(f"  {label}: {val}")

        # Volume & dates
        impressions = deal.get("impressions")
        if impressions is not None:
            lines.append(f"  Impressions: {impressions:,}")
        estimated_vol = deal.get("estimated_volume")
        if estimated_vol is not None:
            lines.append(f"  Estimated Volume: {estimated_vol:,}")

        flight_start = deal.get("flight_start")
        flight_end = deal.get("flight_end")
        if flight_start or flight_end:
            lines.append("")
            lines.append("Flight Dates:")
            if flight_start:
                lines.append(f"  Start: {flight_start}")
            if flight_end:
                lines.append(f"  End: {flight_end}")

        # Description
        description = deal.get("description")
        if description:
            lines.append("")
            lines.append(f"Description: {description}")

        # Timestamps
        lines.append("")
        lines.append("Timestamps:")
        if deal.get("created_at"):
            lines.append(f"  Created: {deal['created_at']}")
        if deal.get("updated_at"):
            lines.append(f"  Updated: {deal['updated_at']}")

        return lines

    def _format_metadata_section(
        self, metadata: Optional[dict[str, Any]]
    ) -> list[str]:
        """Format the portfolio metadata section."""
        lines = ["", "Portfolio Metadata:"]
        if metadata is None:
            lines.append("  No portfolio metadata recorded.")
            return lines

        for field, label in [
            ("import_source", "Import Source"),
            ("import_date", "Import Date"),
            ("advertiser_id", "Advertiser ID"),
            ("agency_id", "Agency ID"),
            ("tags", "Tags"),
        ]:
            val = metadata.get(field)
            if val is not None:
                lines.append(f"  {label}: {val}")

        return lines

    def _format_activations_section(
        self, activations: list[dict[str, Any]]
    ) -> list[str]:
        """Format the deal activations section."""
        lines = ["", "Deal Activations:"]
        if not activations:
            lines.append("  No activations recorded.")
            return lines

        lines.append(f"  {len(activations)} activation(s):")
        for act in activations:
            platform = act.get("platform", "?")
            pid = act.get("platform_deal_id") or "N/A"
            status = act.get("activation_status") or "N/A"
            sync = act.get("last_sync_at") or "never"
            lines.append(
                f"  - {platform}: ID={pid}, Status={status}, Last Sync={sync}"
            )

        return lines

    def _format_performance_section(
        self, perf: Optional[dict[str, Any]]
    ) -> list[str]:
        """Format the performance cache section."""
        lines = ["", "Performance Cache:"]
        if perf is None:
            lines.append("  No performance data cached.")
            return lines

        for field, label, fmt in [
            ("impressions_delivered", "Impressions Delivered", "int"),
            ("spend_to_date", "Spend to Date", "dollar"),
            ("fill_rate", "Fill Rate", "pct"),
            ("win_rate", "Win Rate", "pct"),
            ("avg_effective_cpm", "Avg Effective CPM", "dollar"),
            ("last_delivery_at", "Last Delivery", "str"),
            ("performance_trend", "Trend", "str"),
            ("cached_at", "Cached At", "str"),
        ]:
            val = perf.get(field)
            if val is not None:
                if fmt == "int":
                    lines.append(f"  {label}: {int(val):,}")
                elif fmt == "dollar":
                    lines.append(f"  {label}: ${float(val):,.2f}")
                elif fmt == "pct":
                    lines.append(f"  {label}: {float(val) * 100:.1f}%")
                else:
                    lines.append(f"  {label}: {val}")

        return lines
