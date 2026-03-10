# Author: Agent Range
# Donated to IAB Tech Lab

"""Extended tests for media kit services — coverage gaps and edge cases.

Covers lines and branches NOT covered by the existing test_media_kit.py:
- MediaKitClient.get_package: ConnectError / TimeoutException
- MediaKitClient.search_packages: ConnectError / TimeoutException
- MediaKitClient.search_packages: advertiser_id filter
- MediaKitClient.close() and async context manager (__aenter__/__aexit__)
- MediaKitClient._normalize_url: trailing slash stripping
- PackageSummary / PackageDetail model edge cases
- PlacementDetail model
- SearchFilter defaults and full population
- MediaKitError attributes
- Empty response handling
- Partial data / missing fields
"""

import pytest
from unittest.mock import AsyncMock, patch

import httpx

from ad_buyer.media_kit.client import MediaKitClient
from ad_buyer.media_kit.models import (
    MediaKit,
    MediaKitError,
    PackageDetail,
    PackageSummary,
    PlacementDetail,
    SearchFilter,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

SELLER_URL = "http://seller.example.com"


def _mock_response(json_data: dict, status_code: int = 200) -> httpx.Response:
    """Create a real httpx.Response with canned JSON data."""
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("GET", "http://test"),
    )


# =========================================================================
# get_package: connection errors (lines 246-247)
# =========================================================================


class TestGetPackageErrors:
    """Cover get_package ConnectError and TimeoutException paths."""

    @pytest.mark.asyncio
    async def test_get_package_connect_error(self):
        """get_package should raise MediaKitError on ConnectError."""
        client = MediaKitClient()

        with patch.object(
            client._http,
            "get",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            with pytest.raises(MediaKitError) as exc_info:
                await client.get_package(SELLER_URL, "pkg-missing")
            assert SELLER_URL in exc_info.value.seller_url

    @pytest.mark.asyncio
    async def test_get_package_timeout_error(self):
        """get_package should raise MediaKitError on TimeoutException."""
        client = MediaKitClient()

        with patch.object(
            client._http,
            "get",
            new_callable=AsyncMock,
            side_effect=httpx.TimeoutException("Request timed out"),
        ):
            with pytest.raises(MediaKitError) as exc_info:
                await client.get_package(SELLER_URL, "pkg-timeout")
            assert "timed out" in str(exc_info.value).lower() or "timeout" in str(exc_info.value).lower()


# =========================================================================
# search_packages: connection errors (lines 289-290)
# =========================================================================


class TestSearchPackagesErrors:
    """Cover search_packages ConnectError and TimeoutException paths."""

    @pytest.mark.asyncio
    async def test_search_connect_error(self):
        """search_packages should raise MediaKitError on ConnectError."""
        client = MediaKitClient()

        with patch.object(
            client._http,
            "post",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            with pytest.raises(MediaKitError) as exc_info:
                await client.search_packages(SELLER_URL, query="sports")
            assert SELLER_URL in exc_info.value.seller_url

    @pytest.mark.asyncio
    async def test_search_timeout_error(self):
        """search_packages should raise MediaKitError on TimeoutException."""
        client = MediaKitClient()

        with patch.object(
            client._http,
            "post",
            new_callable=AsyncMock,
            side_effect=httpx.TimeoutException("Request timed out"),
        ):
            with pytest.raises(MediaKitError) as exc_info:
                await client.search_packages(SELLER_URL, query="news")
            assert "timed out" in str(exc_info.value).lower() or "timeout" in str(exc_info.value).lower()


# =========================================================================
# search_packages: advertiser_id filter (line 285)
# =========================================================================


class TestSearchPackagesAdvertiserFilter:
    """Cover the advertiser_id branch in search_packages."""

    @pytest.mark.asyncio
    async def test_search_with_advertiser_id(self):
        """Should include advertiser_id in POST body when provided."""
        client = MediaKitClient()
        mock_resp = _mock_response({"results": []})

        search_filter = SearchFilter(
            query="premium",
            buyer_tier="advertiser",
            advertiser_id="adv-12345",
        )

        with patch.object(
            client._http, "post", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_post:
            await client.search_packages(SELLER_URL, query="premium", filters=search_filter)

        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs.get("json", {})
        assert body["advertiser_id"] == "adv-12345"
        assert body["buyer_tier"] == "advertiser"
        assert body["query"] == "premium"

    @pytest.mark.asyncio
    async def test_search_with_both_agency_and_advertiser(self):
        """Should include both agency_id and advertiser_id when both set."""
        client = MediaKitClient(api_key="combo-key")
        mock_resp = _mock_response({"results": []})

        search_filter = SearchFilter(
            query="sports",
            buyer_tier="agency",
            agency_id="agency-A",
            advertiser_id="adv-B",
        )

        with patch.object(
            client._http, "post", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_post:
            await client.search_packages(SELLER_URL, query="sports", filters=search_filter)

        body = mock_post.call_args.kwargs.get("json", {})
        assert body["agency_id"] == "agency-A"
        assert body["advertiser_id"] == "adv-B"

    @pytest.mark.asyncio
    async def test_search_without_optional_ids(self):
        """When agency_id and advertiser_id are None, they should be omitted."""
        client = MediaKitClient()
        mock_resp = _mock_response({"results": []})

        search_filter = SearchFilter(query="display", buyer_tier="public")

        with patch.object(
            client._http, "post", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_post:
            await client.search_packages(SELLER_URL, query="display", filters=search_filter)

        body = mock_post.call_args.kwargs.get("json", {})
        assert "agency_id" not in body
        assert "advertiser_id" not in body
        assert body["buyer_tier"] == "public"


# =========================================================================
# close() and async context manager (lines 329, 332, 335)
# =========================================================================


class TestMediaKitClientLifecycle:
    """Cover close() and async context manager."""

    @pytest.mark.asyncio
    async def test_close(self):
        """close() should call aclose on the HTTP client."""
        client = MediaKitClient()

        with patch.object(client._http, "aclose", new_callable=AsyncMock) as mock_aclose:
            await client.close()
            mock_aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        """Using as async context manager should call close on exit."""
        with patch.object(MediaKitClient, "close", new_callable=AsyncMock) as mock_close:
            async with MediaKitClient() as client:
                assert isinstance(client, MediaKitClient)
            mock_close.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager_returns_self(self):
        """__aenter__ should return the client instance."""
        client = MediaKitClient()
        with patch.object(client._http, "aclose", new_callable=AsyncMock):
            entered = await client.__aenter__()
            assert entered is client
            await client.__aexit__(None, None, None)


# =========================================================================
# _normalize_url
# =========================================================================


class TestNormalizeUrl:
    """Test URL normalization."""

    def test_strips_trailing_slash(self):
        """Should strip trailing slash from seller URL."""
        client = MediaKitClient()
        assert client._normalize_url("http://seller.test/") == "http://seller.test"

    def test_no_trailing_slash(self):
        """Should leave URL unchanged if no trailing slash."""
        client = MediaKitClient()
        assert client._normalize_url("http://seller.test") == "http://seller.test"

    def test_multiple_trailing_slashes(self):
        """Should strip all trailing slashes."""
        client = MediaKitClient()
        assert client._normalize_url("http://seller.test///") == "http://seller.test"


# =========================================================================
# Model edge cases: PackageSummary
# =========================================================================


class TestPackageSummaryModel:
    """Tests for PackageSummary dataclass."""

    def test_minimal_creation(self):
        """PackageSummary with only required fields."""
        pkg = PackageSummary(package_id="pkg-min", name="Minimal Package")
        assert pkg.package_id == "pkg-min"
        assert pkg.name == "Minimal Package"
        assert pkg.description is None
        assert pkg.ad_formats == []
        assert pkg.device_types == []
        assert pkg.cat == []
        assert pkg.cattax == 2
        assert pkg.geo_targets == []
        assert pkg.tags == []
        assert pkg.price_range == ""
        assert pkg.rate_type == "cpm"
        assert pkg.is_featured is False
        assert pkg.seller_url is None

    def test_full_creation(self):
        """PackageSummary with all fields populated."""
        pkg = PackageSummary(
            package_id="pkg-full",
            name="Full Package",
            description="A fully specified package",
            ad_formats=["banner", "video", "native"],
            device_types=[1, 2, 3],
            cat=["IAB1", "IAB19"],
            cattax=7,
            geo_targets=["US", "UK", "CA"],
            tags=["premium", "sports"],
            price_range="$20-$40 CPM",
            rate_type="cpv",
            is_featured=True,
            seller_url="http://seller.test",
        )
        assert pkg.ad_formats == ["banner", "video", "native"]
        assert pkg.cattax == 7
        assert pkg.is_featured is True


# =========================================================================
# Model edge cases: PackageDetail
# =========================================================================


class TestPackageDetailModel:
    """Tests for PackageDetail dataclass."""

    def test_inherits_from_summary(self):
        """PackageDetail should be a subclass of PackageSummary."""
        assert issubclass(PackageDetail, PackageSummary)

    def test_detail_defaults(self):
        """PackageDetail default values for authenticated fields."""
        detail = PackageDetail(package_id="pkg-d", name="Detail Package")
        assert detail.exact_price is None
        assert detail.floor_price is None
        assert detail.currency == "USD"
        assert detail.placements == []
        assert detail.audience_segment_ids == []
        assert detail.negotiation_enabled is False
        assert detail.volume_discounts_available is False

    def test_detail_with_placements(self):
        """PackageDetail with placement details."""
        placements = [
            PlacementDetail(
                product_id="prod-1",
                product_name="Hero Banner",
                ad_formats=["banner"],
                device_types=[2],
                weight=0.7,
            ),
            PlacementDetail(
                product_id="prod-2",
                product_name="Sidebar Video",
                ad_formats=["video"],
                device_types=[2, 3],
                weight=0.3,
            ),
        ]
        detail = PackageDetail(
            package_id="pkg-placements",
            name="Multi-Placement Package",
            exact_price=35.0,
            floor_price=25.0,
            placements=placements,
            negotiation_enabled=True,
        )
        assert len(detail.placements) == 2
        assert detail.placements[0].weight == 0.7
        assert detail.placements[1].product_name == "Sidebar Video"
        assert detail.negotiation_enabled is True


# =========================================================================
# Model edge cases: PlacementDetail
# =========================================================================


class TestPlacementDetailModel:
    """Tests for PlacementDetail dataclass."""

    def test_defaults(self):
        """PlacementDetail with required fields only."""
        p = PlacementDetail(product_id="prod-x", product_name="Test Placement")
        assert p.ad_formats == []
        assert p.device_types == []
        assert p.weight == 1.0

    def test_custom_weight(self):
        """PlacementDetail with custom weight."""
        p = PlacementDetail(
            product_id="prod-w",
            product_name="Weighted",
            weight=0.5,
        )
        assert p.weight == 0.5


# =========================================================================
# Model edge cases: MediaKit
# =========================================================================


class TestMediaKitModel:
    """Tests for MediaKit dataclass."""

    def test_minimal_media_kit(self):
        """MediaKit with only seller_url."""
        kit = MediaKit(seller_url="http://seller.test")
        assert kit.seller_name == ""
        assert kit.total_packages == 0
        assert kit.featured == []
        assert kit.all_packages == []

    def test_full_media_kit(self):
        """MediaKit with all fields populated."""
        pkg = PackageSummary(package_id="pkg-1", name="Test")
        kit = MediaKit(
            seller_url="http://seller.test",
            seller_name="Big Publisher",
            total_packages=5,
            featured=[pkg],
            all_packages=[pkg],
        )
        assert kit.seller_name == "Big Publisher"
        assert kit.total_packages == 5
        assert len(kit.featured) == 1


# =========================================================================
# Model edge cases: SearchFilter
# =========================================================================


class TestSearchFilterModel:
    """Tests for SearchFilter dataclass."""

    def test_defaults(self):
        """SearchFilter with default values."""
        f = SearchFilter()
        assert f.query == ""
        assert f.buyer_tier == "public"
        assert f.agency_id is None
        assert f.advertiser_id is None

    def test_full_filter(self):
        """SearchFilter with all fields set."""
        f = SearchFilter(
            query="sports video",
            buyer_tier="agency",
            agency_id="ag-001",
            advertiser_id="adv-002",
        )
        assert f.query == "sports video"
        assert f.buyer_tier == "agency"
        assert f.agency_id == "ag-001"
        assert f.advertiser_id == "adv-002"


# =========================================================================
# Model edge cases: MediaKitError
# =========================================================================


class TestMediaKitErrorModel:
    """Tests for MediaKitError exception."""

    def test_error_with_defaults(self):
        """MediaKitError with message only."""
        err = MediaKitError("Something went wrong")
        assert str(err) == "Something went wrong"
        assert err.seller_url == ""
        assert err.status_code == 0

    def test_error_with_all_fields(self):
        """MediaKitError with all attributes."""
        err = MediaKitError(
            message="HTTP 500 from seller",
            seller_url="http://seller.test",
            status_code=500,
        )
        assert str(err) == "HTTP 500 from seller"
        assert err.seller_url == "http://seller.test"
        assert err.status_code == 500

    def test_error_is_exception(self):
        """MediaKitError should be a proper Exception subclass."""
        assert issubclass(MediaKitError, Exception)
        with pytest.raises(MediaKitError):
            raise MediaKitError("Test error")


# =========================================================================
# Client parsing: edge cases
# =========================================================================


class TestClientParsing:
    """Test internal parsing methods on MediaKitClient."""

    def test_parse_package_summary(self):
        """_parse_package_summary should create a PackageSummary."""
        client = MediaKitClient()
        data = {
            "package_id": "pkg-parse",
            "name": "Parsed Package",
            "ad_formats": ["banner"],
            "is_featured": True,
        }
        pkg = client._parse_package_summary(data, SELLER_URL)
        assert isinstance(pkg, PackageSummary)
        assert pkg.package_id == "pkg-parse"
        assert pkg.seller_url == SELLER_URL
        assert pkg.is_featured is True

    def test_parse_package_detail(self):
        """_parse_package_detail should create a PackageDetail."""
        client = MediaKitClient()
        data = {
            "package_id": "pkg-det",
            "name": "Detail Parsed",
            "exact_price": 30.0,
            "floor_price": 20.0,
            "placements": [
                {
                    "product_id": "prod-x",
                    "product_name": "Test Product",
                    "ad_formats": ["video"],
                    "weight": 0.8,
                }
            ],
            "audience_segment_ids": ["1", "2"],
            "negotiation_enabled": True,
            "volume_discounts_available": False,
        }
        pkg = client._parse_package_detail(data, SELLER_URL)
        assert isinstance(pkg, PackageDetail)
        assert pkg.exact_price == 30.0
        assert len(pkg.placements) == 1
        assert pkg.placements[0].weight == 0.8

    def test_parse_package_dispatches_to_detail_when_exact_price_present(self):
        """_parse_package should return PackageDetail when exact_price is in data."""
        client = MediaKitClient()
        data = {"package_id": "pkg-disp", "name": "Dispatch Test", "exact_price": 25.0}
        pkg = client._parse_package(data, SELLER_URL)
        assert isinstance(pkg, PackageDetail)

    def test_parse_package_dispatches_to_summary_without_exact_price(self):
        """_parse_package should return PackageSummary when no exact_price."""
        client = MediaKitClient()
        data = {"package_id": "pkg-summ", "name": "Summary Test"}
        pkg = client._parse_package(data, SELLER_URL)
        assert isinstance(pkg, PackageSummary)
        assert not isinstance(pkg, PackageDetail)

    def test_parse_package_summary_empty_data(self):
        """_parse_package_summary with empty dict uses defaults."""
        client = MediaKitClient()
        pkg = client._parse_package_summary({}, SELLER_URL)
        assert pkg.package_id == ""
        assert pkg.name == ""
        assert pkg.description is None
        assert pkg.seller_url == SELLER_URL

    def test_parse_package_detail_empty_placements(self):
        """_parse_package_detail with missing placements returns empty list."""
        client = MediaKitClient()
        data = {"package_id": "pkg-empty", "name": "No Placements"}
        pkg = client._parse_package_detail(data, SELLER_URL)
        assert pkg.placements == []


# =========================================================================
# _handle_response
# =========================================================================


class TestHandleResponse:
    """Test _handle_response error detection."""

    @pytest.mark.asyncio
    async def test_successful_response(self):
        """Should return JSON for 200 response."""
        client = MediaKitClient()
        resp = _mock_response({"key": "value"}, status_code=200)
        data = await client._handle_response(resp, SELLER_URL)
        assert data == {"key": "value"}

    @pytest.mark.asyncio
    async def test_400_response(self):
        """Should raise MediaKitError for 400 status."""
        client = MediaKitClient()
        resp = _mock_response({"error": "bad request"}, status_code=400)
        with pytest.raises(MediaKitError) as exc_info:
            await client._handle_response(resp, SELLER_URL)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_403_response(self):
        """Should raise MediaKitError for 403 status."""
        client = MediaKitClient()
        resp = _mock_response({"error": "forbidden"}, status_code=403)
        with pytest.raises(MediaKitError) as exc_info:
            await client._handle_response(resp, SELLER_URL)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_503_response(self):
        """Should raise MediaKitError for 503 status."""
        client = MediaKitClient()
        resp = _mock_response({"error": "service unavailable"}, status_code=503)
        with pytest.raises(MediaKitError) as exc_info:
            await client._handle_response(resp, SELLER_URL)
        assert exc_info.value.status_code == 503


# =========================================================================
# _build_headers
# =========================================================================


class TestBuildHeaders:
    """Test header building for different auth states."""

    def test_no_api_key(self):
        """Without api_key, headers should be empty."""
        client = MediaKitClient()
        headers = client._build_headers()
        assert headers == {}

    def test_with_api_key(self):
        """With api_key, headers should include X-API-Key."""
        client = MediaKitClient(api_key="test-key-xyz")
        headers = client._build_headers()
        assert headers == {"X-API-Key": "test-key-xyz"}


# =========================================================================
# Empty and partial responses
# =========================================================================


class TestEmptyResponses:
    """Test handling of empty or partial seller responses."""

    @pytest.mark.asyncio
    async def test_media_kit_empty_packages(self):
        """get_media_kit with no packages should return empty lists."""
        client = MediaKitClient()
        mock_resp = _mock_response({
            "seller_name": "Empty Publisher",
            "total_packages": 0,
            "featured": [],
            "all_packages": [],
        })

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp):
            kit = await client.get_media_kit(SELLER_URL)

        assert kit.total_packages == 0
        assert kit.featured == []
        assert kit.all_packages == []

    @pytest.mark.asyncio
    async def test_media_kit_missing_fields(self):
        """get_media_kit with minimal response data."""
        client = MediaKitClient()
        mock_resp = _mock_response({})  # empty response

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp):
            kit = await client.get_media_kit(SELLER_URL)

        assert kit.seller_name == ""
        assert kit.total_packages == 0
        assert kit.featured == []
        assert kit.all_packages == []

    @pytest.mark.asyncio
    async def test_list_packages_empty(self):
        """list_packages with empty packages list."""
        client = MediaKitClient()
        mock_resp = _mock_response({"packages": []})

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp):
            packages = await client.list_packages(SELLER_URL)

        assert packages == []

    @pytest.mark.asyncio
    async def test_search_packages_no_results(self):
        """search_packages with no matching results."""
        client = MediaKitClient()
        mock_resp = _mock_response({"results": []})

        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_resp):
            results = await client.search_packages(SELLER_URL, query="nonexistent")

        assert results == []

    @pytest.mark.asyncio
    async def test_list_packages_missing_packages_key(self):
        """list_packages with no 'packages' key returns empty list."""
        client = MediaKitClient()
        mock_resp = _mock_response({})

        with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp):
            packages = await client.list_packages(SELLER_URL)

        assert packages == []

    @pytest.mark.asyncio
    async def test_search_missing_results_key(self):
        """search_packages with no 'results' key returns empty list."""
        client = MediaKitClient()
        mock_resp = _mock_response({})

        with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_resp):
            results = await client.search_packages(SELLER_URL, query="test")

        assert results == []


# =========================================================================
# list_packages: no query params when not specified
# =========================================================================


class TestListPackagesParams:
    """Test query parameter handling in list_packages."""

    @pytest.mark.asyncio
    async def test_no_params_when_not_specified(self):
        """When neither layer nor featured_only is set, params should be empty."""
        client = MediaKitClient()
        mock_resp = _mock_response({"packages": []})

        with patch.object(
            client._http, "get", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_get:
            await client.list_packages(SELLER_URL)

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params", {})
        assert params == {}

    @pytest.mark.asyncio
    async def test_layer_only_param(self):
        """When only layer is set, only layer should be in params."""
        client = MediaKitClient()
        mock_resp = _mock_response({"packages": []})

        with patch.object(
            client._http, "get", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_get:
            await client.list_packages(SELLER_URL, layer="dynamic")

        params = mock_get.call_args.kwargs.get("params", {})
        assert params["layer"] == "dynamic"
        assert "featured_only" not in params


# =========================================================================
# URL normalization in API calls
# =========================================================================


class TestUrlNormalizationInCalls:
    """Verify URL normalization happens in actual API calls."""

    @pytest.mark.asyncio
    async def test_trailing_slash_stripped_in_get_media_kit(self):
        """get_media_kit should strip trailing slash from URL."""
        client = MediaKitClient()
        mock_resp = _mock_response({
            "seller_name": "Test",
            "total_packages": 0,
            "featured": [],
            "all_packages": [],
        })

        with patch.object(
            client._http, "get", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_get:
            await client.get_media_kit("http://seller.test/")

        # The URL called should have the trailing slash stripped
        called_url = mock_get.call_args[0][0]
        assert called_url == "http://seller.test/media-kit"

    @pytest.mark.asyncio
    async def test_trailing_slash_stripped_in_search(self):
        """search_packages should strip trailing slash from URL."""
        client = MediaKitClient()
        mock_resp = _mock_response({"results": []})

        with patch.object(
            client._http, "post", new_callable=AsyncMock, return_value=mock_resp
        ) as mock_post:
            await client.search_packages("http://seller.test/", query="test")

        called_url = mock_post.call_args[0][0]
        assert called_url == "http://seller.test/media-kit/search"
