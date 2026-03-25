# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for the Magnite SSP connector.

Tests cover:
- Connector identity (ssp_name, import_source)
- Configuration (required env vars, is_configured)
- Deal normalization (_normalize_deal) for both streaming and DV+ platforms
- Full fetch_deals() flow using mocked httpx responses
- Auth session management (login -> cookie)
- Error handling: auth failures, rate limits, connection errors
- test_connection() method
- Fixture-driven tests using magnite_deals_response.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# These imports will fail until the module is created (RED phase)
from ad_buyer.tools.deal_library.connectors.magnite import MagniteConnector
from ad_buyer.tools.deal_library.ssp_connector_base import (
    SSPAuthError,
    SSPConnectionError,
    SSPFetchResult,
    SSPRateLimitError,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def magnite_api_response() -> dict:
    """Load the Magnite API fixture response."""
    fixture_path = FIXTURES_DIR / "magnite_deals_response.json"
    with open(fixture_path) as f:
        return json.load(f)


@pytest.fixture
def magnite_deals_list(magnite_api_response) -> list[dict]:
    """Return just the deals list from the fixture."""
    return magnite_api_response["data"]["deals"]


@pytest.fixture
def streaming_connector(monkeypatch) -> MagniteConnector:
    """Magnite Streaming connector with env vars set."""
    monkeypatch.setenv("MAGNITE_ACCESS_KEY", "test-access-key")
    monkeypatch.setenv("MAGNITE_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("MAGNITE_SEAT_ID", "seat-12345")
    return MagniteConnector(platform="streaming")


@pytest.fixture
def dvplus_connector(monkeypatch) -> MagniteConnector:
    """Magnite DV+ connector with env vars set."""
    monkeypatch.setenv("MAGNITE_ACCESS_KEY", "test-access-key")
    monkeypatch.setenv("MAGNITE_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("MAGNITE_SEAT_ID", "seat-12345")
    return MagniteConnector(platform="dv_plus")


@pytest.fixture
def raw_ctv_deal() -> dict:
    """A single raw Magnite Streaming CTV deal (PG)."""
    return {
        "id": "MAG-CTV-001",
        "name": "Roku Premium CTV - Q2 2026",
        "dealType": "PG",
        "status": "active",
        "currency": "USD",
        "price": {"cpm": 35.00, "type": "fixed"},
        "floor": 28.00,
        "impressions": 5000000,
        "startDate": "2026-04-01",
        "endDate": "2026-06-30",
        "mediaType": "CTV",
        "seatId": "seat-12345",
        "publisherId": "pub-roku-001",
        "publisherName": "Roku Channel",
        "publisherDomain": "roku.com",
        "description": "Premium Roku CTV inventory, primetime slots",
        "targeting": {
            "geo": ["US"],
            "contentCategories": ["IAB1", "IAB2"],
            "audiences": ["18-49", "HHI 75k+"],
        },
        "formats": ["video_30sec", "video_15sec"],
        "buyerSeatId": "buyer-seat-abc",
    }


@pytest.fixture
def raw_open_auction_deal() -> dict:
    """A raw Magnite deal with minimal fields (open auction)."""
    return {
        "id": "MAG-CTV-003",
        "name": "Samsung TV Plus Open Auction",
        "dealType": "OPEN_AUCTION",
        "status": "active",
        "currency": "USD",
        "price": {"type": "floor"},
        "floor": 8.00,
        "impressions": None,
        "startDate": None,
        "endDate": None,
        "mediaType": "CTV",
        "seatId": "seat-12345",
        "publisherId": "pub-samsung-001",
        "publisherName": "Samsung TV Plus",
        "publisherDomain": "samsung.com",
        "description": None,
        "targeting": {"geo": ["US"], "contentCategories": [], "audiences": []},
        "formats": ["video_30sec"],
        "buyerSeatId": "buyer-seat-abc",
    }


# ---------------------------------------------------------------------------
# Connector identity tests
# ---------------------------------------------------------------------------


class TestMagniteConnectorIdentity:
    """Tests for ssp_name, import_source, and platform properties."""

    def test_ssp_name_streaming(self, streaming_connector):
        """ssp_name returns 'Magnite' for streaming platform."""
        assert streaming_connector.ssp_name == "Magnite"

    def test_ssp_name_dvplus(self, dvplus_connector):
        """ssp_name returns 'Magnite' for DV+ platform."""
        assert dvplus_connector.ssp_name == "Magnite"

    def test_import_source_streaming(self, streaming_connector):
        """import_source returns 'MAGNITE' for streaming platform."""
        assert streaming_connector.import_source == "MAGNITE"

    def test_import_source_dvplus(self, dvplus_connector):
        """import_source returns 'MAGNITE' for DV+ platform."""
        assert dvplus_connector.import_source == "MAGNITE"

    def test_platform_streaming(self, streaming_connector):
        """platform property returns 'streaming' when set."""
        assert streaming_connector.platform == "streaming"

    def test_platform_dvplus(self, dvplus_connector):
        """platform property returns 'dv_plus' when set."""
        assert dvplus_connector.platform == "dv_plus"

    def test_default_platform_is_streaming(self, monkeypatch):
        """Default platform is 'streaming' when not specified."""
        monkeypatch.setenv("MAGNITE_ACCESS_KEY", "key")
        monkeypatch.setenv("MAGNITE_SECRET_KEY", "secret")
        monkeypatch.setenv("MAGNITE_SEAT_ID", "seat-123")
        connector = MagniteConnector()
        assert connector.platform == "streaming"

    def test_streaming_api_url(self, streaming_connector):
        """Streaming connector uses api.tremorhub.com base URL."""
        assert "tremorhub.com" in streaming_connector.api_base_url

    def test_dvplus_api_url(self, dvplus_connector):
        """DV+ connector uses api.rubiconproject.com base URL."""
        assert "rubiconproject.com" in dvplus_connector.api_base_url

    def test_invalid_platform_raises(self, monkeypatch):
        """Invalid platform raises ValueError on construction."""
        monkeypatch.setenv("MAGNITE_ACCESS_KEY", "key")
        monkeypatch.setenv("MAGNITE_SECRET_KEY", "secret")
        monkeypatch.setenv("MAGNITE_SEAT_ID", "seat-123")
        with pytest.raises(ValueError, match="platform"):
            MagniteConnector(platform="invalid_platform")


# ---------------------------------------------------------------------------
# Configuration tests
# ---------------------------------------------------------------------------


class TestMagniteConnectorConfiguration:
    """Tests for get_required_config() and is_configured()."""

    def test_required_config_includes_access_key(self):
        """Required config includes MAGNITE_ACCESS_KEY."""
        connector = MagniteConnector.__new__(MagniteConnector)
        config = connector.get_required_config()
        assert "MAGNITE_ACCESS_KEY" in config

    def test_required_config_includes_secret_key(self):
        """Required config includes MAGNITE_SECRET_KEY."""
        connector = MagniteConnector.__new__(MagniteConnector)
        config = connector.get_required_config()
        assert "MAGNITE_SECRET_KEY" in config

    def test_required_config_includes_seat_id(self):
        """Required config includes MAGNITE_SEAT_ID."""
        connector = MagniteConnector.__new__(MagniteConnector)
        config = connector.get_required_config()
        assert "MAGNITE_SEAT_ID" in config

    def test_is_configured_false_when_vars_missing(self):
        """is_configured() returns False when env vars not set."""
        connector = MagniteConnector.__new__(MagniteConnector)
        # Remove vars from environment
        for var in ["MAGNITE_ACCESS_KEY", "MAGNITE_SECRET_KEY", "MAGNITE_SEAT_ID"]:
            os.environ.pop(var, None)
        assert connector.is_configured() is False

    def test_is_configured_true_when_all_vars_set(self, streaming_connector):
        """is_configured() returns True when all required env vars are set."""
        assert streaming_connector.is_configured() is True

    def test_platform_env_var_overrides_constructor(self, monkeypatch):
        """MAGNITE_PLATFORM env var sets platform if not passed in constructor."""
        monkeypatch.setenv("MAGNITE_ACCESS_KEY", "key")
        monkeypatch.setenv("MAGNITE_SECRET_KEY", "secret")
        monkeypatch.setenv("MAGNITE_SEAT_ID", "seat-123")
        monkeypatch.setenv("MAGNITE_PLATFORM", "dv_plus")
        connector = MagniteConnector()
        assert connector.platform == "dv_plus"

    def test_constructor_platform_overrides_env(self, monkeypatch):
        """Explicit platform= in constructor overrides MAGNITE_PLATFORM env var."""
        monkeypatch.setenv("MAGNITE_ACCESS_KEY", "key")
        monkeypatch.setenv("MAGNITE_SECRET_KEY", "secret")
        monkeypatch.setenv("MAGNITE_SEAT_ID", "seat-123")
        monkeypatch.setenv("MAGNITE_PLATFORM", "dv_plus")
        connector = MagniteConnector(platform="streaming")
        assert connector.platform == "streaming"


# ---------------------------------------------------------------------------
# Deal normalization tests
# ---------------------------------------------------------------------------


class TestMagniteNormalizeDeal:
    """Tests for _normalize_deal() method."""

    def test_normalize_pg_ctv_deal(self, streaming_connector, raw_ctv_deal):
        """_normalize_deal maps a full PG CTV deal correctly."""
        result = streaming_connector._normalize_deal(raw_ctv_deal)

        assert result["seller_deal_id"] == "MAG-CTV-001"
        assert result["display_name"] == "Roku Premium CTV - Q2 2026"
        assert result["deal_type"] == "PG"
        assert result["seller_org"] == "Roku Channel"
        assert result["seller_domain"] == "roku.com"
        assert result["seller_type"] == "SSP"
        assert result["status"] == "imported"
        assert result["currency"] == "USD"
        assert result["media_type"] == "CTV"
        assert result["fixed_price_cpm"] == 35.00
        assert result["bid_floor_cpm"] == 28.00
        assert result["impressions"] == 5000000
        assert result["flight_start"] == "2026-04-01"
        assert result["flight_end"] == "2026-06-30"
        assert result["description"] == "Premium Roku CTV inventory, primetime slots"

    def test_normalize_sets_seller_url_streaming(self, streaming_connector, raw_ctv_deal):
        """_normalize_deal sets seller_url to Magnite Streaming API URL."""
        result = streaming_connector._normalize_deal(raw_ctv_deal)
        assert "tremorhub.com" in result["seller_url"]

    def test_normalize_sets_seller_url_dvplus(self, dvplus_connector, raw_ctv_deal):
        """_normalize_deal sets seller_url to Magnite DV+ API URL."""
        result = dvplus_connector._normalize_deal(raw_ctv_deal)
        assert "rubiconproject.com" in result["seller_url"]

    def test_normalize_sets_product_id(self, streaming_connector, raw_ctv_deal):
        """_normalize_deal sets product_id to the Magnite deal ID."""
        result = streaming_connector._normalize_deal(raw_ctv_deal)
        assert result["product_id"] == "MAG-CTV-001"

    def test_normalize_open_auction_no_fixed_price(
        self, streaming_connector, raw_open_auction_deal
    ):
        """Open auction deals have no fixed_price_cpm (only floor)."""
        result = streaming_connector._normalize_deal(raw_open_auction_deal)
        assert result["deal_type"] == "OPEN_AUCTION"
        assert result["fixed_price_cpm"] is None
        assert result["bid_floor_cpm"] == 8.00

    def test_normalize_null_dates_become_none(
        self, streaming_connector, raw_open_auction_deal
    ):
        """Null dates in the Magnite response map to None."""
        result = streaming_connector._normalize_deal(raw_open_auction_deal)
        assert result["flight_start"] is None
        assert result["flight_end"] is None

    def test_normalize_null_impressions_becomes_none(
        self, streaming_connector, raw_open_auction_deal
    ):
        """Null impressions in the Magnite response map to None."""
        result = streaming_connector._normalize_deal(raw_open_auction_deal)
        assert result["impressions"] is None

    def test_normalize_null_description_becomes_none(
        self, streaming_connector, raw_open_auction_deal
    ):
        """Null description in the Magnite response maps to None."""
        result = streaming_connector._normalize_deal(raw_open_auction_deal)
        assert result["description"] is None

    def test_normalize_geo_targets_mapped(self, streaming_connector, raw_ctv_deal):
        """Targeting geo array is mapped to geo_targets field."""
        result = streaming_connector._normalize_deal(raw_ctv_deal)
        assert result["geo_targets"] is not None
        assert "US" in result["geo_targets"]

    def test_normalize_formats_mapped(self, streaming_connector, raw_ctv_deal):
        """Formats array is mapped to formats field."""
        result = streaming_connector._normalize_deal(raw_ctv_deal)
        assert result["formats"] is not None
        assert "video_30sec" in result["formats"]

    def test_normalize_content_categories_mapped(self, streaming_connector, raw_ctv_deal):
        """Content categories array is mapped to content_categories field."""
        result = streaming_connector._normalize_deal(raw_ctv_deal)
        assert result["content_categories"] is not None
        assert "IAB1" in result["content_categories"]

    def test_normalize_missing_id_raises(self, streaming_connector):
        """_normalize_deal raises KeyError when deal ID is missing."""
        bad_deal = {"name": "No ID Deal", "dealType": "PD"}
        with pytest.raises(KeyError):
            streaming_connector._normalize_deal(bad_deal)

    def test_normalize_unknown_deal_type_raises(self, streaming_connector, raw_ctv_deal):
        """_normalize_deal raises ValueError for unrecognized deal types."""
        raw_ctv_deal["dealType"] = "UNKNOWN_TYPE"
        with pytest.raises(ValueError, match="deal type"):
            streaming_connector._normalize_deal(raw_ctv_deal)

    def test_normalize_pd_deal(self, streaming_connector):
        """PD deal type is mapped correctly."""
        raw = {
            "id": "MAG-PD-001",
            "name": "Fire TV Sports Package",
            "dealType": "PD",
            "status": "active",
            "currency": "USD",
            "price": {"cpm": 22.50, "type": "fixed"},
            "floor": 18.00,
            "impressions": 2000000,
            "startDate": "2026-05-01",
            "endDate": "2026-07-31",
            "mediaType": "CTV",
            "seatId": "seat-12345",
            "publisherId": "pub-amazon-001",
            "publisherName": "Amazon Fire TV",
            "publisherDomain": "amazon.com",
            "description": None,
            "targeting": {"geo": ["US"], "contentCategories": [], "audiences": []},
            "formats": ["video_30sec"],
            "buyerSeatId": "buyer-seat-abc",
        }
        result = streaming_connector._normalize_deal(raw)
        assert result["deal_type"] == "PD"

    def test_normalized_deal_has_all_required_fields(
        self, streaming_connector, raw_ctv_deal
    ):
        """Normalized deal contains all fields required by DealStore.save_deal()."""
        result = streaming_connector._normalize_deal(raw_ctv_deal)
        required_fields = [
            "seller_url",
            "product_id",
            "deal_type",
            "status",
            "seller_deal_id",
            "display_name",
            "seller_org",
            "seller_type",
        ]
        for field in required_fields:
            assert field in result, f"Missing required field: {field}"


# ---------------------------------------------------------------------------
# fetch_deals() tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestMagniteConnectorFetchDeals:
    """Tests for fetch_deals() with mocked httpx HTTP calls."""

    def _make_auth_response(self, session_cookie: str = "test-session-xyz") -> MagicMock:
        """Build a mock successful auth (login) response."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.cookies = {"SESSION": session_cookie}
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    def _make_deals_response(self, fixture_data: dict) -> MagicMock:
        """Build a mock deals API response from fixture data."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fixture_data
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    def test_fetch_deals_returns_ssp_fetch_result(
        self, streaming_connector, magnite_api_response
    ):
        """fetch_deals() returns an SSPFetchResult instance."""
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = self._make_auth_response()
            mock_client.get.return_value = self._make_deals_response(magnite_api_response)

            result = streaming_connector.fetch_deals()

        assert isinstance(result, SSPFetchResult)

    def test_fetch_deals_returns_correct_count(
        self, streaming_connector, magnite_api_response
    ):
        """fetch_deals() returns all 3 deals from the fixture."""
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = self._make_auth_response()
            mock_client.get.return_value = self._make_deals_response(magnite_api_response)

            result = streaming_connector.fetch_deals()

        assert result.successful == 3
        assert result.failed == 0
        assert result.total_fetched == 3
        assert result.raw_response_count == 3

    def test_fetch_deals_sets_ssp_name(
        self, streaming_connector, magnite_api_response
    ):
        """fetch_deals() sets ssp_name to 'Magnite' in the result."""
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = self._make_auth_response()
            mock_client.get.return_value = self._make_deals_response(magnite_api_response)

            result = streaming_connector.fetch_deals()

        assert result.ssp_name == "Magnite"

    def test_fetch_deals_normalizes_all_deals(
        self, streaming_connector, magnite_api_response
    ):
        """fetch_deals() normalizes all deals in the response."""
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = self._make_auth_response()
            mock_client.get.return_value = self._make_deals_response(magnite_api_response)

            result = streaming_connector.fetch_deals()

        deal_ids = {d["seller_deal_id"] for d in result.deals}
        assert "MAG-CTV-001" in deal_ids
        assert "MAG-CTV-002" in deal_ids
        assert "MAG-CTV-003" in deal_ids

    def test_fetch_deals_posts_to_login_endpoint(
        self, streaming_connector, magnite_api_response
    ):
        """fetch_deals() calls the Magnite login endpoint for session auth."""
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = self._make_auth_response()
            mock_client.get.return_value = self._make_deals_response(magnite_api_response)

            streaming_connector.fetch_deals()

        # Auth login was called
        assert mock_client.post.called
        call_url = mock_client.post.call_args[0][0]
        assert "login" in call_url.lower() or "auth" in call_url.lower()

    def test_fetch_deals_sends_credentials_in_login(
        self, streaming_connector, magnite_api_response
    ):
        """fetch_deals() sends access_key and secret_key in login request."""
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = self._make_auth_response()
            mock_client.get.return_value = self._make_deals_response(magnite_api_response)

            streaming_connector.fetch_deals()

        post_kwargs = mock_client.post.call_args
        # Credentials should be in JSON body or json= kwarg
        body = post_kwargs[1].get("json", {})
        assert "access-key" in body or "access_key" in body or "accessKey" in body

    def test_fetch_deals_uses_seat_id_in_url(
        self, streaming_connector, magnite_api_response
    ):
        """fetch_deals() uses the seat ID in the deals endpoint URL."""
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = self._make_auth_response()
            mock_client.get.return_value = self._make_deals_response(magnite_api_response)

            streaming_connector.fetch_deals()

        get_call_url = mock_client.get.call_args[0][0]
        assert "seat-12345" in get_call_url

    def test_fetch_deals_auth_failure_raises_ssp_auth_error(self, streaming_connector):
        """fetch_deals() raises SSPAuthError on 401 from login endpoint."""
        import httpx

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

            auth_err_resp = MagicMock()
            auth_err_resp.status_code = 401
            mock_client.post.side_effect = httpx.HTTPStatusError(
                "401 Unauthorized",
                request=MagicMock(),
                response=auth_err_resp,
            )

            with pytest.raises(SSPAuthError):
                streaming_connector.fetch_deals()

    def test_fetch_deals_rate_limit_raises_ssp_rate_limit_error(
        self, streaming_connector, magnite_api_response
    ):
        """fetch_deals() raises SSPRateLimitError on 429 from deals endpoint."""
        import httpx

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = self._make_auth_response()

            rate_limit_resp = MagicMock()
            rate_limit_resp.status_code = 429
            rate_limit_resp.headers = {"Retry-After": "60"}
            mock_client.get.side_effect = httpx.HTTPStatusError(
                "429 Too Many Requests",
                request=MagicMock(),
                response=rate_limit_resp,
            )

            with pytest.raises(SSPRateLimitError):
                streaming_connector.fetch_deals()

    def test_fetch_deals_connection_error_raises_ssp_connection_error(
        self, streaming_connector
    ):
        """fetch_deals() raises SSPConnectionError on network failure."""
        import httpx

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.post.side_effect = httpx.ConnectError("Connection refused")

            with pytest.raises(SSPConnectionError):
                streaming_connector.fetch_deals()

    def test_fetch_deals_bad_deal_captured_as_error(
        self, streaming_connector
    ):
        """fetch_deals() captures normalization errors without crashing."""
        bad_response = {
            "data": {
                "deals": [
                    {"id": "VALID-001", "name": "Good Deal", "dealType": "PD",
                     "publisherName": "Test Publisher", "publisherDomain": "test.com",
                     "currency": "USD", "price": {"type": "floor"}, "floor": 5.0,
                     "mediaType": "CTV", "status": "active", "seatId": "seat-12345",
                     "impressions": None, "startDate": None, "endDate": None,
                     "description": None,
                     "targeting": {"geo": [], "contentCategories": [], "audiences": []},
                     "formats": [], "publisherId": "pub-001", "buyerSeatId": "bseat"},
                    {"name": "Missing ID", "dealType": "PD"},  # Missing required id
                ],
                "totalCount": 2,
                "page": 1,
                "pageSize": 100,
            }
        }
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = self._make_auth_response()

            deals_resp = MagicMock()
            deals_resp.status_code = 200
            deals_resp.json.return_value = bad_response
            deals_resp.raise_for_status = MagicMock()
            mock_client.get.return_value = deals_resp

            result = streaming_connector.fetch_deals()

        assert result.successful == 1
        assert result.failed == 1
        assert len(result.errors) == 1

    def test_fetch_deals_empty_response(self, streaming_connector):
        """fetch_deals() handles an empty deals list gracefully."""
        empty_response = {
            "data": {"deals": [], "totalCount": 0, "page": 1, "pageSize": 100}
        }
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = self._make_auth_response()

            empty_resp = MagicMock()
            empty_resp.status_code = 200
            empty_resp.json.return_value = empty_response
            empty_resp.raise_for_status = MagicMock()
            mock_client.get.return_value = empty_resp

            result = streaming_connector.fetch_deals()

        assert result.successful == 0
        assert result.total_fetched == 0
        assert result.deals == []


# ---------------------------------------------------------------------------
# test_connection() tests
# ---------------------------------------------------------------------------


class TestMagniteTestConnection:
    """Tests for test_connection() method."""

    def test_connection_success_returns_true(self, streaming_connector):
        """test_connection() returns True when login succeeds."""
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.cookies = {"SESSION": "test-session"}
            mock_resp.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_resp

            result = streaming_connector.test_connection()

        assert result is True

    def test_connection_auth_failure_returns_false(self, streaming_connector):
        """test_connection() returns False when credentials are invalid."""
        import httpx

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.status_code = 401
            mock_client.post.side_effect = httpx.HTTPStatusError(
                "401 Unauthorized", request=MagicMock(), response=mock_resp
            )

            result = streaming_connector.test_connection()

        assert result is False

    def test_connection_network_error_returns_false(self, streaming_connector):
        """test_connection() returns False on network errors."""
        import httpx

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.post.side_effect = httpx.ConnectError("Unreachable")

            result = streaming_connector.test_connection()

        assert result is False

    def test_connection_returns_false_when_not_configured(self):
        """test_connection() returns False when connector is not configured."""
        connector = MagniteConnector.__new__(MagniteConnector)
        # Don't set env vars
        for var in ["MAGNITE_ACCESS_KEY", "MAGNITE_SECRET_KEY", "MAGNITE_SEAT_ID"]:
            os.environ.pop(var, None)
        result = connector.test_connection()
        assert result is False


# ---------------------------------------------------------------------------
# Fixture-driven integration-style tests
# ---------------------------------------------------------------------------


class TestMagniteFixtureDriven:
    """Tests driven by the magnite_deals_response.json fixture."""

    def test_all_fixture_deals_normalize_successfully(
        self, streaming_connector, magnite_deals_list
    ):
        """All deals in the fixture file can be normalized without error."""
        errors = []
        normalized = []
        for deal in magnite_deals_list:
            try:
                normalized.append(streaming_connector._normalize_deal(deal))
            except Exception as exc:
                errors.append(str(exc))

        assert errors == [], f"Normalization errors: {errors}"
        assert len(normalized) == 3

    def test_fixture_pg_deal_has_fixed_price(
        self, streaming_connector, magnite_deals_list
    ):
        """The PG deal in the fixture has fixed_price_cpm set."""
        pg_deal = next(d for d in magnite_deals_list if d["dealType"] == "PG")
        normalized = streaming_connector._normalize_deal(pg_deal)
        assert normalized["fixed_price_cpm"] == 35.00

    def test_fixture_open_auction_deal_has_no_fixed_price(
        self, streaming_connector, magnite_deals_list
    ):
        """The OPEN_AUCTION deal in the fixture has no fixed_price_cpm."""
        oa_deal = next(d for d in magnite_deals_list if d["dealType"] == "OPEN_AUCTION")
        normalized = streaming_connector._normalize_deal(oa_deal)
        assert normalized["fixed_price_cpm"] is None

    def test_fixture_all_deals_have_ctv_media_type(
        self, streaming_connector, magnite_deals_list
    ):
        """All fixture deals normalize to CTV media type."""
        for deal in magnite_deals_list:
            normalized = streaming_connector._normalize_deal(deal)
            assert normalized["media_type"] == "CTV"

    def test_fixture_all_deals_have_seller_type_ssp(
        self, streaming_connector, magnite_deals_list
    ):
        """All fixture deals have seller_type set to SSP."""
        for deal in magnite_deals_list:
            normalized = streaming_connector._normalize_deal(deal)
            assert normalized["seller_type"] == "SSP"

    def test_fixture_fetch_full_flow(
        self, streaming_connector, magnite_api_response
    ):
        """Full fetch flow using fixture returns 3 successful deals."""
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

            auth_resp = MagicMock()
            auth_resp.status_code = 200
            auth_resp.cookies = {"SESSION": "session-abc"}
            auth_resp.raise_for_status = MagicMock()
            mock_client.post.return_value = auth_resp

            deals_resp = MagicMock()
            deals_resp.status_code = 200
            deals_resp.json.return_value = magnite_api_response
            deals_resp.raise_for_status = MagicMock()
            mock_client.get.return_value = deals_resp

            result = streaming_connector.fetch_deals()

        assert result.successful == 3
        assert result.failed == 0
        assert len(result.deals) == 3
        assert all(d["seller_type"] == "SSP" for d in result.deals)
        assert all(d["status"] == "imported" for d in result.deals)


# ---------------------------------------------------------------------------
# Module export tests
# ---------------------------------------------------------------------------


class TestMagniteModuleExports:
    """Tests that the module and connectors package export correctly."""

    def test_magnite_connector_importable(self):
        """MagniteConnector is importable from connectors.magnite."""
        from ad_buyer.tools.deal_library.connectors.magnite import MagniteConnector  # noqa: F401

    def test_magnite_connector_importable_from_connectors_package(self):
        """MagniteConnector is importable from connectors package __init__."""
        from ad_buyer.tools.deal_library.connectors import MagniteConnector  # noqa: F401

    def test_magnite_is_ssp_connector_subclass(self):
        """MagniteConnector is a subclass of SSPConnector."""
        from ad_buyer.tools.deal_library.ssp_connector_base import SSPConnector
        assert issubclass(MagniteConnector, SSPConnector)
