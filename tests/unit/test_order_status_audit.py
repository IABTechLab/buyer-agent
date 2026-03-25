# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for Order Status & Audit API Integration (buyer-nz9).

Covers:
- Order storage CRUD (get_order, set_order, list_orders)
- Order state machine transitions
- Seller API client (with mocked responses)
- Order sync logic
- Buyer API endpoints (GET /api/v1/buyer/orders, GET /api/v1/buyer/orders/{id}/audit)
- Audit trail
"""

import json
import time
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ad_buyer.models.state_machine import (
    BuyerDealStatus,
    DealStateMachine,
    InvalidTransitionError,
    OrderAuditLog,
    StateTransition,
)
from ad_buyer.storage.order_store import OrderStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def order_store():
    """Create an in-memory OrderStore for testing."""
    store = OrderStore("sqlite:///:memory:")
    store.connect()
    yield store
    store.disconnect()


@pytest.fixture
def sample_order_data():
    """Minimal order data dict for testing."""
    return {
        "order_id": "ORD-TEST001",
        "deal_id": "deal-abc",
        "seller_url": "http://seller.example.com",
        "status": "pending",
        "audit_log": {
            "order_id": "ORD-TEST001",
            "transitions": [],
        },
        "created_at": "2026-03-01T00:00:00.000Z",
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# Order Store CRUD Tests
# ---------------------------------------------------------------------------


class TestOrderStoreCRUD:
    """Test order storage backend with order:{id} key format."""

    def test_set_and_get_order(self, order_store, sample_order_data):
        """set_order persists and get_order retrieves correctly."""
        order_store.set_order("ORD-TEST001", sample_order_data)
        result = order_store.get_order("ORD-TEST001")
        assert result is not None
        assert result["order_id"] == "ORD-TEST001"
        assert result["deal_id"] == "deal-abc"
        assert result["status"] == "pending"

    def test_get_order_not_found(self, order_store):
        """get_order returns None for nonexistent order."""
        result = order_store.get_order("ORD-NONEXISTENT")
        assert result is None

    def test_set_order_update(self, order_store, sample_order_data):
        """set_order overwrites existing data (upsert)."""
        order_store.set_order("ORD-TEST001", sample_order_data)

        updated_data = dict(sample_order_data)
        updated_data["status"] = "approved"
        order_store.set_order("ORD-TEST001", updated_data)

        result = order_store.get_order("ORD-TEST001")
        assert result["status"] == "approved"

    def test_list_orders_empty(self, order_store):
        """list_orders returns empty list when no orders exist."""
        result = order_store.list_orders()
        assert result == []

    def test_list_orders_returns_all(self, order_store, sample_order_data):
        """list_orders returns all stored orders."""
        order_store.set_order("ORD-TEST001", sample_order_data)

        order2 = dict(sample_order_data)
        order2["order_id"] = "ORD-TEST002"
        order2["deal_id"] = "deal-def"
        order_store.set_order("ORD-TEST002", order2)

        result = order_store.list_orders()
        assert len(result) == 2

    def test_list_orders_filter_by_status(self, order_store, sample_order_data):
        """list_orders filters by status when provided."""
        order_store.set_order("ORD-TEST001", sample_order_data)

        order2 = dict(sample_order_data)
        order2["order_id"] = "ORD-TEST002"
        order2["status"] = "approved"
        order_store.set_order("ORD-TEST002", order2)

        pending = order_store.list_orders(filters={"status": "pending"})
        assert len(pending) == 1
        assert pending[0]["order_id"] == "ORD-TEST001"

        approved = order_store.list_orders(filters={"status": "approved"})
        assert len(approved) == 1
        assert approved[0]["order_id"] == "ORD-TEST002"

    def test_order_key_format(self, order_store, sample_order_data):
        """Verify orders are stored with order:{id} key format internally."""
        order_store.set_order("ORD-TEST001", sample_order_data)
        # The key in the DB should use order: prefix
        with order_store._lock:
            cursor = order_store._conn.execute(
                "SELECT key FROM orders WHERE key = ?", ("order:ORD-TEST001",)
            )
            row = cursor.fetchone()
        assert row is not None

    def test_set_order_stores_audit_log(self, order_store, sample_order_data):
        """Audit log is preserved through set/get cycle."""
        sample_order_data["audit_log"]["transitions"] = [
            {
                "transition_id": "t-1",
                "from_status": "pending",
                "to_status": "approved",
                "timestamp": "2026-03-01T01:00:00Z",
                "actor": "system",
                "reason": "test",
                "metadata": {},
            }
        ]
        order_store.set_order("ORD-TEST001", sample_order_data)
        result = order_store.get_order("ORD-TEST001")
        assert len(result["audit_log"]["transitions"]) == 1
        assert result["audit_log"]["transitions"][0]["to_status"] == "approved"


# ---------------------------------------------------------------------------
# Order State Machine Tests
# ---------------------------------------------------------------------------


class TestOrderStateMachine:
    """Test DealStateMachine transitions (used as order state machine on buyer)."""

    def test_happy_path_transitions(self):
        """Walk through the happy path: quoted -> negotiating -> accepted -> booking -> booked."""
        machine = DealStateMachine("ORD-001")
        assert machine.status == BuyerDealStatus.QUOTED

        machine.transition(BuyerDealStatus.NEGOTIATING, actor="agent:buyer")
        assert machine.status == BuyerDealStatus.NEGOTIATING

        machine.transition(BuyerDealStatus.ACCEPTED, actor="agent:buyer")
        assert machine.status == BuyerDealStatus.ACCEPTED

        machine.transition(BuyerDealStatus.BOOKING, actor="system")
        assert machine.status == BuyerDealStatus.BOOKING

        machine.transition(BuyerDealStatus.BOOKED, actor="seller")
        assert machine.status == BuyerDealStatus.BOOKED

    def test_invalid_transition_raises(self):
        """Cannot skip states or go backwards without a rule."""
        machine = DealStateMachine("ORD-002")
        with pytest.raises(InvalidTransitionError):
            machine.transition(BuyerDealStatus.BOOKED)

    def test_audit_log_records_transitions(self):
        """Each transition creates an audit log entry."""
        machine = DealStateMachine("ORD-003")
        machine.transition(BuyerDealStatus.NEGOTIATING, actor="agent:buyer", reason="starting neg")
        machine.transition(BuyerDealStatus.ACCEPTED, actor="agent:buyer", reason="deal done")

        assert len(machine.audit_log.transitions) == 2
        assert machine.audit_log.transitions[0].from_status == "quoted"
        assert machine.audit_log.transitions[0].to_status == "negotiating"
        assert machine.audit_log.transitions[1].to_status == "accepted"

    def test_to_dict_and_from_dict_roundtrip(self):
        """State machine can be serialized and restored."""
        machine = DealStateMachine("ORD-004")
        machine.transition(BuyerDealStatus.NEGOTIATING, actor="system")

        data = machine.to_dict()
        restored = DealStateMachine.from_dict(data)

        assert restored.status == BuyerDealStatus.NEGOTIATING
        assert restored.order_id == "ORD-004"
        assert len(restored.audit_log.transitions) == 1

    def test_cancellation_from_any_active_state(self):
        """Can cancel from any active state."""
        for status in [
            BuyerDealStatus.QUOTED,
            BuyerDealStatus.NEGOTIATING,
            BuyerDealStatus.ACCEPTED,
            BuyerDealStatus.BOOKING,
            BuyerDealStatus.BOOKED,
            BuyerDealStatus.DELIVERING,
        ]:
            machine = DealStateMachine("ORD-cancel", initial_status=status)
            machine.transition(BuyerDealStatus.CANCELLED, reason="cancel test")
            assert machine.status == BuyerDealStatus.CANCELLED


# ---------------------------------------------------------------------------
# Seller API Client Tests
# ---------------------------------------------------------------------------


class TestSellerOrderClient:
    """Test the buyer's client for calling seller order endpoints."""

    @pytest.mark.asyncio
    async def test_get_order_status(self):
        """Client fetches order status from seller."""
        from ad_buyer.clients.seller_order_client import SellerOrderClient

        mock_response_data = {
            "order_id": "ORD-SELLER001",
            "status": "approved",
            "deal_id": "deal-xyz",
            "audit_log": {"order_id": "ORD-SELLER001", "transitions": []},
            "created_at": "2026-03-01T00:00:00Z",
            "metadata": {},
        }

        def mock_handler(request):
            return httpx.Response(200, json=mock_response_data)

        client = SellerOrderClient(base_url="http://seller.test:8001")

        with patch(
            "httpx.AsyncClient",
            return_value=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)),
        ):
            result = await client.get_order_status("ORD-SELLER001")

        assert result["order_id"] == "ORD-SELLER001"
        assert result["status"] == "approved"

    @pytest.mark.asyncio
    async def test_get_order_history(self):
        """Client fetches order transition history from seller."""
        from ad_buyer.clients.seller_order_client import SellerOrderClient

        mock_response_data = {
            "order_id": "ORD-SELLER001",
            "current_status": "approved",
            "transitions": [
                {
                    "transition_id": "t-1",
                    "from_status": "pending",
                    "to_status": "approved",
                    "timestamp": "2026-03-01T01:00:00Z",
                    "actor": "system",
                    "reason": "auto-approved",
                    "metadata": {},
                }
            ],
            "transition_count": 1,
        }

        def mock_handler(request):
            return httpx.Response(200, json=mock_response_data)

        client = SellerOrderClient(base_url="http://seller.test:8001")

        with patch(
            "httpx.AsyncClient",
            return_value=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)),
        ):
            result = await client.get_order_history("ORD-SELLER001")

        assert result["order_id"] == "ORD-SELLER001"
        assert len(result["transitions"]) == 1
        assert result["transitions"][0]["to_status"] == "approved"

    @pytest.mark.asyncio
    async def test_get_order_status_not_found(self):
        """Client handles 404 from seller gracefully."""
        from ad_buyer.clients.seller_order_client import SellerOrderClient

        def mock_handler(request):
            return httpx.Response(
                404,
                json={"error": "order_not_found", "message": "Not found"},
            )

        client = SellerOrderClient(base_url="http://seller.test:8001")

        with patch(
            "httpx.AsyncClient",
            return_value=httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)),
        ):
            result = await client.get_order_status("ORD-NOPE")

        assert result is None


# ---------------------------------------------------------------------------
# Order Sync Tests
# ---------------------------------------------------------------------------


class TestOrderSync:
    """Test sync logic that pulls order status from seller to local buyer DB."""

    @pytest.mark.asyncio
    async def test_sync_updates_local_order(self, order_store, sample_order_data):
        """Sync updates local order when seller has a newer status."""
        from ad_buyer.clients.seller_order_client import SellerOrderClient
        from ad_buyer.sync.order_sync import OrderSyncService

        # Store an initial order locally
        order_store.set_order("ORD-TEST001", sample_order_data)

        # Mock seller client returning an updated status
        seller_data = {
            "order_id": "ORD-TEST001",
            "status": "approved",
            "deal_id": "deal-abc",
            "audit_log": {
                "order_id": "ORD-TEST001",
                "transitions": [
                    {
                        "transition_id": "t-1",
                        "from_status": "pending",
                        "to_status": "approved",
                        "timestamp": "2026-03-01T01:00:00Z",
                        "actor": "system",
                        "reason": "auto-approved",
                        "metadata": {},
                    }
                ],
            },
            "created_at": "2026-03-01T00:00:00Z",
            "metadata": {},
        }

        mock_client = AsyncMock(spec=SellerOrderClient)
        mock_client.get_order_status.return_value = seller_data

        sync_service = OrderSyncService(
            order_store=order_store,
            seller_client=mock_client,
        )

        await sync_service.sync_order("ORD-TEST001")

        # Verify local order was updated
        local_order = order_store.get_order("ORD-TEST001")
        assert local_order["status"] == "approved"
        assert len(local_order["audit_log"]["transitions"]) == 1

    @pytest.mark.asyncio
    async def test_sync_skips_when_seller_unavailable(self, order_store, sample_order_data):
        """Sync does not modify local order when seller returns None."""
        from ad_buyer.clients.seller_order_client import SellerOrderClient
        from ad_buyer.sync.order_sync import OrderSyncService

        order_store.set_order("ORD-TEST001", sample_order_data)

        mock_client = AsyncMock(spec=SellerOrderClient)
        mock_client.get_order_status.return_value = None

        sync_service = OrderSyncService(
            order_store=order_store,
            seller_client=mock_client,
        )

        await sync_service.sync_order("ORD-TEST001")

        # Local order should be unchanged
        local_order = order_store.get_order("ORD-TEST001")
        assert local_order["status"] == "pending"

    @pytest.mark.asyncio
    async def test_sync_all_orders(self, order_store, sample_order_data):
        """sync_all syncs each local order with the seller."""
        from ad_buyer.clients.seller_order_client import SellerOrderClient
        from ad_buyer.sync.order_sync import OrderSyncService

        order_store.set_order("ORD-TEST001", sample_order_data)

        order2 = dict(sample_order_data)
        order2["order_id"] = "ORD-TEST002"
        order_store.set_order("ORD-TEST002", order2)

        seller_data = {
            "order_id": "ORD-TEST001",
            "status": "approved",
            "deal_id": "deal-abc",
            "audit_log": {"order_id": "ORD-TEST001", "transitions": []},
            "created_at": "2026-03-01T00:00:00Z",
            "metadata": {},
        }

        mock_client = AsyncMock(spec=SellerOrderClient)
        mock_client.get_order_status.return_value = seller_data

        sync_service = OrderSyncService(
            order_store=order_store,
            seller_client=mock_client,
        )

        results = await sync_service.sync_all_orders()
        assert results["synced"] == 2
        assert mock_client.get_order_status.call_count == 2


# ---------------------------------------------------------------------------
# Buyer API Endpoint Tests
# ---------------------------------------------------------------------------


class TestBuyerOrderEndpoints:
    """Test the buyer-side order API endpoints."""

    @pytest.fixture
    def api_client(self, order_store):
        """Create a test client for the buyer API with order endpoints."""
        from fastapi.testclient import TestClient

        from ad_buyer.interfaces.api.order_endpoints import create_order_router

        from fastapi import FastAPI

        test_app = FastAPI()
        router = create_order_router(order_store)
        test_app.include_router(router)

        return TestClient(test_app)

    def test_list_orders_empty(self, api_client):
        """GET /api/v1/buyer/orders returns empty list initially."""
        response = api_client.get("/api/v1/buyer/orders")
        assert response.status_code == 200
        data = response.json()
        assert data["orders"] == []
        assert data["count"] == 0

    def test_list_orders_with_data(self, api_client, order_store, sample_order_data):
        """GET /api/v1/buyer/orders returns stored orders."""
        order_store.set_order("ORD-TEST001", sample_order_data)

        response = api_client.get("/api/v1/buyer/orders")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["orders"][0]["order_id"] == "ORD-TEST001"

    def test_list_orders_status_filter(self, api_client, order_store, sample_order_data):
        """GET /api/v1/buyer/orders?status=pending filters correctly."""
        order_store.set_order("ORD-TEST001", sample_order_data)

        order2 = dict(sample_order_data)
        order2["order_id"] = "ORD-TEST002"
        order2["status"] = "approved"
        order_store.set_order("ORD-TEST002", order2)

        response = api_client.get("/api/v1/buyer/orders?status=pending")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["orders"][0]["status"] == "pending"

    def test_get_order_audit(self, api_client, order_store, sample_order_data):
        """GET /api/v1/buyer/orders/{id}/audit returns audit trail."""
        sample_order_data["audit_log"]["transitions"] = [
            {
                "transition_id": "t-1",
                "from_status": "pending",
                "to_status": "approved",
                "timestamp": "2026-03-01T01:00:00Z",
                "actor": "system",
                "reason": "auto-approved",
                "metadata": {},
            }
        ]
        order_store.set_order("ORD-TEST001", sample_order_data)

        response = api_client.get("/api/v1/buyer/orders/ORD-TEST001/audit")
        assert response.status_code == 200
        data = response.json()
        assert data["order_id"] == "ORD-TEST001"
        assert data["transition_count"] == 1
        assert data["transitions"][0]["to_status"] == "approved"

    def test_get_order_audit_not_found(self, api_client):
        """GET /api/v1/buyer/orders/{id}/audit returns 404 for missing order."""
        response = api_client.get("/api/v1/buyer/orders/ORD-NOPE/audit")
        assert response.status_code == 404

    def test_get_order_audit_empty_trail(self, api_client, order_store, sample_order_data):
        """GET /api/v1/buyer/orders/{id}/audit returns empty trail for new order."""
        order_store.set_order("ORD-TEST001", sample_order_data)

        response = api_client.get("/api/v1/buyer/orders/ORD-TEST001/audit")
        assert response.status_code == 200
        data = response.json()
        assert data["transition_count"] == 0
        assert data["transitions"] == []
