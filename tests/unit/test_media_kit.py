# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for MediaKitClient — buyer-side media kit discovery.

Tests cover:
- Media kit overview fetching
- Package listing and detail retrieval
- Search with filters
- Multi-seller aggregation (parallel)
- Auth vs public views
- Error handling (seller down, 404, timeouts)
"""

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from ad_buyer.media_kit.client import MediaKitClient
from ad_buyer.media_kit.models import (
    MediaKit,
    MediaKitError,
    PackageDetail,
    PackageSummary,
    SearchFilter,
)


# ---------------------------------------------------------------------------
# Fixtures: sample seller responses
# ---------------------------------------------------------------------------

SELLER_URL = "http://seller1.example.com"
SELLER_URL_2 = "http://seller2.example.com"

SAMPLE_MEDIA_KIT_RESPONSE = {
    "seller_name": "Premium Publisher",
    "total_packages": 2,
    "featured": [
        {
            "package_id": "pkg-001",
            "name": "Sports Live",
            "description": "Live sports inventory",
            "ad_formats": ["video"],
            "device_types": [3],
            "cat": ["IAB19"],
            "cattax": 2,
            "geo_targets": ["US"],
            "tags": ["sports", "live"],
            "price_range": "$28-$42 CPM",
            "rate_type": "cpm",
            "is_featured": True,
        }
    ],
    "all_packages": [
        {
            "package_id": "pkg-001",
            "name": "Sports Live",
            "description": "Live sports inventory",
            "ad_formats": ["video"],
            "device_types": [3],
            "cat": ["IAB19"],
            "cattax": 2,
            "geo_targets": ["US"],
            "tags": ["sports", "live"],
            "price_range": "$28-$42 CPM",
            "rate_type": "cpm",
            "is_featured": True,
        },
        {
            "package_id": "pkg-002",
            "name": "News Display",
            "description": "News display ads",
            "ad_formats": ["banner"],
            "device_types": [2],
            "cat": ["IAB12"],
            "cattax": 2,
            "geo_targets": ["US"],
            "tags": ["news"],
            "price_range": "$10-$18 CPM",
            "rate_type": "cpm",
            "is_featured": False,
        },
    ],
}

SAMPLE_PACKAGES_RESPONSE = {
    "packages": [
        {
            "package_id": "pkg-001",
            "name": "Sports Live",
            "ad_formats": ["video"],
            "device_types": [3],
            "cat": ["IAB19"],
            "cattax": 2,
            "geo_targets": ["US"],
            "tags": ["sports", "live"],
            "price_range": "$28-$42 CPM",
            "rate_type": "cpm",
            "is_featured": True,
        },
    ],
}

SAMPLE_PACKAGE_DETAIL_PUBLIC = {
    "package_id": "pkg-001",
    "name": "Sports Live",
    "description": "Live sports inventory",
    "ad_formats": ["video"],
    "device_types": [3],
    "cat": ["IAB19"],
    "cattax": 2,
    "geo_targets": ["US"],
    "tags": ["sports", "live"],
    "price_range": "$28-$42 CPM",
    "rate_type": "cpm",
    "is_featured": True,
}

SAMPLE_PACKAGE_DETAIL_AUTH = {
    "package_id": "pkg-001",
    "name": "Sports Live",
    "description": "Live sports inventory",
    "ad_formats": ["video"],
    "device_types": [3],
    "cat": ["IAB19"],
    "cattax": 2,
    "geo_targets": ["US"],
    "tags": ["sports", "live"],
    "price_range": "$28-$42 CPM",
    "rate_type": "cpm",
    "is_featured": True,
    "exact_price": 35.0,
    "floor_price": 28.0,
    "currency": "USD",
    "placements": [
        {
            "product_id": "prod-1",
            "product_name": "Live Sports Video",
            "ad_formats": ["video"],
            "device_types": [3],
            "weight": 1.0,
        }
    ],
    "audience_segment_ids": ["3", "5"],
    "negotiation_enabled": True,
    "volume_discounts_available": True,
}

SAMPLE_SEARCH_RESPONSE = {
    "results": [
        {
            "package_id": "pkg-001",
            "name": "Sports Live",
            "ad_formats": ["video"],
            "device_types": [3],
            "cat": ["IAB19"],
            "cattax": 2,
            "geo_targets": ["US"],
            "tags": ["sports", "live"],
            "price_range": "$28-$42 CPM",
            "rate_type": "cpm",
            "is_featured": True,
        }
    ],
}


def _mock_response(json_data: dict, status_code: int = 200) -> httpx.Response:
    """Create a mock httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("GET", "http://test"),
    )


# ---------------------------------------------------------------------------
# Test: get_media_kit
# ---------------------------------------------------------------------------


class TestGetMediaKit:
    """Tests for MediaKitClient.get_media_kit()."""

    @pytest.mark.asyncio
    async def test_fetches_media_kit_overview(self):
        """Should fetch and parse a seller's media kit overview."""
        client = MediaKitClient()
        mock_resp = _mock_response(SAMPLE_MEDIA_KIT_RESPONSE)

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp):
            kit = await client.get_media_kit(SELLER_URL)

        assert isinstance(kit, MediaKit)
        assert kit.seller_url == SELLER_URL
        assert kit.seller_name == "Premium Publisher"
        assert kit.total_packages == 2
        assert len(kit.featured) == 1
        assert len(kit.all_packages) == 2
        assert kit.featured[0].name == "Sports Live"
        assert kit.featured[0].is_featured is True

    @pytest.mark.asyncio
    async def test_sets_seller_url_on_packages(self):
        """All packages should have the seller_url set for tracking."""
        client = MediaKitClient()
        mock_resp = _mock_response(SAMPLE_MEDIA_KIT_RESPONSE)

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp):
            kit = await client.get_media_kit(SELLER_URL)

        for pkg in kit.all_packages:
            assert pkg.seller_url == SELLER_URL

    @pytest.mark.asyncio
    async def test_handles_seller_down(self):
        """Should raise MediaKitError when seller is unreachable."""
        client = MediaKitClient()

        with patch.object(
            client._http,
            "get",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            with pytest.raises(MediaKitError) as exc_info:
                await client.get_media_kit(SELLER_URL)
            assert SELLER_URL in str(exc_info.value.seller_url)

    @pytest.mark.asyncio
    async def test_handles_server_error(self):
        """Should raise MediaKitError on 5xx responses."""
        client = MediaKitClient()
        mock_resp = _mock_response({"error": "Internal Server Error"}, status_code=500)

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(MediaKitError) as exc_info:
                await client.get_media_kit(SELLER_URL)
            assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# Test: list_packages
# ---------------------------------------------------------------------------


class TestListPackages:
    """Tests for MediaKitClient.list_packages()."""

    @pytest.mark.asyncio
    async def test_lists_packages(self):
        """Should return a list of PackageSummary objects."""
        client = MediaKitClient()
        mock_resp = _mock_response(SAMPLE_PACKAGES_RESPONSE)

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp):
            packages = await client.list_packages(SELLER_URL)

        assert len(packages) == 1
        assert isinstance(packages[0], PackageSummary)
        assert packages[0].package_id == "pkg-001"
        assert packages[0].seller_url == SELLER_URL

    @pytest.mark.asyncio
    async def test_passes_query_params(self):
        """Should forward layer and featured_only query params."""
        client = MediaKitClient()
        mock_resp = _mock_response(SAMPLE_PACKAGES_RESPONSE)

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            await client.list_packages(SELLER_URL, layer="curated", featured_only=True)

        # Check that params were passed
        call_kwargs = mock_get.call_args
        assert "params" in call_kwargs.kwargs
        assert call_kwargs.kwargs["params"]["layer"] == "curated"
        assert call_kwargs.kwargs["params"]["featured_only"] is True


# ---------------------------------------------------------------------------
# Test: get_package
# ---------------------------------------------------------------------------


class TestGetPackage:
    """Tests for MediaKitClient.get_package()."""

    @pytest.mark.asyncio
    async def test_gets_package_public(self):
        """Should return a PackageSummary for public (unauthenticated) view."""
        client = MediaKitClient()
        mock_resp = _mock_response(SAMPLE_PACKAGE_DETAIL_PUBLIC)

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp):
            pkg = await client.get_package(SELLER_URL, "pkg-001")

        assert isinstance(pkg, PackageSummary)
        assert pkg.package_id == "pkg-001"

    @pytest.mark.asyncio
    async def test_gets_package_authenticated(self):
        """Should return a PackageDetail when auth fields are present."""
        client = MediaKitClient(api_key="test-key-123")
        mock_resp = _mock_response(SAMPLE_PACKAGE_DETAIL_AUTH)

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp):
            pkg = await client.get_package(SELLER_URL, "pkg-001")

        assert isinstance(pkg, PackageDetail)
        assert pkg.exact_price == 35.0
        assert pkg.floor_price == 28.0
        assert len(pkg.placements) == 1
        assert pkg.placements[0].product_id == "prod-1"
        assert pkg.negotiation_enabled is True

    @pytest.mark.asyncio
    async def test_handles_404(self):
        """Should raise MediaKitError when package not found."""
        client = MediaKitClient()
        mock_resp = _mock_response({"detail": "Package not found"}, status_code=404)

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(MediaKitError) as exc_info:
                await client.get_package(SELLER_URL, "pkg-nonexistent")
            assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Test: search_packages
# ---------------------------------------------------------------------------


class TestSearchPackages:
    """Tests for MediaKitClient.search_packages()."""

    @pytest.mark.asyncio
    async def test_search_with_query(self):
        """Should POST search query and return results."""
        client = MediaKitClient()
        mock_resp = _mock_response(SAMPLE_SEARCH_RESPONSE)

        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_resp):
            results = await client.search_packages(SELLER_URL, query="sports")

        assert len(results) == 1
        assert results[0].name == "Sports Live"
        assert results[0].seller_url == SELLER_URL

    @pytest.mark.asyncio
    async def test_search_with_filters(self):
        """Should include filter fields in POST body."""
        client = MediaKitClient(api_key="test-key")
        mock_resp = _mock_response(SAMPLE_SEARCH_RESPONSE)

        search_filter = SearchFilter(
            query="sports",
            buyer_tier="agency",
            agency_id="agency-1",
        )

        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
            await client.search_packages(SELLER_URL, query="sports", filters=search_filter)

        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs.get("json", {})
        assert body["query"] == "sports"
        assert body["buyer_tier"] == "agency"
        assert body["agency_id"] == "agency-1"

    @pytest.mark.asyncio
    async def test_search_sends_api_key_header(self):
        """Should send X-API-Key header when api_key is configured."""
        client = MediaKitClient(api_key="secret-key")
        mock_resp = _mock_response(SAMPLE_SEARCH_RESPONSE)

        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
            await client.search_packages(SELLER_URL, query="sports")

        call_kwargs = mock_post.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert headers.get("X-API-Key") == "secret-key"


# ---------------------------------------------------------------------------
# Test: aggregate_across_sellers
# ---------------------------------------------------------------------------


class TestAggregateAcrossSellers:
    """Tests for MediaKitClient.aggregate_across_sellers()."""

    @pytest.mark.asyncio
    async def test_aggregates_from_multiple_sellers(self):
        """Should query multiple sellers in parallel and merge results."""
        client = MediaKitClient()

        resp1 = _mock_response({
            "packages": [
                {
                    "package_id": "pkg-s1-001",
                    "name": "Seller 1 Package",
                    "ad_formats": ["banner"],
                    "device_types": [2],
                    "cat": [],
                    "cattax": 2,
                    "geo_targets": ["US"],
                    "tags": [],
                    "price_range": "$10-$15 CPM",
                    "rate_type": "cpm",
                    "is_featured": False,
                }
            ]
        })
        resp2 = _mock_response({
            "packages": [
                {
                    "package_id": "pkg-s2-001",
                    "name": "Seller 2 Package",
                    "ad_formats": ["video"],
                    "device_types": [3],
                    "cat": [],
                    "cattax": 2,
                    "geo_targets": ["UK"],
                    "tags": [],
                    "price_range": "$20-$30 CPM",
                    "rate_type": "cpm",
                    "is_featured": True,
                }
            ]
        })

        call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if SELLER_URL in url:
                return resp1
            return resp2

        with patch.object(client._http, "get", side_effect=mock_get):
            results = await client.aggregate_across_sellers([SELLER_URL, SELLER_URL_2])

        assert len(results) == 2
        seller_urls = {r.seller_url for r in results}
        assert SELLER_URL in seller_urls
        assert SELLER_URL_2 in seller_urls

    @pytest.mark.asyncio
    async def test_aggregate_skips_failed_sellers(self):
        """Should return results from reachable sellers, skip failed ones."""
        client = MediaKitClient()

        resp_ok = _mock_response({
            "packages": [
                {
                    "package_id": "pkg-ok",
                    "name": "OK Package",
                    "ad_formats": [],
                    "device_types": [],
                    "cat": [],
                    "cattax": 2,
                    "geo_targets": [],
                    "tags": [],
                    "price_range": "$10 CPM",
                    "rate_type": "cpm",
                    "is_featured": False,
                }
            ]
        })

        async def mock_get(url, **kwargs):
            if SELLER_URL in url:
                return resp_ok
            raise httpx.ConnectError("Connection refused")

        with patch.object(client._http, "get", side_effect=mock_get):
            results = await client.aggregate_across_sellers([SELLER_URL, SELLER_URL_2])

        assert len(results) == 1
        assert results[0].seller_url == SELLER_URL

    @pytest.mark.asyncio
    async def test_aggregate_empty_sellers_list(self):
        """Should return empty list when no sellers provided."""
        client = MediaKitClient()
        results = await client.aggregate_across_sellers([])
        assert results == []


# ---------------------------------------------------------------------------
# Test: Auth behavior
# ---------------------------------------------------------------------------


class TestAuthBehavior:
    """Tests for API key auth integration."""

    @pytest.mark.asyncio
    async def test_public_client_no_auth_header(self):
        """Client without api_key should not send X-API-Key header."""
        client = MediaKitClient()  # no api_key
        mock_resp = _mock_response(SAMPLE_PACKAGES_RESPONSE)

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            await client.list_packages(SELLER_URL)

        call_kwargs = mock_get.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert "X-API-Key" not in headers

    @pytest.mark.asyncio
    async def test_authenticated_client_sends_header(self):
        """Client with api_key should send X-API-Key on every request."""
        client = MediaKitClient(api_key="my-key")
        mock_resp = _mock_response(SAMPLE_MEDIA_KIT_RESPONSE)

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            await client.get_media_kit(SELLER_URL)

        call_kwargs = mock_get.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert headers["X-API-Key"] == "my-key"


# ---------------------------------------------------------------------------
# Test: Timeout handling
# ---------------------------------------------------------------------------


class TestTimeoutHandling:
    """Tests for request timeout behavior."""

    @pytest.mark.asyncio
    async def test_timeout_raises_error(self):
        """Should raise MediaKitError on timeout."""
        client = MediaKitClient(timeout=5.0)

        with patch.object(
            client._http,
            "get",
            new_callable=AsyncMock,
            side_effect=httpx.TimeoutException("Request timed out"),
        ):
            with pytest.raises(MediaKitError) as exc_info:
                await client.get_media_kit(SELLER_URL)
            assert "timed out" in str(exc_info.value).lower() or "timeout" in str(exc_info.value).lower()
