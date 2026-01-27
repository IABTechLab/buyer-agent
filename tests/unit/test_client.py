# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for OpenDirect client."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from ad_buyer.clients.opendirect_client import OpenDirectClient
from ad_buyer.models.opendirect import Product, Order, Line, RateType, DeliveryType


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
        """Test listing products."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "products": [
                {
                    "id": "prod_1",
                    "publisherId": "pub_1",
                    "name": "Test Product",
                    "currency": "USD",
                    "basePrice": 15.00,
                    "rateType": "CPM",
                    "deliveryType": "Guaranteed",
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._client, 'get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            products = await client.list_products(skip=0, top=10)

        assert len(products) == 1
        assert products[0].id == "prod_1"
        assert products[0].name == "Test Product"
        mock_get.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_product(self, client):
        """Test getting a single product."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": "prod_123",
            "publisherId": "pub_abc",
            "name": "Homepage Banner",
            "currency": "USD",
            "basePrice": 20.00,
            "rateType": "CPM",
            "deliveryType": "PMP",
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._client, 'get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            product = await client.get_product("prod_123")

        assert product.id == "prod_123"
        assert product.base_price == 20.00
        assert product.delivery_type == DeliveryType.PMP

    @pytest.mark.asyncio
    async def test_create_order(self, client):
        """Test creating an order."""
        from datetime import datetime

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": "order_new",
            "name": "Test Order",
            "accountId": "acct_123",
            "budget": 25000,
            "currency": "USD",
            "startDate": "2025-02-01T00:00:00Z",
            "endDate": "2025-02-28T23:59:59Z",
            "orderStatus": "PENDING",
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

        with patch.object(client._client, 'post', new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            result = await client.create_order("acct_123", order)

        assert result.id == "order_new"
        assert result.name == "Test Order"

    @pytest.mark.asyncio
    async def test_book_line(self, client):
        """Test booking a line."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": "line_123",
            "orderId": "order_456",
            "productId": "prod_789",
            "name": "Test Line",
            "startDate": "2025-02-01T00:00:00Z",
            "endDate": "2025-02-28T23:59:59Z",
            "rateType": "CPM",
            "rate": 15.00,
            "quantity": 500000,
            "bookingStatus": "Booked",
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._client, 'patch', new_callable=AsyncMock) as mock_patch:
            mock_patch.return_value = mock_response
            result = await client.book_line("acct_123", "order_456", "line_123")

        assert result.id == "line_123"
        assert result.booking_status.value == "Booked"

    @pytest.mark.asyncio
    async def test_client_context_manager(self):
        """Test client as async context manager."""
        async with OpenDirectClient(base_url="http://localhost:3000") as client:
            assert client is not None
