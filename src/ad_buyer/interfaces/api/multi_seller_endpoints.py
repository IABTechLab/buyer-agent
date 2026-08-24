# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Multi-seller booking endpoint.

Wires `MultiSellerOrchestrator` up to explicit seller URLs (no agent
registry involved) instead of a caller-supplied product_id: the caller
names sellers, this endpoint finds a matching product on one of them to
seed the comparison, then leans on the orchestrator's own cross-seller
product resolution (`catalog_client_factory`) to find the equivalent
product on every other named seller before quoting all of them.

Two seller "kinds" are supported today, each resolved through the same
single check (`MedalDealsClient.matches`) rather than scattered
conditionals: sellers implementing OpenDirect classic (catalog) + the IAB
Deals API (quote/book) use `OpenDirectClient`/`DealsClient` directly;
medal-sales-agent, which speaks neither, uses `MedalDealsClient` for both
roles. Adding a third seller kind means adding one adapter with its own
`matches()` check, not editing this endpoint's control flow.
"""

import logging
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...clients.deals_client import DealsClient
from ...clients.medal_client import MedalDealsClient
from ...clients.opendirect_client import OpenDirectClient, _normalize_ad_format
from ...models.deals import DealResponse
from ...models.opendirect import Product
from ...orchestration.multi_seller import DealParams, MultiSellerOrchestrator
from ...registry.models import AgentCard, TrustLevel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bookings", tags=["Bookings"])


class MultiSellerBookingRequest(BaseModel):
    """Request to quote (and optionally book) across named sellers.

    No product_id is provided -- only the sellers to compare and the kind
    of inventory wanted. A concrete product is discovered automatically.
    """

    seller_urls: list[str] = Field(..., min_length=1)
    channel: str | None = Field(
        default=None,
        description="Ad format/channel to match, e.g. 'display', 'ctv'.",
    )
    deal_type: str = "PD"
    impressions: int = Field(..., gt=0)
    flight_start: str
    flight_end: str
    target_cpm: float | None = None
    media_type: str = "digital"
    budget: float = Field(..., gt=0)
    max_deals: int = Field(default=1, ge=1)
    auto_book: bool = Field(
        default=False,
        description="Book the top-ranked quote(s) after ranking.",
    )


class SellerQuoteView(BaseModel):
    """One seller's outcome in a multi-seller quote request."""

    seller_url: str
    quote_id: str | None = None
    resolved_product_id: str | None = None
    final_cpm: float | None = None
    error: str | None = None


class RankedQuoteView(BaseModel):
    """A normalized quote's position in the cross-seller ranking."""

    seller_url: str
    quote_id: str
    effective_cpm: float | None
    score: float


class MultiSellerBookingResponse(BaseModel):
    """Result of quoting (and optionally booking) across multiple sellers."""

    seed_seller_url: str
    seed_product_id: str
    seed_product_name: str
    quotes: list[SellerQuoteView]
    ranked: list[RankedQuoteView]
    booked_deals: list[DealResponse] = Field(default_factory=list)
    failed_bookings: list[dict[str, Any]] = Field(default_factory=list)
    total_spend: float = 0.0
    remaining_budget: float = 0.0


def _catalog_client_for(seller_url: str) -> Any:
    """Resolve the catalog client for a seller URL."""
    if MedalDealsClient.matches(seller_url):
        return MedalDealsClient(seller_url)
    return OpenDirectClient(base_url=seller_url)


def _deals_client_for(seller_url: str) -> Any:
    """Resolve the quote/book client for a seller URL."""
    if MedalDealsClient.matches(seller_url):
        return MedalDealsClient(seller_url)
    return DealsClient(seller_url)


def _agent_card_for(seller_url: str) -> AgentCard:
    """Build an AgentCard for a seller URL without going through the registry."""
    label = urlparse(seller_url).netloc or seller_url
    return AgentCard(agent_id=label, name=label, url=seller_url, trust_level=TrustLevel.REGISTERED)


def _matches_channel(product: Product, channel: str | None) -> bool:
    """True when a product's declared ad_formats match the requested channel.

    Undeclared ad_formats stay eligible (mirrors the orchestrator's own
    channel-match tier), so catalogs that don't tag format aren't excluded
    outright.
    """
    if channel is None:
        return True
    formats = (product.ext or {}).get("ad_formats") or []
    if not formats:
        return True
    wanted = _normalize_ad_format(channel)
    return wanted in {_normalize_ad_format(f) for f in formats}


async def _find_seed_product(
    seller_urls: list[str], channel: str | None
) -> tuple[str, Product] | None:
    """Find a concrete product to seed the comparison, trying each seller in order.

    Returns (seller_url, product) for the first seller whose catalog has a
    channel-matching product, cheapest first. None if no seller has one.
    """
    for seller_url in seller_urls:
        client = _catalog_client_for(seller_url)
        try:
            products = await client.list_products(top=200)
        except Exception as exc:  # noqa: BLE001 - per-seller isolation; try the next seller
            logger.warning("Catalog fetch failed for %s: %s", seller_url, exc)
            continue

        candidates = [p for p in products if _matches_channel(p, channel) and p.id]
        if not candidates:
            continue

        candidates.sort(key=lambda p: (p.base_price <= 0, p.base_price, p.id or ""))
        return seller_url, candidates[0]

    return None


@router.post("/multi-seller", response_model=MultiSellerBookingResponse)
async def create_multi_seller_booking(
    request: MultiSellerBookingRequest,
) -> MultiSellerBookingResponse:
    """Quote the same inventory across multiple sellers, by URL only.

    Discovers a concrete product on one of the given sellers, then uses
    `MultiSellerOrchestrator`'s cross-seller product resolution to find the
    equivalent product on every other seller before quoting all of them
    concurrently, ranking by effective CPM, and optionally booking the
    winner(s).
    """
    seed = await _find_seed_product(request.seller_urls, request.channel)
    if seed is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"No product matching channel={request.channel!r} found on any of "
                f"the given sellers: {request.seller_urls}"
            ),
        )
    seed_url, seed_product = seed

    sellers = [_agent_card_for(url) for url in request.seller_urls]
    deal_params = DealParams(
        product_id=seed_product.id,
        product_name=seed_product.name,
        channel=request.channel,
        deal_type=request.deal_type,
        impressions=request.impressions,
        flight_start=request.flight_start,
        flight_end=request.flight_end,
        target_cpm=request.target_cpm,
        media_type=request.media_type,
    )

    orchestrator = MultiSellerOrchestrator(
        registry_client=None,
        deals_client_factory=lambda url, **_kw: _deals_client_for(url),
        catalog_client_factory=_catalog_client_for,
    )

    quote_results = await orchestrator.request_quotes_parallel(sellers, deal_params)
    ranked = await orchestrator.evaluate_and_rank(quote_results)
    quote_seller_map = {r.quote.quote_id: r.seller_url for r in quote_results if r.quote}

    selection = None
    if request.auto_book and ranked:
        selection = await orchestrator.select_and_book(
            ranked_quotes=ranked,
            budget=request.budget,
            count=request.max_deals,
            quote_seller_map=quote_seller_map,
        )

    return MultiSellerBookingResponse(
        seed_seller_url=seed_url,
        seed_product_id=seed_product.id,
        seed_product_name=seed_product.name,
        quotes=[
            SellerQuoteView(
                seller_url=r.seller_url,
                quote_id=r.quote.quote_id if r.quote else None,
                resolved_product_id=r.quote.product.product_id if r.quote else None,
                final_cpm=r.quote.pricing.final_cpm if r.quote else None,
                error=r.error,
            )
            for r in quote_results
        ],
        ranked=[
            RankedQuoteView(
                seller_url=quote_seller_map.get(nq.quote_id, "unknown"),
                quote_id=nq.quote_id,
                effective_cpm=nq.effective_cpm,
                score=nq.score,
            )
            for nq in ranked
        ],
        booked_deals=selection.booked_deals if selection else [],
        failed_bookings=selection.failed_bookings if selection else [],
        total_spend=selection.total_spend if selection else 0.0,
        remaining_budget=selection.remaining_budget if selection else request.budget,
    )
