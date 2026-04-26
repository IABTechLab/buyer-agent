# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the buyer-side pre-flight integration (proposal §5.7 + §13).

The orchestrator's `_book_with_preflight_then_retry` method runs the
seller's capability discovery before booking, applies
`degrade_plan_for_seller` per the campaign's `audience_strictness`, and
composes with §12's retry-on-rejection path for stale-cache cases.

The `CapabilityClient` is responsible for the discovery + cache half of
the flow:
- TTL ceiling of 1h with `Cache-Control: max-age` honored.
- Legacy-seller fallback (no `audience_capabilities` in the agent card).

Bead: ar-gkbr (proposal §5.7 layer 1+2 + §6 row 13).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from ad_buyer.booking.quote_normalizer import NormalizedQuote, QuoteNormalizer
from ad_buyer.clients.capability_client import (
    CapabilityClient,
    CapabilityDiscoveryResult,
    DEFAULT_CACHE_TTL_SECONDS,
)
from ad_buyer.clients.deals_client import DealsClientError
from ad_buyer.models.audience_plan import (
    AudiencePlan,
    AudienceRef,
    AudienceStrictness,
    ComplianceContext,
)
from ad_buyer.models.deals import DealBookingRequest, DealResponse
from ad_buyer.orchestration.audience_degradation import (
    SellerAudienceCapabilities,
)
from ad_buyer.orchestration.multi_seller import (
    DealSelection,
    MultiSellerOrchestrator,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_card_payload(
    *,
    supports_extensions: bool = False,
    supports_constraints: bool = True,
    supports_exclusions: bool = False,
    standard_versions: list[str] | None = None,
    contextual_versions: list[str] | None = None,
    agentic_supported: bool = False,
    schema_version: str = "1",
) -> dict[str, Any]:
    """Build a minimal agent-card JSON with `audience_capabilities`."""

    return {
        "name": "test-seller",
        "description": "test",
        "url": "https://seller.example.com/a2a",
        "version": "1.0.0",
        "provider": {"organization": "test"},
        "audience_capabilities": {
            "schema_version": schema_version,
            "standard_taxonomy_versions": standard_versions or ["1.1"],
            "contextual_taxonomy_versions": contextual_versions or ["3.1"],
            "agentic": {"supported": agentic_supported},
            "supports_constraints": supports_constraints,
            "supports_extensions": supports_extensions,
            "supports_exclusions": supports_exclusions,
            "max_refs_per_role": {
                "primary": 1,
                "constraints": 3,
                "extensions": 0,
                "exclusions": 0,
            },
            "taxonomy_lock_hashes": {
                "audience": "sha256:aaa",
                "content": "sha256:bbb",
            },
        },
    }


def _build_audience_plan(
    *, with_extension: bool = True, with_constraint: bool = True
) -> AudiencePlan:
    """Plan with primary + optional constraint + optional agentic extension."""

    constraints = []
    extensions = []
    if with_constraint:
        constraints.append(
            AudienceRef(
                type="contextual",
                identifier="IAB1-2",
                taxonomy="iab-content",
                version="3.1",
                source="explicit",
            )
        )
    if with_extension:
        extensions.append(
            AudienceRef(
                type="agentic",
                identifier="emb://buyer.example.com/x",
                taxonomy="agentic-audiences",
                version="draft-2026-01",
                source="explicit",
                compliance_context=ComplianceContext(
                    jurisdiction="US",
                    consent_framework="IAB-TCFv2",
                ),
            )
        )

    return AudiencePlan(
        primary=AudienceRef(
            type="standard",
            identifier="3-7",
            taxonomy="iab-audience",
            version="1.1",
            source="explicit",
        ),
        constraints=constraints,
        extensions=extensions,
        rationale="Test plan",
    )


def _make_deal_response(
    *, deal_id: str = "deal-1", quote_id: str = "q-1", final_cpm: float = 12.0
) -> DealResponse:
    return DealResponse.model_validate(
        {
            "deal_id": deal_id,
            "quote_id": quote_id,
            "deal_type": "PD",
            "status": "booked",
            "product": {
                "product_id": "prod-1",
                "name": "Test Product",
                "format": "video",
                "channel": "ctv",
            },
            "pricing": {
                "base_cpm": 10.0,
                "final_cpm": final_cpm,
                "currency": "USD",
            },
            "terms": {
                "impressions": 100_000,
                "flight_start": "2026-05-01",
                "flight_end": "2026-05-31",
            },
            "buyer_tier": "public",
            "expires_at": "2026-06-30T00:00:00Z",
        }
    )


def _ranked_quote(
    quote_id: str = "q-1", seller_id: str = "seller-a"
) -> NormalizedQuote:
    return NormalizedQuote(
        seller_id=seller_id,
        quote_id=quote_id,
        raw_cpm=10.0,
        effective_cpm=10.0,
        deal_type="PD",
        fee_estimate=0.0,
        minimum_spend=0.0,
        score=90.0,
    )


def _audience_plan_unsupported_error(
    unsupported: list[dict[str, str]] | None = None,
) -> DealsClientError:
    return DealsClientError(
        message="Seller API error 400: audience_plan_unsupported",
        status_code=400,
        error_code="audience_plan_unsupported",
        detail="",
        unsupported=unsupported
        or [
            {
                "path": "constraints[0]",
                "reason": "constraints not supported by this seller",
            }
        ],
    )


class _ManualClock:
    """Monotonic clock that only advances when `advance` is called.

    Lets the cache-TTL tests pin time deterministically without relying on
    real clock behavior or `freezegun`.
    """

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _capability_client(
    *,
    handler,
    clock: _ManualClock | None = None,
    cache_ttl: float = DEFAULT_CACHE_TTL_SECONDS,
) -> tuple[CapabilityClient, _ManualClock, dict[str, int]]:
    """Build a CapabilityClient backed by an `httpx.MockTransport`.

    The handler closure receives each request; `call_count` exposes how
    many HTTP calls actually went out so tests can assert cache hits.
    """

    state = {"calls": 0}
    clock = clock or _ManualClock()

    def transport_handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        return handler(request)

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(transport_handler))

    return (
        CapabilityClient(
            cache_ttl_seconds=cache_ttl,
            clock=clock,
            client_factory=factory,
        ),
        clock,
        state,
    )


# ---------------------------------------------------------------------------
# CapabilityClient: discover happy path
# ---------------------------------------------------------------------------


class TestDiscoverCapabilitiesHappyPath:
    """Test 1: discover_capabilities parses the seller's response correctly."""

    @pytest.mark.asyncio
    async def test_parses_audience_capabilities_block(self):
        payload = _agent_card_payload(supports_extensions=True)

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/.well-known/agent.json")
            return httpx.Response(200, json=payload)

        client, _clock, state = _capability_client(handler=handler)

        result = await client.discover_capabilities("https://seller.example.com")

        assert isinstance(result, CapabilityDiscoveryResult)
        assert result.cache_status == "miss"
        assert result.capabilities.schema_version == "1"
        assert result.capabilities.supports_extensions is True
        assert result.capabilities.supports_constraints is True
        assert result.capabilities.agentic.supported is False
        assert state["calls"] == 1


# ---------------------------------------------------------------------------
# CapabilityClient: cache hit
# ---------------------------------------------------------------------------


class TestCacheHit:
    """Test 2: second call within TTL is served from cache, no second HTTP."""

    @pytest.mark.asyncio
    async def test_second_call_within_ttl_is_cache_hit(self):
        payload = _agent_card_payload()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        client, clock, state = _capability_client(handler=handler)

        first = await client.discover_capabilities("https://seller.example.com")
        assert first.cache_status == "miss"
        assert state["calls"] == 1

        # Advance by 30 minutes -- well within the 1h TTL.
        clock.advance(30 * 60)

        second = await client.discover_capabilities("https://seller.example.com")
        assert second.cache_status == "hit"
        assert state["calls"] == 1  # no second HTTP call
        # Caps round-tripped from cache, not re-parsed.
        assert second.capabilities.schema_version == first.capabilities.schema_version


# ---------------------------------------------------------------------------
# CapabilityClient: TTL expiry
# ---------------------------------------------------------------------------


class TestCacheTTLExpiry:
    """Test 3: third call after 1h re-fetches (TTL expired)."""

    @pytest.mark.asyncio
    async def test_call_after_one_hour_refetches(self):
        payload = _agent_card_payload()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        client, clock, state = _capability_client(handler=handler)

        await client.discover_capabilities("https://seller.example.com")
        assert state["calls"] == 1

        # 30 min: still fresh.
        clock.advance(30 * 60)
        assert (
            await client.discover_capabilities("https://seller.example.com")
        ).cache_status == "hit"
        assert state["calls"] == 1

        # +31 min => 61 min total: TTL expired.
        clock.advance(31 * 60)
        third = await client.discover_capabilities("https://seller.example.com")
        assert third.cache_status == "stale"
        assert state["calls"] == 2


# ---------------------------------------------------------------------------
# CapabilityClient: Cache-Control: max-age honored
# ---------------------------------------------------------------------------


class TestCacheControlHonored:
    """Test 4: `Cache-Control: max-age=N` shortens the cache TTL."""

    @pytest.mark.asyncio
    async def test_max_age_shortens_ttl(self):
        """A seller-set max-age of 60s expires the cache much sooner than 1h."""

        payload = _agent_card_payload()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=payload,
                headers={"Cache-Control": "public, max-age=60"},
            )

        client, clock, state = _capability_client(handler=handler)

        await client.discover_capabilities("https://seller.example.com")
        assert state["calls"] == 1

        # 30s -- still fresh under the 60s ceiling.
        clock.advance(30)
        assert (
            await client.discover_capabilities("https://seller.example.com")
        ).cache_status == "hit"
        assert state["calls"] == 1

        # +31s => 61s total: max-age expired, re-fetch.
        clock.advance(31)
        third = await client.discover_capabilities("https://seller.example.com")
        assert third.cache_status == "stale"
        assert state["calls"] == 2

    @pytest.mark.asyncio
    async def test_max_age_clamped_to_one_hour_ceiling(self):
        """A seller-set max-age longer than 1h is clamped to the ceiling.

        A misconfigured seller cannot push the buyer into multi-hour stale
        capability state; the 1h ceiling is non-negotiable per proposal §5.7.
        """

        payload = _agent_card_payload()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=payload,
                headers={"Cache-Control": "max-age=86400"},  # 24h
            )

        client, clock, state = _capability_client(handler=handler)

        await client.discover_capabilities("https://seller.example.com")

        # Even though the seller asked for 24h, the buyer caps at 1h.
        clock.advance(60 * 61)  # 1h 1min
        result = await client.discover_capabilities("https://seller.example.com")
        assert result.cache_status == "stale"
        assert state["calls"] == 2


# ---------------------------------------------------------------------------
# CapabilityClient: legacy seller fallback
# ---------------------------------------------------------------------------


class TestLegacySellerFallback:
    """Test 5: seller without `audience_capabilities` returns legacy_default."""

    @pytest.mark.asyncio
    async def test_legacy_seller_no_block(self):
        """Agent card lands but lacks `audience_capabilities`."""

        payload = {
            "name": "legacy-seller",
            "description": "legacy",
            "url": "https://legacy.example.com/a2a",
            "version": "1.0.0",
            "provider": {"organization": "legacy"},
            # no audience_capabilities here
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        client, _clock, _state = _capability_client(handler=handler)

        result = await client.discover_capabilities("https://legacy.example.com")

        assert result.cache_status == "legacy"
        assert result.capabilities == SellerAudienceCapabilities.legacy_default()
        # legacy_default specifics: no constraints/extensions/exclusions.
        assert result.capabilities.supports_constraints is False
        assert result.capabilities.supports_extensions is False
        assert result.capabilities.supports_exclusions is False
        assert result.capabilities.agentic.supported is False

    @pytest.mark.asyncio
    async def test_http_error_returns_legacy_default(self):
        """A 5xx / connection failure also degrades to legacy_default."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="service unavailable")

        client, _clock, _state = _capability_client(handler=handler)

        result = await client.discover_capabilities("https://flaky.example.com")
        assert result.cache_status == "error"
        assert result.capabilities == SellerAudienceCapabilities.legacy_default()


# ---------------------------------------------------------------------------
# Pre-flight integration: orchestrator calls discover before booking
# ---------------------------------------------------------------------------


class _RecordingCapabilityClient:
    """Test double: records every `discover_capabilities` call.

    Returns a configurable `SellerAudienceCapabilities` per seller URL.
    """

    def __init__(self, caps_by_url: dict[str, SellerAudienceCapabilities]):
        self._caps_by_url = caps_by_url
        self.calls: list[str] = []

    async def discover_capabilities(
        self, seller_endpoint: str
    ) -> CapabilityDiscoveryResult:
        self.calls.append(seller_endpoint)
        caps = self._caps_by_url.get(
            seller_endpoint, SellerAudienceCapabilities.legacy_default()
        )
        return CapabilityDiscoveryResult(
            capabilities=caps,
            cache_status="miss",
            fetched_at=0.0,
        )


def _orchestrator_with_caps(
    caps_by_url: dict[str, SellerAudienceCapabilities],
    *,
    deals_client_factory,
):
    """Build a MultiSellerOrchestrator wired to a recording capability client."""

    return MultiSellerOrchestrator(
        registry_client=AsyncMock(),
        deals_client_factory=deals_client_factory,
        event_bus=None,
        quote_normalizer=QuoteNormalizer(),
        quote_timeout=5.0,
        capability_client=_RecordingCapabilityClient(caps_by_url),
    )


@pytest.fixture
def deals_client_factory():
    """Per-URL mock client factory; tests configure `book_deal` per seller."""

    clients: dict[str, AsyncMock] = {}

    def factory(seller_url: str, **kwargs: Any) -> AsyncMock:
        if seller_url not in clients:
            mock = AsyncMock()
            mock.seller_url = seller_url
            mock.book_deal = AsyncMock()
            mock.close = AsyncMock()
            clients[seller_url] = mock
        return clients[seller_url]

    factory._clients = clients  # type: ignore[attr-defined]
    return factory


class TestPreflightCallsDiscoverBeforeBooking:
    """Test 6: orchestrator calls discover_capabilities before booking."""

    @pytest.mark.asyncio
    async def test_discover_runs_first(self, deals_client_factory):
        seller_url = "https://seller-a.example.com"

        # Caps that fully accept this plan: nothing degraded, booking succeeds.
        caps = SellerAudienceCapabilities(
            schema_version="1",
            standard_taxonomy_versions=["1.1"],
            contextual_taxonomy_versions=["3.1"],
            supports_constraints=True,
            supports_extensions=True,
            supports_exclusions=False,
        )
        orchestrator = _orchestrator_with_caps(
            {seller_url: caps},
            deals_client_factory=deals_client_factory,
        )

        client = deals_client_factory(seller_url)
        client.book_deal.return_value = _make_deal_response(deal_id="deal-1")

        plan = _build_audience_plan(with_extension=False, with_constraint=True)
        selection = await orchestrator.select_and_book(
            ranked_quotes=[_ranked_quote()],
            budget=100_000.0,
            count=1,
            quote_seller_map={"q-1": seller_url},
            audience_plan=plan,
            audience_strictness=AudienceStrictness(),
        )

        # discover called exactly once for this seller, before booking.
        assert orchestrator._capability_client.calls == [seller_url]
        assert len(selection.booked_deals) == 1
        # Booking saw the (unmodified) plan since caps fully covered it.
        booking_arg: DealBookingRequest = client.book_deal.await_args_list[0].args[0]
        assert booking_arg.audience_plan is not None
        assert booking_arg.audience_plan.constraints[0].identifier == "IAB1-2"


# ---------------------------------------------------------------------------
# Pre-flight: degradation per audience_strictness
# ---------------------------------------------------------------------------


class TestPreflightStrictnessGate:
    """Tests 7a / 7b / 7c: pre-flight degrades plan, applies strictness."""

    @pytest.mark.asyncio
    async def test_primary_required_with_version_mismatch_skips_seller(
        self, deals_client_factory
    ):
        """primary=required + standard taxonomy version mismatch -> seller skipped.

        The seller advertises only Audience Taxonomy v2.0 (which the buyer's
        plan does not target). With `primary=required`, pre-flight refuses
        to degrade past the primary -- seller marked incompatible, no
        booking attempt.
        """

        seller_url = "https://seller-mismatch.example.com"
        caps = SellerAudienceCapabilities(
            schema_version="1",
            # Buyer's plan uses 1.1 -- seller offers only 2.0.
            standard_taxonomy_versions=["2.0"],
            contextual_taxonomy_versions=["3.1"],
            supports_constraints=True,
            supports_extensions=False,
        )
        orchestrator = _orchestrator_with_caps(
            {seller_url: caps},
            deals_client_factory=deals_client_factory,
        )
        client = deals_client_factory(seller_url)

        plan = _build_audience_plan(with_extension=False, with_constraint=False)
        selection = await orchestrator.select_and_book(
            ranked_quotes=[_ranked_quote()],
            budget=100_000.0,
            count=1,
            quote_seller_map={"q-1": seller_url},
            audience_plan=plan,
            audience_strictness=AudienceStrictness(primary="required"),
        )

        # No booking attempt.
        assert client.book_deal.await_count == 0
        # Seller marked incompatible.
        assert "seller-a" in selection.incompatible_sellers
        assert selection.booked_deals == []
        assert len(selection.failed_bookings) == 1
        assert (
            selection.failed_bookings[0]["error_code"]
            == "audience_plan_unsupported"
        )

    @pytest.mark.asyncio
    async def test_extensions_optional_dropped_proceeds(self, deals_client_factory):
        """extensions=optional + dropped -> proceed with degraded plan."""

        seller_url = "https://seller-no-ext.example.com"
        caps = SellerAudienceCapabilities(
            schema_version="1",
            standard_taxonomy_versions=["1.1"],
            contextual_taxonomy_versions=["3.1"],
            supports_constraints=True,
            supports_extensions=False,  # this seller doesn't honor extensions
        )
        orchestrator = _orchestrator_with_caps(
            {seller_url: caps},
            deals_client_factory=deals_client_factory,
        )
        client = deals_client_factory(seller_url)
        client.book_deal.return_value = _make_deal_response(deal_id="deal-1")

        plan = _build_audience_plan(with_extension=True, with_constraint=True)
        selection = await orchestrator.select_and_book(
            ranked_quotes=[_ranked_quote()],
            budget=100_000.0,
            count=1,
            quote_seller_map={"q-1": seller_url},
            audience_plan=plan,
            audience_strictness=AudienceStrictness(extensions="optional"),
        )

        assert len(selection.booked_deals) == 1
        # Booking went out with extensions stripped.
        booking_arg: DealBookingRequest = client.book_deal.await_args_list[0].args[0]
        assert booking_arg.audience_plan is not None
        assert booking_arg.audience_plan.extensions == []
        # The constraint survived.
        assert booking_arg.audience_plan.constraints[0].identifier == "IAB1-2"
        # Degradation log surfaced.
        assert "q-1" in selection.degradation_logs
        log = selection.degradation_logs["q-1"]
        assert any("extensions" in entry.path for entry in log)

    @pytest.mark.asyncio
    async def test_constraints_preferred_dropped_proceeds(self, deals_client_factory):
        """constraints=preferred + dropped -> proceed (no skip)."""

        seller_url = "https://seller-no-constraints.example.com"
        caps = SellerAudienceCapabilities(
            schema_version="1",
            standard_taxonomy_versions=["1.1"],
            contextual_taxonomy_versions=["3.1"],
            supports_constraints=False,  # constraints unsupported
            supports_extensions=False,
        )
        orchestrator = _orchestrator_with_caps(
            {seller_url: caps},
            deals_client_factory=deals_client_factory,
        )
        client = deals_client_factory(seller_url)
        client.book_deal.return_value = _make_deal_response(deal_id="deal-1")

        plan = _build_audience_plan(with_extension=False, with_constraint=True)
        selection = await orchestrator.select_and_book(
            ranked_quotes=[_ranked_quote()],
            budget=100_000.0,
            count=1,
            quote_seller_map={"q-1": seller_url},
            audience_plan=plan,
            audience_strictness=AudienceStrictness(constraints="preferred"),
        )

        assert len(selection.booked_deals) == 1
        booking_arg: DealBookingRequest = client.book_deal.await_args_list[0].args[0]
        assert booking_arg.audience_plan is not None
        assert booking_arg.audience_plan.constraints == []
        # Primary preserved.
        assert booking_arg.audience_plan.primary.identifier == "3-7"

    @pytest.mark.asyncio
    async def test_constraints_required_dropped_skips_seller(
        self, deals_client_factory
    ):
        """constraints=required + dropped -> seller skipped.

        Promotes the optional-by-default constraint policy to required; the
        same seller that booked in the previous test is now incompatible.
        Verifies the strictness gate is dynamic, not hard-coded.
        """

        seller_url = "https://seller-no-constraints.example.com"
        caps = SellerAudienceCapabilities(
            schema_version="1",
            standard_taxonomy_versions=["1.1"],
            contextual_taxonomy_versions=["3.1"],
            supports_constraints=False,
            supports_extensions=False,
        )
        orchestrator = _orchestrator_with_caps(
            {seller_url: caps},
            deals_client_factory=deals_client_factory,
        )
        client = deals_client_factory(seller_url)

        plan = _build_audience_plan(with_extension=False, with_constraint=True)
        selection = await orchestrator.select_and_book(
            ranked_quotes=[_ranked_quote()],
            budget=100_000.0,
            count=1,
            quote_seller_map={"q-1": seller_url},
            audience_plan=plan,
            audience_strictness=AudienceStrictness(constraints="required"),
        )

        assert client.book_deal.await_count == 0
        assert "seller-a" in selection.incompatible_sellers
        assert selection.booked_deals == []


# ---------------------------------------------------------------------------
# Pre-flight + retry composition
# ---------------------------------------------------------------------------


class TestPreflightRetryComposition:
    """Test 8: pre-flight passes, but seller still rejects -> retry path fires."""

    @pytest.mark.asyncio
    async def test_stale_cache_seller_rejects_retry_fires(self, deals_client_factory):
        """Pre-flight thinks the seller accepts everything; seller rejects.

        Verifies §13 + §12 compose: when the cache says "seller accepts X"
        but the seller actually rejects it (stale-cache scenario), the §12
        retry path kicks in and produces a successful booking with a
        further-degraded plan.
        """

        seller_url = "https://seller-stale.example.com"
        # Pre-flight reports a fully-permissive seller.
        permissive_caps = SellerAudienceCapabilities(
            schema_version="1",
            standard_taxonomy_versions=["1.1"],
            contextual_taxonomy_versions=["3.1"],
            supports_constraints=True,
            supports_extensions=True,  # cache says yes...
            supports_exclusions=False,
        )
        orchestrator = _orchestrator_with_caps(
            {seller_url: permissive_caps},
            deals_client_factory=deals_client_factory,
        )
        client = deals_client_factory(seller_url)

        # ...but the seller actually rejects extensions on the first call.
        first_rejection = _audience_plan_unsupported_error(
            unsupported=[
                {
                    "path": "extensions[0]",
                    "reason": "extensions not supported by this seller",
                }
            ]
        )
        client.book_deal.side_effect = [
            first_rejection,
            _make_deal_response(deal_id="deal-1"),
        ]

        plan = _build_audience_plan(with_extension=True, with_constraint=True)
        selection = await orchestrator.select_and_book(
            ranked_quotes=[_ranked_quote()],
            budget=100_000.0,
            count=1,
            quote_seller_map={"q-1": seller_url},
            audience_plan=plan,
            audience_strictness=AudienceStrictness(),  # defaults
        )

        # Two book_deal calls: first rejected, second succeeded.
        assert client.book_deal.await_count == 2
        # Final booking landed.
        assert len(selection.booked_deals) == 1
        # Combined log: pre-flight didn't drop anything, but retry did.
        assert "q-1" in selection.degradation_logs
        log = selection.degradation_logs["q-1"]
        assert any(entry.path.startswith("extensions") for entry in log)

        # Inspect retry call: extensions stripped.
        retry_args = client.book_deal.await_args_list[1]
        retry_request: DealBookingRequest = retry_args.args[0]
        assert retry_request.audience_plan is not None
        assert retry_request.audience_plan.extensions == []

    @pytest.mark.asyncio
    async def test_preflight_dropped_extensions_no_retry_needed(
        self, deals_client_factory
    ):
        """When pre-flight already strips ext, the seller never sees them."""

        seller_url = "https://seller-no-ext.example.com"
        caps_no_ext = SellerAudienceCapabilities(
            schema_version="1",
            standard_taxonomy_versions=["1.1"],
            contextual_taxonomy_versions=["3.1"],
            supports_constraints=True,
            supports_extensions=False,
        )
        orchestrator = _orchestrator_with_caps(
            {seller_url: caps_no_ext},
            deals_client_factory=deals_client_factory,
        )
        client = deals_client_factory(seller_url)
        client.book_deal.return_value = _make_deal_response(deal_id="deal-1")

        plan = _build_audience_plan(with_extension=True, with_constraint=True)
        selection = await orchestrator.select_and_book(
            ranked_quotes=[_ranked_quote()],
            budget=100_000.0,
            count=1,
            quote_seller_map={"q-1": seller_url},
            audience_plan=plan,
            audience_strictness=AudienceStrictness(),
        )

        # Single book_deal call -- pre-flight already stripped extensions.
        assert client.book_deal.await_count == 1
        assert len(selection.booked_deals) == 1
