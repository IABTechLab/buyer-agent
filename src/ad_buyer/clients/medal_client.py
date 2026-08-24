# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Adapter client for medal-sales-agent.

Medal doesn't implement the newer /media-kit discovery protocol or
OpenDirect classic -- confirmed by probing its hosted instance directly
(unknown paths and /media-kit both 401 identically via its catch-all auth
gate, while real-but-wrong-method paths get a genuine 404). It implements
its own catalog endpoint (/products) plus quote/book endpoints at the same
paths as the IAB Deals API (/api/v1/quotes, /api/v1/deals), but with a
different wire format: flat response bodies (cpm, total_cost, floor_price)
instead of DealsClient's nested product{}/pricing{}/terms{} shape, and
start_date/end_date instead of flight_start/flight_end on the request when
the modern field names are rejected.

This client normalizes all of that so it can serve as both:
  - a catalog client (list_products), for MultiSellerOrchestrator's
    per-seller product resolution
  - a deals client (request_quote/book_deal), for quoting and booking

exposing the same shapes DealsClient and OpenDirectClient's catalog method
use, so it's a drop-in for both orchestrator factories.
"""

import logging
from typing import Any

import httpx

from ..models.deals import (
    DealBookingRequest,
    DealResponse,
    PricingInfo,
    ProductInfo,
    QuoteRequest,
    QuoteResponse,
    TermsInfo,
)
from ..models.opendirect import DeliveryType, Product, RateType
from .deals_client import DealsClientError

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0


class MedalDealsClient:
    """Deals-API-shaped adapter for medal-sales-agent's actual wire format."""

    # Keyed by quote_id, shared across instances. Orchestrator callers
    # construct a fresh client per stage (quoting vs. booking), so an
    # instance-level cache would never see its own quote by booking time.
    # Medal's book response tends to omit fields that were only present on
    # the quote (product name, flight terms), so this fills out a complete
    # DealResponse at booking time.
    _quote_cache: dict[str, dict[str, Any]] = {}

    def __init__(self, seller_url: str, *, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self.seller_url = seller_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.seller_url, timeout=timeout)

    @staticmethod
    def matches(seller_url: str) -> bool:
        """True when this seller URL should be handled by the medal adapter."""
        from urllib.parse import urlparse

        return "medal" in urlparse(seller_url).netloc.lower()

    async def list_products(self, top: int = 50, **_filters: Any) -> list[Product]:
        """Fetch medal's catalog, mapped to the shared `Product` model.

        Tries `/products` first (the currently observed hosted path),
        falling back to `/api/v1/products` for builds that only serve the
        versioned path. Same signature shape as `OpenDirectClient.list_products`
        so it's a drop-in `catalog_client_factory` target.
        """
        try:
            response = await self._client.get("/products", params={"limit": top})
            if response.status_code == 404:
                response = await self._client.get("/api/v1/products", params={"limit": top})
        except httpx.HTTPError as exc:
            raise DealsClientError(
                f"Medal product listing failed: {exc}", error_code="transport_error"
            ) from exc

        data = self._parse(response)
        raw_products = data.get("products", data) if isinstance(data, dict) else data
        if not isinstance(raw_products, list):
            return []

        products: list[Product] = []
        for item in raw_products:
            product_id = item.get("product_id") or item.get("id")
            if not product_id:
                continue
            # Live catalog fields (confirmed against the hosted instance):
            # floor_cpm for price, a single `channel` string (e.g. "display")
            # rather than a ad_formats list -- wrapped as one so it flows
            # through the same channel-match normalization as OpenDirect
            # sellers' declared ad_formats.
            price = item.get("floor_cpm", item.get("cpm", item.get("base_price", 0.0)))
            ad_formats = [item["channel"]] if item.get("channel") else item.get("ad_formats")
            products.append(
                Product(
                    id=product_id,
                    publisherid="medal-sales-agent",
                    name=item.get("name") or item.get("product_name") or product_id,
                    base_price=price if isinstance(price, int | float) else 0.0,
                    ratetype=RateType.CPM,
                    deliverytype=DeliveryType.GUARANTEED,
                    ext={"ad_formats": ad_formats} if ad_formats else None,
                )
            )
        return products

    async def request_quote(self, quote_request: QuoteRequest) -> QuoteResponse:
        """POST /api/v1/quotes, normalized to the shared QuoteResponse shape."""
        body = {
            "product_id": quote_request.product_id,
            "deal_type": quote_request.deal_type,
            "impressions": quote_request.impressions,
            "flight_start": quote_request.flight_start,
            "flight_end": quote_request.flight_end,
        }
        response = await self._post("/api/v1/quotes", body)
        if response.status_code == 400:
            body = {
                "product_id": quote_request.product_id,
                "deal_type": quote_request.deal_type,
                "impressions": quote_request.impressions,
                "start_date": quote_request.flight_start,
                "end_date": quote_request.flight_end,
            }
            response = await self._post("/api/v1/quotes", body)

        data = self._parse(response)
        quote_id = data.get("quote_id") or data.get("id")
        if not quote_id:
            raise DealsClientError("Medal quote response missing quote_id", detail=str(data))

        self._quote_cache[quote_id] = {
            **data,
            "product_id": data.get("product_id", quote_request.product_id),
            "impressions": quote_request.impressions,
            "flight_start": quote_request.flight_start,
            "flight_end": quote_request.flight_end,
        }

        final_cpm = data.get("cpm", data.get("final_cpm", 0.0))
        return QuoteResponse(
            quote_id=quote_id,
            status=data.get("status", "available"),
            product=ProductInfo(
                product_id=data.get("product_id", quote_request.product_id),
                name=data.get("product_name", quote_request.product_id),
            ),
            pricing=PricingInfo(
                base_cpm=data.get("floor_price", final_cpm),
                final_cpm=final_cpm,
            ),
            terms=TermsInfo(
                impressions=quote_request.impressions,
                flight_start=quote_request.flight_start,
                flight_end=quote_request.flight_end,
            ),
            expires_at=data.get("expires_at"),
        )

    async def book_deal(self, booking_request: DealBookingRequest) -> DealResponse:
        """POST /api/v1/deals, normalized to the shared DealResponse shape."""
        response = await self._post("/api/v1/deals", {"quote_id": booking_request.quote_id})
        data = self._parse(response)

        deal_id = data.get("deal_id") or data.get("ssp_deal_id") or data.get("id")
        if not deal_id:
            raise DealsClientError("Medal deal response missing deal_id", detail=str(data))

        cached = self._quote_cache.get(booking_request.quote_id, {})
        final_cpm = data.get("cpm", cached.get("cpm", 0.0))
        return DealResponse(
            deal_id=deal_id,
            deal_type=data.get("deal_type", "PD"),
            status=data.get("status", "proposed"),
            quote_id=booking_request.quote_id,
            product=ProductInfo(
                product_id=data.get("product_id", cached.get("product_id", "unknown")),
                name=data.get("product_name", cached.get("product_name", "unknown")),
            ),
            pricing=PricingInfo(
                base_cpm=data.get("floor_price", cached.get("floor_price", final_cpm)),
                final_cpm=final_cpm,
            ),
            terms=TermsInfo(
                impressions=cached.get("impressions"),
                flight_start=cached.get("flight_start"),
                flight_end=cached.get("flight_end"),
            ),
        )

    async def _post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        try:
            return await self._client.post(path, json=body)
        except httpx.HTTPError as exc:
            raise DealsClientError(
                f"Medal request failed: {exc}", error_code="transport_error"
            ) from exc

    @staticmethod
    def _parse(response: httpx.Response) -> dict[str, Any]:
        if not response.is_success:
            raise DealsClientError(
                f"Medal returned {response.status_code}: {response.text}",
                status_code=response.status_code,
            )
        return response.json()
