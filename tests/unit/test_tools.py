# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for CrewAI tools."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ad_buyer.tools.research.product_search import ProductSearchTool, ProductSearchInput
from ad_buyer.tools.research.avails_check import AvailsCheckTool, AvailsCheckInput
from ad_buyer.tools.execution.order_management import CreateOrderTool, CreateOrderInput
from ad_buyer.tools.execution.line_management import (
    CreateLineTool,
    BookLineTool,
    CreateLineInput,
)
from ad_buyer.tools.reporting.stats_retrieval import GetStatsTool
from ad_buyer.models.opendirect import Product, RateType, DeliveryType, AvailsResponse


class TestProductSearchTool:
    """Tests for the ProductSearchTool."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock OpenDirect client."""
        client = MagicMock()
        return client

    @pytest.fixture
    def tool(self, mock_client):
        """Create the tool with mock client."""
        return ProductSearchTool(mock_client)

    def test_tool_initialization(self, tool):
        """Test tool initializes correctly."""
        assert tool.name == "search_advertising_products"
        assert "Search for advertising products" in tool.description

    def test_input_schema(self):
        """Test input schema validation."""
        input_data = ProductSearchInput(
            channel="display",
            format="banner",
            min_price=10.0,
            max_price=50.0,
            limit=5,
        )
        assert input_data.channel == "display"
        assert input_data.limit == 5

    @pytest.mark.asyncio
    async def test_search_with_results(self, tool, mock_client):
        """Test search returns formatted results."""
        mock_products = [
            Product(
                id="prod_1",
                publisher_id="pub_1",
                name="Banner Ad",
                currency="USD",
                base_price=15.00,
                rate_type=RateType.CPM,
                delivery_type=DeliveryType.GUARANTEED,
                available_impressions=1000000,
            ),
        ]

        mock_client.search_products = AsyncMock(return_value=mock_products)

        result = await tool._arun(channel="display", limit=10)

        assert "Found 1 matching products" in result
        assert "Banner Ad" in result
        assert "$15.00" in result

    @pytest.mark.asyncio
    async def test_search_no_results(self, tool, mock_client):
        """Test search with no matching products."""
        mock_client.search_products = AsyncMock(return_value=[])

        result = await tool._arun(channel="ctv", max_price=5.0)

        assert "No products found" in result

    @pytest.mark.asyncio
    async def test_search_price_filter(self, tool, mock_client):
        """Test price filtering works correctly."""
        mock_products = [
            Product(
                id="prod_1",
                publisher_id="pub_1",
                name="Cheap Ad",
                currency="USD",
                base_price=10.00,
                rate_type=RateType.CPM,
                delivery_type=DeliveryType.GUARANTEED,
            ),
            Product(
                id="prod_2",
                publisher_id="pub_2",
                name="Expensive Ad",
                currency="USD",
                base_price=100.00,
                rate_type=RateType.CPM,
                delivery_type=DeliveryType.GUARANTEED,
            ),
        ]

        mock_client.list_products = AsyncMock(return_value=mock_products)

        result = await tool._arun(max_price=50.0)

        assert "Cheap Ad" in result
        assert "Expensive Ad" not in result


class TestAvailsCheckTool:
    """Tests for the AvailsCheckTool."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock OpenDirect client."""
        return MagicMock()

    @pytest.fixture
    def tool(self, mock_client):
        """Create the tool with mock client."""
        return AvailsCheckTool(mock_client)

    def test_tool_initialization(self, tool):
        """Test tool initializes correctly."""
        assert tool.name == "check_inventory_availability"

    @pytest.mark.asyncio
    async def test_check_avails(self, tool, mock_client):
        """Test availability check returns formatted results."""
        mock_response = AvailsResponse(
            product_id="prod_123",
            available_impressions=800000,
            guaranteed_impressions=750000,
            estimated_cpm=18.50,
            total_cost=13875.00,
            delivery_confidence=95.0,
            available_targeting=["geo", "demographic"],
        )

        mock_client.check_avails = AsyncMock(return_value=mock_response)

        result = await tool._arun(
            product_id="prod_123",
            start_date="2025-02-01",
            end_date="2025-02-28",
            impressions=800000,
        )

        assert "prod_123" in result
        assert "800,000" in result
        assert "$18.50" in result
        assert "Good to book" in result

    @pytest.mark.asyncio
    async def test_check_avails_low_confidence(self, tool, mock_client):
        """Test availability check with low delivery confidence."""
        mock_response = AvailsResponse(
            product_id="prod_123",
            available_impressions=200000,
            estimated_cpm=18.50,
            total_cost=3700.00,
            delivery_confidence=60.0,
        )

        mock_client.check_avails = AsyncMock(return_value=mock_response)

        result = await tool._arun(
            product_id="prod_123",
            start_date="2025-02-01",
            end_date="2025-02-28",
        )

        assert "Consider alternatives" in result


class TestCreateOrderTool:
    """Tests for the CreateOrderTool."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock OpenDirect client."""
        return MagicMock()

    @pytest.fixture
    def tool(self, mock_client):
        """Create the tool with mock client."""
        return CreateOrderTool(mock_client)

    def test_tool_initialization(self, tool):
        """Test tool initializes correctly."""
        assert tool.name == "create_advertising_order"


class TestBookLineTool:
    """Tests for the BookLineTool."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock OpenDirect client."""
        return MagicMock()

    @pytest.fixture
    def tool(self, mock_client):
        """Create the tool with mock client."""
        return BookLineTool(mock_client)

    def test_tool_initialization(self, tool):
        """Test tool initializes correctly."""
        assert tool.name == "book_line_item"
        assert "Confirm booking" in tool.description
