# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Meta Ads reporting API endpoints (port of main PR #87).

- ``GET /meta/campaigns`` and ``GET /meta/report`` proxy the Meta Graph
  API directly; both return 503 when Meta credentials are unconfigured.
- ``GET /reports/{job_id}`` aggregates delivery reporting for a booking
  job: Meta campaign insights for social lines, the seller's deal
  performance endpoint for orchestrator-booked lines (keyed by the
  seller-issued ``deal_id``).
- Error responses never leak the Meta access token.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from ad_buyer.interfaces.api import main as api_module


def _client() -> TestClient:
    return TestClient(api_module.app)


def _no_auth():
    """Disable API-key auth for these tests."""
    return patch.object(api_module.settings, "api_key", "")


def _meta_unconfigured():
    return patch.multiple(
        api_module.settings,
        meta_access_token="",
        meta_ad_account_id="",
        meta_page_id="",
    )


def _meta_configured():
    return patch.multiple(
        api_module.settings,
        meta_access_token="tok-secret",
        meta_ad_account_id="act_123",
        meta_page_id="page_1",
        meta_api_version="v21.0",
    )


class TestMetaCampaignsEndpoint:
    def test_unconfigured_returns_503(self):
        with _no_auth(), _meta_unconfigured():
            response = _client().get("/meta/campaigns")
        assert response.status_code == 503
        assert "Meta not configured" in response.json()["detail"]


class TestMetaReportEndpoint:
    def test_unconfigured_returns_503(self):
        with _no_auth(), _meta_unconfigured():
            response = _client().get("/meta/report", params={"campaign_ids": "1,2"})
        assert response.status_code == 503

    def test_empty_campaign_ids_returns_400(self):
        with _no_auth(), _meta_configured():
            response = _client().get("/meta/report", params={"campaign_ids": " , "})
        assert response.status_code == 400

    def test_error_response_never_leaks_token(self):
        with _no_auth(), _meta_configured():
            with patch.object(api_module, "MetaAdsClient", create=True) as client_cls:
                client_cls.side_effect = RuntimeError("boom tok-secret leaked")
                response = _client().get("/meta/report", params={"campaign_ids": "1"})
        assert response.status_code == 502
        assert "tok-secret" not in response.json()["detail"]


class TestSanitizeMetaError:
    def test_strips_token(self):
        with _meta_configured():
            msg = api_module._sanitize_meta_error(Exception("failed with tok-secret"))
        assert "tok-secret" not in msg
        assert "***" in msg


class TestCampaignReportEndpoint:
    def test_unknown_job_returns_404(self):
        with _no_auth():
            response = _client().get("/reports/nonexistent-job")
        assert response.status_code == 404

    def test_job_without_booked_lines(self):
        api_module.jobs["job-empty"] = {"brief": {"name": "X"}, "booked_lines": []}
        try:
            with _no_auth():
                response = _client().get("/reports/job-empty")
            assert response.status_code == 200
            assert response.json()["reports"] == []
        finally:
            api_module.jobs.pop("job-empty", None)

    def test_seller_lines_keyed_by_deal_id(self):
        """Non-social lines report via the seller's deal performance API,
        keyed by the seller-issued deal_id (v2 BookedLine model)."""
        api_module.jobs["job-seller"] = {
            "brief": {"name": "X"},
            "booked_lines": [
                {
                    "deal_id": "SELLER-DEAL-1",
                    "channel": "branding",
                    "product_id": "prod_a",
                }
            ],
        }
        try:
            with _no_auth(), _meta_unconfigured():
                with patch.object(api_module.httpx, "get", create=True) as http_get:
                    http_get.return_value.status_code = 200
                    http_get.return_value.json.return_value = {"impressions": 1}
                    http_get.return_value.raise_for_status.return_value = None
                    response = _client().get("/reports/job-seller")
            assert response.status_code == 200
            body = response.json()
            seller_report = next(r for r in body["reports"] if r["source"] == "Seller")
            assert seller_report["deals"][0]["deal_id"] == "SELLER-DEAL-1"
            # The seller performance URL is keyed by the deal id
            called_url = http_get.call_args.args[0]
            assert "SELLER-DEAL-1" in called_url
        finally:
            api_module.jobs.pop("job-seller", None)

    def test_meta_lines_unconfigured_message(self):
        api_module.jobs["job-meta"] = {
            "brief": {"name": "X"},
            "booked_lines": [
                {
                    "deal_id": "camp_1",
                    "order_id": "camp_1",
                    "channel": "social",
                    "product_id": "meta:feed",
                }
            ],
        }
        try:
            with _no_auth(), _meta_unconfigured():
                response = _client().get("/reports/job-meta")
            assert response.status_code == 200
            meta_report = next(r for r in response.json()["reports"] if r["source"] == "Meta")
            assert meta_report["campaign_ids"] == ["camp_1"]
            assert "Meta not configured" in meta_report["message"]
        finally:
            api_module.jobs.pop("job-meta", None)
