# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""PubMatic SSP deal import connector.

Fetches deals from PubMatic's buyer-facing PMP API and normalizes them
to ``DealStore.save_deal()`` kwargs.  This connector is Priority 1 in
the SSP integration plan: PubMatic exposes the most buyer-friendly SSP
API, supporting buyer-side PMP APIs and Targeted PMP (Audience Encore).

API details:
    Base URL: https://api.pubmatic.com
    Auth:     Bearer token (PUBMATIC_API_TOKEN env var)
    Seat:     PUBMATIC_SEAT_ID env var
    Endpoint: GET /pmp/deals

Usage::

    connector = PubMaticConnector()          # reads env vars
    # or
    connector = PubMaticConnector(api_token="...", seat_id="...")

    if not connector.is_configured():
        raise RuntimeError("PubMatic connector not configured")

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

import json
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

# PubMatic deal_type → DealStore deal_type
_DEAL_TYPE_MAP: dict[str, str] = {
    "pg": "PG",
    "preferred": "PD",
    "pmp": "PA",
    # Also accept canonical values in case the API varies
    "pd": "PD",
    "pa": "PA",
}

# PubMatic status → DealStore status
_STATUS_MAP: dict[str, str] = {
    "active": "active",
    "inactive": "paused",
    "pending": "imported",
}

_PUBMATIC_BASE_URL = "https://api.pubmatic.com"
_DEALS_ENDPOINT = "/pmp/deals"


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class PubMaticConnector(SSPConnector):
    """PubMatic SSP deal import connector.

    Fetches PMP, PG, and Preferred deals from PubMatic's buyer-facing
    REST API.  Supports Targeted PMP (Audience Encore) audience_segments.

    Credentials are read from constructor args first, then from env vars:
        PUBMATIC_API_TOKEN: PubMatic API Access Token (bearer token)
        PUBMATIC_SEAT_ID:   PubMatic buyer seat ID

    Fetch filters (all optional, passed as kwargs to ``fetch_deals``):
        status:    "active" | "inactive" | "pending" | "all" (default: all)
        deal_type: "PMP" | "PG" | "preferred" | "all" (default: all)
        page_size: int (default: 100, max: 500)
    """

    def __init__(
        self,
        *,
        api_token: str | None = None,
        seat_id: str | None = None,
        base_url: str = _PUBMATIC_BASE_URL,
    ) -> None:
        """Initialise the connector.

        Args:
            api_token: PubMatic API Access Token.  Falls back to the
                ``PUBMATIC_API_TOKEN`` env var when not provided.
            seat_id: PubMatic buyer seat ID.  Falls back to the
                ``PUBMATIC_SEAT_ID`` env var when not provided.
            base_url: Override the PubMatic API base URL (used in tests).
        """
        self._api_token: str = api_token or os.environ.get("PUBMATIC_API_TOKEN", "")
        self._seat_id: str = seat_id or os.environ.get("PUBMATIC_SEAT_ID", "")
        self._base_url: str = base_url.rstrip("/")
        # Lazily overridden in tests via connector._client = httpx.Client(...)
        self._client: httpx.Client = httpx.Client(
            headers={"Authorization": f"Bearer {self._api_token}"},
            timeout=30.0,
        )

    # ------------------------------------------------------------------
    # SSPConnector abstract properties
    # ------------------------------------------------------------------

    @property
    def ssp_name(self) -> str:
        """Human-readable SSP name."""
        return "PubMatic"

    @property
    def import_source(self) -> str:
        """Import source tag written to portfolio_metadata."""
        return "PUBMATIC"

    def get_required_config(self) -> list[str]:
        """Required env vars for this connector."""
        return ["PUBMATIC_API_TOKEN", "PUBMATIC_SEAT_ID"]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_deals(self, **kwargs: Any) -> SSPFetchResult:
        """Fetch deals from PubMatic's PMP API.

        Handles pagination automatically: iterates pages until the API
        returns an empty deals list or there are no more pages.

        Args:
            status:    Filter by deal status.  Pass ``"all"`` (default)
                to fetch all statuses.  PubMatic values:
                ``"active"``, ``"inactive"``, ``"pending"``.
            deal_type: Filter by deal type.  Pass ``"all"`` (default)
                to fetch all types.  PubMatic values:
                ``"PG"``, ``"PMP"``, ``"preferred"``.
            page_size: Number of results per page (default 100, max 500).

        Returns:
            SSPFetchResult with normalized deals ready for DealStore.

        Raises:
            SSPAuthError: HTTP 401 or 403 from PubMatic API.
            SSPRateLimitError: HTTP 429 from PubMatic API.
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

        return result

    def test_connection(self) -> bool:
        """Test whether the API credentials are valid.

        Makes a minimal API call (page_size=1) and returns True if
        the request succeeds, False on any auth or network failure.

        Returns:
            True if connection and credentials are valid, False otherwise.
        """
        try:
            self._fetch_page(page=1, page_size=1, status_filter="all", deal_type_filter="all")
            return True
        except (SSPAuthError, SSPConnectionError, SSPRateLimitError):
            return False

    # ------------------------------------------------------------------
    # SSPConnector abstract method
    # ------------------------------------------------------------------

    def _normalize_deal(self, raw_deal: dict[str, Any]) -> dict[str, Any]:
        """Map a single PubMatic API deal object to DealStore kwargs.

        Args:
            raw_deal: A single deal dict from the PubMatic ``GET /pmp/deals``
                response.

        Returns:
            Dict matching ``DealStore.save_deal()`` keyword arguments.

        Raises:
            KeyError: If ``deal_id`` or ``name`` is missing.
            ValueError: If ``deal_type`` is present but not a recognized
                PubMatic value.
        """
        # Required fields — KeyError propagates as-is
        seller_deal_id: str = raw_deal["deal_id"]
        display_name: str = raw_deal["name"]

        # Deal type normalization
        raw_deal_type: str = raw_deal.get("deal_type", "pmp")
        normalized_deal_type = _DEAL_TYPE_MAP.get(raw_deal_type.lower())
        if normalized_deal_type is None:
            raise ValueError(
                f"Unrecognized PubMatic deal type: '{raw_deal_type}'. "
                f"Expected one of: {sorted(_DEAL_TYPE_MAP.keys())}"
            )

        # Status normalization (unknown statuses → imported)
        raw_status: str = raw_deal.get("status", "pending")
        normalized_status = _STATUS_MAP.get(raw_status.lower(), "imported")

        # Serialise list fields to JSON strings for DealStore
        formats_raw = raw_deal.get("format")
        geo_raw = raw_deal.get("geo")
        categories_raw = raw_deal.get("categories")
        audience_raw = raw_deal.get("audience_segments")

        return {
            # Identity
            "seller_deal_id": seller_deal_id,
            "product_id": seller_deal_id,
            "display_name": display_name,
            # Counterparty (hardcoded for PubMatic)
            "seller_org": "PubMatic",
            "seller_type": "SSP",
            "seller_url": self._base_url,
            "seller_domain": raw_deal.get("publisher_domain"),
            # Deal metadata
            "deal_type": normalized_deal_type,
            "status": normalized_status,
            # Pricing
            "fixed_price_cpm": raw_deal.get("fixed_cpm"),
            "bid_floor_cpm": raw_deal.get("floor_price"),
            "currency": raw_deal.get("currency") or "USD",
            # Inventory targeting (lists serialised as JSON)
            "formats": json.dumps(formats_raw) if formats_raw else None,
            "geo_targets": json.dumps(geo_raw) if geo_raw else None,
            "content_categories": json.dumps(categories_raw) if categories_raw else None,
            "audience_segments": json.dumps(audience_raw) if audience_raw else None,
            # Flight dates
            "flight_start": raw_deal.get("start_date"),
            "flight_end": raw_deal.get("end_date"),
            # Volume (PG)
            "impressions": raw_deal.get("impressions"),
            # Description
            "description": raw_deal.get("notes"),
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
        """Fetch a single page of deals from the PubMatic API.

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
        # Ensure the client has the correct auth header even if token was
        # set after the client was constructed (e.g. read from env).
        headers = {"Authorization": f"Bearer {self._api_token}"}

        try:
            response = self._client.get(url, params=params, headers=headers)
        except httpx.TransportError as exc:
            raise SSPConnectionError(
                f"PubMatic API network error: {exc}"
            ) from exc

        if response.status_code in (401, 403):
            raise SSPAuthError(
                f"PubMatic API authentication failed (HTTP {response.status_code}): "
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
                f"PubMatic API rate limit exceeded (HTTP 429)",
                retry_after=retry_after,
            )

        if response.status_code >= 500:
            raise SSPConnectionError(
                f"PubMatic API server error (HTTP {response.status_code}): "
                f"{response.text}",
                status_code=response.status_code,
            )

        data: dict[str, Any] = response.json()
        return data.get("deals", [])
