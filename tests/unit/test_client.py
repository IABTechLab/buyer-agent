# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for OpenDirect client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from iab_agentic_primitives.primitives import Product as WireProduct

from ad_buyer.clients.opendirect_client import OpenDirectClient, _filter_wire_products
from ad_buyer.models.opendirect import DeliveryType, Order


class TestOpenDirectClient:
    """Tests for the OpenDirect HTTP client."""

    @pytest.fixture
    def client(self):
        """Create a test client."""
        return OpenDirectClient(
            base_url="http://localhost:3000/api/v2.1",
            api_key="test_key",
        )

    def test_client_initialization(self, client):
        """Test client initializes correctly."""
        assert client.base_url == "http://localhost:3000/api/v2.1"

    def test_client_headers_with_api_key(self):
        """Test headers are set correctly with API key."""
        client = OpenDirectClient(
            base_url="http://localhost:3000",
            api_key="my_api_key",
        )
        headers = client._build_headers("my_api_key", None)
        assert headers["X-API-Key"] == "my_api_key"
        assert headers["Content-Type"] == "application/json"

    def test_client_headers_with_oauth(self):
        """Test headers are set correctly with OAuth token."""
        client = OpenDirectClient(
            base_url="http://localhost:3000",
            oauth_token="bearer_token",
        )
        headers = client._build_headers(None, "bearer_token")
        assert headers["Authorization"] == "Bearer bearer_token"

    @pytest.mark.asyncio
    async def test_list_products(self, client):
        """Test listing products.

        EP-12.1 — GET /products returns the shared ProductListResponse envelope
        (shared Product records, Money base_price); the client maps them to the
        OpenDirect model at the boundary.
        """
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "products": [
                {
                    "product_id": "prod_1",
                    "seller_organization_id": "pub_1",
                    "name": "Test Product",
                    "base_price": {"amount_micros": 15_000_000, "currency": "USD"},
                    "pricing_model": "cpm",
                    "delivery_type": "Guaranteed",
                }
            ],
            "total_count": 1,
            "limit": 10,
            "offset": 0,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_response
            products = await client.list_products(skip=0, top=10)

        assert len(products) == 1
        assert products[0].id == "prod_1"
        assert products[0].name == "Test Product"
        assert products[0].base_price == 15.00
        mock_request.assert_called_once()
        assert mock_request.call_args.args == ("GET", "/products")
        # Wire pagination now matches the shared ProductListRequest (limit/offset).
        assert mock_request.call_args.kwargs["params"] == {"limit": 10, "offset": 0}

    @pytest.mark.asyncio
    async def test_get_product(self, client):
        """Test getting a single product (shared Product primitive on the wire)."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "product_id": "prod_123",
            "seller_organization_id": "pub_abc",
            "name": "Homepage Banner",
            "base_price": {"amount_micros": 20_000_000, "currency": "USD"},
            "pricing_model": "cpm",
            "delivery_type": "PMP",
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_response
            product = await client.get_product("prod_123")

        assert product.id == "prod_123"
        assert product.base_price == 20.00
        assert product.delivery_type == DeliveryType.PMP

    @pytest.mark.asyncio
    async def test_search_products_uses_get_and_filters_client_side(self, client):
        """EP-12.1 — search hits GET /products (no POST /products/search) and
        filters the returned shared Product records client-side."""

        def _wire_product(pid: str, fmt: str) -> dict:
            return {
                "product_id": pid,
                "seller_organization_id": "pub_1",
                "name": pid,
                "base_price": {"amount_micros": 10_000_000, "currency": "USD"},
                "pricing_model": "cpm",
                "delivery_type": "Guaranteed",
                "ad_formats": [fmt],
            }

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "products": [_wire_product("banner_1", "banner"), _wire_product("video_1", "video")],
            "total_count": 2,
            "limit": 500,
            "offset": 0,
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_response
            results = await client.search_products({"adFormat": "video"})

        # The retired POST /products/search route is never called: the only
        # request is GET /products.
        mock_request.assert_called_once()
        assert mock_request.call_args.args == ("GET", "/products")
        # Client-side format filter kept only the matching product.
        assert [p.id for p in results] == ["video_1"]

    @pytest.mark.asyncio
    async def test_create_order(self, client):
        """Test creating an order."""
        from datetime import datetime

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": "order_new",
            "name": "Test Order",
            "accountid": "acct_123",
            "budget": 25000,
            "currency": "USD",
            "startdate": "2025-02-01T00:00:00Z",
            "enddate": "2025-02-28T23:59:59Z",
            "orderstatus": "PENDING",
        }
        mock_response.raise_for_status = MagicMock()

        order = Order(
            name="Test Order",
            account_id="acct_123",
            budget=25000,
            currency="USD",
            start_date=datetime(2025, 2, 1),
            end_date=datetime(2025, 2, 28),
        )

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_response
            result = await client.create_order("acct_123", order)

        assert result.id == "order_new"
        assert result.name == "Test Order"

    @pytest.mark.asyncio
    async def test_book_line(self, client):
        """Test booking a line."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": "line_123",
            "orderid": "order_456",
            "productid": "prod_789",
            "name": "Test Line",
            "startdate": "2025-02-01T00:00:00Z",
            "enddate": "2025-02-28T23:59:59Z",
            "ratetype": "CPM",
            "rate": 15.00,
            "qty": 500000,
            "bookingstatus": "Booked",
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_response
            result = await client.book_line("acct_123", "order_456", "line_123")

        assert result.id == "line_123"
        assert result.booking_status.value == "Booked"

    @pytest.mark.asyncio
    async def test_client_context_manager(self):
        """Test client as async context manager."""
        async with OpenDirectClient(base_url="http://localhost:3000") as client:
            assert client is not None


class TestFilterWireProducts:
    """Semantics of the client-side adFormat filter.

    Regression for the catalog contract seam that walked real-mode scenarios
    with no_booking: sellers may serve products with ``ad_formats: []`` (the
    taxonomy living only in ``ext.inventory_type``), and the old filter
    excluded every such product, so ANY adFormat-filtered search returned
    zero products. Empty/absent ``ad_formats`` means "undeclared — do not
    exclude"; only products that DECLARE formats not matching the requested
    one are excluded.
    """

    @staticmethod
    def _wire_product(pid: str, ad_formats: list[str]) -> WireProduct:
        return WireProduct(
            product_id=pid,
            seller_organization_id="pub_1",
            name=pid,
            ad_formats=ad_formats,
        )

    def test_empty_ad_formats_survives_ad_format_filter(self):
        """A product with ad_formats=[] is undeclared, not a mismatch."""
        undeclared = self._wire_product("undeclared_1", [])
        result = _filter_wire_products([undeclared], {"adFormat": "display"})
        assert [p.product_id for p in result] == ["undeclared_1"]

    def test_declared_mismatch_is_excluded(self):
        """A product declaring only ["video"] is excluded by adFormat=display."""
        video_only = self._wire_product("video_1", ["video"])
        result = _filter_wire_products([video_only], {"adFormat": "display"})
        assert result == []

    def test_declared_match_is_kept(self):
        """A product declaring the requested format is kept."""
        display = self._wire_product("display_1", ["display"])
        video = self._wire_product("video_1", ["video"])
        undeclared = self._wire_product("undeclared_1", [])
        result = _filter_wire_products(
            [display, video, undeclared], {"adFormat": "display"}
        )
        assert [p.product_id for p in result] == ["display_1", "undeclared_1"]

    def test_no_ad_format_filter_keeps_everything(self):
        products = [
            self._wire_product("a", []),
            self._wire_product("b", ["video"]),
        ]
        result = _filter_wire_products(products, {})
        assert result == products

    # -- ad_format VOCABULARY reconciliation (bead ar-mxsp) ------------------
    # The buyer's research crew searches with OpenRTB-ish placement terms
    # ("banner", "interstitial", "video") while the seller declares the IAB
    # inventory_type taxonomy in ad_formats ("display", "video", "ctv", ...).
    # An exact-match filter silently drops ALL display inventory for a
    # "banner" search, which is what walked scenario S1. The filter must
    # normalize both sides to a shared taxonomy before comparing.

    def test_banner_matches_display_product(self):
        """adFormat='banner' must keep a product declaring ['display'].

        This is the exact real-run failure: display inventory was filtered
        out client-side before the LLM ever saw it.
        """
        display = self._wire_product("display_1", ["display"])
        result = _filter_wire_products([display], {"adFormat": "banner"})
        assert [p.product_id for p in result] == ["display_1"]

    def test_interstitial_matches_display_product(self):
        """interstitial is a display placement -> matches ['display']."""
        display = self._wire_product("display_1", ["display"])
        result = _filter_wire_products([display], {"adFormat": "interstitial"})
        assert [p.product_id for p in result] == ["display_1"]

    def test_banner_does_not_match_video_product(self):
        """Normalization must NOT become match-everything: banner != video."""
        video = self._wire_product("video_1", ["video"])
        result = _filter_wire_products([video], {"adFormat": "banner"})
        assert result == []

    def test_empty_ad_formats_survives_normalized_filter(self):
        """ar-mkq5 'undeclared = do not exclude' still holds under banner."""
        undeclared = self._wire_product("undeclared_1", [])
        result = _filter_wire_products([undeclared], {"adFormat": "banner"})
        assert [p.product_id for p in result] == ["undeclared_1"]

    def test_same_vocabulary_exact_match_still_works(self):
        """A seller declaring OpenRTB 'banner' still matches adFormat='banner'."""
        banner = self._wire_product("banner_1", ["banner"])
        result = _filter_wire_products([banner], {"adFormat": "banner"})
        assert [p.product_id for p in result] == ["banner_1"]

    def test_video_request_matches_video_product(self):
        """Shared-vocabulary video still matches (regression guard)."""
        video = self._wire_product("video_1", ["video"])
        result = _filter_wire_products([video], {"adFormat": "video"})
        assert [p.product_id for p in result] == ["video_1"]

    def test_ctv_kept_distinct_from_video(self):
        """CTV is its own buying context: a plain video search must not pull
        CTV-only inventory, and a ctv search must not pull generic video."""
        ctv = self._wire_product("ctv_1", ["ctv"])
        video = self._wire_product("video_1", ["video"])
        assert _filter_wire_products([ctv], {"adFormat": "video"}) == []
        assert _filter_wire_products([video], {"adFormat": "ctv"}) == []
        kept = _filter_wire_products([ctv], {"adFormat": "ctv"})
        assert [p.product_id for p in kept] == ["ctv_1"]
