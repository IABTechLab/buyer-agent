# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the DealsClient linear TV extensions.

Covers:
- request_quote with media_type='linear_tv' and LinearTVParams
- Makegood client: POST /api/v1/deals/{deal_id}/makegoods
- Cancellation client: POST /api/v1/deals/{deal_id}/cancel

Tests written first (TDD) per bead buyer-6io.
"""

import json

import httpx
import pytest

from ad_buyer.clients.deals_client import DealsClient, DealsClientError
from ad_buyer.models.deals import (
    QuoteRequest,
)
from ad_buyer.models.linear_tv import (
    CancellationRequest,
    LinearTVParams,
    MakegoodRequest,
)

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

SELLER_URL = "http://seller.example.com"


def _linear_tv_quote_response_json() -> dict:
    """Minimal valid linear TV QuoteResponse JSON."""
    return {
        "quote_id": "qt-ltv-001",
        "status": "available",
        "product": {
            "product_id": "linear-primetime-nbc",
            "name": "NBC Primetime :30",
            "inventory_type": "linear_tv",
        },
        "pricing": {
            "base_cpm": 0.0,
            "final_cpm": 0.0,
            "pricing_model": "cpp",
            "base_cpp": 50000.0,
            "final_cpp": 45000.0,
            "currency": "USD",
            "rationale": "Scatter CPP: $50K base, -10% volume => $45K",
        },
        "terms": {
            "grps": 200,
            "guaranteed_grps": 180,
            "target_demo": "A18-49",
            "flight_start": "2026-04-01",
            "flight_end": "2026-04-30",
            "guaranteed": True,
        },
        "availability": {
            "inventory_available": True,
            "estimated_fill_rate": 0.85,
        },
        "buyer_tier": "advertiser",
        "expires_at": "2026-03-15T14:30:00Z",
        "seller_id": "seller-network-001",
        "created_at": "2026-03-11T14:30:00Z",
        "media_type": "linear_tv",
        "linear_tv": {
            "target_demo": "A18-49",
            "estimated_grps": 200.0,
            "estimated_rating": 5.2,
            "cpp": 45000.0,
            "dayparts": ["primetime"],
            "networks": ["NBC"],
            "spots_per_week": 10,
            "total_spots": 40,
            "spot_length": 30,
            "measurement_currency": "nielsen",
            "audience_estimate": {
                "demo": "A18-49",
                "universe": 130000000,
                "impressions_equiv": 65000000,
            },
            "cancellation_terms": {
                "notice_days": 14,
                "cancellable_pct": 1.0,
                "force_majeure": True,
            },
            "makegood_policy": "standard",
        },
    }


def _makegood_response_json() -> dict:
    """Response from POST /api/v1/deals/{deal_id}/makegoods."""
    return {
        "makegood_id": "mg-001",
        "deal_id": "DEAL-LTV-001",
        "status": "offered",
        "shortfall_grps": 30.0,
        "offered_grps": 35.0,
        "offered_daypart": "late_night",
        "offered_network": "NBC",
        "notes": "Replacement inventory in Late Night",
    }


def _cancellation_response_json() -> dict:
    """Response from POST /api/v1/deals/{deal_id}/cancel."""
    return {
        "deal_id": "DEAL-LTV-001",
        "status": "canceled",
        "cancel_pct": 1.0,
        "effective_date": "2026-04-15",
        "penalty": 0.0,
        "notes": "Cancellation within notice period, no penalty",
    }


class _RequestCapture:
    """Helper to capture requests sent through a mock transport."""

    def __init__(self):
        self.requests: list[httpx.Request] = []

    def capture(self, request: httpx.Request) -> None:
        self.requests.append(request)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]


def _make_client_with_transport(handler, **kwargs) -> DealsClient:
    """Create a DealsClient backed by an httpx.MockTransport."""
    c = DealsClient(
        seller_url=SELLER_URL,
        timeout=5.0,
        **kwargs,
    )
    transport = httpx.MockTransport(handler)
    c._client = httpx.AsyncClient(
        transport=transport,
        base_url=SELLER_URL,
        headers=dict(c._client.headers),
        timeout=5.0,
    )
    return c


def _json_response(status_code: int, body: dict) -> httpx.Response:
    """Build an httpx.Response with JSON content."""
    return httpx.Response(status_code=status_code, json=body)


# ---------------------------------------------------------------------------
# Linear TV quote request tests
# ---------------------------------------------------------------------------


class TestLinearTVQuoteRequest:
    """Test requesting a linear TV quote through the client."""

    @pytest.mark.asyncio
    async def test_linear_tv_quote_request_sends_media_type(self):
        """Linear TV quote sends media_type and linear_tv params in body."""
        capture = _RequestCapture()

        def handler(request):
            capture.capture(request)
            return _json_response(200, _linear_tv_quote_response_json())

        c = _make_client_with_transport(handler)
        quote_req = QuoteRequest(
            product_id="linear-primetime-nbc",
            deal_type="scatter",
            media_type="linear_tv",
            flight_start="2026-04-01",
            flight_end="2026-04-30",
            linear_tv=LinearTVParams(
                target_demo="A18-49",
                grps_requested=200,
                dayparts=["primetime"],
                networks=["NBC"],
                target_cpp=50000.0,
            ),
        )
        result = await c.request_quote(quote_req)

        body = json.loads(capture.last.content)
        assert body["media_type"] == "linear_tv"
        assert body["deal_type"] == "scatter"
        assert body["linear_tv"]["target_demo"] == "A18-49"
        assert body["linear_tv"]["grps_requested"] == 200
        await c.close()

    @pytest.mark.asyncio
    async def test_linear_tv_quote_response_parsed(self):
        """Linear TV quote response is parsed with linear_tv details."""

        def handler(request):
            return _json_response(200, _linear_tv_quote_response_json())

        c = _make_client_with_transport(handler)
        quote_req = QuoteRequest(
            product_id="linear-primetime-nbc",
            deal_type="scatter",
            media_type="linear_tv",
            linear_tv=LinearTVParams(target_demo="A18-49"),
        )
        result = await c.request_quote(quote_req)

        assert result.media_type == "linear_tv"
        assert result.linear_tv is not None
        assert result.linear_tv.cpp == 45000.0
        assert result.linear_tv.estimated_grps == 200.0
        assert result.linear_tv.target_demo == "A18-49"
        assert result.linear_tv.spots_per_week == 10
        assert result.linear_tv.cancellation_terms is not None
        assert result.linear_tv.cancellation_terms.notice_days == 14
        assert result.linear_tv.makegood_policy == "standard"
        await c.close()

    @pytest.mark.asyncio
    async def test_linear_tv_terms_parsed(self):
        """Linear TV terms include GRP-based fields."""

        def handler(request):
            return _json_response(200, _linear_tv_quote_response_json())

        c = _make_client_with_transport(handler)
        quote_req = QuoteRequest(
            product_id="linear-primetime-nbc",
            deal_type="scatter",
            media_type="linear_tv",
            linear_tv=LinearTVParams(target_demo="A18-49"),
        )
        result = await c.request_quote(quote_req)

        assert result.terms.grps == 200
        assert result.terms.guaranteed_grps == 180
        assert result.terms.target_demo == "A18-49"
        await c.close()

    @pytest.mark.asyncio
    async def test_linear_tv_pricing_cpp(self):
        """Linear TV pricing uses CPP model."""

        def handler(request):
            return _json_response(200, _linear_tv_quote_response_json())

        c = _make_client_with_transport(handler)
        quote_req = QuoteRequest(
            product_id="linear-primetime-nbc",
            deal_type="scatter",
            media_type="linear_tv",
            linear_tv=LinearTVParams(target_demo="A18-49"),
        )
        result = await c.request_quote(quote_req)

        assert result.pricing.pricing_model == "cpp"
        assert result.pricing.base_cpp == 50000.0
        assert result.pricing.final_cpp == 45000.0
        await c.close()


# ---------------------------------------------------------------------------
# Makegood client tests
# ---------------------------------------------------------------------------


class TestMakegoodClient:
    """Test the makegood request method on DealsClient."""

    @pytest.mark.asyncio
    async def test_request_makegood_success(self):
        """POST /deals/{id}/makegoods returns makegood response."""

        def handler(request):
            return _json_response(200, _makegood_response_json())

        c = _make_client_with_transport(handler)
        mg_req = MakegoodRequest(
            shortfall_grps=30.0,
            original_daypart="primetime",
            target_demo="A18-49",
        )
        result = await c.request_makegood("DEAL-LTV-001", mg_req)

        assert result["makegood_id"] == "mg-001"
        assert result["status"] == "offered"
        assert result["shortfall_grps"] == 30.0
        await c.close()

    @pytest.mark.asyncio
    async def test_request_makegood_correct_url(self):
        """POST is sent to /api/v1/deals/{deal_id}/makegoods."""
        capture = _RequestCapture()

        def handler(request):
            capture.capture(request)
            return _json_response(200, _makegood_response_json())

        c = _make_client_with_transport(handler)
        mg_req = MakegoodRequest(
            shortfall_grps=30.0,
            original_daypart="primetime",
            target_demo="A18-49",
        )
        await c.request_makegood("DEAL-LTV-001", mg_req)

        assert capture.last.method == "POST"
        assert str(capture.last.url).endswith("/api/v1/deals/DEAL-LTV-001/makegoods")
        await c.close()

    @pytest.mark.asyncio
    async def test_request_makegood_sends_body(self):
        """Request body contains makegood fields."""
        capture = _RequestCapture()

        def handler(request):
            capture.capture(request)
            return _json_response(200, _makegood_response_json())

        c = _make_client_with_transport(handler)
        mg_req = MakegoodRequest(
            shortfall_grps=30.0,
            original_daypart="primetime",
            target_demo="A18-49",
            preferred_dayparts=["primetime", "late_night"],
            notes="NBC Thursday underdelivery",
        )
        await c.request_makegood("DEAL-LTV-001", mg_req)

        body = json.loads(capture.last.content)
        assert body["shortfall_grps"] == 30.0
        assert body["original_daypart"] == "primetime"
        assert body["target_demo"] == "A18-49"
        assert body["preferred_dayparts"] == ["primetime", "late_night"]
        await c.close()

    @pytest.mark.asyncio
    async def test_request_makegood_404(self):
        """404 for missing deal raises DealsClientError."""

        def handler(request):
            return _json_response(404, {"error": "deal_not_found", "detail": "Deal not found"})

        c = _make_client_with_transport(handler)
        mg_req = MakegoodRequest(
            shortfall_grps=30.0,
            original_daypart="primetime",
            target_demo="A18-49",
        )
        with pytest.raises(DealsClientError) as exc_info:
            await c.request_makegood("DEAL-NONEXISTENT", mg_req)

        assert exc_info.value.status_code == 404
        await c.close()


# ---------------------------------------------------------------------------
# Cancellation client tests
# ---------------------------------------------------------------------------


class TestCancellationClient:
    """Test the cancellation request method on DealsClient."""

    @pytest.mark.asyncio
    async def test_request_cancellation_success(self):
        """POST /deals/{id}/cancel returns cancellation response."""

        def handler(request):
            return _json_response(200, _cancellation_response_json())

        c = _make_client_with_transport(handler)
        cancel_req = CancellationRequest(
            cancel_pct=1.0,
            reason="Campaign budget cut",
        )
        result = await c.request_cancellation("DEAL-LTV-001", cancel_req)

        assert result["deal_id"] == "DEAL-LTV-001"
        assert result["status"] == "canceled"
        assert result["cancel_pct"] == 1.0
        await c.close()

    @pytest.mark.asyncio
    async def test_request_cancellation_correct_url(self):
        """POST is sent to /api/v1/deals/{deal_id}/cancel."""
        capture = _RequestCapture()

        def handler(request):
            capture.capture(request)
            return _json_response(200, _cancellation_response_json())

        c = _make_client_with_transport(handler)
        cancel_req = CancellationRequest(
            cancel_pct=1.0,
            reason="Budget cut",
        )
        await c.request_cancellation("DEAL-LTV-001", cancel_req)

        assert capture.last.method == "POST"
        assert str(capture.last.url).endswith("/api/v1/deals/DEAL-LTV-001/cancel")
        await c.close()

    @pytest.mark.asyncio
    async def test_request_cancellation_sends_body(self):
        """Request body contains cancellation fields."""
        capture = _RequestCapture()

        def handler(request):
            capture.capture(request)
            return _json_response(200, _cancellation_response_json())

        c = _make_client_with_transport(handler)
        cancel_req = CancellationRequest(
            cancel_pct=0.3,
            reason="Reduced Q2 budget",
            effective_date="2026-06-01",
        )
        await c.request_cancellation("DEAL-LTV-001", cancel_req)

        body = json.loads(capture.last.content)
        assert body["cancel_pct"] == 0.3
        assert body["reason"] == "Reduced Q2 budget"
        assert body["effective_date"] == "2026-06-01"
        await c.close()

    @pytest.mark.asyncio
    async def test_request_cancellation_outside_window(self):
        """422 when cancellation is outside the notice window."""

        def handler(request):
            return _json_response(
                422,
                {
                    "error": "cancellation_window_expired",
                    "detail": "14-day notice period not met",
                },
            )

        c = _make_client_with_transport(handler)
        cancel_req = CancellationRequest(
            cancel_pct=1.0,
            reason="Too late",
        )
        with pytest.raises(DealsClientError) as exc_info:
            await c.request_cancellation("DEAL-LTV-001", cancel_req)

        assert exc_info.value.status_code == 422
        await c.close()

    @pytest.mark.asyncio
    async def test_request_cancellation_404(self):
        """404 for missing deal raises DealsClientError."""

        def handler(request):
            return _json_response(404, {"error": "deal_not_found", "detail": "Deal not found"})

        c = _make_client_with_transport(handler)
        cancel_req = CancellationRequest(
            cancel_pct=1.0,
            reason="Cancel",
        )
        with pytest.raises(DealsClientError) as exc_info:
            await c.request_cancellation("DEAL-NONEXISTENT", cancel_req)

        assert exc_info.value.status_code == 404
        await c.close()
