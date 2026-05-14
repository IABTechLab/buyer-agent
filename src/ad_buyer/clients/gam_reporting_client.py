# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""GAM reporting client — reads delivery data from Google Ad Manager SOAP API.

Uses the googleads SOAP library (same as seller agent) via ads.google.com endpoint.
Does NOT require the admanager.googleapis.com Cloud API to be enabled — SOAP uses
a separate endpoint and works with service account credentials directly.

Install: pip install googleads  (or uv pip install -e ".[gam]")

Auth: Google service account JSON key file.
Network: GAM_NETWORK_CODE env var (numeric, e.g. 3790).

Usage:
    client = GAMReportingClient()
    client.connect()
    data = client.get_delivery_report(["12345", "67890"])
    client.disconnect()
"""

import time
from datetime import date, timedelta
from typing import Any

from ..config.settings import settings as _settings

# Current supported SOAP API versions (v202411 is retired)
_SUPPORTED_VERSIONS = ("v202505", "v202508", "v202511", "v202602")
_DEFAULT_VERSION = "v202505"


class GAMReportingClient:
    """Read-only Google Ad Manager SOAP client for delivery reporting.

    Uses googleads.ad_manager (SOAP) — same approach as the seller agent.
    Works without the admanager.googleapis.com REST API being enabled.
    """

    def __init__(
        self,
        network_code: str | None = None,
        credentials_path: str | None = None,
        application_name: str | None = None,
        api_version: str | None = None,
    ):
        self._network_code     = network_code     or _settings.gam_network_code
        self._credentials_path = credentials_path or _settings.gam_json_key_path
        self._application_name = application_name or _settings.gam_application_name
        # Normalise version — fall back to default if retired version supplied
        raw_version = api_version or _settings.gam_api_version or _DEFAULT_VERSION
        self._api_version = raw_version if raw_version in _SUPPORTED_VERSIONS else _DEFAULT_VERSION
        self._client: Any | None = None

    # -------------------------------------------------------------------------
    # Connection
    # -------------------------------------------------------------------------

    def connect(self) -> None:
        """Connect to GAM SOAP API using service account credentials."""
        if not self._network_code or not self._credentials_path:
            raise ValueError(
                "GAM_NETWORK_CODE and GAM_JSON_KEY_PATH must be set in .env."
            )
        try:
            from googleads import ad_manager, oauth2

            oauth2_client = oauth2.GoogleServiceAccountClient(
                self._credentials_path,
                oauth2.GetAPIScope("ad_manager"),
            )
            self._client = ad_manager.AdManagerClient(
                oauth2_client,
                self._application_name,
                network_code=self._network_code,
            )
        except ImportError:
            raise ImportError(
                "GAM reporting requires the googleads package.\n"
                "Install with: pip install googleads  or  pip install -e '.[gam]'"
            )

    def disconnect(self) -> None:
        self._client = None

    def _ensure_connected(self) -> None:
        if not self._client:
            raise RuntimeError("Not connected. Call connect() first.")

    def _service(self, name: str) -> Any:
        self._ensure_connected()
        return self._client.GetService(name, version=self._api_version)

    # -------------------------------------------------------------------------
    # Network
    # -------------------------------------------------------------------------

    def get_network(self) -> dict[str, Any]:
        """Return basic network metadata (name, currency, timezone)."""
        svc = self._service("NetworkService")
        net = svc.getCurrentNetwork()
        return {
            "network_code": net.networkCode,
            "display_name": net.displayName,
            "currency":     net.currencyCode,
            "timezone":     net.timeZone,
        }

    # -------------------------------------------------------------------------
    # Orders
    # -------------------------------------------------------------------------

    def get_order(self, order_id: str) -> dict[str, Any]:
        """Get a single GAM order by numeric ID."""
        from googleads import ad_manager
        svc = self._service("OrderService")
        sb = ad_manager.StatementBuilder()
        sb.Where("id = :id").WithBindVariable("id", int(order_id))
        sb.Limit(1)
        result = svc.getOrdersByStatement(sb.ToStatement())
        orders = result.results or []
        if not orders:
            return {}
        o = orders[0]
        return {
            "id":            str(o.id),
            "name":          o.name,
            "status":        o.status,
            "advertiser_id": str(getattr(o, "advertiserId", "")),
            "start_date":    str(getattr(o, "startDateTime", "")),
            "end_date":      str(getattr(o, "endDateTime", "")),
        }

    def list_orders(self, limit: int = 10) -> list[dict[str, Any]]:
        """List recent orders in the network."""
        from googleads import ad_manager
        svc = self._service("OrderService")
        sb = ad_manager.StatementBuilder()
        sb.Limit(limit)
        result = svc.getOrdersByStatement(sb.ToStatement())
        return [
            {"id": str(o.id), "name": o.name, "status": o.status}
            for o in (result.results or [])
        ]

    # -------------------------------------------------------------------------
    # Line Items
    # -------------------------------------------------------------------------

    def list_line_items(self, order_id: str) -> list[dict[str, Any]]:
        """List line items for a given order ID."""
        from googleads import ad_manager
        svc = self._service("LineItemService")
        sb = ad_manager.StatementBuilder()
        sb.Where("orderId = :orderId").WithBindVariable("orderId", int(order_id))
        result = svc.getLineItemsByStatement(sb.ToStatement())
        return [
            {
                "id":               str(li.id),
                "name":             li.name,
                "status":           li.status,
                "impressions_goal": getattr(getattr(li, "primaryGoal", None), "units", -1),
                "cost_type":        getattr(li, "costType", "CPM"),
            }
            for li in (result.results or [])
        ]

    # -------------------------------------------------------------------------
    # Reports
    # -------------------------------------------------------------------------

    def run_delivery_report(
        self,
        order_ids: list[str],
        days: int = 30,
    ) -> list[dict[str, Any]]:
        """Submit a delivery report job and return rows as dicts.

        Polls until the job completes (max ~60s) then downloads CSV rows.

        Args:
            order_ids: List of GAM order IDs (numeric strings)
            days: Look-back window in days (default 30)

        Returns:
            List of row dicts with keys:
                order_id, order_name, line_item_id, line_item_name,
                impressions, clicks, revenue_usd
        """
        report_svc = self._service("ReportService")
        today = date.today()
        start = today - timedelta(days=days)

        report_job = {
            "reportQuery": {
                "dimensions": [
                    "ORDER_ID", "ORDER_NAME",
                    "LINE_ITEM_ID", "LINE_ITEM_NAME",
                ],
                "columns": [
                    "AD_SERVER_IMPRESSIONS",
                    "AD_SERVER_CLICKS",
                    "AD_SERVER_CPM_AND_CPC_REVENUE",
                ],
                "dateRangeType": "CUSTOM_DATE",
                "startDate": {"year": start.year, "month": start.month, "day": start.day},
                "endDate":   {"year": today.year, "month": today.month, "day": today.day},
            }
        }

        job = report_svc.runReportJob(report_job)
        job_id = job.id

        # Poll until complete (max ~60s)
        for _ in range(20):
            time.sleep(3)
            status = str(report_svc.getReportJobStatus(job_id))
            if status == "COMPLETED":
                break
            if status == "FAILED":
                return [{"error": f"Report job {job_id} failed"}]

        # Download and parse CSV
        import io
        downloader = self._client.GetDataDownloader(version=self._api_version)
        buf = io.BytesIO()
        downloader.DownloadReportToFile(job_id, "CSV_EXCEL", buf, use_gzip_compression=False)
        buf.seek(0)
        lines = buf.read().decode("utf-8").splitlines()

        if len(lines) < 2:
            return []

        rows = []
        for line in lines[1:]:  # skip header
            if line.startswith("Total"):
                continue
            parts = line.split(",")
            if len(parts) < 7:
                continue
            rows.append({
                "order_id":       parts[0].strip(),
                "order_name":     parts[1].strip(),
                "line_item_id":   parts[2].strip(),
                "line_item_name": parts[3].strip(),
                "impressions":    int(parts[4].strip() or 0),
                "clicks":         int(parts[5].strip() or 0),
                "revenue_usd":    float(parts[6].strip() or 0),
            })
        return rows

    # -------------------------------------------------------------------------
    # High-level combined fetch
    # -------------------------------------------------------------------------

    def get_delivery_report(
        self,
        order_ids: list[str],
        days: int = 30,
    ) -> dict[str, Any]:
        """Fetch order metadata + line items + delivery rows for given order IDs.

        Returns:
            {
                "orders": [{"order_id", "order_name", "status", "line_items": [...]}],
                "report_rows": [{"order_id", "order_name", "line_item_id",
                                 "line_item_name", "impressions", "clicks", "revenue_usd"}],
                "summary": {"impressions": int, "clicks": int, "revenue_usd": float}
            }
        """
        orders_out: list[dict[str, Any]] = []
        for oid in order_ids:
            entry: dict[str, Any] = {"order_id": oid}
            try:
                order = self.get_order(oid)
                entry.update({
                    "order_name": order.get("name", oid),
                    "status":     order.get("status", "UNKNOWN"),
                })
            except Exception as e:
                entry.update({"order_name": oid, "status": "ERROR", "error": str(e)})
            try:
                entry["line_items"] = self.list_line_items(oid)
            except Exception as e:
                entry["line_items"] = []
                entry["line_items_error"] = str(e)
            orders_out.append(entry)

        report_rows: list[dict[str, Any]] = []
        try:
            report_rows = self.run_delivery_report(order_ids, days=days)
        except Exception as e:
            report_rows = [{"error": str(e)}]

        summary = {
            "impressions": sum(r.get("impressions", 0) for r in report_rows if "error" not in r),
            "clicks":      sum(r.get("clicks", 0)      for r in report_rows if "error" not in r),
            "revenue_usd": round(sum(r.get("revenue_usd", 0) for r in report_rows if "error" not in r), 2),
        }

        return {
            "orders":      orders_out,
            "report_rows": report_rows,
            "summary":     summary,
        }
