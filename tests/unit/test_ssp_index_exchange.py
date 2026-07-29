# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for Index Exchange SSP connector.

Tests cover:
- Connector properties (ssp_name, import_source, required config)
- is_configured() with/without env vars
- Constructor argument and env var fallback
- _normalize_deal(): all Index field mappings against the real /v3/deals shape
- _normalize_deal(): deal type derivation from classID + programmaticGuaranteed
  + auctionType (PG, PD, PA — there is no "PMP" DealStore type)
- _normalize_deal(): status normalization (active, paused, unknown)
- _normalize_deal(): pricing (single floor field, PG vs non-PG)
- _normalize_deal(): targeting (structured array, geo keyName filtering)
- _normalize_deal(): missing/null optional fields default to None
- _normalize_deal(): missing required field raises KeyError
- _normalize_deal(): missing/unrecognized classID raises ValueError
- fetch_deals(): happy path with mocked HTTP (MockTransport)
- fetch_deals(): pagination via totalCount / pageOffset
- fetch_deals(): status filter applied in query params, validated
- fetch_deals(): classIDs filter applied in query params
- fetch_deals(): Authorization: Bearer header sent, no seatId param
- fetch_deals(): deduplication within a single fetch
- fetch_deals(): HTTP 401/403 raises SSPAuthError
- fetch_deals(): HTTP 429 raises SSPRateLimitError with retry_after
- fetch_deals(): HTTP 5xx raises SSPConnectionError
- fetch_deals(): network error raises SSPConnectionError
- fetch_deals(): normalization error captured in result, not raised
- test_connection(): success/auth-failure/network-error paths
- Module imports from connectors package
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from ad_buyer.tools.deal_library.connectors.index_exchange import IndexExchangeConnector
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
# Helper to build connector with mocked HTTP
# ---------------------------------------------------------------------------


def _connector_with_transport(
    transport: Any,
    *,
    api_key: str = "test-ix-api-key",
) -> IndexExchangeConnector:
    """Return an IndexExchangeConnector whose HTTP client uses the given transport."""
    connector = IndexExchangeConnector(api_key=api_key)
    connector._client = httpx.Client(transport=transport)
    return connector


# ---------------------------------------------------------------------------
# Properties and configuration
# ---------------------------------------------------------------------------


class TestIndexExchangeConnectorProperties:
    """Tests for connector identity properties."""

    def test_ssp_name(self):
        """ssp_name is 'Index Exchange'."""
        c = IndexExchangeConnector(api_key="key")
        assert c.ssp_name == "Index Exchange"

    def test_import_source(self):
        """import_source is 'INDEX_EXCHANGE'."""
        c = IndexExchangeConnector(api_key="key")
        assert c.import_source == "INDEX_EXCHANGE"

    def test_required_config(self):
        """get_required_config returns only IX_API_KEY — no seat ID."""
        c = IndexExchangeConnector(api_key="key")
        config = c.get_required_config()
        assert config == ["IX_API_KEY"]

    def test_is_configured_false_when_env_vars_missing(self):
        """is_configured returns False when env vars are absent."""
        c = IndexExchangeConnector(api_key="key")
        for var in c.get_required_config():
            os.environ.pop(var, None)
        assert c.is_configured() is False

    def test_is_configured_true_when_env_vars_set(self, monkeypatch):
        """is_configured returns True when IX_API_KEY is set."""
        monkeypatch.setenv("IX_API_KEY", "some-key")
        c = IndexExchangeConnector(api_key="key")
        assert c.is_configured() is True

    def test_constructor_from_env(self, monkeypatch):
        """IndexExchangeConnector() without args reads from env vars."""
        monkeypatch.setenv("IX_API_KEY", "env-key")
        c = IndexExchangeConnector()
        assert c._api_key == "env-key"

    def test_base_url_default(self, monkeypatch):
        """Default base URL is the real Index Deals API endpoint."""
        monkeypatch.delenv("IX_API_URL", raising=False)
        c = IndexExchangeConnector(api_key="key")
        assert c._base_url == "https://app.indexexchange.com/api/deals"

    def test_base_url_from_env(self, monkeypatch):
        """IX_API_URL env var overrides the default (e.g. for staging)."""
        monkeypatch.setenv("IX_API_URL", "https://app.staging.indexexchange.com/api/deals")
        c = IndexExchangeConnector(api_key="key")
        assert c._base_url == "https://app.staging.indexexchange.com/api/deals"


# ---------------------------------------------------------------------------
# _normalize_deal()
# ---------------------------------------------------------------------------


class TestNormalizeDeal:
    """Tests for Index API response field mapping to DealStore schema."""

    def setup_method(self):
        # Explicit base_url so these normalization tests aren't sensitive to
        # a real IX_API_URL set in the developer's own environment/.env.
        self.connector = IndexExchangeConnector(
            api_key="key", base_url="https://app.indexexchange.com/api/deals"
        )

    def _pg_deal(self) -> dict[str, Any]:
        return load_fixture("index_exchange_deals_response.json")["deals"][0]

    def _pd_deal(self) -> dict[str, Any]:
        return load_fixture("index_exchange_deals_response.json")["deals"][1]

    def _mp_deal(self) -> dict[str, Any]:
        return load_fixture("index_exchange_deals_response.json")["deals"][2]

    def _paused_deal(self) -> dict[str, Any]:
        return load_fixture("index_exchange_deals_response.json")["deals"][3]

    # Required fields — identity
    def test_seller_deal_id_mapped(self):
        """externalDealID → seller_deal_id."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["seller_deal_id"] == "IX-PG-2026-001"

    def test_display_name_mapped(self):
        """name → display_name."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["display_name"] == "Premium News PG Package"

    def test_product_id_equals_deal_id(self):
        """product_id is set to the externalDealID."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["product_id"] == "IX-PG-2026-001"

    def test_seller_org_hardcoded(self):
        """seller_org is always 'Index Exchange'."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["seller_org"] == "Index Exchange"

    def test_seller_type_hardcoded(self):
        """seller_type is always 'SSP'."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["seller_type"] == "SSP"

    def test_seller_url_hardcoded(self):
        """seller_url is Index Exchange API base URL."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["seller_url"] == "https://app.indexexchange.com/api/deals"

    def test_seller_domain_always_none(self):
        """seller_domain is always None — no publisherDomain field on this API."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["seller_domain"] is None

    def test_internal_deal_id_preserved_in_description(self):
        """internalDealID is preserved (in description) for future write ops."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["description"] == "44001"

    # Deal type derivation
    def test_deal_type_pg(self):
        """classID=1, programmaticGuaranteed=true → 'PG'."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["deal_type"] == "PG"

    def test_deal_type_pd(self):
        """classID=1, programmaticGuaranteed=false, auctionType=fixed → 'PD'."""
        normalized = self.connector._normalize_deal(self._pd_deal())
        assert normalized["deal_type"] == "PD"

    def test_deal_type_marketplace_package_maps_to_pa(self):
        """classID=4 (Marketplace Package) → 'PA' (no 'PMP' in VALID_DEAL_TYPES)."""
        normalized = self.connector._normalize_deal(self._mp_deal())
        assert normalized["deal_type"] == "PA"

    def test_deal_type_missing_class_id_raises_value_error(self):
        """Missing classID raises ValueError."""
        raw = {k: v for k, v in self._pg_deal().items() if k != "classID"}
        with pytest.raises(ValueError, match="Missing classID"):
            self.connector._normalize_deal(raw)

    def test_deal_type_unrecognized_class_id_raises_value_error(self):
        """Unrecognized classID raises ValueError."""
        raw = {**self._pg_deal(), "classID": 99}
        with pytest.raises(ValueError, match="Unrecognized Index Exchange classID"):
            self.connector._normalize_deal(raw)

    # Status normalization
    def test_status_active_passthrough(self):
        """status 'active' → 'active'."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["status"] == "active"

    def test_status_paused_passthrough(self):
        """status 'paused' → 'paused'."""
        normalized = self.connector._normalize_deal(self._paused_deal())
        assert normalized["status"] == "paused"

    def test_status_expired_maps_to_paused(self):
        """status 'expired' → 'paused' (terminal state, no DealStore equivalent)."""
        raw = {**self._pg_deal(), "status": "expired"}
        normalized = self.connector._normalize_deal(raw)
        assert normalized["status"] == "paused"

    def test_status_auto_paused_maps_to_paused(self):
        """status 'auto-paused' → 'paused'."""
        raw = {**self._pg_deal(), "status": "auto-paused"}
        normalized = self.connector._normalize_deal(raw)
        assert normalized["status"] == "paused"

    def test_status_unknown_defaults_to_paused(self):
        """Unrecognized status defaults to 'paused' — 'imported' is not a
        valid DealStore status."""
        raw = {**self._pg_deal(), "status": "some_weird_status"}
        normalized = self.connector._normalize_deal(raw)
        assert normalized["status"] == "paused"

    # Pricing fields — single `floor`, split by PG vs non-PG
    def test_floor_mapped_to_fixed_cpm_for_pg(self):
        """floor → fixed_price_cpm for PG deals; bid_floor_cpm is None."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["fixed_price_cpm"] == 52.00
        assert normalized["bid_floor_cpm"] is None

    def test_floor_mapped_to_bid_floor_for_non_pg(self):
        """floor → bid_floor_cpm for non-PG deals; fixed_price_cpm is None."""
        normalized = self.connector._normalize_deal(self._pd_deal())
        assert normalized["bid_floor_cpm"] == 14.00
        assert normalized["fixed_price_cpm"] is None

    def test_null_floor_is_none(self):
        """null floor → both price fields are None."""
        normalized = self.connector._normalize_deal(self._mp_deal())
        assert normalized["fixed_price_cpm"] is None
        assert normalized["bid_floor_cpm"] is None

    def test_currency_always_usd(self):
        """currency is always 'USD' — not returned by the API."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["currency"] == "USD"

    # Media type / formats — fields that don't exist on this API
    def test_media_type_always_none(self):
        """media_type is always None — no adType field on this API."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["media_type"] is None

    def test_formats_always_none(self):
        """formats is always None — no formats array on this API."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["formats"] is None

    # Targeting — structured array, filtered to geo keyNames
    def test_geo_targets_collected_from_country_key(self):
        """targeting[].keyName='Country' values → geo_targets, comma-joined."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["geo_targets"] == "US, CA"

    def test_geo_targets_collected_from_domain_key(self):
        """targeting[].keyName='domain' also counts as a geo key."""
        normalized = self.connector._normalize_deal(self._paused_deal())
        assert normalized["geo_targets"] == "example.com"

    def test_empty_targeting_gives_none_geo_targets(self):
        """Empty targeting array → geo_targets is None."""
        normalized = self.connector._normalize_deal(self._pd_deal())
        assert normalized["geo_targets"] is None

    def test_non_geo_targeting_key_ignored(self):
        """A custom, non-geo keyName produces no geo_targets."""
        normalized = self.connector._normalize_deal(self._mp_deal())
        assert normalized["geo_targets"] is None

    def test_content_categories_always_none(self):
        """content_categories is always None — no confirmed standard key."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["content_categories"] is None

    def test_audience_segments_always_none(self):
        """audience_segments is always None — no confirmed standard key."""
        normalized = self.connector._normalize_deal(self._mp_deal())
        assert normalized["audience_segments"] is None

    # Date fields
    def test_start_date_mapped(self):
        """startDate → flight_start."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["flight_start"] == "2026-04-01"

    def test_end_date_mapped(self):
        """endDate → flight_end."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["flight_end"] == "2026-06-30"

    # Impressions — only populated for PG deals, via directConfigurations
    def test_impressions_mapped_from_impression_goal_for_pg(self):
        """directConfigurations.impressionGoal → impressions for PG deal."""
        normalized = self.connector._normalize_deal(self._pg_deal())
        assert normalized["impressions"] == 3000000

    def test_impressions_none_when_no_impression_goal(self):
        """Missing directConfigurations.impressionGoal → impressions is None."""
        normalized = self.connector._normalize_deal(self._pd_deal())
        assert normalized["impressions"] is None

    def test_impressions_none_when_no_direct_configurations(self):
        """Marketplace deals (no directConfigurations) → impressions is None."""
        normalized = self.connector._normalize_deal(self._mp_deal())
        assert normalized["impressions"] is None

    # Required field validation
    def test_missing_external_deal_id_raises_key_error(self):
        """Missing externalDealID raises KeyError."""
        raw = {k: v for k, v in self._pg_deal().items() if k != "externalDealID"}
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
        return _make_response(200, load_fixture("index_exchange_deals_response.json"))

    def test_fetch_deals_returns_ssp_fetch_result(self):
        """fetch_deals() returns an SSPFetchResult."""
        transport = _MockTransport(self._fixture_response())
        connector = _connector_with_transport(transport)
        result = connector.fetch_deals()
        assert isinstance(result, SSPFetchResult)

    def test_fetch_deals_ssp_name(self):
        """fetch_deals() result has ssp_name set to 'Index Exchange'."""
        transport = _MockTransport(self._fixture_response())
        connector = _connector_with_transport(transport)
        result = connector.fetch_deals()
        assert result.ssp_name == "Index Exchange"

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

        pg_deal = next(d for d in result.deals if d["seller_deal_id"] == "IX-PG-2026-001")
        assert pg_deal["deal_type"] == "PG"
        assert pg_deal["fixed_price_cpm"] == 52.00
        assert pg_deal["seller_org"] == "Index Exchange"
        assert pg_deal["seller_type"] == "SSP"

    def test_fetch_deals_empty_response(self):
        """fetch_deals() handles empty deals list."""
        transport = _MockTransport(_make_response(200, {"totalCount": 0, "deals": []}))
        connector = _connector_with_transport(transport)
        result = connector.fetch_deals()
        assert result.successful == 0
        assert result.deals == []


# ---------------------------------------------------------------------------
# fetch_deals() — query parameters / request headers
# ---------------------------------------------------------------------------


class TestFetchDealsRequestParams:
    """Tests that fetch_deals() passes correct params and headers."""

    def _recording_connector(self) -> tuple[IndexExchangeConnector, _RecordingTransport]:
        fixture_data = load_fixture("index_exchange_deals_response.json")
        transport = _RecordingTransport(_make_response(200, fixture_data))
        connector = _connector_with_transport(transport)
        return connector, transport

    def test_status_filter_sent_as_query_param(self):
        """status kwarg is sent as ?status= query param."""
        connector, transport = self._recording_connector()
        connector.fetch_deals(status="active")
        assert transport.last_request is not None
        assert "status=active" in str(transport.last_request.url)

    def test_invalid_status_filter_raises_value_error(self):
        """An unrecognized status filter raises ValueError before any request."""
        connector, transport = self._recording_connector()
        with pytest.raises(ValueError, match="Invalid status filter"):
            connector.fetch_deals(status="inactive")
        assert transport.last_request is None

    def test_class_ids_filter_sent_as_query_param(self):
        """class_ids kwarg is sent as ?classIDs= query param(s)."""
        connector, transport = self._recording_connector()
        connector.fetch_deals(class_ids=[1])
        assert transport.last_request is not None
        assert "classIDs=1" in str(transport.last_request.url)

    def test_multiple_class_ids_sent_as_repeated_param(self):
        """class_ids=[1, 3] produces two classIDs query values."""
        connector, transport = self._recording_connector()
        connector.fetch_deals(class_ids=[1, 3])
        assert transport.last_request is not None
        url_str = str(transport.last_request.url)
        assert "classIDs=1" in url_str
        assert "classIDs=3" in url_str

    def test_account_ids_filter_sent_as_query_param(self):
        """account_ids kwarg is sent as ?accountIDs= query param(s)."""
        connector, transport = self._recording_connector()
        connector.fetch_deals(account_ids=[1470498])
        assert transport.last_request is not None
        assert "accountIDs=1470498" in str(transport.last_request.url)

    def test_multiple_account_ids_sent_as_repeated_param(self):
        """account_ids=[1, 2] produces two accountIDs query values."""
        connector, transport = self._recording_connector()
        connector.fetch_deals(account_ids=[1, 2])
        assert transport.last_request is not None
        url_str = str(transport.last_request.url)
        assert "accountIDs=1" in url_str
        assert "accountIDs=2" in url_str

    def test_no_account_ids_param_when_empty(self):
        """Omitted account_ids does not add an accountIDs query param —
        confirms an unscoped call is possible, not that it's advisable."""
        connector, transport = self._recording_connector()
        connector.fetch_deals()
        assert transport.last_request is not None
        assert "accountIDs" not in str(transport.last_request.url)

    def test_page_size_sent_as_page_size_param(self):
        """page_size kwarg is sent as ?pageSize= query param."""
        connector, transport = self._recording_connector()
        connector.fetch_deals(page_size=50)
        assert transport.last_request is not None
        assert "pageSize=50" in str(transport.last_request.url)

    def test_page_offset_starts_at_zero(self):
        """First request uses pageOffset=0 (0-indexed)."""
        connector, transport = self._recording_connector()
        connector.fetch_deals()
        assert transport.last_request is not None
        assert "pageOffset=0" in str(transport.last_request.url)

    def test_authorization_bearer_header_sent(self):
        """Authorization: Bearer <key> header is included in the request."""
        connector, transport = self._recording_connector()
        connector.fetch_deals()
        assert transport.last_request is not None
        auth_header = transport.last_request.headers.get("authorization", "")
        assert auth_header == "Bearer test-ix-api-key"

    def test_no_x_api_key_header(self):
        """X-API-Key header is never sent — Authorization: Bearer only."""
        connector, transport = self._recording_connector()
        connector.fetch_deals()
        assert transport.last_request is not None
        assert "x-api-key" not in transport.last_request.headers

    def test_no_seat_id_param(self):
        """seatId is never sent — GET /v3/deals has no such parameter."""
        connector, transport = self._recording_connector()
        connector.fetch_deals()
        assert transport.last_request is not None
        assert "seatId" not in str(transport.last_request.url)

    def test_no_status_filter_when_all(self):
        """status='all' does not add status query param."""
        connector, transport = self._recording_connector()
        connector.fetch_deals(status="all")
        assert transport.last_request is not None
        assert "status=all" not in str(transport.last_request.url)

    def test_no_class_ids_param_when_empty(self):
        """Omitted class_ids does not add a classIDs query param."""
        connector, transport = self._recording_connector()
        connector.fetch_deals()
        assert transport.last_request is not None
        assert "classIDs" not in str(transport.last_request.url)


# ---------------------------------------------------------------------------
# fetch_deals() — deduplication
# ---------------------------------------------------------------------------


class TestFetchDealsDeduplication:
    """Tests that fetch_deals() deduplicates by seller_deal_id."""

    def test_duplicate_deal_ids_skipped(self):
        """Duplicate seller_deal_id entries are counted in skipped."""
        fixture_data = load_fixture("index_exchange_deals_response.json")
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
    """Tests for multi-page fetch behavior, driven by totalCount."""

    def test_fetches_multiple_pages(self):
        """fetch_deals() follows pagination until pageOffset*pageSize >= totalCount."""

        def _deal(external_id: str) -> dict[str, Any]:
            return {
                "externalDealID": external_id,
                "internalDealID": 1,
                "name": f"Deal {external_id}",
                "status": "active",
                "classID": 1,
                "floor": 10.0,
                "startDate": "2026-01-01",
                "endDate": "2026-12-31",
                "account": {"accountID": 1},
                "auctionType": "fixed",
                "directConfigurations": {"dspID": 1, "programmaticGuaranteed": True},
                "targeting": [],
            }

        page1 = {"totalCount": 3, "deals": [_deal("IX-001"), _deal("IX-002")]}
        page2 = {"totalCount": 3, "deals": [_deal("IX-003")]}
        transport = _MultiPageTransport(
            _make_response(200, page1),
            _make_response(200, page2),
        )
        connector = _connector_with_transport(transport)
        result = connector.fetch_deals(page_size=2)
        assert result.successful == 3
        assert len(result.deals) == 3

    def test_page_size_capped_at_2000(self):
        """page_size above the API max (2000) is capped, not sent as-is."""
        transport = _RecordingTransport(_make_response(200, {"totalCount": 0, "deals": []}))
        connector = _connector_with_transport(transport)
        connector.fetch_deals(page_size=5000)
        assert transport.last_request is not None
        assert "pageSize=2000" in str(transport.last_request.url)


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
        bad_deal = {"name": "Missing externalDealID — should fail normalization"}
        fixture_data = load_fixture("index_exchange_deals_response.json")
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
        fixture_data = load_fixture("index_exchange_deals_response.json")
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

    def test_index_exchange_connector_importable(self):
        """IndexExchangeConnector can be imported from the connectors package."""
        from ad_buyer.tools.deal_library.connectors.index_exchange import (
            IndexExchangeConnector,  # noqa: F401
        )

    def test_index_exchange_connector_in_connectors_init(self):
        """IndexExchangeConnector is exported from the connectors __init__."""
        from ad_buyer.tools.deal_library.connectors import IndexExchangeConnector  # noqa: F401
