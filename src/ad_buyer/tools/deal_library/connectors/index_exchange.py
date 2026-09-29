# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Index Exchange SSP connector for deal import.

Index Exchange is the #1 US web SSP (19% share).  Its deal model is strictly
publisher-side: publishers create deals in Index's UI or API and specify buyer
seat IDs.  This connector discovers and imports deals that publishers have
targeted to the buyer's seat — it uses the GET endpoints only.

API details (confirmed against the Deals API, the service implementing
these routes):
    Base URL: https://app.indexexchange.com/api/deals
    Auth:     Authorization: Bearer <keycloak-jwt> (IX_API_KEY env var)
    Endpoints:
        GET /v3/deals               — list deals targeted to the buyer seat
        GET /v3/deals/{internalDealID} — single deal detail

There is no seatId query parameter on GET /v3/deals — which deals are
visible is determined server-side by the caller's authenticated identity,
not a client-supplied seat.

Usage::

    connector = IndexExchangeConnector()         # reads env vars
    # or
    connector = IndexExchangeConnector(api_key="...")

    if not connector.is_configured():
        raise RuntimeError("Set IX_API_KEY")

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

# Index status string → DealStore status. "expired" and "auto-paused" are both
# terminal/system-set states not settable via PATCH; mapped to "paused" for
# buyer readability since DealStore has no equivalent distinct states.
_STATUS_MAP: dict[str, str] = {
    "active": "active",
    "paused": "paused",
    "expired": "paused",
    "auto-paused": "paused",
}

_VALID_STATUS_FILTERS = {"active", "paused", "expired", "auto-paused", "all"}

# Standard targeting keyNames that represent geographic targeting, per the
# targeting key catalogue managed in supply-configuration-api.
_GEO_TARGETING_KEYS = {"Country", "region", "continent", "place", "area", "zipcode", "domain"}

_IX_BASE_URL = "https://app.indexexchange.com/api/deals"
_DEALS_ENDPOINT = "/v3/deals"


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class IndexExchangeConnector(SSPConnector):
    """Index Exchange SSP deal import connector.

    Discovers and imports deals that publishers have targeted to the buyer's
    seat.  Index Exchange deal creation is publisher-side only; this connector
    uses GET-only endpoints to fetch deals from the Index /v3/deals API.

    Credentials are read from constructor args first, then from env vars:
        IX_API_KEY: Index Exchange Keycloak bearer token, sent as
            ``Authorization: Bearer <token>``.

    Fetch filters (all optional, passed as kwargs to ``fetch_deals``):
        status:     "active" | "paused" | "expired" | "auto-paused" | "all"
            (default: "all")
        class_ids:  list[int] of Index classID values to filter on — 1 (Direct),
            3 (Inventory Package), 4 (Marketplace Package), 5 (Deal with
            Marketplaces). Omitted/empty means no filter.
        account_ids: list[int] of Index account.accountID values to filter on.
            Omitted/empty means no filter — strongly recommended whenever
            the credential's visibility spans more than one account (e.g.
            an admin-scoped token), since an unfiltered call pages through
            every deal that credential can see platform-wide.
        page_size:  int (default 100, capped at 2000 — the API maximum)
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        """Initialise the connector.

        Args:
            api_key: Index Exchange Keycloak bearer token.  Falls back to
                the ``IX_API_KEY`` env var when not provided.
            base_url: Override the Index API base URL. Falls back to the
                ``IX_API_URL`` env var, then to the production URL, when
                not provided. A token issued for one environment will not
                authenticate against another — get this wrong and every
                call fails with a connection/auth error that has nothing
                to do with the credential itself.
        """
        self._api_key: str = api_key or os.environ.get("IX_API_KEY", "")
        self._base_url: str = (base_url or os.environ.get("IX_API_URL") or _IX_BASE_URL).rstrip("/")
        # Lazily overridden in tests via connector._client = httpx.Client(...)
        self._client: httpx.Client = httpx.Client(
            headers={"Authorization": f"Bearer {self._api_key}"},
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
        return ["IX_API_KEY"]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_deals(self, **kwargs: Any) -> SSPFetchResult:
        """Fetch deals from Index Exchange that are targeted to the buyer seat.

        Handles pagination automatically: iterates pages (0-indexed, via
        pageOffset) until the cumulative offset reaches the API's reported
        totalCount, or a page comes back empty.

        Args:
            status: Filter by deal status. Pass "all" (default) to fetch
                all statuses. Index values: "active", "paused", "expired",
                "auto-paused".
            class_ids: List of Index classID ints to filter on (1, 3, 4, 5).
                Omitted/empty means no filter.
            account_ids: List of Index account.accountID ints to filter on.
                Omitted/empty means no filter — with a broadly-scoped
                credential (e.g. an admin token), an unfiltered call can
                page through every deal the token can see across the
                entire platform, not just this buyer's own account(s).
                Scope this whenever the credential's visibility is
                broader than a single seat/account.
            page_size: Number of results per page (default 100, max 2000).

        Returns:
            SSPFetchResult with normalized deals ready for DealStore.

        Raises:
            ValueError: If status is not a recognized filter value.
            SSPAuthError: HTTP 401 or 403 from Index API.
            SSPRateLimitError: HTTP 429 from Index API.
            SSPConnectionError: HTTP 5xx or network error.
        """
        status_filter: str = kwargs.get("status", "all")
        if status_filter.lower() not in _VALID_STATUS_FILTERS:
            raise ValueError(
                f"Invalid status filter '{status_filter}'. "
                f"Must be one of: {sorted(_VALID_STATUS_FILTERS)}"
            )
        class_ids: list[int] = list(kwargs.get("class_ids") or [])
        account_ids: list[int] = list(kwargs.get("account_ids") or [])
        page_size: int = min(int(kwargs.get("page_size", 100)), 2000)

        result = SSPFetchResult(ssp_name=self.ssp_name)
        seen_deal_ids: set[str] = set()
        page_offset = 0

        while True:
            raw_deals, total_count = self._fetch_page(
                page_offset=page_offset,
                page_size=page_size,
                status_filter=status_filter,
                class_ids=class_ids,
                account_ids=account_ids,
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

            page_offset += 1
            if (page_offset * page_size) >= total_count:
                break

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
                page_offset=0,
                page_size=1,
                status_filter="all",
                class_ids=[],
                account_ids=[],
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
        """Map a single Index API deal object to DealStore kwargs.

        Index Exchange deal structure (example, GET /v3/deals response
        item — see the Index deal-response schema for the full
        field reference)::

            {
                "externalDealID": "IX-PG-2026-001",
                "internalDealID": 44001,
                "name": "Premium News PG Package",
                "status": "active",
                "classID": 1,
                "floor": 52.00,
                "startDate": "2026-04-01",
                "endDate": "2026-06-30",
                "account": {"accountID": 1470498},
                "auctionType": "fixed",
                "directConfigurations": {
                    "dspID": 85,
                    "programmaticGuaranteed": true,
                    "impressionGoal": 3000000
                },
                "targeting": [
                    {
                        "targetingType": "standard",
                        "keyName": "Country",
                        "sets": [{"values": [{"value": "US"}], "operator": "ANY_OF"}]
                    }
                ]
            }

        Args:
            raw_deal: A single deal dict from the Index ``GET /v3/deals``
                response's ``deals`` array.

        Returns:
            Dict matching ``DealStore.save_deal()`` keyword arguments.

        Raises:
            KeyError: If ``externalDealID`` or ``name`` is missing from
                ``raw_deal``.
            ValueError: If ``classID`` is missing or not a recognized value.
        """
        # Required fields — KeyError propagates as-is
        seller_deal_id: str = raw_deal["externalDealID"]
        display_name: str = raw_deal["name"]
        internal_deal_id = raw_deal.get("internalDealID")

        # Deal type normalization — there is no dealType field on the API;
        # it's derived from classID + directConfigurations.programmaticGuaranteed
        # + auctionType. "PMP" has no DealStore equivalent (VALID_DEAL_TYPES
        # doesn't include it) — classID 1-first/3/4 all map to "PA" (Private
        # Auction), the same closest-analog choice this connector already
        # made for the old, fictional "PMP" dealType value.
        class_id = raw_deal.get("classID")
        if class_id is None:
            raise ValueError("Missing classID — cannot determine deal type")

        direct_config: dict[str, Any] = raw_deal.get("directConfigurations") or {}
        programmatic_guaranteed = bool(direct_config.get("programmaticGuaranteed", False))
        auction_type = raw_deal.get("auctionType")

        if class_id == 1 and programmatic_guaranteed:
            normalized_deal_type = "PG"
        elif class_id == 1 and not programmatic_guaranteed and auction_type == "fixed":
            normalized_deal_type = "PD"
        elif class_id in (1, 3, 4):
            normalized_deal_type = "PA"
        elif class_id == 5:
            normalized_deal_type = "PA"
        else:
            raise ValueError(
                f"Unrecognized Index Exchange classID: {class_id!r}. Expected one of: 1, 3, 4, 5"
            )

        # Status normalization (unknown statuses → paused, matching
        # DealStore.VALID_STATUSES — "imported" is not a valid status there)
        raw_status: str = raw_deal.get("status", "")
        normalized_status = _STATUS_MAP.get(raw_status.lower(), "paused")

        # Targeting — a structured array, not a flat dict. Filter to known
        # geo keyNames for geo_targets; content_categories/audience_segments
        # have no confirmed standard-key equivalent in the targeting
        # catalogue (see gap analysis), so they're left unset unless a
        # future standard key is identified.
        targeting_items: list[dict[str, Any]] = raw_deal.get("targeting") or []
        geo_values: list[str] = []
        for item in targeting_items:
            if item.get("keyName") in _GEO_TARGETING_KEYS:
                for value_set in item.get("sets") or []:
                    for value_obj in value_set.get("values") or []:
                        v = value_obj.get("value")
                        if v:
                            geo_values.append(str(v))
        geo_targets = ", ".join(geo_values) if geo_values else None

        return {
            # Identity
            "seller_deal_id": seller_deal_id,
            "product_id": seller_deal_id,
            "display_name": display_name,
            # Counterparty (hardcoded for Index Exchange)
            "seller_org": "Index Exchange",
            "seller_type": "SSP",
            "seller_url": self._base_url,
            "seller_domain": None,  # not a field on this API — no publisherDomain
            # Deal metadata
            "deal_type": normalized_deal_type,
            "media_type": None,  # not a field on this API — no adType
            "status": normalized_status,
            # Pricing — a single "floor" field; fixed_price_cpm only makes
            # sense for PG deals (a true guaranteed fixed rate).
            "fixed_price_cpm": raw_deal.get("floor") if normalized_deal_type == "PG" else None,
            "bid_floor_cpm": raw_deal.get("floor") if normalized_deal_type != "PG" else None,
            "currency": "USD",  # not returned by the API — always USD today
            # Inventory targeting
            "formats": None,  # not a field on this API — no formats array
            "geo_targets": geo_targets,
            "content_categories": None,  # no confirmed standard key
            "audience_segments": None,  # no confirmed standard key
            # Flight dates
            "flight_start": raw_deal.get("startDate"),
            "flight_end": raw_deal.get("endDate"),
            # Volume (PG only — impressionGoal lives under directConfigurations)
            "impressions": direct_config.get("impressionGoal"),
            # internalDealID isn't a first-class DealStore field; preserved
            # here so future write operations (PATCH, etc.) aren't left with
            # no way to address the deal on Index's side.
            "description": str(internal_deal_id) if internal_deal_id is not None else None,
        }

    # ------------------------------------------------------------------
    # Private HTTP helpers
    # ------------------------------------------------------------------

    def _build_params(
        self,
        *,
        page_offset: int,
        page_size: int,
        status_filter: str,
        class_ids: list[int],
        account_ids: list[int],
    ) -> dict[str, Any]:
        """Build query parameters for the deals endpoint."""
        params: dict[str, Any] = {
            "pageOffset": page_offset,
            "pageSize": page_size,
        }
        # Only add status/classIDs/accountIDs params when filtering — avoids
        # sending ?status=all which some APIs treat as a literal filter value.
        if status_filter and status_filter.lower() != "all":
            params["status"] = status_filter
        if class_ids:
            params["classIDs"] = class_ids
        if account_ids:
            params["accountIDs"] = account_ids
        return params

    def _fetch_page(
        self,
        *,
        page_offset: int,
        page_size: int,
        status_filter: str,
        class_ids: list[int],
        account_ids: list[int],
    ) -> tuple[list[dict[str, Any]], int]:
        """Fetch a single page of deals from the Index API.

        Args:
            page_offset: 0-indexed page offset.
            page_size: Number of results per page.
            status_filter: Status filter string ("all" = no filter).
            class_ids: classID filter list (empty = no filter).
            account_ids: account.accountID filter list (empty = no filter).

        Returns:
            Tuple of (list of raw deal dicts, totalCount from the response
            envelope).

        Raises:
            SSPAuthError: HTTP 401 or 403.
            SSPRateLimitError: HTTP 429.
            SSPConnectionError: HTTP 5xx or network error.
        """
        url = f"{self._base_url}{_DEALS_ENDPOINT}"
        params = self._build_params(
            page_offset=page_offset,
            page_size=page_size,
            status_filter=status_filter,
            class_ids=class_ids,
            account_ids=account_ids,
        )
        # Ensure the client has the correct auth header even if api_key was
        # set after the client was constructed (e.g. read from env).
        headers = {"Authorization": f"Bearer {self._api_key}"}

        try:
            response = self._client.get(url, params=params, headers=headers)
        except httpx.TransportError as exc:
            raise SSPConnectionError(f"Index Exchange API network error: {exc}") from exc

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
                f"Index Exchange API server error (HTTP {response.status_code}): {response.text}",
                status_code=response.status_code,
            )

        data: dict[str, Any] = response.json()
        return data.get("deals", []), int(data.get("totalCount", 0))
