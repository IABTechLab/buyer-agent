# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for PubMatic SSP connector.

Tests cover:
- Connector properties (ssp_name, import_source, required config)
- is_configured() with/without env vars
- _normalize_deal(): all PubMatic field mappings
- _normalize_deal(): deal type normalization (PMP→PA, PG→PG, preferred→PD)
- _normalize_deal(): status normalization (active, inactive, pending)
- _normalize_deal(): Targeted PMP audience_segments
- _normalize_deal(): missing/null optional fields default to None
- _normalize_deal(): missing required field raises KeyError
- fetch_deals(): happy path with mocked HTTP (MockTransport)
- fetch_deals(): pagination (multiple pages)
- fetch_deals(): status filter applied in query params
- fetch_deals(): deal_type filter applied in query params
- fetch_deals(): deduplication within a single fetch
- fetch_deals(): HTTP 401 raises SSPAuthError
- fetch_deals(): HTTP 429 raises SSPRateLimitError with retry_after
- fetch_deals(): HTTP 500 raises SSPConnectionError
- fetch_deals(): network error raises SSPConnectionError
- test_connection(): success path
- test_connection(): auth failure path
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from ad_buyer.tools.deal_library.connectors.pubmatic import PubMaticConnector
from ad_buyer.tools.deal_library.ssp_connector_base import (
    SSPAuthError,
    SSPConnectionError,
    SSPFetchResult,
    SSPRateLimitError,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    """Load a JSON fixture file from the fixtures directory."""
    return json.loads((FIXTURES_DIR / name).read_text())


def _make_response(
    status_code: int,
    json_body: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Build an httpx.Response for use in MockTransport."""
    body = json.dumps(json_body or {}).encode()
    return httpx.Response(
        status_code=status_code,
        content=body,
        headers={"content-type": "application/json", **(headers or {})},
    )


class _MockTransport(httpx.BaseTransport):
    """httpx transport that returns a fixed response for any request."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._response


class _MultiPageTransport(httpx.BaseTransport):
    """Transport that returns page1 on first call, page2 on second."""

    def __init__(self, page1: httpx.Response, page2: httpx.Response) -> None:
        self._responses = [page1, page2]
        self._call_count = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        resp = self._responses[min(self._call_count, len(self._responses) - 1)]
        self._call_count += 1
        return resp


class _RecordingTransport(httpx.BaseTransport):
    """Transport that records the request and returns a fixed response."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.last_request: httpx.Request | None = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.last_request = request
        return self._response


# ---------------------------------------------------------------------------
# Helpers to build connector with mocked HTTP
# ---------------------------------------------------------------------------


def _connector_with_transport(
    transport: Any,
    *,
    api_token: str = "test-bearer-token",
    seat_id: str = "seat-123",
) -> PubMaticConnector:
    """Return a PubMaticConnector whose HTTP client uses the given transport."""
    connector = PubMaticConnector(api_token=api_token, seat_id=seat_id)
    connector._client = httpx.Client(transport=transport)
    return connector


# ---------------------------------------------------------------------------
# Properties and configuration
# ---------------------------------------------------------------------------


class TestPubMaticConnectorProperties:
    """Tests for connector identity properties."""

    def test_ssp_name(self):
        """ssp_name is 'PubMatic'."""
        c = PubMaticConnector(api_token="tok", seat_id="seat")
        assert c.ssp_name == "PubMatic"

    def test_import_source(self):
        """import_source is 'PUBMATIC'."""
        c = PubMaticConnector(api_token="tok", seat_id="seat")
        assert c.import_source == "PUBMATIC"

    def test_required_config(self):
        """get_required_config returns PUBMATIC_API_TOKEN and PUBMATIC_SEAT_ID."""
        c = PubMaticConnector(api_token="tok", seat_id="seat")
        config = c.get_required_config()
        assert "PUBMATIC_API_TOKEN" in config
        assert "PUBMATIC_SEAT_ID" in config

    def test_is_configured_false_when_env_vars_missing(self):
        """is_configured returns False when env vars are absent."""
        c = PubMaticConnector(api_token="tok", seat_id="seat")
        for var in c.get_required_config():
            os.environ.pop(var, None)
        assert c.is_configured() is False

    def test_is_configured_true_when_env_vars_set(self, monkeypatch):
        """is_configured returns True when both env vars are set."""
        monkeypatch.setenv("PUBMATIC_API_TOKEN", "some-token")
        monkeypatch.setenv("PUBMATIC_SEAT_ID", "some-seat")
        c = PubMaticConnector(api_token="tok", seat_id="seat")
        assert c.is_configured() is True

    def test_constructor_from_env(self, monkeypatch):
        """PubMaticConnector() without args reads from env vars."""
        monkeypatch.setenv("PUBMATIC_API_TOKEN", "env-token")
        monkeypatch.setenv("PUBMATIC_SEAT_ID", "env-seat")
        c = PubMaticConnector()
        assert c._api_token == "env-token"
        assert c._seat_id == "env-seat"

    def test_base_url_default(self):
        """Default base URL is PubMatic API endpoint."""
        c = PubMaticConnector(api_token="tok", seat_id="seat")
        assert c._base_url == "https://api.pubmatic.com"


# ---------------------------------------------------------------------------
# _normalize_deal()
# ---------------------------------------------------------------------------


class TestNormalizeDeal:
    """Tests for PubMatic API response field mapping to DealStore schema."""

    def setup_method(self):
        self.connector = PubMaticConnector(api_token="tok", seat_id="seat")

    def _pg_deal(self) -> dict[str, Any]:
        return load_fixture("pubmatic_deals_response.json")["deals"][0]

    def _pd_deal(self) -> dict[str, Any]:
        return load_fixture("pubmatic_deals_response.json")["deals"][1]

    def _pmp_targeted_deal(self) -> dict[str, Any]:
        return load_fixture("pubmatic_deals_response.json")["deals"][2]

    def _paused_deal(self) -> dict[str, Any]:
        return load_fixture("pubmatic_deals_response.json")["deals"][3]

    # Required fields
    def test_seller_deal_id_mapped(self):
        """deal_id → seller_deal_id."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["seller_deal_id"] == "PM-PG-2026-001"

    def test_display_name_mapped(self):
        """name → display_name."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["display_name"] == "Premium Sports PG Package"

    def test_seller_org_hardcoded(self):
        """seller_org is always 'PubMatic'."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["seller_org"] == "PubMatic"

    def test_seller_type_hardcoded(self):
        """seller_type is always 'SSP'."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["seller_type"] == "SSP"

    def test_seller_url_hardcoded(self):
        """seller_url is PubMatic API base URL."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["seller_url"] == "https://api.pubmatic.com"

    def test_product_id_equals_deal_id(self):
        """product_id is set to the deal_id."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["product_id"] == "PM-PG-2026-001"

    # Deal type normalization
    def test_deal_type_pg_passthrough(self):
        """deal_type 'PG' → 'PG'."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["deal_type"] == "PG"

    def test_deal_type_preferred_to_pd(self):
        """deal_type 'preferred' → 'PD'."""
        normalized = self.connector._normalize_deal(self._pd_deal())
        assert normalized["deal_type"] == "PD"

    def test_deal_type_pmp_to_pa(self):
        """deal_type 'PMP' → 'PA' (Private Auction)."""
        normalized = self.connector._normalize_deal(self._pmp_targeted_deal())
        assert normalized["deal_type"] == "PA"

    def test_deal_type_unknown_raises(self):
        """Unknown deal_type raises ValueError."""
        raw = {**self._pg_deal(), "deal_type": "UNKNOWN_TYPE"}
        with pytest.raises(ValueError, match="Unrecognized PubMatic deal type"):
            self.connector._normalize_deal(raw)

    # Status normalization
    def test_status_active_passthrough(self):
        """status 'active' → 'active'."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["status"] == "active"

    def test_status_inactive_to_paused(self):
        """status 'inactive' → 'paused'."""
        normalized = self.connector._normalize_deal(self._paused_deal())
        assert normalized["status"] == "paused"

    def test_status_pending_to_imported(self):
        """status 'pending' → 'imported'."""
        raw = {**self._pg_deal(), "status": "pending"}
        normalized = self.connector._normalize_deal(raw)
        assert normalized["status"] == "imported"

    def test_status_unknown_defaults_to_imported(self):
        """Unrecognized status defaults to 'imported'."""
        raw = {**self._pg_deal(), "status": "weird_status"}
        normalized = self.connector._normalize_deal(raw)
        assert normalized["status"] == "imported"

    # Pricing fields
    def test_fixed_cpm_mapped(self):
        """fixed_cpm → fixed_price_cpm (PG deal)."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["fixed_price_cpm"] == 45.00

    def test_floor_price_mapped(self):
        """floor_price → bid_floor_cpm."""
        normalized = self.connector._normalize_deal(self._pd_deal())
        assert normalized["bid_floor_cpm"] == 12.50

    def test_null_floor_price_is_none(self):
        """null floor_price → bid_floor_cpm is None."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["bid_floor_cpm"] is None

    def test_null_fixed_cpm_is_none(self):
        """null fixed_cpm → fixed_price_cpm is None (PMP deal)."""
        normalized = self.connector._normalize_deal(self._pmp_targeted_deal())
        assert normalized["fixed_price_cpm"] is None

    def test_currency_mapped(self):
        """currency field is passed through."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["currency"] == "USD"

    def test_missing_currency_defaults_usd(self):
        """Missing currency field defaults to 'USD'."""
        raw = {k: v for k, v in self._pg_deal().items() if k != "currency"}
        normalized = self.connector._normalize_deal(raw)
        assert normalized["currency"] == "USD"

    # Inventory/targeting fields
    def test_publisher_domain_mapped(self):
        """publisher_domain → seller_domain."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["seller_domain"] == "sports.example.com"

    def test_null_publisher_domain_is_none(self):
        """null publisher_domain → seller_domain is None."""
        normalized = self.connector._normalize_deal(self._pmp_targeted_deal())
        assert normalized["seller_domain"] is None

    def test_formats_serialized_as_json(self):
        """format list → formats as JSON string."""
        import json as json_mod

        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["formats"] is not None
        parsed = json_mod.loads(normalized["formats"])
        assert "display" in parsed
        assert "video" in parsed

    def test_geo_serialized_as_json(self):
        """geo list → geo_targets as JSON string."""
        import json as json_mod

        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["geo_targets"] is not None
        parsed = json_mod.loads(normalized["geo_targets"])
        assert "US" in parsed

    def test_categories_serialized_as_json(self):
        """categories list → content_categories as JSON string."""
        import json as json_mod

        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["content_categories"] is not None
        parsed = json_mod.loads(normalized["content_categories"])
        assert "IAB17" in parsed

    # Targeted PMP — audience_segments
    def test_audience_segments_serialized_as_json(self):
        """audience_segments list → audience_segments as JSON string."""
        import json as json_mod

        normalized = self.connector._normalize_deal(self._pmp_targeted_deal())
        assert normalized["audience_segments"] is not None
        parsed = json_mod.loads(normalized["audience_segments"])
        assert "auto-intender-q1" in parsed

    def test_null_audience_segments_is_none(self):
        """null audience_segments → audience_segments is None."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["audience_segments"] is None

    # Date fields
    def test_start_date_mapped(self):
        """start_date → flight_start."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["flight_start"] == "2026-04-01"

    def test_end_date_mapped(self):
        """end_date → flight_end."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["flight_end"] == "2026-06-30"

    # Impressions
    def test_impressions_mapped_pg(self):
        """impressions mapped for PG deal."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["impressions"] == 5000000

    def test_null_impressions_is_none(self):
        """null impressions → None."""
        normalized = self.connector._normalize_deal(self._pd_deal())
        assert normalized["impressions"] is None

    # Description/notes
    def test_notes_mapped_to_description(self):
        """notes → description."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["description"] == "Premium sports programming package with guaranteed delivery"

    def test_null_notes_is_none(self):
        """null notes → description is None."""
        normalized = self.connector._normalize_deal(self._paused_deal())
        assert normalized["description"] is None

    # Required field validation
    def test_missing_deal_id_raises_key_error(self):
        """Missing deal_id raises KeyError."""
        raw = {k: v for k, v in self._pg_deal().items() if k != "deal_id"}
        with pytest.raises(KeyError):
            self.connector._normalize_deal(raw)

    def test_missing_name_raises_key_error(self):
        """Missing name raises KeyError."""
        raw = {k: v for k, v in self._pg_deal().items() if k != "name"}
        with pytest.raises(KeyError):
            self.connector._normalize_deal(raw)


# ---------------------------------------------------------------------------
# fetch_deals() — happy path with MockTransport
# ---------------------------------------------------------------------------


class TestFetchDealsHappyPath:
    """Tests for fetch_deals() with mocked HTTP responses."""

    def _fixture_response(self) -> httpx.Response:
        return _make_response(200, load_fixture("pubmatic_deals_response.json"))

    def test_fetch_deals_returns_ssp_fetch_result(self):
        """fetch_deals() returns an SSPFetchResult."""
        transport = _MockTransport(self._fixture_response())
        connector = _connector_with_transport(transport)
        result = connector.fetch_deals()
        assert isinstance(result, SSPFetchResult)

    def test_fetch_deals_ssp_name(self):
        """fetch_deals() result has ssp_name set to 'PubMatic'."""
        transport = _MockTransport(self._fixture_response())
        connector = _connector_with_transport(transport)
        result = connector.fetch_deals()
        assert result.ssp_name == "PubMatic"

    def test_fetch_deals_successful_count(self):
        """fetch_deals() normalizes all 4 fixture deals."""
        transport = _MockTransport(self._fixture_response())
        connector = _connector_with_transport(transport)
        result = connector.fetch_deals()
        assert result.successful == 4
        assert result.failed == 0
        assert len(result.deals) == 4

    def test_fetch_deals_raw_response_count(self):
        """fetch_deals() sets raw_response_count to number of deals from API."""
        transport = _MockTransport(self._fixture_response())
        connector = _connector_with_transport(transport)
        result = connector.fetch_deals()
        assert result.raw_response_count == 4

    def test_fetch_deals_deal_fields_correct(self):
        """fetch_deals() produces correctly normalized deals."""
        transport = _MockTransport(self._fixture_response())
        connector = _connector_with_transport(transport)
        result = connector.fetch_deals()

        pg_deal = next(d for d in result.deals if d["seller_deal_id"] == "PM-PG-2026-001")
        assert pg_deal["deal_type"] == "PG"
        assert pg_deal["fixed_price_cpm"] == 45.00
        assert pg_deal["seller_org"] == "PubMatic"
        assert pg_deal["seller_type"] == "SSP"

    def test_fetch_deals_empty_response(self):
        """fetch_deals() handles empty deals list."""
        transport = _MockTransport(
            _make_response(200, {"status": "success", "total": 0, "page": 1, "page_size": 100, "deals": []})
        )
        connector = _connector_with_transport(transport)
        result = connector.fetch_deals()
        assert result.successful == 0
        assert result.deals == []


# ---------------------------------------------------------------------------
# fetch_deals() — query parameters
# ---------------------------------------------------------------------------


class TestFetchDealsQueryParams:
    """Tests that fetch_deals() passes correct query params to the API."""

    def _recording_connector(self) -> tuple[PubMaticConnector, _RecordingTransport]:
        fixture_data = load_fixture("pubmatic_deals_response.json")
        transport = _RecordingTransport(_make_response(200, fixture_data))
        connector = _connector_with_transport(transport)
        return connector, transport

    def test_status_filter_sent_as_query_param(self):
        """status kwarg is sent as ?status= query param."""
        connector, transport = self._recording_connector()
        connector.fetch_deals(status="active")
        assert transport.last_request is not None
        assert "status=active" in str(transport.last_request.url)

    def test_deal_type_filter_sent_as_query_param(self):
        """deal_type kwarg is sent as ?deal_type= query param."""
        connector, transport = self._recording_connector()
        connector.fetch_deals(deal_type="PG")
        assert transport.last_request is not None
        assert "deal_type=PG" in str(transport.last_request.url)

    def test_page_size_sent_as_query_param(self):
        """page_size kwarg is sent as ?page_size= query param."""
        connector, transport = self._recording_connector()
        connector.fetch_deals(page_size=50)
        assert transport.last_request is not None
        assert "page_size=50" in str(transport.last_request.url)

    def test_auth_header_sent(self):
        """Authorization: Bearer header is included in the request."""
        connector, transport = self._recording_connector()
        connector.fetch_deals()
        assert transport.last_request is not None
        auth = transport.last_request.headers.get("authorization", "")
        assert auth == "Bearer test-bearer-token"

    def test_no_status_filter_when_all(self):
        """status='all' does not add status query param."""
        connector, transport = self._recording_connector()
        connector.fetch_deals(status="all")
        assert transport.last_request is not None
        # "status=all" should NOT be sent — the API returns all by default
        assert "status=all" not in str(transport.last_request.url)


# ---------------------------------------------------------------------------
# fetch_deals() — deduplication
# ---------------------------------------------------------------------------


class TestFetchDealsDeduplication:
    """Tests that fetch_deals() deduplicates by seller_deal_id."""

    def test_duplicate_deal_ids_skipped(self):
        """Duplicate seller_deal_id entries are counted in skipped."""
        fixture_data = load_fixture("pubmatic_deals_response.json")
        # Duplicate the first deal
        duplicated_deal = fixture_data["deals"][0].copy()
        fixture_data = {**fixture_data, "deals": fixture_data["deals"] + [duplicated_deal]}

        transport = _MockTransport(_make_response(200, fixture_data))
        connector = _connector_with_transport(transport)
        result = connector.fetch_deals()

        assert result.successful == 4  # 4 unique deals
        assert result.skipped == 1  # 1 duplicate skipped
        assert len(result.deals) == 4


# ---------------------------------------------------------------------------
# fetch_deals() — pagination
# ---------------------------------------------------------------------------


class TestFetchDealsPagination:
    """Tests for multi-page fetch behavior."""

    def test_fetches_multiple_pages(self):
        """fetch_deals() follows pagination until no more pages."""
        page1 = {
            "status": "success",
            "total": 3,
            "page": 1,
            "page_size": 2,
            "deals": [
                {
                    "deal_id": "PM-001",
                    "name": "Deal 1",
                    "status": "active",
                    "deal_type": "PG",
                    "floor_price": None,
                    "fixed_cpm": 10.0,
                    "currency": "USD",
                    "publisher_domain": "pub1.example.com",
                    "format": ["display"],
                    "geo": ["US"],
                    "categories": ["IAB1"],
                    "audience_segments": None,
                    "start_date": "2026-01-01",
                    "end_date": "2026-12-31",
                    "impressions": 1000000,
                    "notes": None,
                },
                {
                    "deal_id": "PM-002",
                    "name": "Deal 2",
                    "status": "active",
                    "deal_type": "PMP",
                    "floor_price": 5.0,
                    "fixed_cpm": None,
                    "currency": "USD",
                    "publisher_domain": None,
                    "format": ["display"],
                    "geo": ["US"],
                    "categories": ["IAB2"],
                    "audience_segments": None,
                    "start_date": "2026-01-01",
                    "end_date": "2026-12-31",
                    "impressions": None,
                    "notes": None,
                },
            ],
        }
        page2 = {
            "status": "success",
            "total": 3,
            "page": 2,
            "page_size": 2,
            "deals": [
                {
                    "deal_id": "PM-003",
                    "name": "Deal 3",
                    "status": "active",
                    "deal_type": "preferred",
                    "floor_price": 3.0,
                    "fixed_cpm": 8.0,
                    "currency": "USD",
                    "publisher_domain": "pub3.example.com",
                    "format": ["display"],
                    "geo": ["US"],
                    "categories": ["IAB3"],
                    "audience_segments": None,
                    "start_date": "2026-01-01",
                    "end_date": "2026-12-31",
                    "impressions": None,
                    "notes": None,
                }
            ],
        }
        transport = _MultiPageTransport(
            _make_response(200, page1),
            _make_response(200, page2),
        )
        connector = _connector_with_transport(transport)
        # page_size=2 matches the fixture data so pagination triggers correctly
        result = connector.fetch_deals(page_size=2)
        assert result.successful == 3
        assert len(result.deals) == 3


# ---------------------------------------------------------------------------
# fetch_deals() — HTTP error handling
# ---------------------------------------------------------------------------


class TestFetchDealsErrorHandling:
    """Tests that fetch_deals() raises correct errors for HTTP failures."""

    def test_http_401_raises_ssp_auth_error(self):
        """HTTP 401 raises SSPAuthError."""
        transport = _MockTransport(_make_response(401, {"error": "Unauthorized"}))
        connector = _connector_with_transport(transport)
        with pytest.raises(SSPAuthError):
            connector.fetch_deals()

    def test_http_403_raises_ssp_auth_error(self):
        """HTTP 403 raises SSPAuthError."""
        transport = _MockTransport(_make_response(403, {"error": "Forbidden"}))
        connector = _connector_with_transport(transport)
        with pytest.raises(SSPAuthError):
            connector.fetch_deals()

    def test_http_429_raises_ssp_rate_limit_error(self):
        """HTTP 429 raises SSPRateLimitError."""
        transport = _MockTransport(
            _make_response(429, {"error": "Too Many Requests"}, headers={"Retry-After": "30"})
        )
        connector = _connector_with_transport(transport)
        with pytest.raises(SSPRateLimitError):
            connector.fetch_deals()

    def test_http_429_retry_after_parsed(self):
        """HTTP 429 SSPRateLimitError carries retry_after from header."""
        transport = _MockTransport(
            _make_response(429, {"error": "Rate limited"}, headers={"Retry-After": "60"})
        )
        connector = _connector_with_transport(transport)
        with pytest.raises(SSPRateLimitError) as exc_info:
            connector.fetch_deals()
        assert exc_info.value.retry_after == 60

    def test_http_500_raises_ssp_connection_error(self):
        """HTTP 500 raises SSPConnectionError."""
        transport = _MockTransport(_make_response(500, {"error": "Internal Server Error"}))
        connector = _connector_with_transport(transport)
        with pytest.raises(SSPConnectionError):
            connector.fetch_deals()

    def test_http_503_raises_ssp_connection_error(self):
        """HTTP 503 raises SSPConnectionError."""
        transport = _MockTransport(_make_response(503, {"error": "Service Unavailable"}))
        connector = _connector_with_transport(transport)
        with pytest.raises(SSPConnectionError):
            connector.fetch_deals()

    def test_network_error_raises_ssp_connection_error(self):
        """Network-level errors are wrapped in SSPConnectionError."""

        class _ErrorTransport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                raise httpx.ConnectError("Connection refused")

        connector = _connector_with_transport(_ErrorTransport())
        with pytest.raises(SSPConnectionError):
            connector.fetch_deals()

    def test_normalization_error_captured_not_raised(self):
        """Deals that fail normalization are counted in failed, not raised."""
        bad_deal = {"name": "Missing deal_id — should fail normalization"}
        fixture_data = load_fixture("pubmatic_deals_response.json")
        fixture_data = {**fixture_data, "deals": fixture_data["deals"] + [bad_deal]}

        transport = _MockTransport(_make_response(200, fixture_data))
        connector = _connector_with_transport(transport)
        result = connector.fetch_deals()

        assert result.failed == 1
        assert result.successful == 4
        assert len(result.errors) == 1


# ---------------------------------------------------------------------------
# test_connection()
# ---------------------------------------------------------------------------


class TestTestConnection:
    """Tests for the test_connection() method."""

    def test_connection_success_returns_true(self):
        """test_connection() returns True when API responds 200."""
        fixture_data = load_fixture("pubmatic_deals_response.json")
        transport = _MockTransport(_make_response(200, fixture_data))
        connector = _connector_with_transport(transport)
        assert connector.test_connection() is True

    def test_connection_auth_failure_returns_false(self):
        """test_connection() returns False (not raises) on 401."""
        transport = _MockTransport(_make_response(401, {"error": "Unauthorized"}))
        connector = _connector_with_transport(transport)
        assert connector.test_connection() is False

    def test_connection_network_error_returns_false(self):
        """test_connection() returns False on network error."""

        class _ErrorTransport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                raise httpx.ConnectError("No route to host")

        connector = _connector_with_transport(_ErrorTransport())
        assert connector.test_connection() is False


# ---------------------------------------------------------------------------
# Module imports
# ---------------------------------------------------------------------------


class TestModuleImports:
    """Tests that the module and class are importable."""

    def test_pubmatic_connector_importable(self):
        """PubMaticConnector can be imported from the connectors package."""
        from ad_buyer.tools.deal_library.connectors.pubmatic import PubMaticConnector  # noqa: F401

    def test_pubmatic_connector_in_connectors_init(self):
        """PubMaticConnector is exported from the connectors __init__."""
        from ad_buyer.tools.deal_library.connectors import PubMaticConnector  # noqa: F401
