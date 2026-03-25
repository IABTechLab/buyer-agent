# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""HTTP client for the seller's Order Status & Audit API.

Provides async methods to query the seller's order endpoints:
- GET /api/v1/orders/{order_id}         -- current order status
- GET /api/v1/orders/{order_id}/history -- full transition history

Used by the OrderSyncService to pull seller-side order state into the
buyer's local database.

bead: buyer-nz9 (Order Status & Audit API Integration)
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class SellerOrderClient:
    """Async client for seller order API endpoints.

    Args:
        base_url: Base URL of the seller API (e.g. ``http://localhost:8001``).
        api_key: Optional API key for authenticated requests.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def _build_headers(self) -> dict[str, str]:
        """Build request headers, including auth if configured."""
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        return headers

    async def get_order_status(self, order_id: str) -> dict[str, Any] | None:
        """Fetch the current status of an order from the seller.

        Calls GET /api/v1/orders/{order_id} on the seller API.

        Args:
            order_id: The order ID to query.

        Returns:
            Order data dict from the seller, or None if not found
            or the seller is unreachable.
        """
        url = f"{self._base_url}/api/v1/orders/{order_id}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, headers=self._build_headers())
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.info("Order %s not found on seller", order_id)
                return None
            logger.error(
                "Seller API error for order %s: %s %s",
                order_id,
                e.response.status_code,
                str(e),
            )
            return None
        except (httpx.RequestError, OSError) as e:
            logger.error("Failed to reach seller for order %s: %s", order_id, e)
            return None

    async def get_order_history(self, order_id: str) -> dict[str, Any] | None:
        """Fetch the full transition history for an order from the seller.

        Calls GET /api/v1/orders/{order_id}/history on the seller API.

        Args:
            order_id: The order ID to query.

        Returns:
            History data dict with transitions list, or None if not found
            or the seller is unreachable.
        """
        url = f"{self._base_url}/api/v1/orders/{order_id}/history"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, headers=self._build_headers())
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.info("Order history for %s not found on seller", order_id)
                return None
            logger.error(
                "Seller API error for order %s history: %s %s",
                order_id,
                e.response.status_code,
                str(e),
            )
            return None
        except (httpx.RequestError, OSError) as e:
            logger.error(
                "Failed to reach seller for order %s history: %s", order_id, e
            )
            return None
