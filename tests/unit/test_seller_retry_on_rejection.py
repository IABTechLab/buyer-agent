# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for `MultiSellerOrchestrator`'s retry-on-`audience_plan_unsupported`.

Implements proposal §5.7 layer 2's retry side: when the seller responds to
a `DealBookingRequest` with a structured `audience_plan_unsupported` 400,
the orchestrator runs `degrade_plan_for_seller` against a synthesized cap
view and retries the booking once with the degraded plan. Other errors
surface unchanged. If the retry also fails, the seller is marked
incompatible for this campaign (recorded on `DealSelection`).

Bead: ar-0w48 (proposal §5.7 layer 2 + §6 row 12).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from ad_buyer.booking.quote_normalizer import NormalizedQuote, QuoteNormalizer
from ad_buyer.clients.deals_client import DealsClientError
from ad_buyer.models.audience_plan import (
    AudiencePlan,
    AudienceRef,
    ComplianceContext,
)
from ad_buyer.models.deals import (
    DealBookingRequest,
    DealResponse,
)
from ad_buyer.orchestration.multi_seller import (
    DealSelection,
    MultiSellerOrchestrator,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_deal_response(
    *, deal_id: str = "deal-1", quote_id: str = "q-1", final_cpm: float = 12.0
) -> DealResponse:
    """Build a minimal valid DealResponse for tests.

    Mirrors the helper in test_multi_seller_orchestrator.py but local to
    keep this file self-contained.
    """

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


def _audience_plan() -> AudiencePlan:
    """Build a plan with all four roles populated.

    Used to verify the orchestrator's retry path can degrade arbitrary
    parts of the plan (extensions in particular).
    """

    return AudiencePlan(
        primary=AudienceRef(
            type="standard",
            identifier="3-7",
            taxonomy="iab-audience",
            version="1.1",
            source="explicit",
        ),
        constraints=[
            AudienceRef(
                type="contextual",
                identifier="IAB1-2",
                taxonomy="iab-content",
                version="3.1",
                source="explicit",
            )
        ],
        extensions=[
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
        ],
        rationale="Test plan",
    )


def _ranked_quote(quote_id: str = "q-1", seller_id: str = "seller-a") -> NormalizedQuote:
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
    """Build the seller's structured rejection."""

    return DealsClientError(
        message="Seller API error 400: audience_plan_unsupported",
        status_code=400,
        error_code="audience_plan_unsupported",
        detail="",
        unsupported=unsupported
        or [
            {
                "path": "extensions[0]",
                "reason": "extensions not supported by this seller",
            }
        ],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_registry_client():
    client = AsyncMock()
    client.discover_sellers = AsyncMock(return_value=[])
    return client


@pytest.fixture
def deals_client_factory():
    """Factory that hands out per-URL mock clients with `book_deal` configurable."""

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


@pytest.fixture
def orchestrator(mock_registry_client, deals_client_factory):
    return MultiSellerOrchestrator(
        registry_client=mock_registry_client,
        deals_client_factory=deals_client_factory,
        event_bus=None,
        quote_normalizer=QuoteNormalizer(),
        quote_timeout=5.0,
    )


# ---------------------------------------------------------------------------
# Test 11: 400 audience_plan_unsupported with extension drop -> retry
# ---------------------------------------------------------------------------


class TestRetryOnAudiencePlanUnsupported:
    @pytest.mark.asyncio
    async def test_retry_drops_unsupported_extension_and_succeeds(
        self, orchestrator, deals_client_factory
    ):
        """Seller rejects extensions; retry without them succeeds.

        Verifies (a) the retry happens, (b) the second `book_deal` carries
        the degraded plan (no extensions), and (c) the returned selection
        records the degradation log keyed by quote_id.
        """

        seller_url = "http://seller-a.example.com"
        client = deals_client_factory(seller_url)

        # First call: seller rejects with audience_plan_unsupported.
        # Second call: seller accepts and books.
        success_response = _make_deal_response(deal_id="deal-1", quote_id="q-1")
        client.book_deal.side_effect = [
            _audience_plan_unsupported_error(
                unsupported=[
                    {
                        "path": "extensions[0]",
                        "reason": "extensions not supported by this seller",
                    }
                ]
            ),
            success_response,
        ]

        plan = _audience_plan()
        selection = await orchestrator.select_and_book(
            ranked_quotes=[_ranked_quote()],
            budget=100_000.0,
            count=1,
            quote_seller_map={"q-1": seller_url},
            audience_plan=plan,
        )

        assert isinstance(selection, DealSelection)
        assert len(selection.booked_deals) == 1
        assert selection.booked_deals[0].deal_id == "deal-1"
        assert selection.incompatible_sellers == []

        # The retry happened: book_deal was called twice on this client.
        assert client.book_deal.await_count == 2

        # Inspect the second (retry) call -- it should carry the degraded plan
        # with extensions stripped.
        retry_args = client.book_deal.await_args_list[1]
        retry_request: DealBookingRequest = retry_args.args[0]
        assert retry_request.audience_plan is not None
        assert retry_request.audience_plan.extensions == []
        # Primary preserved.
        assert retry_request.audience_plan.primary.identifier == "3-7"

        # Degradation log surfaced on the selection.
        assert "q-1" in selection.degradation_logs
        log = selection.degradation_logs["q-1"]
        assert len(log) >= 1
        assert any("extensions" in e.path for e in log)

    @pytest.mark.asyncio
    async def test_retry_succeeds_clean_first_try_no_log(
        self, orchestrator, deals_client_factory
    ):
        """When the first booking succeeds, no retry, no degradation log."""

        seller_url = "http://seller-a.example.com"
        client = deals_client_factory(seller_url)
        client.book_deal.return_value = _make_deal_response(
            deal_id="deal-1", quote_id="q-1"
        )

        selection = await orchestrator.select_and_book(
            ranked_quotes=[_ranked_quote()],
            budget=100_000.0,
            count=1,
            quote_seller_map={"q-1": seller_url},
            audience_plan=_audience_plan(),
        )

        assert len(selection.booked_deals) == 1
        assert selection.degradation_logs == {}
        assert client.book_deal.await_count == 1


# ---------------------------------------------------------------------------
# Test 13: retry fails -> seller marked incompatible
# ---------------------------------------------------------------------------


class TestRetryFails:
    @pytest.mark.asyncio
    async def test_second_rejection_marks_seller_incompatible(
        self, orchestrator, deals_client_factory
    ):
        """If the retry also fails, seller is marked incompatible for campaign."""

        seller_url = "http://seller-a.example.com"
        client = deals_client_factory(seller_url)

        # Both attempts fail.
        client.book_deal.side_effect = [
            _audience_plan_unsupported_error(),
            _audience_plan_unsupported_error(
                unsupported=[
                    {
                        "path": "primary.taxonomy",
                        "reason": "standard taxonomy version '1.1' not supported",
                    }
                ]
            ),
        ]

        selection = await orchestrator.select_and_book(
            ranked_quotes=[_ranked_quote(seller_id="seller-a")],
            budget=100_000.0,
            count=1,
            quote_seller_map={"q-1": seller_url},
            audience_plan=_audience_plan(),
        )

        assert selection.booked_deals == []
        assert "seller-a" in selection.incompatible_sellers
        assert len(selection.failed_bookings) == 1
        assert (
            selection.failed_bookings[0]["error_code"]
            == "audience_plan_unsupported"
        )
        assert selection.failed_bookings[0]["seller_id"] == "seller-a"

    @pytest.mark.asyncio
    async def test_primary_stripped_during_degradation_marks_incompatible(
        self, orchestrator, deals_client_factory
    ):
        """If degradation strips the primary, no retry happens; seller incompatible."""

        seller_url = "http://seller-a.example.com"
        client = deals_client_factory(seller_url)

        # Seller rejects the primary's taxonomy.
        client.book_deal.side_effect = [
            _audience_plan_unsupported_error(
                unsupported=[
                    {
                        "path": "primary.taxonomy",
                        "reason": "standard taxonomy version '1.1' not supported",
                    }
                ]
            ),
        ]

        selection = await orchestrator.select_and_book(
            ranked_quotes=[_ranked_quote(seller_id="seller-a")],
            budget=100_000.0,
            count=1,
            quote_seller_map={"q-1": seller_url},
            audience_plan=_audience_plan(),
        )

        # The retry never went out because degradation raised CannotFulfillPlan.
        # That's exactly one book_deal call.
        assert client.book_deal.await_count == 1
        assert selection.booked_deals == []
        assert "seller-a" in selection.incompatible_sellers


# ---------------------------------------------------------------------------
# Test 14: non-audience errors don't trigger retry
# ---------------------------------------------------------------------------


class TestNonAudienceErrorsNoRetry:
    @pytest.mark.asyncio
    async def test_500_error_no_retry(self, orchestrator, deals_client_factory):
        """500 from seller surfaces as a generic failure, no retry."""

        seller_url = "http://seller-a.example.com"
        client = deals_client_factory(seller_url)

        client.book_deal.side_effect = DealsClientError(
            message="Seller API error 500: internal_error",
            status_code=500,
            error_code="internal_error",
            detail="boom",
        )

        selection = await orchestrator.select_and_book(
            ranked_quotes=[_ranked_quote()],
            budget=100_000.0,
            count=1,
            quote_seller_map={"q-1": "http://seller-a.example.com"},
            audience_plan=_audience_plan(),
        )

        assert selection.booked_deals == []
        assert client.book_deal.await_count == 1
        # NOT marked incompatible: 500 is not an audience-negotiation problem.
        assert selection.incompatible_sellers == []
        assert len(selection.failed_bookings) == 1
        # Recorded as a generic failure, not the audience-plan-specific shape.
        assert "error_code" not in selection.failed_bookings[0]

    @pytest.mark.asyncio
    async def test_503_error_no_retry(self, orchestrator, deals_client_factory):
        """503 transient (post-client-retry) surfaces as a generic failure."""

        seller_url = "http://seller-a.example.com"
        client = deals_client_factory(seller_url)

        client.book_deal.side_effect = DealsClientError(
            message="Seller API error 503: service_unavailable",
            status_code=503,
            error_code="",
            detail="",
        )

        selection = await orchestrator.select_and_book(
            ranked_quotes=[_ranked_quote()],
            budget=100_000.0,
            count=1,
            quote_seller_map={"q-1": "http://seller-a.example.com"},
            audience_plan=_audience_plan(),
        )

        assert selection.booked_deals == []
        assert client.book_deal.await_count == 1
        assert selection.incompatible_sellers == []

    @pytest.mark.asyncio
    async def test_400_without_audience_plan_unsupported_no_retry(
        self, orchestrator, deals_client_factory
    ):
        """A different 400 (e.g. invalid_quote_status) does not trigger the retry path."""

        seller_url = "http://seller-a.example.com"
        client = deals_client_factory(seller_url)

        client.book_deal.side_effect = DealsClientError(
            message="Seller API error 400: invalid_quote_status",
            status_code=400,
            error_code="invalid_quote_status",
            detail="quote already booked",
            unsupported=[],
        )

        selection = await orchestrator.select_and_book(
            ranked_quotes=[_ranked_quote()],
            budget=100_000.0,
            count=1,
            quote_seller_map={"q-1": "http://seller-a.example.com"},
            audience_plan=_audience_plan(),
        )

        assert selection.booked_deals == []
        assert client.book_deal.await_count == 1
        assert selection.incompatible_sellers == []

    @pytest.mark.asyncio
    async def test_no_audience_plan_no_retry_even_on_unsupported(
        self, orchestrator, deals_client_factory
    ):
        """If the campaign has no audience_plan, an `audience_plan_unsupported` error
        cannot be retried (nothing to degrade) -- surface as a generic failure."""

        seller_url = "http://seller-a.example.com"
        client = deals_client_factory(seller_url)

        client.book_deal.side_effect = _audience_plan_unsupported_error()

        selection = await orchestrator.select_and_book(
            ranked_quotes=[_ranked_quote()],
            budget=100_000.0,
            count=1,
            quote_seller_map={"q-1": "http://seller-a.example.com"},
            audience_plan=None,  # legacy path
        )

        assert selection.booked_deals == []
        assert client.book_deal.await_count == 1
        # No retry happened, but also not auto-marked incompatible -- without
        # a plan, "incompatibility" is not the right framing. It's just a
        # generic failure.
        assert selection.incompatible_sellers == []


# ---------------------------------------------------------------------------
# Bonus: client-side error parsing of the FastAPI-wrapped detail dict
# ---------------------------------------------------------------------------


class TestDealsClientErrorParsing:
    """Verify the deals client surfaces the structured rejection correctly.

    The seller emits FastAPI's `HTTPException(detail=<dict>)` shape, which
    lands on the wire as `{"detail": {"error": "...", "unsupported": [...]}}`.
    The client's `_build_error_from_response` must extract both the error
    code and the unsupported list.
    """

    def test_fastapi_wrapped_error_extracts_error_code_and_unsupported(self):
        import httpx

        from ad_buyer.clients.deals_client import DealsClient

        body = (
            b'{"detail": {"error": "audience_plan_unsupported", '
            b'"unsupported": [{"path": "extensions[0]", '
            b'"reason": "extensions not supported by this seller"}]}}'
        )
        response = httpx.Response(
            status_code=400,
            content=body,
            headers={"content-type": "application/json"},
        )

        err = DealsClient._build_error_from_response(response)

        assert err.status_code == 400
        assert err.error_code == "audience_plan_unsupported"
        assert err.unsupported == [
            {
                "path": "extensions[0]",
                "reason": "extensions not supported by this seller",
            }
        ]

    def test_flat_error_shape_still_parses(self):
        """Pre-existing flat-shape errors must keep working."""

        import httpx

        from ad_buyer.clients.deals_client import DealsClient

        body = (
            b'{"error": "product_not_found", '
            b'"detail": "Product bad-id does not exist"}'
        )
        response = httpx.Response(
            status_code=404,
            content=body,
            headers={"content-type": "application/json"},
        )

        err = DealsClient._build_error_from_response(response)

        assert err.status_code == 404
        assert err.error_code == "product_not_found"
        assert err.unsupported == []
        assert "Product bad-id" in err.detail
