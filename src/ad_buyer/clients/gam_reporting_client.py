# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""GAM reporting client — reads delivery data from Google Ad Manager REST API.

Adapted from the seller agent's GAMRestClient (clients/gam_rest_client.py).
Read-only — the seller agent handles all write operations (order, line item creation).

Auth: Google service account JSON key file.
Requires: pip install google-api-python-client google-auth

Usage:
    async with GAMReportingClient() as client:
        data = await client.get_delivery_report(["ORD-A255", "ORD-81A3"])
"""

from typing import Any

from ..config.settings import settings as _settings


class GAMReportingClient:
    """Read-only Google Ad Manager REST API client for delivery reporting.

    Uses the GAM REST API v1 via google-api-python-client with a service
    account credential. The same service account used by the seller agent
    can be reused here with read-only permissions.
    """

    def __init__(
        self,
        network_code: str | None = None,
        credentials_path: str | None = None,
        application_name: str | None = None,
    ):
        self._network_code    = network_code    or _settings.gam_network_code
        self._credentials_path = credentials_path or _settings.gam_json_key_path
        self._application_name = application_name or _settings.gam_application_name
        self._service: Any | None = None

    async def __aenter__(self) -> "GAMReportingClient":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect()

    async def connect(self) -> None:
        """Connect to GAM REST API using service account credentials."""
        if not self._network_code or not self._credentials_path:
            raise ValueError(
                "GAM_NETWORK_CODE and GAM_JSON_KEY_PATH must be set in .env. "
                "See ActionPlanGAMImplement.md for service account setup steps."
            )
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            credentials = service_account.Credentials.from_service_account_file(
                self._credentials_path,
                scopes=["https://www.googleapis.com/auth/admanager"],
            )
            self._service = build(
                "admanager",
                "v1",
                credentials=credentials,
                cache_discovery=False,
            )
        except ImportError:
            raise ImportError(
                "GAM reporting requires google-api-python-client and google-auth.\n"
                "Install with: pip install google-api-python-client google-auth"
            )

    async def disconnect(self) -> None:
        self._service = None

    def _ensure_connected(self) -> None:
        if not self._service:
            raise RuntimeError("Not connected. Use 'async with GAMReportingClient()' context.")

    def _parent(self) -> str:
        return f"networks/{self._network_code}"

    # -------------------------------------------------------------------------
    # Order
    # -------------------------------------------------------------------------

    async def get_order(self, order_id: str) -> dict[str, Any]:
        """Get a GAM order by ID.

        Args:
            order_id: GAM order ID (numeric portion of ORD-xxxx or the full ID)

        Returns:
            Dict with displayName, status, advertiser fields
        """
        self._ensure_connected()
        name = f"{self._parent()}/orders/{order_id}"
        return self._service.networks().orders().get(name=name).execute()

    async def list_line_items(
        self,
        order_id: str,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """List line items for a given order."""
        self._ensure_connected()
        filter_str = f'order="{self._parent()}/orders/{order_id}"'
        resp = (
            self._service.networks()
            .lineItems()
            .list(parent=self._parent(), pageSize=page_size, filter=filter_str)
            .execute()
        )
        return resp.get("lineItems", [])

    # -------------------------------------------------------------------------
    # Reports
    # -------------------------------------------------------------------------

    async def run_delivery_report(
        self,
        order_ids: list[str],
        date_range_type: str = "LAST_30_DAYS",
    ) -> dict[str, Any]:
        """Submit a GAM delivery report job for the given order IDs.

        Args:
            order_ids: List of GAM order IDs
            date_range_type: LAST_7_DAYS | LAST_30_DAYS | LAST_90_DAYS |
                             THIS_MONTH | LAST_MONTH

        Returns:
            Raw report job response from GAM API
        """
        self._ensure_connected()

        order_filter_values = ", ".join(f"'{oid}'" for oid in order_ids)

        report_query = {
            "displayName": f"Buyer Delivery Report — {date_range_type}",
            "reportQuery": {
                "dimensions": [
                    "ORDER_ID",
                    "ORDER_NAME",
                    "LINE_ITEM_ID",
                    "LINE_ITEM_NAME",
                ],
                "metrics": [
                    "AD_SERVER_IMPRESSIONS",
                    "AD_SERVER_CLICKS",
                    "AD_SERVER_CTR",
                    "AD_SERVER_CPM_AND_CPC_REVENUE",
                    "AD_SERVER_WITHOUT_CPD_AVERAGE_ECPM",
                    "AD_SERVER_VIEWABILITY",
                ],
                "dateRangeType": date_range_type,
                "statement": {
                    "query": f"WHERE order_id IN ({order_filter_values})"
                },
            },
        }

        return (
            self._service.networks()
            .reports()
            .create(parent=self._parent(), body=report_query)
            .execute()
        )

    # -------------------------------------------------------------------------
    # High-level combined fetch
    # -------------------------------------------------------------------------

    async def get_delivery_report(
        self,
        order_ids: list[str],
        date_range_type: str = "LAST_30_DAYS",
    ) -> dict[str, Any]:
        """Fetch order + line item status and submit a delivery report job.

        Combines get_order + list_line_items + run_delivery_report for each
        order ID into one structured response for GAMReportingTool.

        Returns:
            {
                "orders": [
                    {
                        "order_id": str,
                        "order_name": str,
                        "status": str,
                        "line_items": [{"id", "name", "status", "impressions_goal"}]
                    }
                ],
                "report_job": {...}
            }
        """
        results: list[dict[str, Any]] = []

        for order_id in order_ids:
            entry: dict[str, Any] = {"order_id": order_id}
            try:
                order = await self.get_order(order_id)
                entry["order_name"] = order.get("displayName", order_id)
                entry["status"]     = order.get("status", "UNKNOWN")
                entry["advertiser"] = order.get("advertiser", "")
            except Exception as e:
                entry["order_name"] = order_id
                entry["status"]     = "ERROR"
                entry["error"]      = str(e)

            try:
                line_items = await self.list_line_items(order_id)
                entry["line_items"] = [
                    {
                        "id":               li.get("name", "").split("/")[-1],
                        "name":             li.get("displayName", ""),
                        "status":           li.get("status", "UNKNOWN"),
                        "impressions_goal": li.get("primaryGoal", {}).get("units", -1),
                        "cost_type":        li.get("costType", "CPM"),
                    }
                    for li in line_items
                ]
            except Exception as e:
                entry["line_items"]       = []
                entry["line_items_error"] = str(e)

            results.append(entry)

        report_result: dict[str, Any] = {}
        try:
            report_result = await self.run_delivery_report(order_ids, date_range_type)
        except Exception as e:
            report_result = {"error": str(e)}

        return {"orders": results, "report_job": report_result}
