# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for SSP connector MCP tools.

Tests three SSP connector MCP tools:
  - list_ssp_connectors
  - import_deals_ssp
  - test_ssp_connection

bead: buyer-sozw
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from ad_buyer.interfaces.mcp_server import mcp, _set_deal_store
from ad_buyer.storage.deal_store import DealStore
from ad_buyer.tools.deal_library.ssp_connector_base import SSPFetchResult


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


@pytest.fixture(autouse=True)
def _clean_deal_store_override():
    """Ensure the deal store override is cleared after each test."""
    yield
    _set_deal_store(None)


@pytest.fixture(autouse=True)
def _clean_ssp_env(monkeypatch):
    """Remove SSP env vars before each test to ensure clean state."""
    ssp_vars = [
        "PUBMATIC_API_TOKEN",
        "PUBMATIC_SEAT_ID",
        "MAGNITE_ACCESS_KEY",
        "MAGNITE_SECRET_KEY",
        "MAGNITE_SEAT_ID",
        "MAGNITE_PLATFORM",
    ]
    for var in ssp_vars:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Tool Registration Tests
# ---------------------------------------------------------------------------


class TestSSPToolRegistration:
    """Verify all 3 SSP MCP tools are registered."""

    @pytest.mark.asyncio
    async def test_list_ssp_connectors_registered(self):
        """list_ssp_connectors should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        tool_names = [t.name for t in tools]
        assert "list_ssp_connectors" in tool_names

    @pytest.mark.asyncio
    async def test_import_deals_ssp_registered(self):
        """import_deals_ssp should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        tool_names = [t.name for t in tools]
        assert "import_deals_ssp" in tool_names

    @pytest.mark.asyncio
    async def test_test_ssp_connection_registered(self):
        """test_ssp_connection should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        tool_names = [t.name for t in tools]
        assert "test_ssp_connection" in tool_names


# ---------------------------------------------------------------------------
# list_ssp_connectors Tests
# ---------------------------------------------------------------------------


class TestListSSPConnectors:
    """Tests for the list_ssp_connectors MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_three_connectors(self):
        """Should always return exactly 3 connectors."""
        result = await mcp.call_tool("list_ssp_connectors", {})
        data = json.loads(_extract_text(result))
        assert data["total"] == 3

    @pytest.mark.asyncio
    async def test_connector_names_present(self):
        """Response should list pubmatic, magnite, and index_exchange."""
        result = await mcp.call_tool("list_ssp_connectors", {})
        data = json.loads(_extract_text(result))
        names = {c["name"] for c in data["connectors"]}
        assert "pubmatic" in names
        assert "magnite" in names
        assert "index_exchange" in names

    @pytest.mark.asyncio
    async def test_connectors_unconfigured_without_env_vars(self):
        """All connectors should show configured=false when env vars are absent."""
        result = await mcp.call_tool("list_ssp_connectors", {})
        data = json.loads(_extract_text(result))
        for connector in data["connectors"]:
            assert connector["configured"] is False, (
                f"Expected {connector['name']} to be unconfigured"
            )

    @pytest.mark.asyncio
    async def test_pubmatic_shows_configured_with_env_vars(self, monkeypatch):
        """PubMatic should show configured=true when env vars are set."""
        monkeypatch.setenv("PUBMATIC_API_TOKEN", "test-token")
        monkeypatch.setenv("PUBMATIC_SEAT_ID", "test-seat")
        result = await mcp.call_tool("list_ssp_connectors", {})
        data = json.loads(_extract_text(result))
        pubmatic = next(c for c in data["connectors"] if c["name"] == "pubmatic")
        assert pubmatic["configured"] is True

    @pytest.mark.asyncio
    async def test_magnite_shows_configured_with_env_vars(self, monkeypatch):
        """Magnite should show configured=true when env vars are set."""
        monkeypatch.setenv("MAGNITE_ACCESS_KEY", "test-key")
        monkeypatch.setenv("MAGNITE_SECRET_KEY", "test-secret")
        monkeypatch.setenv("MAGNITE_SEAT_ID", "test-seat")
        result = await mcp.call_tool("list_ssp_connectors", {})
        data = json.loads(_extract_text(result))
        magnite = next(c for c in data["connectors"] if c["name"] == "magnite")
        assert magnite["configured"] is True

    @pytest.mark.asyncio
    async def test_response_includes_required_env_vars(self):
        """Each connector entry should list its required_env_vars."""
        result = await mcp.call_tool("list_ssp_connectors", {})
        data = json.loads(_extract_text(result))
        for connector in data["connectors"]:
            assert "required_env_vars" in connector
            assert isinstance(connector["required_env_vars"], list)

    @pytest.mark.asyncio
    async def test_response_has_timestamp(self):
        """Response should include a timestamp."""
        result = await mcp.call_tool("list_ssp_connectors", {})
        data = json.loads(_extract_text(result))
        assert "timestamp" in data


# ---------------------------------------------------------------------------
# import_deals_ssp Tests
# ---------------------------------------------------------------------------


class TestImportDealsSSP:
    """Tests for the import_deals_ssp MCP tool."""

    @pytest.mark.asyncio
    async def test_unknown_ssp_returns_error(self):
        """Passing an unknown ssp_name should return an error, not raise."""
        result = await mcp.call_tool("import_deals_ssp", {"ssp_name": "unknown_ssp"})
        data = json.loads(_extract_text(result))
        assert "error" in data

    @pytest.mark.asyncio
    async def test_unconfigured_pubmatic_returns_error(self):
        """PubMatic import should return an error when not configured."""
        result = await mcp.call_tool("import_deals_ssp", {"ssp_name": "pubmatic"})
        data = json.loads(_extract_text(result))
        assert "error" in data
        # Error message should mention which env vars are needed
        assert "PUBMATIC_API_TOKEN" in data["error"] or "PUBMATIC_SEAT_ID" in data["error"]

    @pytest.mark.asyncio
    async def test_unconfigured_magnite_returns_error(self):
        """Magnite import should return an error when not configured."""
        result = await mcp.call_tool("import_deals_ssp", {"ssp_name": "magnite"})
        data = json.loads(_extract_text(result))
        assert "error" in data

    @pytest.mark.asyncio
    async def test_unconfigured_index_exchange_returns_error(self):
        """Index Exchange import should return an error when not configured."""
        result = await mcp.call_tool("import_deals_ssp", {"ssp_name": "index_exchange"})
        data = json.loads(_extract_text(result))
        assert "error" in data

    @pytest.mark.asyncio
    async def test_successful_pubmatic_import(self, monkeypatch):
        """A configured PubMatic connector that returns deals should save them."""
        monkeypatch.setenv("PUBMATIC_API_TOKEN", "test-token")
        monkeypatch.setenv("PUBMATIC_SEAT_ID", "test-seat")

        store = _make_deal_store()
        _set_deal_store(store)

        fake_result = SSPFetchResult(
            ssp_name="PubMatic",
            total_fetched=2,
            successful=2,
            failed=0,
            skipped=0,
            deals=[
                {
                    "seller_url": "https://api.pubmatic.com",
                    "product_id": "PM-001",
                    "seller_deal_id": "PM-001",
                    "display_name": "PubMatic Deal 1",
                    "deal_type": "PD",
                    "status": "active",
                    "seller_org": "PubMatic",
                    "seller_type": "SSP",
                    "currency": "USD",
                },
                {
                    "seller_url": "https://api.pubmatic.com",
                    "product_id": "PM-002",
                    "seller_deal_id": "PM-002",
                    "display_name": "PubMatic Deal 2",
                    "deal_type": "PG",
                    "status": "active",
                    "seller_org": "PubMatic",
                    "seller_type": "SSP",
                    "currency": "USD",
                },
            ],
        )

        with patch(
            "ad_buyer.interfaces.mcp_server.PubMaticConnector"
        ) as MockConnector:
            instance = MagicMock()
            instance.is_configured.return_value = True
            instance.fetch_deals.return_value = fake_result
            instance.import_source = "PUBMATIC"
            MockConnector.return_value = instance

            result = await mcp.call_tool("import_deals_ssp", {"ssp_name": "pubmatic"})
            data = json.loads(_extract_text(result))

        assert "error" not in data
        assert data["successful"] == 2
        assert data["failed"] == 0
        assert len(data["deal_ids"]) == 2

    @pytest.mark.asyncio
    async def test_successful_magnite_import(self, monkeypatch):
        """A configured Magnite connector that returns deals should save them."""
        monkeypatch.setenv("MAGNITE_ACCESS_KEY", "test-key")
        monkeypatch.setenv("MAGNITE_SECRET_KEY", "test-secret")
        monkeypatch.setenv("MAGNITE_SEAT_ID", "test-seat")

        store = _make_deal_store()
        _set_deal_store(store)

        fake_result = SSPFetchResult(
            ssp_name="Magnite",
            total_fetched=1,
            successful=1,
            failed=0,
            skipped=0,
            deals=[
                {
                    "seller_url": "https://api.tremorhub.com",
                    "product_id": "MAG-001",
                    "seller_deal_id": "MAG-001",
                    "display_name": "Magnite CTV Deal",
                    "deal_type": "PG",
                    "status": "imported",
                    "seller_org": "Magnite",
                    "seller_type": "SSP",
                    "currency": "USD",
                }
            ],
        )

        with patch(
            "ad_buyer.interfaces.mcp_server.MagniteConnector"
        ) as MockConnector:
            instance = MagicMock()
            instance.is_configured.return_value = True
            instance.fetch_deals.return_value = fake_result
            instance.import_source = "MAGNITE"
            MockConnector.return_value = instance

            result = await mcp.call_tool("import_deals_ssp", {"ssp_name": "magnite"})
            data = json.loads(_extract_text(result))

        assert "error" not in data
        assert data["successful"] == 1
        assert len(data["deal_ids"]) == 1

    @pytest.mark.asyncio
    async def test_import_result_has_standard_fields(self, monkeypatch):
        """import_deals_ssp result should mirror import_deals_csv structure."""
        monkeypatch.setenv("PUBMATIC_API_TOKEN", "test-token")
        monkeypatch.setenv("PUBMATIC_SEAT_ID", "test-seat")

        store = _make_deal_store()
        _set_deal_store(store)

        fake_result = SSPFetchResult(
            ssp_name="PubMatic",
            total_fetched=0,
            successful=0,
            failed=0,
            skipped=0,
            deals=[],
        )

        with patch(
            "ad_buyer.interfaces.mcp_server.PubMaticConnector"
        ) as MockConnector:
            instance = MagicMock()
            instance.is_configured.return_value = True
            instance.fetch_deals.return_value = fake_result
            instance.import_source = "PUBMATIC"
            MockConnector.return_value = instance

            result = await mcp.call_tool("import_deals_ssp", {"ssp_name": "pubmatic"})
            data = json.loads(_extract_text(result))

        # Must have same structure as import_deals_csv
        for field in ("total_rows", "successful", "failed", "skipped", "errors", "deal_ids", "timestamp"):
            assert field in data, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_import_ssp_name_case_insensitive(self, monkeypatch):
        """ssp_name should be matched case-insensitively."""
        monkeypatch.setenv("PUBMATIC_API_TOKEN", "test-token")
        monkeypatch.setenv("PUBMATIC_SEAT_ID", "test-seat")

        store = _make_deal_store()
        _set_deal_store(store)

        fake_result = SSPFetchResult(ssp_name="PubMatic", total_fetched=0, successful=0, deals=[])

        with patch(
            "ad_buyer.interfaces.mcp_server.PubMaticConnector"
        ) as MockConnector:
            instance = MagicMock()
            instance.is_configured.return_value = True
            instance.fetch_deals.return_value = fake_result
            instance.import_source = "PUBMATIC"
            MockConnector.return_value = instance

            result = await mcp.call_tool("import_deals_ssp", {"ssp_name": "PubMatic"})
            data = json.loads(_extract_text(result))

        # Should NOT be an error (case mismatch shouldn't matter)
        assert "error" not in data

    @pytest.mark.asyncio
    async def test_import_with_partial_failures(self, monkeypatch):
        """Connector fetch_deals failures are reported but do not raise."""
        monkeypatch.setenv("PUBMATIC_API_TOKEN", "test-token")
        monkeypatch.setenv("PUBMATIC_SEAT_ID", "test-seat")

        store = _make_deal_store()
        _set_deal_store(store)

        fake_result = SSPFetchResult(
            ssp_name="PubMatic",
            total_fetched=3,
            successful=2,
            failed=1,
            skipped=0,
            errors=["Deal normalization failed: missing deal_id"],
            deals=[
                {
                    "seller_url": "https://api.pubmatic.com",
                    "product_id": "PM-001",
                    "seller_deal_id": "PM-001",
                    "display_name": "Deal 1",
                    "deal_type": "PD",
                    "status": "active",
                    "seller_org": "PubMatic",
                    "seller_type": "SSP",
                    "currency": "USD",
                },
                {
                    "seller_url": "https://api.pubmatic.com",
                    "product_id": "PM-002",
                    "seller_deal_id": "PM-002",
                    "display_name": "Deal 2",
                    "deal_type": "PD",
                    "status": "active",
                    "seller_org": "PubMatic",
                    "seller_type": "SSP",
                    "currency": "USD",
                },
            ],
        )

        with patch("ad_buyer.interfaces.mcp_server.PubMaticConnector") as MockConnector:
            instance = MagicMock()
            instance.is_configured.return_value = True
            instance.fetch_deals.return_value = fake_result
            instance.import_source = "PUBMATIC"
            MockConnector.return_value = instance

            result = await mcp.call_tool("import_deals_ssp", {"ssp_name": "pubmatic"})
            data = json.loads(_extract_text(result))

        assert data["successful"] == 2
        assert data["failed"] == 1
        assert len(data["errors"]) == 1


# ---------------------------------------------------------------------------
# test_ssp_connection Tests
# ---------------------------------------------------------------------------


class TestSSPConnectionTest:
    """Tests for the test_ssp_connection MCP tool."""

    @pytest.mark.asyncio
    async def test_unknown_ssp_returns_error(self):
        """Unknown ssp_name should return an error object."""
        result = await mcp.call_tool("test_ssp_connection", {"ssp_name": "unknown_ssp"})
        data = json.loads(_extract_text(result))
        assert "error" in data

    @pytest.mark.asyncio
    async def test_unconfigured_returns_not_configured(self):
        """Unconfigured connector should return connected=false with explanation."""
        result = await mcp.call_tool("test_ssp_connection", {"ssp_name": "pubmatic"})
        data = json.loads(_extract_text(result))
        assert data["connected"] is False
        assert "ssp_name" in data

    @pytest.mark.asyncio
    async def test_configured_and_reachable_returns_true(self, monkeypatch):
        """A configured connector whose test_connection() returns True → connected=true."""
        monkeypatch.setenv("PUBMATIC_API_TOKEN", "test-token")
        monkeypatch.setenv("PUBMATIC_SEAT_ID", "test-seat")

        with patch("ad_buyer.interfaces.mcp_server.PubMaticConnector") as MockConnector:
            instance = MagicMock()
            instance.is_configured.return_value = True
            instance.test_connection.return_value = True
            instance.ssp_name = "PubMatic"
            MockConnector.return_value = instance

            result = await mcp.call_tool("test_ssp_connection", {"ssp_name": "pubmatic"})
            data = json.loads(_extract_text(result))

        assert data["connected"] is True
        assert data["ssp_name"] == "pubmatic"

    @pytest.mark.asyncio
    async def test_configured_but_unreachable_returns_false(self, monkeypatch):
        """A configured connector whose test_connection() returns False → connected=false."""
        monkeypatch.setenv("PUBMATIC_API_TOKEN", "bad-token")
        monkeypatch.setenv("PUBMATIC_SEAT_ID", "bad-seat")

        with patch("ad_buyer.interfaces.mcp_server.PubMaticConnector") as MockConnector:
            instance = MagicMock()
            instance.is_configured.return_value = True
            instance.test_connection.return_value = False
            instance.ssp_name = "PubMatic"
            MockConnector.return_value = instance

            result = await mcp.call_tool("test_ssp_connection", {"ssp_name": "pubmatic"})
            data = json.loads(_extract_text(result))

        assert data["connected"] is False

    @pytest.mark.asyncio
    async def test_magnite_connection_test(self, monkeypatch):
        """Magnite test_ssp_connection should work the same way."""
        monkeypatch.setenv("MAGNITE_ACCESS_KEY", "test-key")
        monkeypatch.setenv("MAGNITE_SECRET_KEY", "test-secret")
        monkeypatch.setenv("MAGNITE_SEAT_ID", "test-seat")

        with patch("ad_buyer.interfaces.mcp_server.MagniteConnector") as MockConnector:
            instance = MagicMock()
            instance.is_configured.return_value = True
            instance.test_connection.return_value = True
            instance.ssp_name = "Magnite"
            MockConnector.return_value = instance

            result = await mcp.call_tool("test_ssp_connection", {"ssp_name": "magnite"})
            data = json.loads(_extract_text(result))

        assert data["connected"] is True

    @pytest.mark.asyncio
    async def test_response_includes_timestamp(self):
        """test_ssp_connection response should always include a timestamp."""
        result = await mcp.call_tool("test_ssp_connection", {"ssp_name": "pubmatic"})
        data = json.loads(_extract_text(result))
        assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_index_exchange_not_configured_returns_false(self):
        """Index Exchange connection test without env vars → connected=false."""
        result = await mcp.call_tool("test_ssp_connection", {"ssp_name": "index_exchange"})
        data = json.loads(_extract_text(result))
        assert data["connected"] is False
