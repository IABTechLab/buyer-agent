# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Multi-seller deal orchestration for Campaign Automation.

Coordinates the multi-seller flow described in the Campaign Automation
Strategic Plan, Section 7.2:

  1. Discover sellers via agent registry
  2. Request quotes from qualifying sellers in parallel
  3. Normalize and rank quotes using QuoteNormalizer
  4. Select optimal deals within budget constraints
  5. Book selected deals through the deals API

This module is the core of Campaign Automation's "shop the market"
capability.  It enables the buyer agent to simultaneously contact
multiple sellers, compare pricing on an apples-to-apples basis, and
book the best deals for a campaign channel.

Integration points:
  - RegistryClient (buyer-f8l): seller discovery
  - DealsClient (buyer-hu7): quote requests and deal booking
  - QuoteNormalizer (buyer-lae): cross-seller quote comparison
  - EventBus (buyer-ppi): event emission at each stage

Reference: Campaign Automation Strategic Plan, Section 7.2
Bead: buyer-8ih (2A: Multi-Seller Deal Orchestration)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..booking.quote_normalizer import NormalizedQuote, QuoteNormalizer
from ..events.models import Event, EventType
from ..models.deals import (
    DealBookingRequest,
    DealResponse,
    QuoteRequest,
    QuoteResponse,
)
from ..registry.models import AgentCard, TrustLevel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class InventoryRequirements:
    """Describes what inventory the campaign needs.

    Used by discover_sellers to find qualifying sellers in the
    agent registry.

    Attributes:
        media_type: Type of media inventory needed (ctv, display, audio).
        deal_types: Acceptable deal types (PG, PD, PA).
        content_categories: Optional IAB content category codes.
        excluded_sellers: Seller IDs to exclude from discovery.
        min_impressions: Minimum impression volume needed.
        max_cpm: Maximum acceptable CPM for filtering quotes.
    """

    media_type: str
    deal_types: list[str]
    content_categories: list[str] = field(default_factory=list)
    excluded_sellers: list[str] = field(default_factory=list)
    min_impressions: Optional[int] = None
    max_cpm: Optional[float] = None


@dataclass
class DealParams:
    """Parameters for requesting quotes from sellers.

    Maps to the QuoteRequest model used by the deals API client.

    Attributes:
        product_id: Seller product to request a quote for.
        deal_type: Desired deal type (PG, PD, PA).
        impressions: Desired impression volume.
        flight_start: Campaign start date (ISO string).
        flight_end: Campaign end date (ISO string).
        target_cpm: Optional target CPM to include in the request.
        media_type: Media type (digital, ctv, linear_tv).
    """

    product_id: str
    deal_type: str
    impressions: int
    flight_start: str
    flight_end: str
    target_cpm: Optional[float] = None
    media_type: str = "digital"


@dataclass
class SellerQuoteResult:
    """Result of requesting a quote from a single seller.

    Captures either a successful QuoteResponse or an error string
    for sellers that failed to respond.

    Attributes:
        seller_id: The seller's agent ID.
        seller_url: The seller's base URL.
        quote: The QuoteResponse if successful, None on failure.
        deal_type: The deal type that was requested.
        error: Error message if the request failed, None on success.
    """

    seller_id: str
    seller_url: str
    quote: Optional[QuoteResponse]
    deal_type: str
    error: Optional[str]


@dataclass
class DealSelection:
    """Result of the deal selection and booking phase.

    Attributes:
        booked_deals: List of successfully booked DealResponses.
        failed_bookings: List of dicts with quote_id and error details.
        total_spend: Total estimated spend across booked deals.
        remaining_budget: Budget remaining after booking.
    """

    booked_deals: list[DealResponse]
    failed_bookings: list[dict[str, Any]]
    total_spend: float
    remaining_budget: float


@dataclass
class OrchestrationResult:
    """Complete result from an end-to-end orchestration run.

    Captures data from every stage of the orchestration flow so
    the caller can inspect what happened.

    Attributes:
        discovered_sellers: Sellers found via registry discovery.
        quote_results: Raw results from parallel quote requests.
        ranked_quotes: Quotes after normalization and ranking.
        selection: The deal selection and booking result.
    """

    discovered_sellers: list[AgentCard]
    quote_results: list[SellerQuoteResult]
    ranked_quotes: list[NormalizedQuote]
    selection: DealSelection


# ---------------------------------------------------------------------------
# MultiSellerOrchestrator
# ---------------------------------------------------------------------------


class MultiSellerOrchestrator:
    """Coordinates multi-seller deal discovery, quoting, and booking.

    This is the core orchestration engine for Campaign Automation's
    multi-seller flow.  It connects the registry client (for seller
    discovery), deals client (for quoting and booking), quote
    normalizer (for cross-seller comparison), and event bus (for
    observability).

    Usage::

        orchestrator = MultiSellerOrchestrator(
            registry_client=registry,
            deals_client_factory=lambda url, **kw: DealsClient(url, **kw),
            event_bus=bus,
        )

        result = await orchestrator.orchestrate(
            inventory_requirements=InventoryRequirements(
                media_type="ctv",
                deal_types=["PD", "PG"],
            ),
            deal_params=DealParams(
                product_id="prod-ctv-001",
                deal_type="PD",
                impressions=500_000,
                flight_start="2026-04-01",
                flight_end="2026-04-30",
            ),
            budget=100_000.0,
            max_deals=3,
        )

    Args:
        registry_client: RegistryClient instance for seller discovery.
        deals_client_factory: Callable that creates a DealsClient for a
            given seller URL.  Signature: ``(seller_url, **kwargs) -> DealsClient``.
        event_bus: Optional EventBus for emitting events.  When None,
            events are silently skipped.
        quote_normalizer: Optional QuoteNormalizer for comparing quotes.
            When None, a default normalizer with no supply-path data is used.
        quote_timeout: Timeout in seconds for individual quote requests.
            Defaults to 30.0 seconds.
    """

    def __init__(
        self,
        registry_client: Any,
        deals_client_factory: Callable[..., Any],
        event_bus: Optional[Any] = None,
        quote_normalizer: Optional[QuoteNormalizer] = None,
        quote_timeout: float = 30.0,
    ) -> None:
        self._registry = registry_client
        self._deals_client_factory = deals_client_factory
        self._event_bus = event_bus
        self._normalizer = quote_normalizer or QuoteNormalizer()
        self._quote_timeout = quote_timeout

    # ------------------------------------------------------------------
    # Event helpers
    # ------------------------------------------------------------------

    async def _emit(
        self,
        event_type: EventType,
        payload: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        """Emit an event to the event bus.  Fail-open."""
        if self._event_bus is None:
            return
        try:
            event = Event(
                event_type=event_type,
                payload=payload or {},
                metadata=kwargs,
            )
            await self._event_bus.publish(event)
        except Exception as exc:
            logger.warning(
                "Failed to emit event %s: %s", event_type, exc
            )

    # ------------------------------------------------------------------
    # Stage 1: Discover sellers
    # ------------------------------------------------------------------

    async def discover_sellers(
        self, requirements: InventoryRequirements
    ) -> list[AgentCard]:
        """Discover qualifying sellers from the agent registry.

        Queries the registry for sellers matching the media type and
        capabilities filter, then applies exclusion rules:
        - Removes sellers in the excluded_sellers list
        - Removes sellers with BLOCKED trust level

        Args:
            requirements: Inventory requirements for filtering sellers.

        Returns:
            List of AgentCards for qualifying sellers.
        """
        # Build capabilities filter from media type
        capabilities_filter = [requirements.media_type]

        sellers = await self._registry.discover_sellers(
            capabilities_filter=capabilities_filter,
        )

        # Filter out excluded sellers
        excluded_set = set(requirements.excluded_sellers)
        sellers = [
            s for s in sellers
            if s.agent_id not in excluded_set
        ]

        # Filter out blocked sellers
        sellers = [
            s for s in sellers
            if s.trust_level != TrustLevel.BLOCKED
        ]

        # Emit discovery event
        await self._emit(
            EventType.INVENTORY_DISCOVERED,
            payload={
                "media_type": requirements.media_type,
                "sellers_found": len(sellers),
                "seller_ids": [s.agent_id for s in sellers],
            },
        )

        logger.info(
            "Discovered %d sellers for media_type=%s",
            len(sellers),
            requirements.media_type,
        )
        return sellers

    # ------------------------------------------------------------------
    # Stage 2: Request quotes in parallel
    # ------------------------------------------------------------------

    async def request_quotes_parallel(
        self,
        sellers: list[AgentCard],
        deal_params: DealParams,
    ) -> list[SellerQuoteResult]:
        """Send quote requests to multiple sellers concurrently.

        Creates a DealsClient for each seller via the factory, sends
        a QuoteRequest, and collects results.  Sellers that fail or
        time out are recorded as errors rather than crashing the flow.

        Args:
            sellers: List of seller AgentCards to request quotes from.
            deal_params: Parameters for the quote request.

        Returns:
            List of SellerQuoteResult (one per seller, success or failure).
        """
        if not sellers:
            return []

        async def _request_one(seller: AgentCard) -> SellerQuoteResult:
            """Request a single quote from one seller."""
            seller_url = seller.url
            try:
                client = self._deals_client_factory(seller_url)

                quote_request = QuoteRequest(
                    product_id=deal_params.product_id,
                    deal_type=deal_params.deal_type,
                    impressions=deal_params.impressions,
                    flight_start=deal_params.flight_start,
                    flight_end=deal_params.flight_end,
                    target_cpm=deal_params.target_cpm,
                    media_type=deal_params.media_type,
                )

                # Apply timeout
                quote = await asyncio.wait_for(
                    client.request_quote(quote_request),
                    timeout=self._quote_timeout,
                )

                logger.info(
                    "Received quote %s from seller %s (CPM: %.2f)",
                    quote.quote_id,
                    seller.agent_id,
                    quote.pricing.final_cpm,
                )

                return SellerQuoteResult(
                    seller_id=seller.agent_id,
                    seller_url=seller_url,
                    quote=quote,
                    deal_type=deal_params.deal_type,
                    error=None,
                )

            except asyncio.TimeoutError:
                msg = f"Quote request timed out after {self._quote_timeout}s"
                logger.warning(
                    "Seller %s timed out on quote request", seller.agent_id
                )
                return SellerQuoteResult(
                    seller_id=seller.agent_id,
                    seller_url=seller_url,
                    quote=None,
                    deal_type=deal_params.deal_type,
                    error=msg,
                )
            except Exception as exc:
                msg = f"Quote request failed: {exc}"
                logger.warning(
                    "Seller %s quote request failed: %s",
                    seller.agent_id,
                    exc,
                )
                return SellerQuoteResult(
                    seller_id=seller.agent_id,
                    seller_url=seller_url,
                    quote=None,
                    deal_type=deal_params.deal_type,
                    error=msg,
                )

        # Fire all quote requests concurrently
        results = await asyncio.gather(
            *[_request_one(seller) for seller in sellers],
            return_exceptions=False,
        )

        # Emit events for collected quotes
        successful = [r for r in results if r.quote is not None]
        failed = [r for r in results if r.error is not None]

        await self._emit(
            EventType.QUOTE_RECEIVED,
            payload={
                "quotes_received": len(successful),
                "quotes_failed": len(failed),
                "seller_ids_success": [r.seller_id for r in successful],
                "seller_ids_failed": [r.seller_id for r in failed],
            },
        )

        logger.info(
            "Parallel quote collection complete: %d received, %d failed",
            len(successful),
            len(failed),
        )
        return list(results)

    # ------------------------------------------------------------------
    # Stage 3: Evaluate and rank quotes
    # ------------------------------------------------------------------

    async def evaluate_and_rank(
        self,
        quote_results: list[SellerQuoteResult],
        max_cpm: Optional[float] = None,
    ) -> list[NormalizedQuote]:
        """Normalize and rank collected quotes.

        Filters out failed quotes, applies the QuoteNormalizer for
        cross-seller comparison, and optionally filters by max CPM.

        Args:
            quote_results: Raw results from request_quotes_parallel.
            max_cpm: Optional maximum effective CPM to filter by.

        Returns:
            List of NormalizedQuote sorted by score descending
            (best quote first).
        """
        # Filter to successful quotes only
        valid_results = [
            r for r in quote_results if r.quote is not None
        ]

        if not valid_results:
            return []

        # Build (QuoteResponse, deal_type) tuples for the normalizer
        quote_tuples: list[tuple[QuoteResponse, str]] = [
            (r.quote, r.deal_type) for r in valid_results
        ]

        # Normalize and rank
        ranked = self._normalizer.compare_quotes(quote_tuples)

        # Apply max CPM filter
        if max_cpm is not None:
            ranked = [
                nq for nq in ranked
                if nq.effective_cpm <= max_cpm
            ]

        logger.info(
            "Evaluated %d quotes, %d passed filters",
            len(valid_results),
            len(ranked),
        )
        return ranked

    # ------------------------------------------------------------------
    # Stage 4: Select and book deals
    # ------------------------------------------------------------------

    async def select_and_book(
        self,
        ranked_quotes: list[NormalizedQuote],
        budget: float,
        count: int,
        quote_seller_map: dict[str, str],
    ) -> DealSelection:
        """Select and book optimal deals from ranked quotes.

        Iterates through ranked quotes (best first), skipping any whose
        minimum_spend exceeds the remaining budget, and books up to
        ``count`` deals.

        Args:
            ranked_quotes: Quotes sorted by score (best first), from
                evaluate_and_rank.
            budget: Total budget available for booking.
            count: Maximum number of deals to book.
            quote_seller_map: Mapping of quote_id to seller URL, needed
                to create the correct DealsClient for booking.

        Returns:
            DealSelection with booked deals, failures, and budget info.
        """
        booked_deals: list[DealResponse] = []
        failed_bookings: list[dict[str, Any]] = []
        remaining_budget = budget
        total_spend = 0.0

        for nq in ranked_quotes:
            if len(booked_deals) >= count:
                break

            # Skip if minimum spend exceeds remaining budget
            if nq.minimum_spend > 0 and nq.minimum_spend > remaining_budget:
                logger.info(
                    "Skipping quote %s: minimum spend %.2f exceeds "
                    "remaining budget %.2f",
                    nq.quote_id,
                    nq.minimum_spend,
                    remaining_budget,
                )
                continue

            seller_url = quote_seller_map.get(nq.quote_id)
            if seller_url is None:
                logger.warning(
                    "No seller URL for quote %s, skipping", nq.quote_id
                )
                failed_bookings.append({
                    "quote_id": nq.quote_id,
                    "error": "No seller URL mapping found",
                })
                continue

            try:
                client = self._deals_client_factory(seller_url)
                booking_request = DealBookingRequest(quote_id=nq.quote_id)

                deal = await client.book_deal(booking_request)
                booked_deals.append(deal)

                # Track spend
                deal_spend = nq.minimum_spend if nq.minimum_spend > 0 else 0.0
                total_spend += deal_spend
                remaining_budget -= deal_spend

                # Emit deal.booked event
                await self._emit(
                    EventType.DEAL_BOOKED,
                    payload={
                        "deal_id": deal.deal_id,
                        "quote_id": nq.quote_id,
                        "seller_id": nq.seller_id,
                        "deal_type": deal.deal_type,
                        "final_cpm": deal.pricing.final_cpm,
                    },
                )

                logger.info(
                    "Booked deal %s from seller %s (CPM: %.2f)",
                    deal.deal_id,
                    nq.seller_id,
                    deal.pricing.final_cpm,
                )

            except Exception as exc:
                logger.warning(
                    "Failed to book deal from quote %s: %s",
                    nq.quote_id,
                    exc,
                )
                failed_bookings.append({
                    "quote_id": nq.quote_id,
                    "error": str(exc),
                })

        return DealSelection(
            booked_deals=booked_deals,
            failed_bookings=failed_bookings,
            total_spend=total_spend,
            remaining_budget=remaining_budget,
        )

    # ------------------------------------------------------------------
    # End-to-end orchestration
    # ------------------------------------------------------------------

    async def orchestrate(
        self,
        inventory_requirements: InventoryRequirements,
        deal_params: DealParams,
        budget: float,
        max_deals: int = 3,
    ) -> OrchestrationResult:
        """Run the complete multi-seller orchestration flow.

        Executes all stages in sequence:
        1. Discover sellers matching inventory requirements
        2. Request quotes from all discovered sellers in parallel
        3. Normalize, rank, and filter quotes
        4. Select and book the top deals within budget

        Args:
            inventory_requirements: What inventory the campaign needs.
            deal_params: Parameters for the quote requests.
            budget: Total budget available for this channel.
            max_deals: Maximum number of deals to book.

        Returns:
            OrchestrationResult capturing data from every stage.
        """
        # Stage 1: Discover
        sellers = await self.discover_sellers(inventory_requirements)

        if not sellers:
            logger.info("No sellers discovered, returning empty result")
            return OrchestrationResult(
                discovered_sellers=[],
                quote_results=[],
                ranked_quotes=[],
                selection=DealSelection(
                    booked_deals=[],
                    failed_bookings=[],
                    total_spend=0.0,
                    remaining_budget=budget,
                ),
            )

        # Stage 2: Quote
        quote_results = await self.request_quotes_parallel(sellers, deal_params)

        # Stage 3: Evaluate
        ranked = await self.evaluate_and_rank(
            quote_results,
            max_cpm=inventory_requirements.max_cpm,
        )

        if not ranked:
            logger.info("No viable quotes after evaluation")
            return OrchestrationResult(
                discovered_sellers=sellers,
                quote_results=quote_results,
                ranked_quotes=[],
                selection=DealSelection(
                    booked_deals=[],
                    failed_bookings=[],
                    total_spend=0.0,
                    remaining_budget=budget,
                ),
            )

        # Build quote -> seller URL map from quote results
        quote_seller_map: dict[str, str] = {}
        for qr in quote_results:
            if qr.quote is not None:
                quote_seller_map[qr.quote.quote_id] = qr.seller_url

        # Stage 4: Select and book
        selection = await self.select_and_book(
            ranked_quotes=ranked,
            budget=budget,
            count=max_deals,
            quote_seller_map=quote_seller_map,
        )

        # Emit campaign booking completed event
        await self._emit(
            EventType.CAMPAIGN_BOOKING_COMPLETED,
            payload={
                "deals_booked": len(selection.booked_deals),
                "deals_failed": len(selection.failed_bookings),
                "total_spend": selection.total_spend,
                "remaining_budget": selection.remaining_budget,
            },
        )

        return OrchestrationResult(
            discovered_sellers=sellers,
            quote_results=quote_results,
            ranked_quotes=ranked,
            selection=selection,
        )
