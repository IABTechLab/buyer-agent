# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""IAB Deals API v1.0 Client.

Async HTTP client for the seller's quote-then-book deal endpoints:

- POST /api/v1/quotes            -- request a non-binding price quote
- GET  /api/v1/quotes/{id}       -- retrieve a quote
- POST /api/v1/deals             -- book a deal from a quote
- GET  /api/v1/deals/{id}        -- retrieve a deal
- POST /api/v1/deals/{id}/makegoods -- request makegood (linear TV)
- POST /api/v1/deals/{id}/cancel    -- request cancellation (linear TV)

Follows the API contract defined in docs/api/deal-creation-api-contract.md.
Uses httpx for async HTTP, with auth header injection, configurable
timeouts, and retry logic for transient server failures (502/503/504).
Optionally persists results to a DealStore when one is attached.
"""

import json
import logging
import sqlite3
from typing import Any, Optional

import httpx

from ..models.deals import (
    DealBookingRequest,
    DealResponse,
    QuoteRequest,
    QuoteResponse,
    SellerErrorResponse,
)
from ..models.linear_tv import CancellationRequest, MakegoodRequest

logger = logging.getLogger(__name__)

# HTTP status codes that indicate transient failures worth retrying
_RETRYABLE_STATUS_CODES = {502, 503, 504}

# Default configuration
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_RETRIES = 3


class DealsClientError(Exception):
    """Error raised by the DealsClient for API or transport failures.

    Attributes:
        status_code: HTTP status code (0 for transport errors like timeout).
        error_code: Machine-readable error code from the seller, if available.
        detail: Human-readable detail message.
        unsupported: When the seller rejects with the structured
            `audience_plan_unsupported` shape (proposal §5.7 layer 3),
            this carries the list of `{"path": ..., "reason": ...}` entries
            so the orchestrator's retry path can run
            `degrade_plan_for_seller` against the precise drops the seller
            asked for. Empty list for any other error.
    """

    def __init__(
        self,
        message: str,
        status_code: int = 0,
        error_code: str = "",
        detail: str = "",
        unsupported: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.detail = detail
        self.unsupported: list[dict[str, Any]] = unsupported or []


class DealsClient:
    """Async client for the IAB Deals API v1.0 (quote-then-book flow).

    Args:
        seller_url: Base URL of the seller system (e.g. ``http://seller.example.com``).
        api_key: Optional API key sent via ``X-Api-Key`` header.
        bearer_token: Optional bearer token sent via ``Authorization`` header.
        timeout: Request timeout in seconds.
        max_retries: Maximum retries for transient failures (502/503/504).
        deal_store: Optional DealStore for persisting quotes and deals.
    """

    def __init__(
        self,
        seller_url: str,
        *,
        api_key: Optional[str] = None,
        bearer_token: Optional[str] = None,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        deal_store: Any = None,
    ) -> None:
        self.seller_url = seller_url.rstrip("/")
        self._api_key = api_key
        self._bearer_token = bearer_token
        self._timeout = timeout
        self._max_retries = max_retries
        self.deal_store = deal_store

        # Build default headers
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if api_key:
            headers["X-Api-Key"] = api_key
        elif bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"

        self._client = httpx.AsyncClient(
            base_url=self.seller_url,
            headers=headers,
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def request_quote(self, quote_request: QuoteRequest) -> QuoteResponse:
        """Request a non-binding price quote from the seller.

        POST /api/v1/quotes

        Args:
            quote_request: Quote request parameters.

        Returns:
            QuoteResponse from the seller.

        Raises:
            DealsClientError: On HTTP or transport errors.
        """
        body = quote_request.model_dump(exclude_none=True)
        response = await self._request_with_retry("POST", "/api/v1/quotes", json=body)
        data = response.json()
        result = QuoteResponse.model_validate(data)

        # Persist to DealStore if available
        self._persist_quote(result, quote_request)

        return result

    async def get_quote(self, quote_id: str) -> QuoteResponse:
        """Retrieve a previously issued quote.

        GET /api/v1/quotes/{quote_id}

        Args:
            quote_id: The quote identifier.

        Returns:
            QuoteResponse reflecting current state.

        Raises:
            DealsClientError: On HTTP or transport errors.
        """
        response = await self._request_with_retry("GET", f"/api/v1/quotes/{quote_id}")
        data = response.json()
        return QuoteResponse.model_validate(data)

    async def book_deal(self, booking_request: DealBookingRequest) -> DealResponse:
        """Book a deal from an existing quote.

        POST /api/v1/deals

        Args:
            booking_request: Deal booking parameters including the quote_id.

        Returns:
            DealResponse with the seller-issued Deal ID.

        Raises:
            DealsClientError: On HTTP or transport errors.
        """
        body = booking_request.model_dump(exclude_none=True)
        response = await self._request_with_retry("POST", "/api/v1/deals", json=body)
        data = response.json()
        result = DealResponse.model_validate(data)

        # Persist to DealStore if available
        self._persist_deal(result)

        return result

    async def get_deal(self, deal_id: str) -> DealResponse:
        """Retrieve the current state of a deal.

        GET /api/v1/deals/{deal_id}

        Args:
            deal_id: The deal identifier.

        Returns:
            DealResponse reflecting current status.

        Raises:
            DealsClientError: On HTTP or transport errors.
        """
        response = await self._request_with_retry("GET", f"/api/v1/deals/{deal_id}")
        data = response.json()
        result = DealResponse.model_validate(data)

        # Update stored status if DealStore is available
        self._update_stored_deal_status(result)

        return result

    # ------------------------------------------------------------------
    # Linear TV post-booking endpoints
    # ------------------------------------------------------------------

    async def request_makegood(
        self, deal_id: str, makegood_request: MakegoodRequest
    ) -> dict[str, Any]:
        """Request makegood inventory for a linear TV deal.

        POST /api/v1/deals/{deal_id}/makegoods

        Sent when audience delivery falls short of the guaranteed
        GRP level. The seller responds with offered replacement
        inventory.

        Args:
            deal_id: The deal identifier.
            makegood_request: Makegood request parameters.

        Returns:
            Dict with makegood offer details from the seller.

        Raises:
            DealsClientError: On HTTP or transport errors.
        """
        body = makegood_request.model_dump(exclude_none=True)
        response = await self._request_with_retry(
            "POST", f"/api/v1/deals/{deal_id}/makegoods", json=body
        )
        return response.json()

    async def request_cancellation(
        self, deal_id: str, cancellation_request: CancellationRequest
    ) -> dict[str, Any]:
        """Request cancellation of a linear TV deal.

        POST /api/v1/deals/{deal_id}/cancel

        Supports full and partial cancellation with notice period
        awareness. The seller validates against the deal's
        cancellation terms before processing.

        Args:
            deal_id: The deal identifier.
            cancellation_request: Cancellation request parameters.

        Returns:
            Dict with cancellation result from the seller.

        Raises:
            DealsClientError: On HTTP or transport errors (including
                422 if cancellation window has expired).
        """
        body = cancellation_request.model_dump(exclude_none=True)
        response = await self._request_with_retry(
            "POST", f"/api/v1/deals/{deal_id}/cancel", json=body
        )
        return response.json()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> "DealsClient":
        """Async context manager entry."""
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Async context manager exit."""
        await self.close()

    # ------------------------------------------------------------------
    # Internal: HTTP with retry
    # ------------------------------------------------------------------

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send an HTTP request with retry logic for transient failures.

        Retries on 502, 503, 504 status codes up to ``_max_retries`` times.
        Client errors (4xx) are NOT retried.

        Args:
            method: HTTP method (GET, POST, etc.).
            path: URL path relative to seller_url.
            **kwargs: Additional arguments passed to httpx (json, params, etc.).

        Returns:
            The successful httpx.Response.

        Raises:
            DealsClientError: On non-retryable errors or when retries are exhausted.
        """
        last_error: Optional[DealsClientError] = None

        for attempt in range(1, self._max_retries + 1):
            try:
                response = await self._client.request(method, path, **kwargs)
            except httpx.TimeoutException as exc:
                last_error = DealsClientError(
                    f"Request timeout after {self._timeout}s: {exc}",
                    status_code=0,
                    error_code="timeout",
                )
                if attempt < self._max_retries:
                    logger.warning(
                        "Timeout on attempt %d/%d for %s %s",
                        attempt, self._max_retries, method, path,
                    )
                    continue
                raise last_error from exc
            except httpx.ConnectError as exc:
                raise DealsClientError(
                    f"Connection error: {exc}",
                    status_code=0,
                    error_code="connect_error",
                ) from exc
            except httpx.HTTPError as exc:
                raise DealsClientError(
                    f"HTTP error: {exc}",
                    status_code=0,
                    error_code="http_error",
                ) from exc

            # Success
            if response.is_success:
                return response

            # Retryable server error
            if response.status_code in _RETRYABLE_STATUS_CODES:
                last_error = self._build_error_from_response(response)
                if attempt < self._max_retries:
                    logger.warning(
                        "Retryable error %d on attempt %d/%d for %s %s",
                        response.status_code, attempt, self._max_retries, method, path,
                    )
                    continue
                raise last_error

            # Non-retryable error (4xx or other 5xx)
            raise self._build_error_from_response(response)

        # Should not reach here, but just in case
        if last_error:
            raise last_error
        raise DealsClientError("Unexpected retry loop exit", status_code=0)

    @staticmethod
    def _build_error_from_response(response: httpx.Response) -> DealsClientError:
        """Extract error details from an HTTP error response.

        Handles two seller error shapes:

        1. Flat: ``{"error": "...", "detail": "..."}`` -- legacy / non-
           HTTPException-wrapped errors. The buyer's pre-existing tests use
           this shape.
        2. FastAPI-wrapped: ``{"detail": {"error": "...", ...}}`` -- emitted
           when the seller raises ``HTTPException(detail=<dict>)``. The
           ``audience_plan_unsupported`` rejection (proposal §5.7 layer 3)
           lives here, with an additional ``unsupported`` list inside the
           wrapped ``detail``.

        Falls back to the raw response text if JSON parsing fails entirely.
        """
        error_code = ""
        detail: str = ""
        unsupported: list[dict[str, Any]] = []
        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError):
            data = None

        if isinstance(data, dict):
            # Try the FastAPI-wrapped shape first: detail is itself a dict.
            inner = data.get("detail")
            if isinstance(inner, dict):
                error_code = str(inner.get("error", "") or "")
                # Surface the inner "message" / "detail" / repr for humans.
                detail = str(
                    inner.get("message")
                    or inner.get("detail")
                    or ""
                )
                raw_unsupported = inner.get("unsupported")
                if isinstance(raw_unsupported, list):
                    unsupported = [
                        u for u in raw_unsupported if isinstance(u, dict)
                    ]
            else:
                # Flat shape: {"error": "...", "detail": "..."}
                error_code = str(data.get("error", "") or "")
                detail = str(data.get("detail", "") or "")

        if not detail and not error_code and response.text:
            detail = response.text[:500]

        message = f"Seller API error {response.status_code}"
        if error_code:
            message += f": {error_code}"
        if detail:
            message += f" - {detail}"

        return DealsClientError(
            message=message,
            status_code=response.status_code,
            error_code=error_code,
            detail=detail,
            unsupported=unsupported,
        )

    # ------------------------------------------------------------------
    # Internal: DealStore persistence
    # ------------------------------------------------------------------

    def _persist_quote(self, quote: QuoteResponse, request: QuoteRequest) -> None:
        """Save a quote to the DealStore as a deal record with status 'quoted'.

        Non-fatal: logs errors but does not re-raise.
        """
        if self.deal_store is None:
            return
        try:
            self.deal_store.save_deal(
                seller_url=self.seller_url,
                product_id=quote.product.product_id,
                product_name=quote.product.name,
                deal_type=request.deal_type,
                status="quoted",
                price=quote.pricing.final_cpm,
                original_price=quote.pricing.base_cpm,
                impressions=quote.terms.impressions,
                flight_start=quote.terms.flight_start,
                flight_end=quote.terms.flight_end,
                metadata=json.dumps({
                    "quote_id": quote.quote_id,
                    "buyer_tier": quote.buyer_tier,
                    "expires_at": quote.expires_at,
                }),
            )
        except (sqlite3.Error, OSError, ValueError, AttributeError):
            logger.exception("Failed to persist quote %s to DealStore", quote.quote_id)

    def _persist_deal(self, deal: DealResponse) -> None:
        """Save a booked deal to the DealStore with status 'booked'.

        Non-fatal: logs errors but does not re-raise.
        """
        if self.deal_store is None:
            return
        try:
            self.deal_store.save_deal(
                seller_url=self.seller_url,
                seller_deal_id=deal.deal_id,
                product_id=deal.product.product_id,
                product_name=deal.product.name,
                deal_type=deal.deal_type,
                status="booked",
                price=deal.pricing.final_cpm,
                original_price=deal.pricing.base_cpm,
                impressions=deal.terms.impressions,
                flight_start=deal.terms.flight_start,
                flight_end=deal.terms.flight_end,
                metadata=json.dumps({
                    "quote_id": deal.quote_id,
                    "buyer_tier": deal.buyer_tier,
                    "expires_at": deal.expires_at,
                    "activation_instructions": deal.activation_instructions,
                    "openrtb_params": (
                        deal.openrtb_params.model_dump() if deal.openrtb_params else None
                    ),
                }),
            )
        except (sqlite3.Error, OSError, ValueError, AttributeError):
            logger.exception("Failed to persist deal %s to DealStore", deal.deal_id)

    def _update_stored_deal_status(self, deal: DealResponse) -> None:
        """Update the status of a stored deal after a GET /deals/{id} call.

        Non-fatal: logs errors but does not re-raise.
        """
        if self.deal_store is None:
            return
        try:
            # Find by seller_deal_id and update status
            existing_deals = self.deal_store.list_deals(seller_url=self.seller_url)
            for stored in existing_deals:
                if stored.get("seller_deal_id") == deal.deal_id:
                    self.deal_store.update_deal_status(
                        stored["id"],
                        deal.status,
                        triggered_by="deals_client",
                        notes=f"Updated from GET /api/v1/deals/{deal.deal_id}",
                    )
                    break
        except (sqlite3.Error, OSError, ValueError, AttributeError):
            logger.exception(
                "Failed to update stored deal status for %s", deal.deal_id
            )
