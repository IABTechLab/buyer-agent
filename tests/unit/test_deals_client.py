# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the IAB Deals API v1.0 Client.

Covers all 4 client methods (request_quote, get_quote, book_deal, get_deal),
auth header injection, timeout/retry behavior, response model parsing,
DealStore integration, and error cases.
"""

import json
from unittest.mock import MagicMock

import httpx
import pytest

from ad_buyer.clients.deals_client import DealsClient, DealsClientError
from ad_buyer.models.deals import (
    BuyerIdentityPayload,
    DealBookingRequest,
    DealResponse,
    QuoteRequest,
    QuoteResponse,
    SellerErrorResponse,
)

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

SELLER_URL = "http://seller.example.com"


def _quote_response_json() -> dict:
    """Minimal valid QuoteResponse JSON matching the API contract."""
    return {
        "quote_id": "qt-abc123",
        "status": "available",
        "product": {
            "product_id": "ctv-premium-sports",
            "name": "Premium CTV - Sports",
            "inventory_type": "ctv",
        },
        "pricing": {
            "base_cpm": 35.00,
            "tier_discount_pct": 15.0,
            "volume_discount_pct": 5.0,
            "final_cpm": 28.26,
            "currency": "USD",
            "pricing_model": "cpm",
            "rationale": "Base $35 | -15% tier | -5% volume => $28.26",
        },
        "terms": {
            "impressions": 5000000,
            "flight_start": "2026-04-01",
            "flight_end": "2026-04-30",
            "guaranteed": False,
        },
        "availability": {
            "inventory_available": True,
            "estimated_fill_rate": 0.92,
            "competing_demand": "moderate",
        },
        "buyer_tier": "advertiser",
        "expires_at": "2026-03-09T14:30:00Z",
        "seller_id": "seller-premium-pub-001",
        "created_at": "2026-03-08T14:30:00Z",
    }


def _deal_response_json() -> dict:
    """Minimal valid DealResponse JSON matching the API contract."""
    return {
        "deal_id": "DEMO-A1B2C3D4E5F6",
        "deal_type": "PD",
        "status": "proposed",
        "quote_id": "qt-abc123",
        "product": {
            "product_id": "ctv-premium-sports",
            "name": "Premium CTV - Sports",
            "inventory_type": "ctv",
        },
        "pricing": {
            "base_cpm": 35.00,
            "tier_discount_pct": 15.0,
            "volume_discount_pct": 5.0,
            "final_cpm": 28.26,
            "currency": "USD",
            "pricing_model": "cpm",
            "rationale": "Base $35 | -15% tier | -5% volume => $28.26",
        },
        "terms": {
            "impressions": 5000000,
            "flight_start": "2026-04-01",
            "flight_end": "2026-04-30",
            "guaranteed": False,
        },
        "buyer_tier": "advertiser",
        "expires_at": "2026-04-08T00:00:00Z",
        "activation_instructions": {
            "ttd": "The Trade Desk > Inventory > PMP > Add Deal ID: DEMO-A1B2C3D4E5F6",
            "dv360": "DV360 > Inventory > My Inventory > Deal ID: DEMO-A1B2C3D4E5F6",
        },
        "openrtb_params": {
            "id": "DEMO-A1B2C3D4E5F6",
            "bidfloor": 28.26,
            "bidfloorcur": "USD",
            "at": 3,
            "wseat": [],
            "wadomain": [],
        },
        "created_at": "2026-03-08T14:30:00Z",
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


def _make_client_with_transport(
    handler,
    *,
    api_key: str | None = None,
    bearer_token: str | None = None,
    deal_store=None,
) -> DealsClient:
    """Create a DealsClient backed by an httpx.MockTransport.

    The ``handler`` receives an ``httpx.Request`` and must return an
    ``httpx.Response``.  This is the idiomatic httpx testing pattern.
    """
    c = DealsClient(
        seller_url=SELLER_URL,
        api_key=api_key,
        bearer_token=bearer_token,
        timeout=5.0,
        deal_store=deal_store,
    )
    # Replace the internal client with one using the mock transport
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
    return httpx.Response(
        status_code=status_code,
        json=body,
    )


@pytest.fixture
def client():
    """Create a DealsClient with a no-op transport (for init tests)."""
    return DealsClient(seller_url=SELLER_URL, timeout=5.0)


# ---------------------------------------------------------------------------
# Model parsing tests
# ---------------------------------------------------------------------------


class TestModelParsing:
    """Test that response JSON is parsed into correct Pydantic models."""

    def test_quote_response_parsing(self):
        """QuoteResponse parses all fields from JSON."""
        data = _quote_response_json()
        resp = QuoteResponse.model_validate(data)
        assert resp.quote_id == "qt-abc123"
        assert resp.status == "available"
        assert resp.product.product_id == "ctv-premium-sports"
        assert resp.pricing.final_cpm == 28.26
        assert resp.terms.impressions == 5000000
        assert resp.availability is not None
        assert resp.availability.estimated_fill_rate == 0.92
        assert resp.buyer_tier == "advertiser"

    def test_deal_response_parsing(self):
        """DealResponse parses all fields from JSON."""
        data = _deal_response_json()
        resp = DealResponse.model_validate(data)
        assert resp.deal_id == "DEMO-A1B2C3D4E5F6"
        assert resp.deal_type == "PD"
        assert resp.status == "proposed"
        assert resp.quote_id == "qt-abc123"
        assert resp.product.name == "Premium CTV - Sports"
        assert resp.pricing.base_cpm == 35.00
        assert resp.openrtb_params is not None
        assert resp.openrtb_params.bidfloor == 28.26
        assert "ttd" in resp.activation_instructions

    def test_seller_error_parsing(self):
        """SellerErrorResponse parses error JSON."""
        data = {
            "error": "quote_expired",
            "detail": "Quote expired at 2026-03-09T14:30:00Z",
            "status_code": 410,
        }
        err = SellerErrorResponse.model_validate(data)
        assert err.error == "quote_expired"
        assert err.status_code == 410


# ---------------------------------------------------------------------------
# Client initialization
# ---------------------------------------------------------------------------


class TestClientInit:
    """Test client construction and configuration."""

    def test_default_init(self):
        """Client initializes with seller URL."""
        c = DealsClient(seller_url=SELLER_URL)
        assert c.seller_url == SELLER_URL

    def test_trailing_slash_stripped(self):
        """Trailing slash on seller URL is stripped."""
        c = DealsClient(seller_url=SELLER_URL + "/")
        assert c.seller_url == SELLER_URL

    def test_custom_timeout(self):
        """Custom timeout is stored."""
        c = DealsClient(seller_url=SELLER_URL, timeout=60.0)
        assert c._timeout == 60.0

    def test_api_key_stored(self):
        """API key is stored for auth headers."""
        c = DealsClient(seller_url=SELLER_URL, api_key="secret-key")
        assert c._api_key == "secret-key"


# ---------------------------------------------------------------------------
# Auth header injection
# ---------------------------------------------------------------------------


class TestAuthHeaders:
    """Test that auth headers are injected correctly."""

    @pytest.mark.asyncio
    async def test_api_key_header_injected(self):
        """X-Api-Key header is added when api_key is set."""
        capture = _RequestCapture()

        def handler(request: httpx.Request) -> httpx.Response:
            capture.capture(request)
            return _json_response(200, _quote_response_json())

        c = _make_client_with_transport(handler, api_key="my-key")
        quote_req = QuoteRequest(product_id="test-product", deal_type="PD")
        await c.request_quote(quote_req)

        assert capture.last.headers.get("x-api-key") == "my-key"
        await c.close()

    @pytest.mark.asyncio
    async def test_bearer_token_header_injected(self):
        """Authorization: Bearer header is added when bearer_token is set."""
        capture = _RequestCapture()

        def handler(request: httpx.Request) -> httpx.Response:
            capture.capture(request)
            return _json_response(200, _quote_response_json())

        c = _make_client_with_transport(handler, bearer_token="my-token")
        quote_req = QuoteRequest(product_id="test-product", deal_type="PD")
        await c.request_quote(quote_req)

        assert capture.last.headers.get("authorization") == "Bearer my-token"
        await c.close()

    @pytest.mark.asyncio
    async def test_no_auth_when_no_key(self):
        """No auth headers when neither api_key nor bearer_token is set."""
        capture = _RequestCapture()

        def handler(request: httpx.Request) -> httpx.Response:
            capture.capture(request)
            return _json_response(200, _quote_response_json())

        c = _make_client_with_transport(handler)
        quote_req = QuoteRequest(product_id="test-product", deal_type="PD")
        await c.request_quote(quote_req)

        assert "x-api-key" not in capture.last.headers
        assert "authorization" not in capture.last.headers
        await c.close()


# ---------------------------------------------------------------------------
# request_quote (POST /api/v1/quotes)
# ---------------------------------------------------------------------------


class TestRequestQuote:
    """Test the request_quote method."""

    @pytest.mark.asyncio
    async def test_request_quote_success(self):
        """Successful quote request returns QuoteResponse."""

        def handler(request):
            return _json_response(200, _quote_response_json())

        c = _make_client_with_transport(handler)
        quote_req = QuoteRequest(
            product_id="ctv-premium-sports",
            deal_type="PD",
            impressions=5000000,
            buyer_identity=BuyerIdentityPayload(
                seat_id="seat-ttd-12345",
                agency_id="agency-groupm-001",
            ),
        )
        result = await c.request_quote(quote_req)

        assert isinstance(result, QuoteResponse)
        assert result.quote_id == "qt-abc123"
        assert result.pricing.final_cpm == 28.26
        await c.close()

    @pytest.mark.asyncio
    async def test_request_quote_posts_to_correct_url(self):
        """POST is sent to /api/v1/quotes."""
        capture = _RequestCapture()

        def handler(request):
            capture.capture(request)
            return _json_response(200, _quote_response_json())

        c = _make_client_with_transport(handler)
        quote_req = QuoteRequest(product_id="test", deal_type="PD")
        await c.request_quote(quote_req)

        assert capture.last.method == "POST"
        assert str(capture.last.url).endswith("/api/v1/quotes")
        await c.close()

    @pytest.mark.asyncio
    async def test_request_quote_sends_body(self):
        """Request body contains quote request fields."""
        capture = _RequestCapture()

        def handler(request):
            capture.capture(request)
            return _json_response(200, _quote_response_json())

        c = _make_client_with_transport(handler)
        quote_req = QuoteRequest(
            product_id="ctv-premium-sports",
            deal_type="PG",
            impressions=1000000,
            target_cpm=28.00,
        )
        await c.request_quote(quote_req)

        body = json.loads(capture.last.content)
        assert body["product_id"] == "ctv-premium-sports"
        assert body["deal_type"] == "PG"
        assert body["impressions"] == 1000000
        assert body["target_cpm"] == 28.00
        await c.close()

    @pytest.mark.asyncio
    async def test_request_quote_404_product_not_found(self):
        """404 from seller raises DealsClientError."""
        error_json = {
            "error": "product_not_found",
            "detail": "Product 'bad-id' does not exist",
            "status_code": 404,
        }

        def handler(request):
            return _json_response(404, error_json)

        c = _make_client_with_transport(handler)
        quote_req = QuoteRequest(product_id="bad-id", deal_type="PD")
        with pytest.raises(DealsClientError) as exc_info:
            await c.request_quote(quote_req)

        assert exc_info.value.status_code == 404
        assert "product_not_found" in exc_info.value.error_code
        await c.close()

    @pytest.mark.asyncio
    async def test_request_quote_400_invalid_deal_type(self):
        """400 from seller raises DealsClientError."""
        error_json = {
            "error": "invalid_deal_type",
            "detail": "Deal type not supported",
            "status_code": 400,
        }

        def handler(request):
            return _json_response(400, error_json)

        c = _make_client_with_transport(handler)
        quote_req = QuoteRequest(product_id="test", deal_type="INVALID")
        with pytest.raises(DealsClientError) as exc_info:
            await c.request_quote(quote_req)

        assert exc_info.value.status_code == 400
        await c.close()


# ---------------------------------------------------------------------------
# get_quote (GET /api/v1/quotes/{quote_id})
# ---------------------------------------------------------------------------


class TestGetQuote:
    """Test the get_quote method."""

    @pytest.mark.asyncio
    async def test_get_quote_success(self):
        """Successful quote retrieval returns QuoteResponse."""

        def handler(request):
            return _json_response(200, _quote_response_json())

        c = _make_client_with_transport(handler)
        result = await c.get_quote("qt-abc123")

        assert isinstance(result, QuoteResponse)
        assert result.quote_id == "qt-abc123"
        await c.close()

    @pytest.mark.asyncio
    async def test_get_quote_correct_url(self):
        """GET is sent to /api/v1/quotes/{quote_id}."""
        capture = _RequestCapture()

        def handler(request):
            capture.capture(request)
            return _json_response(200, _quote_response_json())

        c = _make_client_with_transport(handler)
        await c.get_quote("qt-abc123")

        assert capture.last.method == "GET"
        assert str(capture.last.url).endswith("/api/v1/quotes/qt-abc123")
        await c.close()

    @pytest.mark.asyncio
    async def test_get_quote_404(self):
        """404 raises DealsClientError with quote_not_found."""
        error_json = {
            "error": "quote_not_found",
            "detail": "Quote does not exist",
            "status_code": 404,
        }

        def handler(request):
            return _json_response(404, error_json)

        c = _make_client_with_transport(handler)
        with pytest.raises(DealsClientError) as exc_info:
            await c.get_quote("qt-nonexistent")

        assert exc_info.value.status_code == 404
        assert "quote_not_found" in exc_info.value.error_code
        await c.close()

    @pytest.mark.asyncio
    async def test_get_quote_410_expired(self):
        """410 raises DealsClientError with quote_expired."""
        error_json = {
            "error": "quote_expired",
            "detail": "Quote TTL has elapsed",
            "status_code": 410,
        }

        def handler(request):
            return _json_response(410, error_json)

        c = _make_client_with_transport(handler)
        with pytest.raises(DealsClientError) as exc_info:
            await c.get_quote("qt-expired")

        assert exc_info.value.status_code == 410
        assert "quote_expired" in exc_info.value.error_code
        await c.close()


# ---------------------------------------------------------------------------
# book_deal (POST /api/v1/deals)
# ---------------------------------------------------------------------------


class TestBookDeal:
    """Test the book_deal method."""

    @pytest.mark.asyncio
    async def test_book_deal_success(self):
        """Successful booking returns DealResponse."""

        def handler(request):
            return _json_response(201, _deal_response_json())

        c = _make_client_with_transport(handler)
        booking_req = DealBookingRequest(
            quote_id="qt-abc123",
            buyer_identity=BuyerIdentityPayload(
                seat_id="seat-ttd-12345",
                dsp_platform="ttd",
            ),
        )
        result = await c.book_deal(booking_req)

        assert isinstance(result, DealResponse)
        assert result.deal_id == "DEMO-A1B2C3D4E5F6"
        assert result.status == "proposed"
        assert result.openrtb_params is not None
        assert result.openrtb_params.bidfloor == 28.26
        await c.close()

    @pytest.mark.asyncio
    async def test_book_deal_posts_to_correct_url(self):
        """POST is sent to /api/v1/deals."""
        capture = _RequestCapture()

        def handler(request):
            capture.capture(request)
            return _json_response(201, _deal_response_json())

        c = _make_client_with_transport(handler)
        booking_req = DealBookingRequest(quote_id="qt-abc123")
        await c.book_deal(booking_req)

        assert capture.last.method == "POST"
        assert str(capture.last.url).endswith("/api/v1/deals")
        await c.close()

    @pytest.mark.asyncio
    async def test_book_deal_sends_body(self):
        """Request body contains booking fields."""
        capture = _RequestCapture()

        def handler(request):
            capture.capture(request)
            return _json_response(201, _deal_response_json())

        c = _make_client_with_transport(handler)
        booking_req = DealBookingRequest(
            quote_id="qt-abc123",
            buyer_identity=BuyerIdentityPayload(seat_id="seat-1", dsp_platform="ttd"),
            notes="Booking after comparing sellers",
        )
        await c.book_deal(booking_req)

        body = json.loads(capture.last.content)
        assert body["quote_id"] == "qt-abc123"
        assert body["buyer_identity"]["seat_id"] == "seat-1"
        assert body["notes"] == "Booking after comparing sellers"
        await c.close()

    @pytest.mark.asyncio
    async def test_book_deal_410_expired_quote(self):
        """410 for expired quote raises DealsClientError."""
        error_json = {
            "error": "quote_expired",
            "detail": "Quote expired",
            "status_code": 410,
        }

        def handler(request):
            return _json_response(410, error_json)

        c = _make_client_with_transport(handler)
        booking_req = DealBookingRequest(quote_id="qt-expired")
        with pytest.raises(DealsClientError) as exc_info:
            await c.book_deal(booking_req)

        assert exc_info.value.status_code == 410
        await c.close()

    @pytest.mark.asyncio
    async def test_book_deal_409_inventory_gone(self):
        """409 for unavailable inventory raises DealsClientError."""
        error_json = {
            "error": "inventory_no_longer_available",
            "detail": "Inventory booked by another buyer",
            "status_code": 409,
        }

        def handler(request):
            return _json_response(409, error_json)

        c = _make_client_with_transport(handler)
        booking_req = DealBookingRequest(quote_id="qt-abc123")
        with pytest.raises(DealsClientError) as exc_info:
            await c.book_deal(booking_req)

        assert exc_info.value.status_code == 409
        assert "inventory_no_longer_available" in exc_info.value.error_code
        await c.close()

    @pytest.mark.asyncio
    async def test_book_deal_403_identity_mismatch(self):
        """403 for identity mismatch raises DealsClientError."""
        error_json = {
            "error": "buyer_identity_mismatch",
            "detail": "Identity does not match quote requester",
            "status_code": 403,
        }

        def handler(request):
            return _json_response(403, error_json)

        c = _make_client_with_transport(handler)
        booking_req = DealBookingRequest(quote_id="qt-abc123")
        with pytest.raises(DealsClientError) as exc_info:
            await c.book_deal(booking_req)

        assert exc_info.value.status_code == 403
        await c.close()


# ---------------------------------------------------------------------------
# get_deal (GET /api/v1/deals/{deal_id})
# ---------------------------------------------------------------------------


class TestGetDeal:
    """Test the get_deal method."""

    @pytest.mark.asyncio
    async def test_get_deal_success(self):
        """Successful deal retrieval returns DealResponse."""

        def handler(request):
            return _json_response(200, _deal_response_json())

        c = _make_client_with_transport(handler)
        result = await c.get_deal("DEMO-A1B2C3D4E5F6")

        assert isinstance(result, DealResponse)
        assert result.deal_id == "DEMO-A1B2C3D4E5F6"
        assert result.deal_type == "PD"
        await c.close()

    @pytest.mark.asyncio
    async def test_get_deal_correct_url(self):
        """GET is sent to /api/v1/deals/{deal_id}."""
        capture = _RequestCapture()

        def handler(request):
            capture.capture(request)
            return _json_response(200, _deal_response_json())

        c = _make_client_with_transport(handler)
        await c.get_deal("DEMO-A1B2C3D4E5F6")

        assert capture.last.method == "GET"
        assert str(capture.last.url).endswith("/api/v1/deals/DEMO-A1B2C3D4E5F6")
        await c.close()

    @pytest.mark.asyncio
    async def test_get_deal_404(self):
        """404 for missing deal raises DealsClientError."""
        error_json = {
            "error": "deal_not_found",
            "detail": "Deal does not exist",
            "status_code": 404,
        }

        def handler(request):
            return _json_response(404, error_json)

        c = _make_client_with_transport(handler)
        with pytest.raises(DealsClientError) as exc_info:
            await c.get_deal("DEMO-nonexistent")

        assert exc_info.value.status_code == 404
        await c.close()


# ---------------------------------------------------------------------------
# Server error (500) handling
# ---------------------------------------------------------------------------


class TestServerErrors:
    """Test handling of 500 and other server errors."""

    @pytest.mark.asyncio
    async def test_500_raises_deals_client_error(self):
        """500 from seller raises DealsClientError."""

        def handler(request):
            return _json_response(500, {"error": "internal_error", "detail": "Server failed"})

        c = _make_client_with_transport(handler)
        quote_req = QuoteRequest(product_id="test", deal_type="PD")
        with pytest.raises(DealsClientError) as exc_info:
            await c.request_quote(quote_req)

        assert exc_info.value.status_code == 500
        await c.close()

    @pytest.mark.asyncio
    async def test_non_json_error_response(self):
        """Non-JSON error response still raises DealsClientError."""

        def handler(request):
            return httpx.Response(
                status_code=502,
                content=b"Bad Gateway",
                headers={"content-type": "text/plain"},
            )

        c = _make_client_with_transport(handler)
        quote_req = QuoteRequest(product_id="test", deal_type="PD")
        with pytest.raises(DealsClientError) as exc_info:
            await c.request_quote(quote_req)

        assert exc_info.value.status_code == 502
        await c.close()


# ---------------------------------------------------------------------------
# Timeout behavior
# ---------------------------------------------------------------------------


class TestTimeout:
    """Test timeout configuration."""

    @pytest.mark.asyncio
    async def test_timeout_error_raises_deals_client_error(self):
        """httpx.TimeoutException is wrapped in DealsClientError."""

        def handler(request):
            raise httpx.TimeoutException("Connection timed out")

        c = _make_client_with_transport(handler)
        quote_req = QuoteRequest(product_id="test", deal_type="PD")
        with pytest.raises(DealsClientError) as exc_info:
            await c.request_quote(quote_req)

        assert "timeout" in str(exc_info.value).lower() or exc_info.value.status_code == 0
        await c.close()

    @pytest.mark.asyncio
    async def test_connect_error_raises_deals_client_error(self):
        """httpx.ConnectError is wrapped in DealsClientError."""

        def handler(request):
            raise httpx.ConnectError("Connection refused")

        c = _make_client_with_transport(handler)
        quote_req = QuoteRequest(product_id="test", deal_type="PD")
        with pytest.raises(DealsClientError) as exc_info:
            await c.request_quote(quote_req)

        assert exc_info.value.status_code == 0
        await c.close()


# ---------------------------------------------------------------------------
# Retry behavior
# ---------------------------------------------------------------------------


class TestRetry:
    """Test retry logic for transient failures."""

    @pytest.mark.asyncio
    async def test_retry_on_503(self):
        """Client retries on 503 Service Unavailable."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return _json_response(503, {"error": "service_unavailable"})
            return _json_response(200, _quote_response_json())

        c = _make_client_with_transport(handler)
        quote_req = QuoteRequest(product_id="test", deal_type="PD")
        result = await c.request_quote(quote_req)

        assert isinstance(result, QuoteResponse)
        assert call_count == 3  # 2 retries + 1 success
        await c.close()

    @pytest.mark.asyncio
    async def test_no_retry_on_400(self):
        """Client does NOT retry on 400 Bad Request (not transient)."""
        call_count = 0

        def handler(request):
            nonlocal call_count
            call_count += 1
            return _json_response(
                400,
                {
                    "error": "invalid_deal_type",
                    "detail": "Bad request",
                    "status_code": 400,
                },
            )

        c = _make_client_with_transport(handler)
        quote_req = QuoteRequest(product_id="test", deal_type="BAD")
        with pytest.raises(DealsClientError):
            await c.request_quote(quote_req)

        assert call_count == 1  # No retries for client errors
        await c.close()

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises_error(self):
        """When all retries are exhausted, DealsClientError is raised."""

        def handler(request):
            return _json_response(503, {"error": "service_unavailable"})

        c = _make_client_with_transport(handler)
        quote_req = QuoteRequest(product_id="test", deal_type="PD")
        with pytest.raises(DealsClientError) as exc_info:
            await c.request_quote(quote_req)

        assert exc_info.value.status_code == 503
        await c.close()


# ---------------------------------------------------------------------------
# DealStore integration
# ---------------------------------------------------------------------------


class TestDealStoreIntegration:
    """Test integration with DealStore for persistence."""

    @pytest.mark.asyncio
    async def test_request_quote_persists_to_store(self):
        """After requesting a quote, a deal record is saved with status 'quoted'."""
        mock_store = MagicMock()
        mock_store.save_deal.return_value = "deal-uuid-1"

        def handler(request):
            return _json_response(200, _quote_response_json())

        c = _make_client_with_transport(handler, deal_store=mock_store)
        quote_req = QuoteRequest(product_id="ctv-premium-sports", deal_type="PD")
        result = await c.request_quote(quote_req)

        # Verify store was called
        mock_store.save_deal.assert_called_once()
        call_kwargs = mock_store.save_deal.call_args[1]
        assert call_kwargs["product_id"] == "ctv-premium-sports"
        assert call_kwargs["status"] == "quoted"
        assert call_kwargs["price"] == 28.26
        await c.close()

    @pytest.mark.asyncio
    async def test_book_deal_updates_store_to_booked(self):
        """After booking a deal, a deal record is saved with status 'booked'."""
        mock_store = MagicMock()
        mock_store.save_deal.return_value = "deal-uuid-2"

        def handler(request):
            return _json_response(201, _deal_response_json())

        c = _make_client_with_transport(handler, deal_store=mock_store)
        booking_req = DealBookingRequest(quote_id="qt-abc123")
        result = await c.book_deal(booking_req)

        mock_store.save_deal.assert_called_once()
        call_kwargs = mock_store.save_deal.call_args[1]
        assert call_kwargs["seller_deal_id"] == "DEMO-A1B2C3D4E5F6"
        assert call_kwargs["status"] == "booked"
        await c.close()

    @pytest.mark.asyncio
    async def test_no_store_no_error(self):
        """Client works fine without a DealStore attached."""

        def handler(request):
            return _json_response(200, _quote_response_json())

        c = _make_client_with_transport(handler)
        assert c.deal_store is None

        quote_req = QuoteRequest(product_id="test", deal_type="PD")
        result = await c.request_quote(quote_req)

        assert isinstance(result, QuoteResponse)
        await c.close()

    @pytest.mark.asyncio
    async def test_store_error_does_not_fail_request(self):
        """If DealStore raises, the API result is still returned."""
        mock_store = MagicMock()
        mock_store.save_deal.side_effect = Exception("DB connection lost")

        def handler(request):
            return _json_response(200, _quote_response_json())

        c = _make_client_with_transport(handler, deal_store=mock_store)
        quote_req = QuoteRequest(product_id="test", deal_type="PD")
        # Should NOT raise -- store errors are logged but don't break the flow
        result = await c.request_quote(quote_req)

        assert isinstance(result, QuoteResponse)
        await c.close()


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestContextManager:
    """Test async context manager protocol."""

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        """Client works as async context manager."""
        async with DealsClient(seller_url=SELLER_URL) as c:
            assert c is not None
            assert c.seller_url == SELLER_URL
