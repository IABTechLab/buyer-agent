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
from ..clients.capability_client import (
    CapabilityClient,
    CapabilityDiscoveryResult,
)
from ..clients.deals_client import DealsClientError
from ..events.models import Event, EventType
from ..models.audience_plan import AudiencePlan, AudienceStrictness
from ..models.deals import (
    DealBookingRequest,
    DealResponse,
    QuoteRequest,
    QuoteResponse,
)
from ..registry.models import AgentCard, TrustLevel
from ..storage import audience_audit_log
from .audience_degradation import (
    CannotFulfillPlan,
    DegradationLog,
    DegradationLogEntry,
    SellerAudienceCapabilities,
    degrade_plan_for_seller,
    synthesize_capabilities_from_unsupported,
)

logger = logging.getLogger(__name__)


# Error code emitted by the seller per proposal §5.7 layer 3 when the
# AudiencePlan carries parts the seller can't honor. Used by the retry-on-
# rejection path in `select_and_book` to detect the structured rejection.
_AUDIENCE_PLAN_UNSUPPORTED_CODE = "audience_plan_unsupported"


class _SellerIncompatibleForCampaign(Exception):
    """Internal signal: seller cannot fulfill the campaign's audience plan.

    Raised inside `_book_with_audience_retry` after the degrade-and-retry
    path has been exhausted. Caught by `select_and_book`, which records
    the seller in `DealSelection.incompatible_sellers`. NOT a public type
    -- the higher-level orchestrator surfaces incompatibility via the
    selection result, not by exception type.
    """


def _is_audience_plan_unsupported(exc: DealsClientError) -> bool:
    """True when the seller error is the structured audience-plan rejection.

    The check is forgiving: we accept a 400 with `error_code` matching the
    spec's code, OR a 400 whose payload contained an `unsupported` list (in
    case a seller variant emits a different top-level code but still carries
    the structured list). Either way, we have a list of `{path, reason}`
    entries to drive `degrade_plan_for_seller`.
    """

    if exc.status_code != 400:
        return False
    if exc.error_code == _AUDIENCE_PLAN_UNSUPPORTED_CODE:
        return True
    return bool(exc.unsupported)


# ---------------------------------------------------------------------------
# Pre-flight strictness gating helpers (proposal §5.7 layer 2)
# ---------------------------------------------------------------------------

# Map the path prefix in a `DegradationLogEntry` to the role whose strictness
# governs whether the orchestrator skips the seller. Anything that isn't a
# top-level role (e.g., "primary.taxonomy") is treated as "primary" so a
# version-mismatch on the primary triggers the primary's strictness.
_ROLE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("primary", "primary"),
    ("constraints", "constraints"),
    ("extensions", "extensions"),
    ("exclusions", "exclusions"),
)


def _entry_role(entry: DegradationLogEntry) -> str:
    """Return the top-level role name from a degradation entry's path.

    "primary" -> "primary"; "primary.taxonomy" -> "primary";
    "extensions[0]" -> "extensions"; "constraints[2]" -> "constraints".
    Falls back to "primary" if the path doesn't match a known role -- the
    safest interpretation, since unrecognized drops shouldn't silently
    pass through a relaxed role's policy.
    """

    path = entry.path or ""
    for prefix, role in _ROLE_PREFIXES:
        if path == prefix or path.startswith(f"{prefix}.") or path.startswith(
            f"{prefix}["
        ):
            return role
    return "primary"


def _entry_is_agentic(entry: DegradationLogEntry) -> bool:
    """True when the dropped ref had `type=agentic`.

    Agentic refs are governed by their own strictness key (regardless of
    which role they sat in) per proposal §5.7's policy table.
    """

    if entry.original_ref is None:
        return False
    return entry.original_ref.get("type") == "agentic"


def _strictness_skip_required(
    log: DegradationLog, strictness: AudienceStrictness
) -> tuple[bool, str | None]:
    """Decide whether to skip a seller given a pre-flight degradation log.

    Walks each `DegradationLogEntry`, classifies it (agentic vs. role),
    looks up the matching strictness level, and returns True if any entry
    has its level set to "required". Per proposal §5.7's recommendation:

    - `primary=required` and primary got dropped -> skip seller.
    - `constraints=preferred` and constraints got dropped -> proceed (log).
    - `extensions=optional` and extensions got dropped -> proceed.
    - `agentic=optional` and agentic ref got dropped -> proceed.

    Returns (skip, reason). `reason` is set when skip=True so the caller
    can surface a human-readable cause into the failed_bookings list.
    """

    for entry in log:
        if _entry_is_agentic(entry):
            level = strictness.agentic
            role_label = "agentic"
        else:
            role = _entry_role(entry)
            level = getattr(strictness, role, "optional")
            role_label = role
        if level == "required":
            return True, (
                f"audience_strictness.{role_label}=required but seller dropped "
                f"{entry.path} ({entry.reason})"
            )
    return False, None


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
        audience_plan: Typed audience plan from the brief / Audience Planner.
            None on legacy paths that have not yet been wired through.
            Threaded onto DealParams / QuoteRequest / DealBookingRequest so
            the audience surface survives all the way to the seller. See
            proposal §5.2 + §5.3.
        audience_strictness: Per-role strictness policy (`primary`,
            `constraints`, `extensions`, `agentic`). Defaults to None;
            `select_and_book` falls back to `AudienceStrictness()` defaults
            when None. Threaded from the campaign brief so the
            orchestrator's pre-flight gate (§5.7 layer 2 + §13) knows
            which roles must be preserved when degrading per seller.
    """

    media_type: str
    deal_types: list[str]
    content_categories: list[str] = field(default_factory=list)
    excluded_sellers: list[str] = field(default_factory=list)
    min_impressions: Optional[int] = None
    max_cpm: Optional[float] = None
    audience_plan: AudiencePlan | None = None
    audience_strictness: AudienceStrictness | None = None


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
        audience_plan: Typed audience plan threaded from
            InventoryRequirements / CampaignPlan. None on legacy paths
            that have not yet been wired through. Forwarded to QuoteRequest
            so the seller receives the campaign's audience targeting.
    """

    product_id: str
    deal_type: str
    impressions: int
    flight_start: str
    flight_end: str
    target_cpm: Optional[float] = None
    media_type: str = "digital"
    audience_plan: AudiencePlan | None = None


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
        incompatible_sellers: Seller IDs the orchestrator decided not to
            route this campaign to because their audience-plan capabilities
            cannot be reconciled even after degradation+retry. Surfaced for
            the higher-level error path; this orchestrator does not auto-
            route to a different seller (that's a higher-level concern).
        degradation_logs: Per-deal degradation logs produced when
            `degrade_plan_for_seller` fired during a retry-on-rejection.
            Keyed by quote_id; absent when the original plan booked
            cleanly. Surfaced into the audit-trail surface (proposal §13a).
    """

    booked_deals: list[DealResponse]
    failed_bookings: list[dict[str, Any]]
    total_spend: float
    remaining_budget: float
    incompatible_sellers: list[str] = field(default_factory=list)
    degradation_logs: dict[str, DegradationLog] = field(default_factory=dict)


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
        capability_client: Optional[CapabilityClient] = None,
    ) -> None:
        self._registry = registry_client
        self._deals_client_factory = deals_client_factory
        self._event_bus = event_bus
        self._normalizer = quote_normalizer or QuoteNormalizer()
        self._quote_timeout = quote_timeout
        # Optional pre-flight capability client. When provided alongside an
        # `audience_plan` and `audience_strictness` on `select_and_book`,
        # the orchestrator runs the §5.7 layer 1+2 pre-flight before booking.
        # When None, it falls back to the layer-3 retry-only path.
        self._capability_client = capability_client

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
        except Exception as exc:  # noqa: BLE001 - event emission is fail-open by design
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
                    audience_plan=deal_params.audience_plan,
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
            except Exception as exc:  # noqa: BLE001 - per-seller isolation; one failure must not block others
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
        audience_plan: AudiencePlan | None = None,
        audience_strictness: AudienceStrictness | None = None,
    ) -> DealSelection:
        """Select and book optimal deals from ranked quotes.

        Iterates through ranked quotes (best first), skipping any whose
        minimum_spend exceeds the remaining budget, and books up to
        ``count`` deals.

        When `audience_plan` and a configured `capability_client` are both
        present, the orchestrator runs the §5.7 layer 1+2 pre-flight before
        each booking: discover capabilities, degrade the plan, and decide
        whether to skip the seller per `audience_strictness`. The §5.7
        layer-3 retry path remains in place to catch stale-cache cases.

        Args:
            ranked_quotes: Quotes sorted by score (best first), from
                evaluate_and_rank.
            budget: Total budget available for booking.
            count: Maximum number of deals to book.
            quote_seller_map: Mapping of quote_id to seller URL, needed
                to create the correct DealsClient for booking AND to
                pre-flight the seller's capability discovery.
            audience_plan: Optional typed audience plan to attach to each
                DealBookingRequest. Forwarded as deal-level targeting
                metadata so the seller can enforce audience targeting at
                impression-fulfillment time. See proposal §5.1 Step 1.
            audience_strictness: Optional per-role strictness policy from
                the campaign brief. When omitted, defaults are applied
                (primary=required, constraints=preferred, extensions=
                optional, agentic=optional) per proposal §5.7.

        Returns:
            DealSelection with booked deals, failures, and budget info.
        """
        # Default strictness if the caller didn't pass one. Matches
        # `AudienceStrictness()`'s pydantic defaults so unwired callers and
        # explicit-default callers behave identically.
        effective_strictness = audience_strictness or AudienceStrictness()
        booked_deals: list[DealResponse] = []
        failed_bookings: list[dict[str, Any]] = []
        incompatible_sellers: list[str] = []
        degradation_logs: dict[str, DegradationLog] = {}
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
                if (
                    self._capability_client is not None
                    and audience_plan is not None
                ):
                    deal, deg_log = await self._book_with_preflight_then_retry(
                        client=client,
                        quote_id=nq.quote_id,
                        seller_id=nq.seller_id,
                        seller_url=seller_url,
                        audience_plan=audience_plan,
                        audience_strictness=effective_strictness,
                    )
                else:
                    deal, deg_log = await self._book_with_audience_retry(
                        client=client,
                        quote_id=nq.quote_id,
                        seller_id=nq.seller_id,
                        audience_plan=audience_plan,
                    )
                booked_deals.append(deal)
                if deg_log:
                    degradation_logs[nq.quote_id] = deg_log

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

            except _SellerIncompatibleForCampaign as exc:
                # Audience-plan negotiation exhausted -- the seller stays in the
                # ranked list for other campaigns but is marked incompatible
                # for this one. The orchestrator does NOT auto-route to a
                # different seller; that's a higher-level concern.
                logger.warning(
                    "Seller %s incompatible for quote %s: %s",
                    nq.seller_id,
                    nq.quote_id,
                    exc,
                )
                if nq.seller_id not in incompatible_sellers:
                    incompatible_sellers.append(nq.seller_id)
                failed_bookings.append({
                    "quote_id": nq.quote_id,
                    "error": str(exc),
                    "error_code": "audience_plan_unsupported",
                    "seller_id": nq.seller_id,
                })

            except Exception as exc:  # noqa: BLE001 - per-deal isolation; continue booking remaining deals
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
            incompatible_sellers=incompatible_sellers,
            degradation_logs=degradation_logs,
        )

    # ------------------------------------------------------------------
    # Internal: retry-on-audience_plan_unsupported wrapper around book_deal
    # ------------------------------------------------------------------

    async def _book_with_audience_retry(
        self,
        *,
        client: Any,
        quote_id: str,
        seller_id: str,
        audience_plan: AudiencePlan | None,
    ) -> tuple[DealResponse, DegradationLog]:
        """Book a deal with one retry on `audience_plan_unsupported`.

        Implements proposal §5.7 layer 2's retry path. On the first attempt,
        the buyer's plan goes to the seller as-is. If the seller responds
        with the structured ``audience_plan_unsupported`` error, the buyer
        synthesizes a downgraded capability view from the rejection,
        runs ``degrade_plan_for_seller``, and retries ONCE with the
        degraded plan. Other errors propagate unchanged.

        If the retry also fails (any reason -- second
        ``audience_plan_unsupported``, primary stripped, network error,
        etc.) the seller is marked incompatible for this campaign by
        raising `_SellerIncompatibleForCampaign`. The caller surfaces it
        to `DealSelection.incompatible_sellers`.

        Returns (deal_response, degradation_log). The log is empty when
        the original plan booked cleanly (no retry needed).
        """

        # First attempt with the original plan.
        booking_request = DealBookingRequest(
            quote_id=quote_id,
            audience_plan=audience_plan,
        )
        unsupported: list[dict[str, Any]]
        try:
            deal = await client.book_deal(booking_request)
            return deal, []
        except DealsClientError as exc:
            if not _is_audience_plan_unsupported(exc):
                # Not an audience-negotiation rejection -- surface as-is.
                raise

            # Cannot retry without a plan to degrade.
            if audience_plan is None:
                raise

            logger.info(
                "Seller %s rejected audience_plan on quote %s; degrading and "
                "retrying once. Unsupported parts: %s",
                seller_id,
                quote_id,
                exc.unsupported,
            )
            # Stash the unsupported list so the post-except block can use it
            # (Python does not preserve `except` variable bindings outside
            # the block).
            unsupported = list(exc.unsupported)

            # Surface the seller's structured rejection into the audit trail
            # (proposal §13a). Keyed by the plan id so a reviewer can pull
            # this event alongside the matching `degradation` entry.
            if audience_plan is not None:
                audience_audit_log.log_event(
                    plan_id=audience_plan.audience_plan_id,
                    event_type=audience_audit_log.EVENT_CAPABILITY_REJECTION,
                    payload={
                        "seller_id": seller_id,
                        "quote_id": quote_id,
                        "unsupported": unsupported,
                        "error_code": exc.error_code,
                        "status_code": exc.status_code,
                    },
                )

        # Synthesize what the seller doesn't support, run degradation, retry.
        try:
            caps = synthesize_capabilities_from_unsupported(unsupported)
            degraded_plan, degradation_log = degrade_plan_for_seller(
                audience_plan, caps
            )
        except CannotFulfillPlan as cfp:
            # Degradation stripped the primary -- no usable plan to retry.
            # Record the would-be degradation log so the audit trail still
            # captures what was attempted before the seller was marked
            # incompatible (proposal §13a).
            if cfp.log:
                audience_audit_log.log_event(
                    plan_id=audience_plan.audience_plan_id,
                    event_type=audience_audit_log.EVENT_DEGRADATION,
                    payload={
                        "seller_id": seller_id,
                        "quote_id": quote_id,
                        "deal_id": None,
                        "outcome": "cannot_fulfill",
                        "reason": cfp.reason,
                        "log": [entry.model_dump(mode="json") for entry in cfp.log],
                    },
                )
            raise _SellerIncompatibleForCampaign(
                f"Cannot reconcile audience_plan with seller {seller_id}: "
                f"{cfp.reason}"
            ) from cfp

        retry_request = DealBookingRequest(
            quote_id=quote_id,
            audience_plan=degraded_plan,
        )
        try:
            deal = await client.book_deal(retry_request)
        except DealsClientError as retry_exc:
            # The retry failed too. Per scope: mark seller incompatible for
            # this campaign so the higher-level error path can route around
            # it. We do NOT auto-route here.
            raise _SellerIncompatibleForCampaign(
                f"Seller {seller_id} rejected even the degraded plan: "
                f"{retry_exc}"
            ) from retry_exc

        logger.info(
            "Booked deal from seller %s on retry after degrading "
            "audience_plan (%d log entries)",
            seller_id,
            len(degradation_log),
        )

        # Audit-trail entry for the degradation that produced the retry.
        # We key the entry by the ORIGINAL plan id (what the user briefed)
        # rather than the degraded plan's id so a reviewer can find every
        # event for a campaign by looking up the planner's plan id. The
        # degraded plan id is recorded in the payload for traceability.
        if degradation_log:
            audience_audit_log.log_event(
                plan_id=audience_plan.audience_plan_id,
                event_type=audience_audit_log.EVENT_DEGRADATION,
                payload={
                    "seller_id": seller_id,
                    "quote_id": quote_id,
                    "deal_id": deal.deal_id,
                    "degraded_plan_id": degraded_plan.audience_plan_id,
                    "log": [entry.model_dump(mode="json") for entry in degradation_log],
                },
            )

        return deal, degradation_log

    # ------------------------------------------------------------------
    # Internal: pre-flight capability discovery + degradation + retry
    # ------------------------------------------------------------------

    async def _book_with_preflight_then_retry(
        self,
        *,
        client: Any,
        quote_id: str,
        seller_id: str,
        seller_url: str,
        audience_plan: AudiencePlan,
        audience_strictness: AudienceStrictness,
    ) -> tuple[DealResponse, DegradationLog]:
        """Pre-flight a seller's capabilities, degrade the plan, then book.

        Implements proposal §5.7 layer 1+2 and composes with layer 3
        (the existing retry-on-rejection path):

        1. Call `capability_client.discover_capabilities(seller_url)` to
           get the seller's `audience_capabilities` (cached up to 1h,
           honoring `Cache-Control: max-age`).
        2. Run `degrade_plan_for_seller(plan, capabilities)` to produce
           a degraded plan + structured log of what was stripped.
        3. Apply `audience_strictness`: if any role marked "required"
           was dropped, raise `_SellerIncompatibleForCampaign` so the
           caller marks the seller incompatible. Otherwise proceed with
           the degraded plan.
        4. Delegate to `_book_with_audience_retry` with the degraded plan.
           If the seller still rejects (e.g., the cache was stale and
           the seller's caps tightened), the §12 retry path catches it
           and applies a second degradation pass against the seller's
           structured rejection.

        Audit-trail emissions (§13a):

        - One `EVENT_PREFLIGHT_CACHE` event per call with the cache
          status, seller, and capability summary.
        - One `EVENT_DEGRADATION` event when the pre-flight degraded
          the plan (separate from the retry's own degradation event).

        Args:
            client: Per-seller `DealsClient`.
            quote_id / seller_id: Booking identifiers.
            seller_url: Seller's base URL for capability discovery.
            audience_plan: Original plan from the campaign.
            audience_strictness: Per-role strictness policy.

        Returns:
            (deal_response, combined_degradation_log). The combined log
            stitches together pre-flight + retry-time entries so the
            caller's audit surface sees both.

        Raises:
            _SellerIncompatibleForCampaign: When pre-flight degradation
                strips a role marked "required" in the strictness
                policy, or when `degrade_plan_for_seller` cannot keep a
                primary at all.
        """

        assert self._capability_client is not None  # narrowed by select_and_book

        # ---- 1. capability discovery ----
        discovery: CapabilityDiscoveryResult = (
            await self._capability_client.discover_capabilities(seller_url)
        )

        # Audit: every pre-flight call lands in the trail keyed by plan id.
        # Failures here MUST NOT fail the booking -- audience_audit_log is
        # already fail-open, so we just call it and move on.
        audience_audit_log.log_event(
            plan_id=audience_plan.audience_plan_id,
            event_type=audience_audit_log.EVENT_PREFLIGHT_CACHE,
            payload={
                "seller_id": seller_id,
                "seller_url": seller_url,
                "quote_id": quote_id,
                "cache_status": discovery.cache_status,
                "fetched_at": discovery.fetched_at,
                "capabilities": discovery.capabilities.model_dump(mode="json"),
            },
        )

        # ---- 2. degrade per pre-flight caps ----
        try:
            degraded_plan, preflight_log = degrade_plan_for_seller(
                audience_plan, discovery.capabilities
            )
        except CannotFulfillPlan as cfp:
            # Pre-flight stripped the primary entirely. No retry would help;
            # the seller advertises caps that can't carry this campaign.
            raise _SellerIncompatibleForCampaign(
                f"Pre-flight: seller {seller_id} cannot fulfill plan: "
                f"{cfp.reason}"
            ) from cfp

        # ---- 3. apply strictness gate ----
        skip, reason = _strictness_skip_required(preflight_log, audience_strictness)
        if skip:
            logger.info(
                "Pre-flight strictness skip seller=%s quote=%s reason=%s",
                seller_id,
                quote_id,
                reason,
            )
            raise _SellerIncompatibleForCampaign(
                f"Pre-flight strictness gate: {reason}"
            )

        # Audit: when the pre-flight produced any drops we record them.
        # The retry path emits its own degradation event keyed by the same
        # plan id, so a reviewer pulling events for a plan sees both.
        if preflight_log:
            audience_audit_log.log_event(
                plan_id=audience_plan.audience_plan_id,
                event_type=audience_audit_log.EVENT_DEGRADATION,
                payload={
                    "phase": "preflight",
                    "seller_id": seller_id,
                    "seller_url": seller_url,
                    "quote_id": quote_id,
                    "degraded_plan_id": degraded_plan.audience_plan_id,
                    "log": [entry.model_dump(mode="json") for entry in preflight_log],
                },
            )
            logger.info(
                "Pre-flight degraded plan for seller=%s quote=%s "
                "(%d log entries)",
                seller_id,
                quote_id,
                len(preflight_log),
            )

        # ---- 4. book with retry on stale-cache rejection ----
        # §12's retry path takes care of layer 3: if the seller still
        # rejects (cache went stale between pre-flight and booking), it
        # synthesizes a tighter cap view, runs another degradation, and
        # retries once.
        try:
            deal, retry_log = await self._book_with_audience_retry(
                client=client,
                quote_id=quote_id,
                seller_id=seller_id,
                audience_plan=degraded_plan,
            )
        except _SellerIncompatibleForCampaign:
            # The retry path already decided incompatibility. Surface
            # unchanged so the caller marks the seller.
            raise

        # The combined log: pre-flight drops first, then retry-time drops
        # (if any). This is what the caller surfaces as `degradation_logs`
        # so the audit-trail downstream sees the full sequence of strips.
        combined_log: DegradationLog = list(preflight_log) + list(retry_log)
        return deal, combined_log

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
            audience_plan=deal_params.audience_plan,
            audience_strictness=inventory_requirements.audience_strictness,
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
