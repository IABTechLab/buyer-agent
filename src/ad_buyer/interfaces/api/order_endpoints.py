# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Buyer-side order status and audit API endpoints.

Provides:
- GET /api/v1/buyer/orders           -- list buyer's orders with optional status filter
- GET /api/v1/buyer/orders/{id}/audit -- local audit trail for an order

These endpoints query the buyer's local OrderStore, which is populated
by the OrderSyncService pulling state from the seller's Order API.

bead: buyer-nz9 (Order Status & Audit API Integration)
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from ...storage.order_store import OrderStore

logger = logging.getLogger(__name__)


def create_order_router(order_store: OrderStore) -> APIRouter:
    """Create a FastAPI router for buyer order endpoints.

    Args:
        order_store: The OrderStore instance to query.

    Returns:
        Configured APIRouter with order endpoints.
    """
    router = APIRouter(prefix="/api/v1/buyer", tags=["Buyer Orders"])

    @router.get("/orders")
    async def list_buyer_orders(status: Optional[str] = None):
        """List buyer's orders from local DB with optional status filter.

        Args:
            status: Optional status string to filter orders by.

        Returns:
            Dict with ``orders`` list and ``count``.
        """
        filters = {}
        if status:
            filters["status"] = status

        orders = order_store.list_orders(filters=filters if filters else None)
        return {"orders": orders, "count": len(orders)}

    @router.get("/orders/{order_id}/audit")
    async def get_order_audit(order_id: str):
        """Get the local audit trail for an order.

        Args:
            order_id: The order ID to query.

        Returns:
            Dict with order_id, current_status, transitions list,
            and transition_count.

        Raises:
            HTTPException(404): If the order is not found locally.
        """
        order = order_store.get_order(order_id)
        if order is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "order_not_found",
                    "message": f"Order '{order_id}' not found in local store.",
                },
            )

        audit_log = order.get("audit_log", {})
        transitions = audit_log.get("transitions", [])

        return {
            "order_id": order_id,
            "current_status": order.get("status"),
            "transitions": transitions,
            "transition_count": len(transitions),
        }

    return router
