# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Magnite SSP connector for deal import.

Magnite operates two separate platforms with different API endpoints:

- **Magnite Streaming (CTV/OTT)**: ``api.tremorhub.com``
  The priority platform for CTV inventory (Roku, Fire TV, Samsung TV Plus, etc.).
- **Magnite DV+ (display/video)**: ``api.rubiconproject.com``

Both platforms use session-based authentication: POST credentials to the login
endpoint to receive a session cookie, then include that cookie on all subsequent
requests.

Magnite does not provide a buyer-facing deal creation API. This connector reads
deals that have been configured by sellers/publishers and targeted to the
buyer's seat ID.

Configuration (environment variables)::

    MAGNITE_ACCESS_KEY   Required. API access key (credential username).
    MAGNITE_SECRET_KEY   Required. API secret key (credential password).
    MAGNITE_SEAT_ID      Required. Buyer seat ID for deal endpoint URL.
    MAGNITE_PLATFORM     Optional. "streaming" (default) or "dv_plus".

Usage::

    connector = MagniteConnector(platform="streaming")
    if not connector.is_configured():
        raise RuntimeError("Set MAGNITE_ACCESS_KEY, MAGNITE_SECRET_KEY, MAGNITE_SEAT_ID")

    if connector.test_connection():
        result = connector.fetch_deals()
        for deal in result.deals:
            store.save_deal(**deal)
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from ad_buyer.tools.deal_library.ssp_connector_base import (
    SSPAuthError,
    SSPConnectionError,
    SSPConnector,
    SSPFetchResult,
    SSPRateLimitError,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Platform constants
# ---------------------------------------------------------------------------

PLATFORM_STREAMING = "streaming"
PLATFORM_DV_PLUS = "dv_plus"

_VALID_PLATFORMS = {PLATFORM_STREAMING, PLATFORM_DV_PLUS}

_API_BASES: dict[str, str] = {
    PLATFORM_STREAMING: "https://api.tremorhub.com",
    PLATFORM_DV_PLUS: "https://api.rubiconproject.com",
}

# Login path is the same on both platforms
_LOGIN_PATH = "/v1/resources/login"
# Deals endpoint uses seatId in path
_DEALS_PATH_TEMPLATE = "/v1/resources/seats/{seat_id}/deals"

# Deal type mapping: Magnite API value -> DealStore canonical value
_DEAL_TYPE_MAP: dict[str, str] = {
    "PG": "PG",
    "PD": "PD",
    "PA": "PA",
    "OPEN_AUCTION": "OPEN_AUCTION",
    "UPFRONT": "UPFRONT",
    "SCATTER": "SCATTER",
    # Some Magnite responses use lowercase or alternate spellings
    "pg": "PG",
    "pd": "PD",
    "pa": "PA",
    "open_auction": "OPEN_AUCTION",
    "preferred_deal": "PD",
    "preferred": "PD",
    "private_auction": "PA",
    "programmatic_guaranteed": "PG",
    "programmatic guaranteed": "PG",
}

# Media type mapping: Magnite API value -> DealStore canonical value
_MEDIA_TYPE_MAP: dict[str, str] = {
    "CTV": "CTV",
    "ctv": "CTV",
    "connected_tv": "CTV",
    "CONNECTED_TV": "CTV",
    "VIDEO": "DIGITAL",
    "video": "DIGITAL",
    "DISPLAY": "DIGITAL",
    "display": "DIGITAL",
    "DIGITAL": "DIGITAL",
    "digital": "DIGITAL",
    "AUDIO": "AUDIO",
    "audio": "AUDIO",
    "LINEAR_TV": "LINEAR_TV",
    "linear_tv": "LINEAR_TV",
    "DOOH": "DOOH",
    "dooh": "DOOH",
}


# ---------------------------------------------------------------------------
# MagniteConnector
# ---------------------------------------------------------------------------


class MagniteConnector(SSPConnector):
    """SSP connector for Magnite (Streaming CTV and DV+ platforms).

    Fetches deals from the Magnite API using session-based authentication.
    Supports both the Magnite Streaming (CTV/OTT) and Magnite DV+ platforms.

    The connector reads deals that sellers have configured and targeted to the
    buyer's seat — there is no buyer-side deal creation via this API.

    Args:
        platform: Which Magnite platform to use. One of ``"streaming"``
            (default) or ``"dv_plus"``. If not provided, reads from the
            ``MAGNITE_PLATFORM`` env var, falling back to ``"streaming"``.

    Raises:
        ValueError: If ``platform`` is not one of the valid platform strings.

    Environment Variables:
        MAGNITE_ACCESS_KEY: API access key (required).
        MAGNITE_SECRET_KEY: API secret key (required).
        MAGNITE_SEAT_ID: Buyer seat ID (required).
        MAGNITE_PLATFORM: Platform selection (optional, default "streaming").
    """

    def __init__(self, *, platform: str | None = None) -> None:
        # Resolve platform: constructor arg > env var > default
        resolved_platform = (
            platform
            or os.environ.get("MAGNITE_PLATFORM", "")
            or PLATFORM_STREAMING
        )

        if resolved_platform not in _VALID_PLATFORMS:
            raise ValueError(
                f"Invalid Magnite platform '{resolved_platform}'. "
                f"Must be one of: {', '.join(sorted(_VALID_PLATFORMS))}"
            )

        self._platform = resolved_platform

    # ------------------------------------------------------------------
    # SSPConnector abstract properties
    # ------------------------------------------------------------------

    @property
    def ssp_name(self) -> str:
        """Human-readable SSP name."""
        return "Magnite"

    @property
    def import_source(self) -> str:
        """Import source tag written to portfolio_metadata."""
        return "MAGNITE"

    # ------------------------------------------------------------------
    # Platform and URL properties
    # ------------------------------------------------------------------

    @property
    def platform(self) -> str:
        """Which Magnite platform this connector targets."""
        return self._platform

    @property
    def api_base_url(self) -> str:
        """Base URL for the configured Magnite platform."""
        return _API_BASES[self._platform]

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def get_required_config(self) -> list[str]:
        """Return the environment variable names required for this connector."""
        return ["MAGNITE_ACCESS_KEY", "MAGNITE_SECRET_KEY", "MAGNITE_SEAT_ID"]

    # ------------------------------------------------------------------
    # Internal: session auth
    # ------------------------------------------------------------------

    def _build_login_url(self) -> str:
        return f"{self.api_base_url}{_LOGIN_PATH}"

    def _build_deals_url(self, seat_id: str) -> str:
        path = _DEALS_PATH_TEMPLATE.format(seat_id=seat_id)
        return f"{self.api_base_url}{path}"

    def _login(self, client: httpx.Client) -> str:
        """POST credentials to Magnite login endpoint; return session cookie value.

        Args:
            client: An httpx.Client instance with cookie jar.

        Returns:
            The raw session cookie string (the entire cookie header value).

        Raises:
            SSPAuthError: If the API returns 401 or 403.
            SSPConnectionError: If the API cannot be reached.
        """
        access_key = os.environ.get("MAGNITE_ACCESS_KEY", "")
        secret_key = os.environ.get("MAGNITE_SECRET_KEY", "")
        login_url = self._build_login_url()

        logger.debug("Magnite: authenticating at %s", login_url)

        try:
            response = client.post(
                login_url,
                json={"access-key": access_key, "secret-key": secret_key},
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                raise SSPAuthError(
                    f"Magnite authentication failed (HTTP {status}). "
                    "Check MAGNITE_ACCESS_KEY and MAGNITE_SECRET_KEY.",
                    status_code=status,
                ) from exc
            raise SSPConnectionError(
                f"Magnite login returned unexpected HTTP {status}.",
                status_code=status,
            ) from exc
        except httpx.ConnectError as exc:
            raise SSPConnectionError(
                f"Cannot connect to Magnite API at {login_url}: {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise SSPConnectionError(
                f"Timeout connecting to Magnite API at {login_url}: {exc}"
            ) from exc

        # The session cookie is set by the server; httpx stores it in the
        # client's cookie jar automatically.  Return it for logging/debug.
        session_cookie = response.cookies.get("SESSION", "")
        logger.debug("Magnite: authentication successful (session cookie present: %s)", bool(session_cookie))
        return session_cookie

    # ------------------------------------------------------------------
    # Internal: deals fetch
    # ------------------------------------------------------------------

    def _fetch_raw_deals(self, client: httpx.Client, seat_id: str) -> list[dict]:
        """GET deals for the given seat ID. Client must already be authenticated.

        Args:
            client: An authenticated httpx.Client (session cookie set).
            seat_id: The buyer seat ID to fetch deals for.

        Returns:
            List of raw deal dicts from the Magnite API response.

        Raises:
            SSPAuthError: If the API returns 401/403 (session expired).
            SSPRateLimitError: If the API returns 429.
            SSPConnectionError: On any other HTTP error or network failure.
        """
        deals_url = self._build_deals_url(seat_id)
        logger.debug("Magnite: fetching deals from %s", deals_url)

        try:
            response = client.get(deals_url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                raise SSPAuthError(
                    f"Magnite session rejected during deals fetch (HTTP {status}). "
                    "Session may have expired.",
                    status_code=status,
                ) from exc
            if status == 429:
                retry_after_str = exc.response.headers.get("Retry-After", "")
                retry_after = int(retry_after_str) if retry_after_str.isdigit() else None
                raise SSPRateLimitError(
                    f"Magnite API rate limit exceeded (HTTP 429).",
                    retry_after=retry_after,
                ) from exc
            raise SSPConnectionError(
                f"Magnite deals endpoint returned HTTP {status}.",
                status_code=status,
            ) from exc
        except httpx.ConnectError as exc:
            raise SSPConnectionError(
                f"Cannot connect to Magnite API at {deals_url}: {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise SSPConnectionError(
                f"Timeout fetching Magnite deals from {deals_url}: {exc}"
            ) from exc

        data = response.json()
        deals = data.get("data", {}).get("deals", [])
        logger.debug("Magnite: received %d deals in response", len(deals))
        return deals

    # ------------------------------------------------------------------
    # SSPConnector abstract methods
    # ------------------------------------------------------------------

    def fetch_deals(self, **kwargs: Any) -> SSPFetchResult:
        """Fetch deals from Magnite API for the configured seat.

        Performs session-based authentication (login) then GETs deals
        targeted to the buyer seat. Normalizes each deal and returns
        an ``SSPFetchResult`` ready for ``DealStore.save_deal()``.

        Args:
            **kwargs: Reserved for future filter parameters (e.g., status,
                date range). Currently unused.

        Returns:
            SSPFetchResult with normalized deals, error messages, and counts.

        Raises:
            SSPAuthError: If credentials are invalid or session is rejected.
            SSPRateLimitError: If the API rate limits the request.
            SSPConnectionError: If the API cannot be reached.
        """
        seat_id = os.environ.get("MAGNITE_SEAT_ID", "")
        result = SSPFetchResult(ssp_name=self.ssp_name)

        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            # Step 1: Authenticate — may raise SSPAuthError / SSPConnectionError
            self._login(client)

            # Step 2: Fetch raw deals — may raise SSPRateLimitError / SSPConnectionError
            raw_deals = self._fetch_raw_deals(client, seat_id)

        result.raw_response_count = len(raw_deals)
        result.total_fetched = len(raw_deals)

        # Step 3: Normalize each deal, capturing per-deal errors
        for raw in raw_deals:
            try:
                normalized = self._normalize_deal(raw)
                result.deals.append(normalized)
                result.successful += 1
            except (KeyError, ValueError) as exc:
                deal_id = raw.get("id", "<unknown>")
                error_msg = f"Deal {deal_id!r}: {exc}"
                logger.warning("Magnite: normalization error — %s", error_msg)
                result.errors.append(error_msg)
                result.failed += 1

        logger.info(
            "Magnite (%s): fetched %d deals — %d ok, %d failed",
            self._platform,
            result.total_fetched,
            result.successful,
            result.failed,
        )
        return result

    def _normalize_deal(self, raw_deal: dict[str, Any]) -> dict[str, Any]:
        """Map a single Magnite API deal object to DealStore kwargs.

        Magnite deal structure (Streaming platform example)::

            {
                "id": "MAG-CTV-001",
                "name": "Roku Premium CTV - Q2 2026",
                "dealType": "PG",
                "status": "active",
                "currency": "USD",
                "price": {"cpm": 35.00, "type": "fixed"},
                "floor": 28.00,
                "impressions": 5000000,
                "startDate": "2026-04-01",
                "endDate": "2026-06-30",
                "mediaType": "CTV",
                "publisherName": "Roku Channel",
                "publisherDomain": "roku.com",
                "description": "...",
                "targeting": {
                    "geo": ["US"],
                    "contentCategories": ["IAB1"],
                    "audiences": ["18-49"]
                },
                "formats": ["video_30sec"]
            }

        Args:
            raw_deal: A single deal dict from the Magnite API response.

        Returns:
            Dict ready for ``DealStore.save_deal(**deal)``.

        Raises:
            KeyError: If the required ``id`` field is missing.
            ValueError: If ``dealType`` is not a recognized deal type.
        """
        # Required: deal ID
        if "id" not in raw_deal:
            raise KeyError("Missing required field 'id' in Magnite deal response")

        deal_id = raw_deal["id"]

        # Deal type (required, must be recognized)
        raw_deal_type = raw_deal.get("dealType", "")
        normalized_deal_type = _DEAL_TYPE_MAP.get(raw_deal_type)
        if normalized_deal_type is None:
            raise ValueError(
                f"Unrecognized Magnite deal type '{raw_deal_type}' for deal {deal_id!r}. "
                f"Known types: {sorted(_DEAL_TYPE_MAP.keys())}"
            )

        # Pricing: fixed CPM vs floor-only
        price_obj = raw_deal.get("price", {}) or {}
        price_type = price_obj.get("type", "")
        if price_type == "fixed":
            fixed_price_cpm = price_obj.get("cpm")
        else:
            fixed_price_cpm = None
        bid_floor_cpm = raw_deal.get("floor")

        # Media type (optional, may be None)
        raw_media_type = raw_deal.get("mediaType", "")
        media_type = _MEDIA_TYPE_MAP.get(raw_media_type) if raw_media_type else None

        # Targeting fields — convert arrays to comma-separated strings for DealStore
        targeting = raw_deal.get("targeting") or {}
        geo_list = targeting.get("geo") or []
        content_cats = targeting.get("contentCategories") or []
        audiences = targeting.get("audiences") or []
        formats_list = raw_deal.get("formats") or []

        geo_targets = ", ".join(geo_list) if geo_list else None
        content_categories = ", ".join(content_cats) if content_cats else None
        audience_segments = ", ".join(audiences) if audiences else None
        formats = ", ".join(formats_list) if formats_list else None

        return {
            # Identity
            "seller_deal_id": deal_id,
            "display_name": raw_deal.get("name"),
            "product_id": deal_id,
            # Seller info
            "seller_org": raw_deal.get("publisherName"),
            "seller_domain": raw_deal.get("publisherDomain"),
            "seller_url": self.api_base_url,
            "seller_type": "SSP",
            # Deal classification
            "deal_type": normalized_deal_type,
            "media_type": media_type,
            "status": "imported",
            # Pricing
            "currency": raw_deal.get("currency", "USD"),
            "fixed_price_cpm": fixed_price_cpm,
            "bid_floor_cpm": bid_floor_cpm,
            # Volume
            "impressions": raw_deal.get("impressions"),
            # Flight dates
            "flight_start": raw_deal.get("startDate"),
            "flight_end": raw_deal.get("endDate"),
            # Targeting & formats
            "geo_targets": geo_targets,
            "content_categories": content_categories,
            "audience_segments": audience_segments,
            "formats": formats,
            # Description
            "description": raw_deal.get("description"),
        }

    # ------------------------------------------------------------------
    # test_connection()
    # ------------------------------------------------------------------

    def test_connection(self) -> bool:
        """Verify that API credentials work by attempting a login.

        Returns:
            True if authentication succeeds; False otherwise.
            Never raises — all errors are caught and logged.
        """
        if not self.is_configured():
            logger.warning(
                "Magnite: test_connection() called but connector is not configured. "
                "Set MAGNITE_ACCESS_KEY, MAGNITE_SECRET_KEY, MAGNITE_SEAT_ID."
            )
            return False

        try:
            with httpx.Client(follow_redirects=True, timeout=10.0) as client:
                self._login(client)
            logger.info("Magnite (%s): connection test passed", self._platform)
            return True
        except (SSPAuthError, SSPConnectionError) as exc:
            logger.warning("Magnite: connection test failed — %s", exc)
            return False
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Magnite: unexpected error during connection test — %s", exc)
            return False
