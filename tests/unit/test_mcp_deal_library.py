# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for Deal Library MCP tools.

Tests six deal library MCP tools: list_deals, search_deals,
inspect_deal, import_deals_csv, create_deal_manual, get_portfolio_summary.

bead: buyer-4ds
"""

import json

import pytest

from ad_buyer.interfaces.mcp_server import mcp, _set_deal_store
from ad_buyer.storage.deal_store import DealStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DB_URL = "sqlite:///:memory:"


def _extract_text(call_result) -> str:
    """Extract text content from a FastMCP call_tool result."""
    content_list = call_result[0]
    return content_list[0].text


def _make_deal_store() -> DealStore:
    """Create and connect an in-memory DealStore."""
    store = DealStore(DB_URL)
    store.connect()
    return store


def _seed_deal(store: DealStore, **overrides) -> str:
    """Create a deal with sensible defaults. Returns deal_id."""
    defaults = {
        "seller_url": "https://seller.example.com",
        "product_id": "prod-001",
        "product_name": "Test Deal",
        "display_name": "Test Deal",
        "deal_type": "PD",
        "status": "active",
        "seller_deal_id": "SELL-001",
        "seller_org": "Example Publisher",
        "seller_domain": "example.com",
        "media_type": "DIGITAL",
        "price": 12.50,
        "impressions": 1000000,
        "flight_start": "2026-04-01",
        "flight_end": "2026-06-30",
        "currency": "USD",
    }
    defaults.update(overrides)
    return store.save_deal(**defaults)


@pytest.fixture(autouse=True)
def _clean_deal_store_override():
    """Ensure the deal store override is cleared after each test."""
    yield
    _set_deal_store(None)


# ---------------------------------------------------------------------------
# Test tool registration
# ---------------------------------------------------------------------------


class TestDealLibraryToolRegistration:
    """Verify all 6 deal library MCP tools are registered."""

    @pytest.mark.asyncio
    async def test_list_deals_registered(self):
        """list_deals should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "list_deals" in names

    @pytest.mark.asyncio
    async def test_search_deals_registered(self):
        """search_deals should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "search_deals" in names

    @pytest.mark.asyncio
    async def test_inspect_deal_registered(self):
        """inspect_deal should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "inspect_deal" in names

    @pytest.mark.asyncio
    async def test_import_deals_csv_registered(self):
        """import_deals_csv should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "import_deals_csv" in names

    @pytest.mark.asyncio
    async def test_create_deal_manual_registered(self):
        """create_deal_manual should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "create_deal_manual" in names

    @pytest.mark.asyncio
    async def test_get_portfolio_summary_registered(self):
        """get_portfolio_summary should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "get_portfolio_summary" in names

    @pytest.mark.asyncio
    async def test_total_tool_count(self):
        """Should have at least the 6 deal library tools registered."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        expected = [
            "list_deals",
            "search_deals",
            "inspect_deal",
            "import_deals_csv",
            "create_deal_manual",
            "get_portfolio_summary",
        ]
        for tool_name in expected:
            assert tool_name in names, f"Expected tool {tool_name!r} to be registered"


# ---------------------------------------------------------------------------
# Test list_deals
# ---------------------------------------------------------------------------


class TestListDeals:
    """Tests for the list_deals MCP tool."""

    @pytest.mark.asyncio
    async def test_no_deals_returns_empty(self):
        """list_deals should return empty list when no deals exist."""
        store = _make_deal_store()
        _set_deal_store(store)

        result = await mcp.call_tool("list_deals", {})
        data = json.loads(_extract_text(result))
        assert data["deals"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_lists_all_deals(self):
        """list_deals should return all deals when no filter."""
        store = _make_deal_store()
        _seed_deal(store, display_name="Deal A", seller_deal_id="A-001")
        _seed_deal(store, display_name="Deal B", seller_deal_id="B-001")
        _set_deal_store(store)

        result = await mcp.call_tool("list_deals", {})
        data = json.loads(_extract_text(result))
        assert data["total"] == 2

    @pytest.mark.asyncio
    async def test_filter_by_status(self):
        """list_deals should filter by status when provided."""
        store = _make_deal_store()
        _seed_deal(store, display_name="Active Deal", status="active", seller_deal_id="A-001")
        _seed_deal(store, display_name="Draft Deal", status="draft", seller_deal_id="D-001")
        _set_deal_store(store)

        result = await mcp.call_tool("list_deals", {"status": "active"})
        data = json.loads(_extract_text(result))
        assert data["total"] == 1
        assert data["deals"][0]["status"] == "active"

    @pytest.mark.asyncio
    async def test_filter_by_deal_type(self):
        """list_deals should filter by deal_type when provided."""
        store = _make_deal_store()
        _seed_deal(store, display_name="PG Deal", deal_type="PG", seller_deal_id="PG-001")
        _seed_deal(store, display_name="PD Deal", deal_type="PD", seller_deal_id="PD-001")
        _set_deal_store(store)

        result = await mcp.call_tool("list_deals", {"deal_type": "PG"})
        data = json.loads(_extract_text(result))
        assert data["total"] == 1
        assert data["deals"][0]["deal_type"] == "PG"

    @pytest.mark.asyncio
    async def test_filter_by_media_type(self):
        """list_deals should filter by media_type when provided."""
        store = _make_deal_store()
        _seed_deal(store, display_name="CTV Deal", media_type="CTV", seller_deal_id="CTV-001")
        _seed_deal(store, display_name="Digital Deal", media_type="DIGITAL", seller_deal_id="DIG-001")
        _set_deal_store(store)

        result = await mcp.call_tool("list_deals", {"media_type": "CTV"})
        data = json.loads(_extract_text(result))
        assert data["total"] == 1
        assert data["deals"][0]["media_type"] == "CTV"

    @pytest.mark.asyncio
    async def test_deal_fields_included(self):
        """Each deal in the list should include key fields."""
        store = _make_deal_store()
        _seed_deal(store)
        _set_deal_store(store)

        result = await mcp.call_tool("list_deals", {})
        data = json.loads(_extract_text(result))
        deal = data["deals"][0]

        required_fields = [
            "deal_id", "display_name", "status", "deal_type",
            "seller_org", "media_type", "price",
        ]
        for field in required_fields:
            assert field in deal, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_returns_valid_json(self):
        """list_deals should return valid JSON."""
        store = _make_deal_store()
        _set_deal_store(store)

        result = await mcp.call_tool("list_deals", {})
        data = json.loads(_extract_text(result))
        assert isinstance(data, dict)
        assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_pagination_with_limit(self):
        """list_deals should respect the limit parameter."""
        store = _make_deal_store()
        for i in range(5):
            _seed_deal(store, display_name=f"Deal {i}", seller_deal_id=f"D-{i:03d}")
        _set_deal_store(store)

        result = await mcp.call_tool("list_deals", {"limit": 2})
        data = json.loads(_extract_text(result))
        assert len(data["deals"]) == 2
        assert data["total"] == 2


# ---------------------------------------------------------------------------
# Test search_deals
# ---------------------------------------------------------------------------


class TestSearchDeals:
    """Tests for the search_deals MCP tool."""

    @pytest.mark.asyncio
    async def test_search_by_name(self):
        """search_deals should find deals by display_name."""
        store = _make_deal_store()
        _seed_deal(store, display_name="ESPN Sports PMP", seller_deal_id="ESPN-001")
        _seed_deal(store, display_name="CNN News PMP", seller_deal_id="CNN-001")
        _set_deal_store(store)

        result = await mcp.call_tool("search_deals", {"query": "ESPN"})
        data = json.loads(_extract_text(result))
        assert data["total"] >= 1
        assert any("ESPN" in d["display_name"] for d in data["deals"])

    @pytest.mark.asyncio
    async def test_search_by_seller_org(self):
        """search_deals should find deals by seller_org."""
        store = _make_deal_store()
        _seed_deal(
            store, display_name="Premium Display",
            seller_org="NBCUniversal", seller_deal_id="NBC-001",
        )
        _seed_deal(
            store, display_name="Sports Package",
            seller_org="Disney", seller_deal_id="DIS-001",
        )
        _set_deal_store(store)

        result = await mcp.call_tool("search_deals", {"query": "NBCUniversal"})
        data = json.loads(_extract_text(result))
        assert data["total"] >= 1

    @pytest.mark.asyncio
    async def test_search_no_results(self):
        """search_deals should return empty when nothing matches."""
        store = _make_deal_store()
        _seed_deal(store, display_name="Regular Deal", seller_deal_id="REG-001")
        _set_deal_store(store)

        result = await mcp.call_tool("search_deals", {"query": "ZZZZNOTFOUND"})
        data = json.loads(_extract_text(result))
        assert data["total"] == 0
        assert data["deals"] == []

    @pytest.mark.asyncio
    async def test_search_empty_query_returns_error(self):
        """search_deals should return error for empty query."""
        store = _make_deal_store()
        _set_deal_store(store)

        result = await mcp.call_tool("search_deals", {"query": ""})
        data = json.loads(_extract_text(result))
        assert "error" in data

    @pytest.mark.asyncio
    async def test_search_case_insensitive(self):
        """search_deals should be case-insensitive."""
        store = _make_deal_store()
        _seed_deal(store, display_name="ESPN Sports PMP", seller_deal_id="ESPN-001")
        _set_deal_store(store)

        result = await mcp.call_tool("search_deals", {"query": "espn"})
        data = json.loads(_extract_text(result))
        assert data["total"] >= 1

    @pytest.mark.asyncio
    async def test_search_returns_valid_json(self):
        """search_deals should return valid JSON."""
        store = _make_deal_store()
        _set_deal_store(store)

        result = await mcp.call_tool("search_deals", {"query": "test"})
        data = json.loads(_extract_text(result))
        assert isinstance(data, dict)
        assert "timestamp" in data


# ---------------------------------------------------------------------------
# Test inspect_deal
# ---------------------------------------------------------------------------


class TestInspectDeal:
    """Tests for the inspect_deal MCP tool."""

    @pytest.mark.asyncio
    async def test_inspect_existing_deal(self):
        """inspect_deal should return full details for an existing deal."""
        store = _make_deal_store()
        deal_id = _seed_deal(
            store,
            display_name="ESPN Sports PMP",
            seller_org="ESPN",
            price=15.00,
            media_type="CTV",
        )
        _set_deal_store(store)

        result = await mcp.call_tool("inspect_deal", {"deal_id": deal_id})
        data = json.loads(_extract_text(result))

        assert data["deal_id"] == deal_id
        assert data["display_name"] == "ESPN Sports PMP"
        assert data["seller_org"] == "ESPN"
        assert data["price"] == 15.00
        assert data["media_type"] == "CTV"

    @pytest.mark.asyncio
    async def test_inspect_nonexistent_deal(self):
        """inspect_deal should return error for unknown deal."""
        store = _make_deal_store()
        _set_deal_store(store)

        result = await mcp.call_tool("inspect_deal", {"deal_id": "nonexistent-id"})
        data = json.loads(_extract_text(result))
        assert "error" in data

    @pytest.mark.asyncio
    async def test_inspect_includes_metadata(self):
        """inspect_deal should include portfolio metadata when available."""
        store = _make_deal_store()
        deal_id = _seed_deal(store)
        store.save_portfolio_metadata(
            deal_id=deal_id,
            import_source="MANUAL",
            advertiser_id="adv-001",
            tags=json.dumps(["premium", "sports"]),
        )
        _set_deal_store(store)

        result = await mcp.call_tool("inspect_deal", {"deal_id": deal_id})
        data = json.loads(_extract_text(result))

        assert "portfolio_metadata" in data
        assert data["portfolio_metadata"]["import_source"] == "MANUAL"

    @pytest.mark.asyncio
    async def test_inspect_includes_core_fields(self):
        """inspect_deal should include all core deal fields."""
        store = _make_deal_store()
        deal_id = _seed_deal(store)
        _set_deal_store(store)

        result = await mcp.call_tool("inspect_deal", {"deal_id": deal_id})
        data = json.loads(_extract_text(result))

        required_fields = [
            "deal_id", "display_name", "status", "deal_type",
            "seller_url", "price", "flight_start", "flight_end",
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_inspect_returns_valid_json(self):
        """inspect_deal should return valid JSON."""
        store = _make_deal_store()
        deal_id = _seed_deal(store)
        _set_deal_store(store)

        result = await mcp.call_tool("inspect_deal", {"deal_id": deal_id})
        data = json.loads(_extract_text(result))
        assert isinstance(data, dict)
        assert "timestamp" in data


# ---------------------------------------------------------------------------
# Test import_deals_csv
# ---------------------------------------------------------------------------


class TestImportDealsCsv:
    """Tests for the import_deals_csv MCP tool."""

    @pytest.mark.asyncio
    async def test_import_valid_csv(self):
        """import_deals_csv should successfully import valid CSV data."""
        store = _make_deal_store()
        _set_deal_store(store)

        csv_data = (
            "deal_name,publisher,seller_domain,deal_type,cpm,impressions\n"
            "ESPN Sports PMP,ESPN,espn.com,PG,$15.00,\"1,000,000\"\n"
            "CNN News PMP,CNN,cnn.com,PD,$10.00,500000\n"
        )

        result = await mcp.call_tool("import_deals_csv", {"csv_data": csv_data})
        data = json.loads(_extract_text(result))

        assert data["total_rows"] == 2
        assert data["successful"] == 2
        assert data["failed"] == 0

    @pytest.mark.asyncio
    async def test_import_csv_with_errors(self):
        """import_deals_csv should report row errors for invalid data."""
        store = _make_deal_store()
        _set_deal_store(store)

        # Row missing both deal_id and name (required by parser)
        csv_data = (
            "deal_name,publisher,seller_domain\n"
            "Good Deal,ESPN,espn.com\n"
            ",,,\n"
        )

        result = await mcp.call_tool("import_deals_csv", {"csv_data": csv_data})
        data = json.loads(_extract_text(result))

        # The empty row should be skipped (all empty cells)
        assert data["successful"] >= 1

    @pytest.mark.asyncio
    async def test_import_empty_csv(self):
        """import_deals_csv should handle empty CSV gracefully."""
        store = _make_deal_store()
        _set_deal_store(store)

        csv_data = "deal_name,publisher,seller_domain\n"

        result = await mcp.call_tool("import_deals_csv", {"csv_data": csv_data})
        data = json.loads(_extract_text(result))

        assert data["total_rows"] == 0
        assert data["successful"] == 0

    @pytest.mark.asyncio
    async def test_import_csv_persists_deals(self):
        """Imported deals should be saved to the deal store."""
        store = _make_deal_store()
        _set_deal_store(store)

        csv_data = (
            "deal_name,publisher,seller_domain,deal_type,cpm\n"
            "Test Import Deal,TestPub,testpub.com,PD,$8.00\n"
        )

        result = await mcp.call_tool("import_deals_csv", {"csv_data": csv_data})
        data = json.loads(_extract_text(result))

        assert data["successful"] == 1

        # Verify deal was persisted
        deals = store.list_deals()
        assert len(deals) == 1
        assert deals[0]["display_name"] == "Test Import Deal"

    @pytest.mark.asyncio
    async def test_import_csv_with_custom_seller_url(self):
        """import_deals_csv should accept a default_seller_url parameter."""
        store = _make_deal_store()
        _set_deal_store(store)

        csv_data = (
            "deal_name,publisher,seller_domain\n"
            "Test Deal,TestPub,testpub.com\n"
        )

        result = await mcp.call_tool("import_deals_csv", {
            "csv_data": csv_data,
            "default_seller_url": "https://custom-seller.example.com",
        })
        data = json.loads(_extract_text(result))
        assert data["successful"] == 1

        # Verify seller URL was set
        deals = store.list_deals()
        assert deals[0]["seller_url"] == "https://custom-seller.example.com"

    @pytest.mark.asyncio
    async def test_import_csv_returns_valid_json(self):
        """import_deals_csv should return valid JSON."""
        store = _make_deal_store()
        _set_deal_store(store)

        csv_data = "deal_name,publisher,seller_domain\n"
        result = await mcp.call_tool("import_deals_csv", {"csv_data": csv_data})
        data = json.loads(_extract_text(result))
        assert isinstance(data, dict)
        assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_import_csv_invalid_deal_type(self):
        """import_deals_csv should report errors for invalid deal types."""
        store = _make_deal_store()
        _set_deal_store(store)

        csv_data = (
            "deal_name,publisher,seller_domain,deal_type\n"
            "Bad Deal,TestPub,testpub.com,INVALID_TYPE\n"
        )

        result = await mcp.call_tool("import_deals_csv", {"csv_data": csv_data})
        data = json.loads(_extract_text(result))

        assert data["failed"] >= 1
        assert len(data["errors"]) >= 1


# ---------------------------------------------------------------------------
# Test create_deal_manual
# ---------------------------------------------------------------------------


class TestCreateDealManual:
    """Tests for the create_deal_manual MCP tool."""

    @pytest.mark.asyncio
    async def test_create_minimal_deal(self):
        """create_deal_manual should create a deal with required fields only."""
        store = _make_deal_store()
        _set_deal_store(store)

        result = await mcp.call_tool("create_deal_manual", {
            "display_name": "ESPN Sports PMP",
            "seller_url": "https://espn.seller.example.com",
        })
        data = json.loads(_extract_text(result))

        assert data["success"] is True
        assert "deal_id" in data

    @pytest.mark.asyncio
    async def test_create_deal_with_all_fields(self):
        """create_deal_manual should handle all optional fields."""
        store = _make_deal_store()
        _set_deal_store(store)

        result = await mcp.call_tool("create_deal_manual", {
            "display_name": "Premium CTV Package",
            "seller_url": "https://seller.example.com",
            "deal_type": "PG",
            "media_type": "CTV",
            "price": 25.00,
            "impressions": 2000000,
            "flight_start": "2026-04-01",
            "flight_end": "2026-06-30",
            "seller_org": "NBCUniversal",
            "description": "Premium CTV inventory package",
            "tags": ["premium", "ctv"],
        })
        data = json.loads(_extract_text(result))

        assert data["success"] is True
        assert "deal_id" in data

    @pytest.mark.asyncio
    async def test_create_deal_persists(self):
        """Created deal should be saved to the deal store."""
        store = _make_deal_store()
        _set_deal_store(store)

        result = await mcp.call_tool("create_deal_manual", {
            "display_name": "Test Persistence",
            "seller_url": "https://seller.example.com",
        })
        data = json.loads(_extract_text(result))
        deal_id = data["deal_id"]

        # Verify deal exists in store
        deal = store.get_deal(deal_id)
        assert deal is not None
        # ManualDealEntry maps display_name to product_name in v1 schema
        assert deal["product_name"] == "Test Persistence"

    @pytest.mark.asyncio
    async def test_create_deal_invalid_deal_type(self):
        """create_deal_manual should reject invalid deal_type."""
        store = _make_deal_store()
        _set_deal_store(store)

        result = await mcp.call_tool("create_deal_manual", {
            "display_name": "Bad Deal",
            "seller_url": "https://seller.example.com",
            "deal_type": "INVALID",
        })
        data = json.loads(_extract_text(result))

        assert data["success"] is False
        assert "errors" in data
        assert len(data["errors"]) > 0

    @pytest.mark.asyncio
    async def test_create_deal_missing_display_name(self):
        """create_deal_manual should reject missing display_name."""
        store = _make_deal_store()
        _set_deal_store(store)

        result = await mcp.call_tool("create_deal_manual", {
            "display_name": "",
            "seller_url": "https://seller.example.com",
        })
        data = json.loads(_extract_text(result))

        assert data["success"] is False

    @pytest.mark.asyncio
    async def test_create_deal_returns_valid_json(self):
        """create_deal_manual should return valid JSON."""
        store = _make_deal_store()
        _set_deal_store(store)

        result = await mcp.call_tool("create_deal_manual", {
            "display_name": "Test JSON",
            "seller_url": "https://seller.example.com",
        })
        data = json.loads(_extract_text(result))
        assert isinstance(data, dict)
        assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_create_deal_saves_portfolio_metadata(self):
        """create_deal_manual should save portfolio metadata with import_source=MANUAL."""
        store = _make_deal_store()
        _set_deal_store(store)

        result = await mcp.call_tool("create_deal_manual", {
            "display_name": "Test Metadata",
            "seller_url": "https://seller.example.com",
            "advertiser_id": "adv-001",
            "tags": ["premium"],
        })
        data = json.loads(_extract_text(result))
        deal_id = data["deal_id"]

        # Verify portfolio metadata was saved
        metadata = store.get_portfolio_metadata(deal_id)
        assert metadata is not None
        assert metadata["import_source"] == "MANUAL"


# ---------------------------------------------------------------------------
# Test get_portfolio_summary
# ---------------------------------------------------------------------------


class TestGetPortfolioSummary:
    """Tests for the get_portfolio_summary MCP tool."""

    @pytest.mark.asyncio
    async def test_empty_portfolio(self):
        """get_portfolio_summary should handle empty portfolio."""
        store = _make_deal_store()
        _set_deal_store(store)

        result = await mcp.call_tool("get_portfolio_summary", {})
        data = json.loads(_extract_text(result))

        assert data["total_deals"] == 0

    @pytest.mark.asyncio
    async def test_summary_counts(self):
        """get_portfolio_summary should return correct total counts."""
        store = _make_deal_store()
        _seed_deal(store, display_name="Deal A", seller_deal_id="A-001")
        _seed_deal(store, display_name="Deal B", seller_deal_id="B-001")
        _seed_deal(store, display_name="Deal C", seller_deal_id="C-001")
        _set_deal_store(store)

        result = await mcp.call_tool("get_portfolio_summary", {})
        data = json.loads(_extract_text(result))

        assert data["total_deals"] == 3

    @pytest.mark.asyncio
    async def test_summary_by_status(self):
        """get_portfolio_summary should break down deals by status."""
        store = _make_deal_store()
        _seed_deal(store, display_name="Active 1", status="active", seller_deal_id="A-001")
        _seed_deal(store, display_name="Active 2", status="active", seller_deal_id="A-002")
        _seed_deal(store, display_name="Draft 1", status="draft", seller_deal_id="D-001")
        _set_deal_store(store)

        result = await mcp.call_tool("get_portfolio_summary", {})
        data = json.loads(_extract_text(result))

        assert "by_status" in data
        assert data["by_status"]["active"] == 2
        assert data["by_status"]["draft"] == 1

    @pytest.mark.asyncio
    async def test_summary_by_deal_type(self):
        """get_portfolio_summary should break down deals by deal_type."""
        store = _make_deal_store()
        _seed_deal(store, display_name="PG Deal", deal_type="PG", seller_deal_id="PG-001")
        _seed_deal(store, display_name="PD Deal", deal_type="PD", seller_deal_id="PD-001")
        _set_deal_store(store)

        result = await mcp.call_tool("get_portfolio_summary", {})
        data = json.loads(_extract_text(result))

        assert "by_deal_type" in data
        assert data["by_deal_type"]["PG"] == 1
        assert data["by_deal_type"]["PD"] == 1

    @pytest.mark.asyncio
    async def test_summary_by_media_type(self):
        """get_portfolio_summary should break down deals by media_type."""
        store = _make_deal_store()
        _seed_deal(store, display_name="CTV Deal", media_type="CTV", seller_deal_id="CTV-001")
        _seed_deal(store, display_name="Digital Deal", media_type="DIGITAL", seller_deal_id="DIG-001")
        _set_deal_store(store)

        result = await mcp.call_tool("get_portfolio_summary", {})
        data = json.loads(_extract_text(result))

        assert "by_media_type" in data
        assert data["by_media_type"]["CTV"] == 1
        assert data["by_media_type"]["DIGITAL"] == 1

    @pytest.mark.asyncio
    async def test_summary_portfolio_value(self):
        """get_portfolio_summary should calculate total portfolio value."""
        store = _make_deal_store()
        # price=10 CPM, impressions=1M -> value = 10 * 1M / 1000 = $10,000
        _seed_deal(
            store, display_name="Deal A",
            price=10.0, impressions=1000000,
            seller_deal_id="A-001",
        )
        # price=20 CPM, impressions=500K -> value = 20 * 500K / 1000 = $10,000
        _seed_deal(
            store, display_name="Deal B",
            price=20.0, impressions=500000,
            seller_deal_id="B-001",
        )
        _set_deal_store(store)

        result = await mcp.call_tool("get_portfolio_summary", {})
        data = json.loads(_extract_text(result))

        assert "total_value" in data
        assert data["total_value"] == 20000.0

    @pytest.mark.asyncio
    async def test_summary_top_sellers(self):
        """get_portfolio_summary should include top sellers."""
        store = _make_deal_store()
        _seed_deal(store, display_name="ESPN 1", seller_org="ESPN", seller_deal_id="E-001")
        _seed_deal(store, display_name="ESPN 2", seller_org="ESPN", seller_deal_id="E-002")
        _seed_deal(store, display_name="CNN 1", seller_org="CNN", seller_deal_id="C-001")
        _set_deal_store(store)

        result = await mcp.call_tool("get_portfolio_summary", {})
        data = json.loads(_extract_text(result))

        assert "top_sellers" in data
        assert len(data["top_sellers"]) >= 1

    @pytest.mark.asyncio
    async def test_summary_returns_valid_json(self):
        """get_portfolio_summary should return valid JSON."""
        store = _make_deal_store()
        _set_deal_store(store)

        result = await mcp.call_tool("get_portfolio_summary", {})
        data = json.loads(_extract_text(result))
        assert isinstance(data, dict)
        assert "timestamp" in data


# ---------------------------------------------------------------------------
# Test create_deal_manual -> inspect_deal roundtrip (v2 field persistence)
# ---------------------------------------------------------------------------


class TestCreateDealManualRoundtrip:
    """Verify that v2 fields set via create_deal_manual survive the
    save/load roundtrip and appear in inspect_deal output."""

    @pytest.mark.asyncio
    async def test_v2_fields_survive_roundtrip(self):
        """All v2 fields set in create_deal_manual should be readable
        from inspect_deal after the deal is persisted."""
        store = _make_deal_store()
        _set_deal_store(store)

        create_result = await mcp.call_tool("create_deal_manual", {
            "display_name": "Premium Video PG",
            "seller_url": "https://nbcu.seller.example.com",
            "deal_type": "PG",
            "status": "active",
            "seller_org": "NBCUniversal",
            "seller_domain": "nbcuniversal.com",
            "seller_type": "PUBLISHER",
            "buyer_org": "MediaCo Agency",
            "buyer_id": "buyer-mediaco-001",
            "price": 15.50,
            "fixed_price_cpm": 15.50,
            "bid_floor_cpm": 12.00,
            "price_model": "CPM",
            "currency": "EUR",
            "media_type": "CTV",
            "impressions": 5000000,
            "flight_start": "2026-04-01",
            "flight_end": "2026-06-30",
            "description": "Premium CTV video inventory for Q2 campaign",
        })
        create_data = json.loads(_extract_text(create_result))
        assert create_data["success"] is True
        deal_id = create_data["deal_id"]

        # Read back via inspect_deal
        inspect_result = await mcp.call_tool("inspect_deal", {"deal_id": deal_id})
        inspect_data = json.loads(_extract_text(inspect_result))

        # Verify all v2 fields survived the roundtrip
        assert inspect_data["display_name"] == "Premium Video PG"
        assert inspect_data["seller_org"] == "NBCUniversal"
        assert inspect_data["seller_domain"] == "nbcuniversal.com"
        assert inspect_data["seller_type"] == "PUBLISHER"
        assert inspect_data["buyer_org"] == "MediaCo Agency"
        assert inspect_data["buyer_id"] == "buyer-mediaco-001"
        assert inspect_data["price_model"] == "CPM"
        assert inspect_data["fixed_price_cpm"] == 15.50
        assert inspect_data["bid_floor_cpm"] == 12.00
        assert inspect_data["currency"] == "EUR"
        assert inspect_data["media_type"] == "CTV"
        assert inspect_data["description"] == "Premium CTV video inventory for Q2 campaign"

    @pytest.mark.asyncio
    async def test_filter_by_media_type_after_create(self):
        """After create_deal_manual, filtering by media_type via list_deals
        should find the deal."""
        store = _make_deal_store()
        _set_deal_store(store)

        await mcp.call_tool("create_deal_manual", {
            "display_name": "CTV Deal",
            "seller_url": "https://seller.example.com",
            "media_type": "CTV",
        })

        result = await mcp.call_tool("list_deals", {"media_type": "CTV"})
        data = json.loads(_extract_text(result))
        assert data["total"] == 1
        assert data["deals"][0]["media_type"] == "CTV"

    @pytest.mark.asyncio
    async def test_search_by_seller_org_after_create(self):
        """After create_deal_manual, searching by seller_org should find the deal."""
        store = _make_deal_store()
        _set_deal_store(store)

        await mcp.call_tool("create_deal_manual", {
            "display_name": "NBC Deal",
            "seller_url": "https://nbc.example.com",
            "seller_org": "NBCUniversal",
        })

        result = await mcp.call_tool("search_deals", {"query": "NBCUniversal"})
        data = json.loads(_extract_text(result))
        assert data["total"] >= 1
