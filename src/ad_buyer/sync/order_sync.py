# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Order sync service for pulling seller order state into the buyer's local DB.

Provides both single-order and bulk sync methods. The sync is
event-driven (on demand) or can be called periodically.

The buyer always trusts the seller as the source of truth for order
status and audit trail. The local copy enables the buyer to:
- Check order status without depending on seller API availability
- Query audit trails locally for reporting
- Support offline/degraded-mode operation

bead: buyer-nz9 (Order Status & Audit API Integration)
"""

import logging
from typing import Any

from ..clients.seller_order_client import SellerOrderClient
from ..storage.order_store import OrderStore

logger = logging.getLogger(__name__)


class OrderSyncService:
    """Syncs order status from the seller API to the buyer's local OrderStore.

    Args:
        order_store: Local buyer-side order storage.
        seller_client: Client for the seller's order API.
    """

    def __init__(
        self,
        order_store: OrderStore,
        seller_client: SellerOrderClient,
    ) -> None:
        self._store = order_store
        self._client = seller_client

    async def sync_order(self, order_id: str) -> bool:
        """Sync a single order from the seller.

        Fetches the latest order state from the seller and updates the
        local copy. If the seller is unreachable or the order is not
        found, the local copy is left unchanged.

        Args:
            order_id: The order ID to sync.

        Returns:
            True if the local order was updated, False otherwise.
        """
        seller_data = await self._client.get_order_status(order_id)
        if seller_data is None:
            logger.info(
                "Sync skipped for order %s: seller returned None", order_id
            )
            return False

        # Update local store with seller's data
        self._store.set_order(order_id, seller_data)
        logger.info(
            "Synced order %s: status=%s",
            order_id,
            seller_data.get("status", "unknown"),
        )
        return True

    async def sync_all_orders(self) -> dict[str, Any]:
        """Sync all locally tracked orders from the seller.

        Iterates over all orders in the local store and syncs each one
        with the seller.

        Returns:
            Summary dict with sync results:
            - synced: number of orders synced
            - failed: number of orders that could not be synced
            - total: total number of orders attempted
        """
        local_orders = self._store.list_orders()
        synced = 0
        failed = 0

        for order_data in local_orders:
            order_id = order_data.get("order_id", "")
            if not order_id:
                continue
            try:
                success = await self.sync_order(order_id)
                if success:
                    synced += 1
                else:
                    failed += 1
            except Exception:
                logger.exception("Error syncing order %s", order_id)
                failed += 1

        total = synced + failed
        logger.info(
            "Sync complete: %d/%d orders synced, %d failed",
            synced,
            total,
            failed,
        )
        return {"synced": synced, "failed": failed, "total": total}
