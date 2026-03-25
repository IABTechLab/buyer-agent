# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Index Exchange SSP connector for deal import.

Index Exchange is the #1 US web SSP (19% share).  Its deal model is strictly
publisher-side: publishers create deals in IX's UI or API and specify buyer
seat IDs.  This connector discovers and imports deals that publishers have
targeted to the buyer's seat — it uses the GET endpoints only.

API details:
    Base URL: https://api.indexexchange.com
    Auth:     API key sent as X-API-Key header (IX_API_KEY env var)
    Seat:     IX_SEAT_ID env var, sent as seatId query param
    Endpoints:
        GET /deals              — list deals targeted to the buyer seat
        GET /deals/{deal_id}    — single deal detail

Usage::

    connector = IndexExchangeConnector()         # reads env vars
    # or
    connector = IndexExchangeConnector(api_key="...", seat_id="...")

    if not connector.is_configured():
        raise RuntimeError("Set IX_API_KEY and IX_SEAT_ID")

    result = connector.fetch_deals(status="active")
    for deal in result.deals:
        deal_id = store.save_deal(**deal)
        store.save_portfolio_metadata(
            deal_id=deal_id,
            import_source=connector.import_source,
            import_date=today_iso,
        )
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from ..ssp_connector_base import (
    SSPAuthError,
    SSPConnectionError,
    SSPConnector,
    SSPFetchResult,
    SSPRateLimitError,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Field mapping constants
# ---------------------------------------------------------------------------

# IX dealType → DealStore deal_type
_DEAL_TYPE_MAP: dict[str, str] = {
    "PG": "PG",
    "PD": "PD",
    "PMP": "PA",      # Private Marketplace → Private Auction
    "PA": "PA",
    # Lowercase aliases (defensive; IX docs show uppercase)
    "pg": "PG",
    "pd": "PD",
    "pmp": "PA",
    "pa": "PA",
}

# IX status → DealStore status
_STATUS_MAP: dict[str, str] = {
    "active": "active",
    "inactive": "paused",
    "pending": "imported",
}

# IX adType → DealStore media_type
_MEDIA_TYPE_MAP: dict[str, str] = {
    "display": "DIGITAL",
    "DISPLAY": "DIGITAL",
    "video": "CTV",
    "VIDEO": "CTV",
    "native": "DIGITAL",
    "NATIVE": "DIGITAL",
    "audio": "AUDIO",
    "AUDIO": "AUDIO",
}

_IX_BASE_URL = "https://api.indexexchange.com"
_DEALS_ENDPOINT = "/deals"


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class IndexExchangeConnector(SSPConnector):
    """Index Exchange SSP deal import connector.

    Discovers and imports deals that publishers have targeted to the buyer's
    seat.  Index Exchange deal creation is publisher-side only; this connector
    uses GET-only endpoints to fetch deals from the IX API.

    Credentials are read from constructor args first, then from env vars:
        IX_API_KEY:  Index Exchange API key (sent as X-API-Key header)
        IX_SEAT_ID:  Buyer seat/member ID (sent as seatId query param)

    Fetch filters (all optional, passed as kwargs to ``fetch_deals``):
        status:    "active" | "inactive" | "pending" | "all" (default: all)
        deal_type: "PG" | "PD" | "PMP" | "all" (default: all)
        page_size: int (default: 100)
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        seat_id: str | None = None,
        base_url: str = _IX_BASE_URL,
    ) -> None:
        """Initialise the connector.

        Args:
            api_key: Index Exchange API key.  Falls back to the
                ``IX_API_KEY`` env var when not provided.
            seat_id: Buyer seat/member ID.  Falls back to the
                ``IX_SEAT_ID`` env var when not provided.
            base_url: Override the IX API base URL (used in tests).
        """
        self._api_key: str = api_key or os.environ.get("IX_API_KEY", "")
        self._seat_id: str = seat_id or os.environ.get("IX_SEAT_ID", "")
        self._base_url: str = base_url.rstrip("/")
        # Lazily overridden in tests via connector._client = httpx.Client(...)
        self._client: httpx.Client = httpx.Client(
            headers={"X-API-Key": self._api_key},
            timeout=30.0,
        )

    # ------------------------------------------------------------------
    # SSPConnector abstract properties
    # ------------------------------------------------------------------

    @property
    def ssp_name(self) -> str:
        """Human-readable SSP name."""
        return "Index Exchange"

    @property
    def import_source(self) -> str:
        """Import source tag written to portfolio_metadata."""
        return "INDEX_EXCHANGE"

    def get_required_config(self) -> list[str]:
        """Required env vars for this connector."""
        return ["IX_API_KEY", "IX_SEAT_ID"]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_deals(self, **kwargs: Any) -> SSPFetchResult:
        """Fetch deals from Index Exchange that are targeted to the buyer seat.

        Handles pagination automatically: iterates pages until the API
        returns an empty deals list or a partial page.

        Args:
            status:    Filter by deal status.  Pass ``"all"`` (default)
                to fetch all statuses.  IX values: ``"active"``,
                ``"inactive"``, ``"pending"``.
            deal_type: Filter by deal type.  Pass ``"all"`` (default)
                to fetch all types.  IX values: ``"PG"``, ``"PD"``,
                ``"PMP"``.
            page_size: Number of results per page (default 100).

        Returns:
            SSPFetchResult with normalized deals ready for DealStore.

        Raises:
            SSPAuthError: HTTP 401 or 403 from IX API.
            SSPRateLimitError: HTTP 429 from IX API.
            SSPConnectionError: HTTP 5xx or network error.
        """
        status_filter: str = kwargs.get("status", "all")
        deal_type_filter: str = kwargs.get("deal_type", "all")
        page_size: int = int(kwargs.get("page_size", 100))

        result = SSPFetchResult(ssp_name=self.ssp_name)
        seen_deal_ids: set[str] = set()
        page = 1

        while True:
            raw_deals = self._fetch_page(
                page=page,
                page_size=page_size,
                status_filter=status_filter,
                deal_type_filter=deal_type_filter,
            )

            if not raw_deals:
                break

            result.raw_response_count += len(raw_deals)

            for raw in raw_deals:
                result.total_fetched += 1
                try:
                    normalized = self._normalize_deal(raw)
                except (KeyError, ValueError) as exc:
                    result.errors.append(f"Deal normalization failed: {exc}")
                    result.failed += 1
                    continue

                # Deduplicate by seller_deal_id
                deal_id = normalized.get("seller_deal_id")
                if deal_id and deal_id in seen_deal_ids:
                    result.skipped += 1
                    continue

                if deal_id:
                    seen_deal_ids.add(deal_id)

                result.deals.append(normalized)
                result.successful += 1

            # Stop if we received a partial page (no more data)
            if len(raw_deals) < page_size:
                break

            page += 1

        logger.info(
            "Index Exchange: fetched %d deals — %d ok, %d failed, %d skipped",
            result.total_fetched,
            result.successful,
            result.failed,
            result.skipped,
        )
        return result

    def test_connection(self) -> bool:
        """Test whether the API credentials are valid.

        Makes a minimal API call (page_size=1) and returns True if the
        request succeeds, False on any auth or network failure.  Never
        raises — all errors are caught and logged.

        Returns:
            True if connection and credentials are valid, False otherwise.
        """
        try:
            self._fetch_page(
                page=1,
                page_size=1,
                status_filter="all",
                deal_type_filter="all",
            )
            logger.info("Index Exchange: connection test passed")
            return True
        except (SSPAuthError, SSPConnectionError, SSPRateLimitError) as exc:
            logger.warning("Index Exchange: connection test failed — %s", exc)
            return False

    # ------------------------------------------------------------------
    # SSPConnector abstract method
    # ------------------------------------------------------------------

    def _normalize_deal(self, raw_deal: dict[str, Any]) -> dict[str, Any]:
        """Map a single IX API deal object to DealStore kwargs.

        Index Exchange deal structure (example)::

            {
                "dealId": "IX-PG-2026-001",
                "name": "Premium News PG Package",
                "status": "active",
                "dealType": "PG",
                "floorPrice": null,
                "price": 52.00,
                "currency": "USD",
                "publisherDomain": "news.example.com",
                "adType": "display",
                "startDate": "2026-04-01",
                "endDate": "2026-06-30",
                "impressions": 3000000,
                "description": "...",
                "targeting": {
                    "geo": ["US", "CA"],
                    "contentCategories": ["IAB12"],
                    "audiences": []
                },
                "formats": ["display_banner"]
            }

        Args:
            raw_deal: A single deal dict from the IX ``GET /deals`` response.

        Returns:
            Dict matching ``DealStore.save_deal()`` keyword arguments.

        Raises:
            KeyError: If ``dealId`` or ``name`` is missing from ``raw_deal``.
            ValueError: If ``dealType`` is present but not a recognized IX
                value.
        """
        # Required fields — KeyError propagates as-is
        seller_deal_id: str = raw_deal["dealId"]
        display_name: str = raw_deal["name"]

        # Deal type normalization
        raw_deal_type: str = raw_deal.get("dealType", "")
        normalized_deal_type = _DEAL_TYPE_MAP.get(raw_deal_type)
        if normalized_deal_type is None:
            raise ValueError(
                f"Unrecognized Index Exchange deal type: '{raw_deal_type}'. "
                f"Expected one of: {sorted(_DEAL_TYPE_MAP.keys())}"
            )

        # Status normalization (unknown statuses → imported)
        raw_status: str = raw_deal.get("status", "pending")
        normalized_status = _STATUS_MAP.get(raw_status.lower(), "imported")

        # Media type (optional)
        raw_ad_type: str = raw_deal.get("adType", "")
        media_type = _MEDIA_TYPE_MAP.get(raw_ad_type) if raw_ad_type else None

        # Targeting fields — convert arrays to comma-separated strings for DealStore
        targeting = raw_deal.get("targeting") or {}
        geo_list: list[str] = targeting.get("geo") or []
        content_cats: list[str] = targeting.get("contentCategories") or []
        audiences: list[str] = targeting.get("audiences") or []
        formats_list: list[str] = raw_deal.get("formats") or []

        geo_targets = ", ".join(geo_list) if geo_list else None
        content_categories = ", ".join(content_cats) if content_cats else None
        audience_segments = ", ".join(audiences) if audiences else None
        formats = ", ".join(formats_list) if formats_list else None

        return {
            # Identity
            "seller_deal_id": seller_deal_id,
            "product_id": seller_deal_id,
            "display_name": display_name,
            # Counterparty (hardcoded for Index Exchange)
            "seller_org": "Index Exchange",
            "seller_type": "SSP",
            "seller_url": self._base_url,
            "seller_domain": raw_deal.get("publisherDomain"),
            # Deal metadata
            "deal_type": normalized_deal_type,
            "media_type": media_type,
            "status": normalized_status,
            # Pricing
            "fixed_price_cpm": raw_deal.get("price"),
            "bid_floor_cpm": raw_deal.get("floorPrice"),
            "currency": raw_deal.get("currency") or "USD",
            # Inventory targeting (lists serialised as comma-separated strings)
            "formats": formats,
            "geo_targets": geo_targets,
            "content_categories": content_categories,
            "audience_segments": audience_segments,
            # Flight dates
            "flight_start": raw_deal.get("startDate"),
            "flight_end": raw_deal.get("endDate"),
            # Volume (PG)
            "impressions": raw_deal.get("impressions"),
            # Description
            "description": raw_deal.get("description"),
        }

    # ------------------------------------------------------------------
    # Private HTTP helpers
    # ------------------------------------------------------------------

    def _build_params(
        self,
        *,
        page: int,
        page_size: int,
        status_filter: str,
        deal_type_filter: str,
    ) -> dict[str, str]:
        """Build query parameters for the deals endpoint."""
        params: dict[str, str] = {
            "seatId": self._seat_id,
            "page": str(page),
            "page_size": str(page_size),
        }
        # Only add status/deal_type params when not "all" — avoids sending
        # ?status=all which some APIs treat as a literal filter value.
        if status_filter and status_filter.lower() != "all":
            params["status"] = status_filter
        if deal_type_filter and deal_type_filter.lower() != "all":
            params["deal_type"] = deal_type_filter
        return params

    def _fetch_page(
        self,
        *,
        page: int,
        page_size: int,
        status_filter: str,
        deal_type_filter: str,
    ) -> list[dict[str, Any]]:
        """Fetch a single page of deals from the IX API.

        Args:
            page: 1-indexed page number.
            page_size: Number of results per page.
            status_filter: Status filter string ("all" = no filter).
            deal_type_filter: Deal type filter string ("all" = no filter).

        Returns:
            List of raw deal dicts from the API response.

        Raises:
            SSPAuthError: HTTP 401 or 403.
            SSPRateLimitError: HTTP 429.
            SSPConnectionError: HTTP 5xx or network error.
        """
        url = f"{self._base_url}{_DEALS_ENDPOINT}"
        params = self._build_params(
            page=page,
            page_size=page_size,
            status_filter=status_filter,
            deal_type_filter=deal_type_filter,
        )
        # Ensure the client has the correct auth header even if api_key was
        # set after the client was constructed (e.g. read from env).
        headers = {"X-API-Key": self._api_key}

        try:
            response = self._client.get(url, params=params, headers=headers)
        except httpx.TransportError as exc:
            raise SSPConnectionError(
                f"Index Exchange API network error: {exc}"
            ) from exc

        if response.status_code in (401, 403):
            raise SSPAuthError(
                f"Index Exchange API authentication failed (HTTP {response.status_code}): "
                f"{response.text}",
                status_code=response.status_code,
            )

        if response.status_code == 429:
            retry_after: int | None = None
            raw_retry = response.headers.get("Retry-After")
            if raw_retry is not None:
                try:
                    retry_after = int(raw_retry)
                except ValueError:
                    pass
            raise SSPRateLimitError(
                "Index Exchange API rate limit exceeded (HTTP 429)",
                retry_after=retry_after,
            )

        if response.status_code >= 500:
            raise SSPConnectionError(
                f"Index Exchange API server error (HTTP {response.status_code}): "
                f"{response.text}",
                status_code=response.status_code,
            )

        data: dict[str, Any] = response.json()
        return data.get("deals", [])
