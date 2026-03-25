# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for Negotiation & Orders MCP tools.

Tests six MCP tools: start_negotiation, get_negotiation_status,
list_active_negotiations, list_orders, get_order_status, transition_order.

bead: buyer-r0j
"""

import json

import pytest

from ad_buyer.interfaces.mcp_server import mcp
from ad_buyer.storage.deal_store import DealStore
from ad_buyer.storage.order_store import OrderStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DB_URL = "sqlite:///:memory:"


def _extract_text(call_result) -> str:
    """Extract text content from a FastMCP call_tool result."""
    content_list = call_result[0]
    return content_list[0].text


def _make_deal_store() -> DealStore:
    """Create and connect a file-backed DealStore for testing.

    Uses a temp file rather than :memory: so the database survives
    disconnect/reconnect cycles in MCP tool finally blocks.
    """
    import tempfile
    import os
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = DealStore(f"sqlite:///{path}")
    store.connect()
    return store


def _make_order_store() -> OrderStore:
    """Create and connect a file-backed OrderStore for testing.

    Uses a temp file rather than :memory: so the database survives
    disconnect/reconnect cycles in MCP tool finally blocks.
    """
    import tempfile
    import os
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = OrderStore(f"sqlite:///{path}")
    store.connect()
    return store


def _reconnecting(store):
    """Return a lambda that reconnects the store before returning it.

    MCP tools call store.disconnect() in their finally blocks. For
    multi-call tests, the store needs to be reconnected on each access.
    """
    def _get():
        if store._conn is None:
            store.connect()
        return store
    return _get


def _seed_deal(store: DealStore, **overrides) -> str:
    """Create a deal with sensible defaults. Returns deal_id."""
    defaults = {
        "seller_url": "http://localhost:8001",
        "product_id": "pkg-001",
        "product_name": "Premium CTV Package",
        "deal_type": "PD",
        "status": "negotiating",
        "price": 25.0,
    }
    defaults.update(overrides)
    return store.save_deal(**defaults)


def _seed_negotiation_rounds(
    store: DealStore,
    deal_id: str,
    proposal_id: str = "prop-001",
    rounds: int = 2,
) -> None:
    """Add negotiation rounds to a deal."""
    for i in range(1, rounds + 1):
        store.save_negotiation_round(
            deal_id=deal_id,
            proposal_id=proposal_id,
            round_number=i,
            buyer_price=20.0 + i,
            seller_price=30.0 - i,
            action="counter" if i < rounds else "accept",
            rationale=f"Round {i} rationale",
        )


def _seed_order(store: OrderStore, order_id: str, **overrides) -> None:
    """Create an order in the OrderStore."""
    defaults = {
        "order_id": order_id,
        "deal_id": "deal-001",
        "status": "pending",
        "seller_url": "http://localhost:8001",
        "created_at": "2026-03-15T12:00:00Z",
    }
    defaults.update(overrides)
    store.set_order(order_id, defaults)


# ---------------------------------------------------------------------------
# Test tool registration
# ---------------------------------------------------------------------------


class TestNegotiationOrderToolRegistration:
    """Verify all 6 negotiation/order MCP tools are registered."""

    @pytest.mark.asyncio
    async def test_start_negotiation_registered(self):
        """start_negotiation should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "start_negotiation" in names

    @pytest.mark.asyncio
    async def test_get_negotiation_status_registered(self):
        """get_negotiation_status should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "get_negotiation_status" in names

    @pytest.mark.asyncio
    async def test_list_active_negotiations_registered(self):
        """list_active_negotiations should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "list_active_negotiations" in names

    @pytest.mark.asyncio
    async def test_list_orders_registered(self):
        """list_orders should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "list_orders" in names

    @pytest.mark.asyncio
    async def test_get_order_status_registered(self):
        """get_order_status should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "get_order_status" in names

    @pytest.mark.asyncio
    async def test_transition_order_registered(self):
        """transition_order should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "transition_order" in names


# ---------------------------------------------------------------------------
# Test start_negotiation
# ---------------------------------------------------------------------------


class TestStartNegotiation:
    """Tests for the start_negotiation MCP tool."""

    @pytest.mark.asyncio
    async def test_start_negotiation_creates_session(self, monkeypatch):
        """start_negotiation should create a deal in negotiating status."""
        deal_store = _make_deal_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_deal_store", _reconnecting(deal_store)
        )

        result = await mcp.call_tool("start_negotiation", {
            "seller_url": "http://localhost:8001",
            "product_id": "pkg-001",
            "product_name": "Premium CTV",
            "initial_price": 20.0,
        })
        data = json.loads(_extract_text(result))

        assert "deal_id" in data
        assert data["status"] == "negotiating"
        assert data["initial_price"] == 20.0

    @pytest.mark.asyncio
    async def test_start_negotiation_records_first_round(self, monkeypatch):
        """start_negotiation should record a round visible via get_negotiation_status."""
        deal_store = _make_deal_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_deal_store", _reconnecting(deal_store)
        )

        result = await mcp.call_tool("start_negotiation", {
            "seller_url": "http://localhost:8001",
            "product_id": "pkg-001",
            "product_name": "Premium CTV",
            "initial_price": 20.0,
        })
        data = json.loads(_extract_text(result))
        deal_id = data["deal_id"]

        # Verify round was recorded via the get_negotiation_status tool
        status_result = await mcp.call_tool(
            "get_negotiation_status", {"deal_id": deal_id}
        )
        status_data = json.loads(_extract_text(status_result))

        assert status_data["rounds_count"] == 1
        assert status_data["rounds"][0]["buyer_price"] == 20.0
        assert status_data["rounds"][0]["round_number"] == 1

    @pytest.mark.asyncio
    async def test_start_negotiation_returns_json(self, monkeypatch):
        """start_negotiation should return valid JSON."""
        deal_store = _make_deal_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_deal_store", _reconnecting(deal_store)
        )

        result = await mcp.call_tool("start_negotiation", {
            "seller_url": "http://localhost:8001",
            "product_id": "pkg-001",
            "product_name": "Premium CTV",
            "initial_price": 20.0,
        })
        data = json.loads(_extract_text(result))
        assert isinstance(data, dict)
        assert "timestamp" in data


# ---------------------------------------------------------------------------
# Test get_negotiation_status
# ---------------------------------------------------------------------------


class TestGetNegotiationStatus:
    """Tests for the get_negotiation_status MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_deal_not_found(self, monkeypatch):
        """get_negotiation_status should return error for unknown deal."""
        deal_store = _make_deal_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_deal_store", _reconnecting(deal_store)
        )

        result = await mcp.call_tool("get_negotiation_status", {
            "deal_id": "nonexistent"
        })
        data = json.loads(_extract_text(result))
        assert "error" in data

    @pytest.mark.asyncio
    async def test_returns_deal_with_rounds(self, monkeypatch):
        """get_negotiation_status should return deal info and negotiation rounds."""
        deal_store = _make_deal_store()
        deal_id = _seed_deal(deal_store, status="negotiating")
        _seed_negotiation_rounds(deal_store, deal_id, rounds=3)
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_deal_store", _reconnecting(deal_store)
        )

        result = await mcp.call_tool("get_negotiation_status", {
            "deal_id": deal_id
        })
        data = json.loads(_extract_text(result))

        assert data["deal_id"] == deal_id
        assert data["status"] == "negotiating"
        assert len(data["rounds"]) == 3
        assert data["rounds_count"] == 3

    @pytest.mark.asyncio
    async def test_returns_deal_with_no_rounds(self, monkeypatch):
        """get_negotiation_status should handle deals with no rounds."""
        deal_store = _make_deal_store()
        deal_id = _seed_deal(deal_store, status="quoted")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_deal_store", _reconnecting(deal_store)
        )

        result = await mcp.call_tool("get_negotiation_status", {
            "deal_id": deal_id
        })
        data = json.loads(_extract_text(result))

        assert data["deal_id"] == deal_id
        assert data["rounds"] == []
        assert data["rounds_count"] == 0


# ---------------------------------------------------------------------------
# Test list_active_negotiations
# ---------------------------------------------------------------------------


class TestListActiveNegotiations:
    """Tests for the list_active_negotiations MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_none(self, monkeypatch):
        """list_active_negotiations should return empty list when no deals."""
        deal_store = _make_deal_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_deal_store", _reconnecting(deal_store)
        )

        result = await mcp.call_tool("list_active_negotiations", {})
        data = json.loads(_extract_text(result))
        assert data["negotiations"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_returns_only_negotiating_deals(self, monkeypatch):
        """list_active_negotiations should only return deals in negotiating status."""
        deal_store = _make_deal_store()
        _seed_deal(deal_store, status="negotiating", product_name="Deal A")
        _seed_deal(deal_store, status="accepted", product_name="Deal B")
        _seed_deal(deal_store, status="negotiating", product_name="Deal C")
        _seed_deal(deal_store, status="booked", product_name="Deal D")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_deal_store", _reconnecting(deal_store)
        )

        result = await mcp.call_tool("list_active_negotiations", {})
        data = json.loads(_extract_text(result))

        assert data["total"] == 2
        names = {n["product_name"] for n in data["negotiations"]}
        assert names == {"Deal A", "Deal C"}

    @pytest.mark.asyncio
    async def test_includes_round_count(self, monkeypatch):
        """Each negotiation entry should include a round count."""
        deal_store = _make_deal_store()
        deal_id = _seed_deal(deal_store, status="negotiating")
        _seed_negotiation_rounds(deal_store, deal_id, rounds=3)
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_deal_store", _reconnecting(deal_store)
        )

        result = await mcp.call_tool("list_active_negotiations", {})
        data = json.loads(_extract_text(result))

        assert data["total"] == 1
        assert data["negotiations"][0]["rounds_count"] == 3


# ---------------------------------------------------------------------------
# Test list_orders
# ---------------------------------------------------------------------------


class TestListOrders:
    """Tests for the list_orders MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_none(self, monkeypatch):
        """list_orders should return empty list when no orders exist."""
        order_store = _make_order_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_order_store", _reconnecting(order_store)
        )

        result = await mcp.call_tool("list_orders", {})
        data = json.loads(_extract_text(result))
        assert data["orders"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_lists_all_orders(self, monkeypatch):
        """list_orders should return all orders when no filter."""
        order_store = _make_order_store()
        _seed_order(order_store, "order-001", status="pending")
        _seed_order(order_store, "order-002", status="booked")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_order_store", _reconnecting(order_store)
        )

        result = await mcp.call_tool("list_orders", {})
        data = json.loads(_extract_text(result))
        assert data["total"] == 2

    @pytest.mark.asyncio
    async def test_filters_by_status(self, monkeypatch):
        """list_orders should filter by status when provided."""
        order_store = _make_order_store()
        _seed_order(order_store, "order-001", status="pending")
        _seed_order(order_store, "order-002", status="booked")
        _seed_order(order_store, "order-003", status="pending")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_order_store", _reconnecting(order_store)
        )

        result = await mcp.call_tool("list_orders", {"status": "pending"})
        data = json.loads(_extract_text(result))
        assert data["total"] == 2

    @pytest.mark.asyncio
    async def test_includes_timestamp(self, monkeypatch):
        """list_orders should include a timestamp."""
        order_store = _make_order_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_order_store", _reconnecting(order_store)
        )

        result = await mcp.call_tool("list_orders", {})
        data = json.loads(_extract_text(result))
        assert "timestamp" in data


# ---------------------------------------------------------------------------
# Test get_order_status
# ---------------------------------------------------------------------------


class TestGetOrderStatus:
    """Tests for the get_order_status MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_order_not_found(self, monkeypatch):
        """get_order_status should return error for unknown order."""
        order_store = _make_order_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_order_store", _reconnecting(order_store)
        )

        result = await mcp.call_tool("get_order_status", {
            "order_id": "nonexistent"
        })
        data = json.loads(_extract_text(result))
        assert "error" in data

    @pytest.mark.asyncio
    async def test_returns_order_details(self, monkeypatch):
        """get_order_status should return full order details."""
        order_store = _make_order_store()
        _seed_order(
            order_store,
            "order-001",
            status="booked",
            deal_id="deal-001",
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_order_store", _reconnecting(order_store)
        )

        result = await mcp.call_tool("get_order_status", {
            "order_id": "order-001"
        })
        data = json.loads(_extract_text(result))

        assert data["order_id"] == "order-001"
        assert data["status"] == "booked"
        assert data["deal_id"] == "deal-001"

    @pytest.mark.asyncio
    async def test_includes_timestamp(self, monkeypatch):
        """get_order_status should include a timestamp."""
        order_store = _make_order_store()
        _seed_order(order_store, "order-001")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_order_store", _reconnecting(order_store)
        )

        result = await mcp.call_tool("get_order_status", {
            "order_id": "order-001"
        })
        data = json.loads(_extract_text(result))
        assert "timestamp" in data


# ---------------------------------------------------------------------------
# Test transition_order
# ---------------------------------------------------------------------------


class TestTransitionOrder:
    """Tests for the transition_order MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_order_not_found(self, monkeypatch):
        """transition_order should return error for unknown order."""
        order_store = _make_order_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_order_store", _reconnecting(order_store)
        )

        result = await mcp.call_tool("transition_order", {
            "order_id": "nonexistent",
            "to_status": "booked",
        })
        data = json.loads(_extract_text(result))
        assert "error" in data

    @pytest.mark.asyncio
    async def test_successful_transition(self, monkeypatch):
        """transition_order should update the order status."""
        order_store = _make_order_store()
        _seed_order(order_store, "order-001", status="pending")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_order_store", _reconnecting(order_store)
        )

        result = await mcp.call_tool("transition_order", {
            "order_id": "order-001",
            "to_status": "booked",
        })
        data = json.loads(_extract_text(result))

        assert data["order_id"] == "order-001"
        assert data["new_status"] == "booked"
        assert data["previous_status"] == "pending"

        # Verify the order was actually updated via get_order_status
        status_result = await mcp.call_tool(
            "get_order_status", {"order_id": "order-001"}
        )
        status_data = json.loads(_extract_text(status_result))
        assert status_data["status"] == "booked"

    @pytest.mark.asyncio
    async def test_transition_with_reason(self, monkeypatch):
        """transition_order should accept and store a reason."""
        order_store = _make_order_store()
        _seed_order(order_store, "order-001", status="pending")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_order_store", _reconnecting(order_store)
        )

        result = await mcp.call_tool("transition_order", {
            "order_id": "order-001",
            "to_status": "booked",
            "reason": "Seller confirmed booking",
        })
        data = json.loads(_extract_text(result))

        assert data["reason"] == "Seller confirmed booking"

    @pytest.mark.asyncio
    async def test_transition_includes_timestamp(self, monkeypatch):
        """transition_order should include a timestamp."""
        order_store = _make_order_store()
        _seed_order(order_store, "order-001", status="pending")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_order_store", _reconnecting(order_store)
        )

        result = await mcp.call_tool("transition_order", {
            "order_id": "order-001",
            "to_status": "booked",
        })
        data = json.loads(_extract_text(result))
        assert "timestamp" in data
