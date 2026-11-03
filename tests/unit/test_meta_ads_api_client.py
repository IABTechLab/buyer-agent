# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for MetaAdsAPIClient (Graph API reach estimates).

Contract confirmed against the live Graph API (v21.0): /reachestimate
accepts only ``targeting_spec`` (``optimize_for`` and ``daily_budget`` are
rejected with #100) and wraps the bounds in ``{"data": {...}}``.
"""

import json
from unittest.mock import patch

import httpx
import pytest

from ad_buyer.clients.meta_ads_api_client import MetaAdsAPIClient

_TOKEN = "EAAsecret-token-value"


def _graph_response(status_code, body, request):
    return httpx.Response(status_code, json=body, request=request)


def _patched_get(status_code, body):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        request = httpx.Request("GET", url, params=params, headers=headers)
        calls.append(request)
        return _graph_response(status_code, body, request)

    return patch("ad_buyer.clients.meta_ads_api_client.httpx.get", side_effect=fake_get), calls


def _client():
    return MetaAdsAPIClient(access_token=_TOKEN, ad_account_id="123")


class TestReachEstimate:
    def test_sends_only_targeting_spec_to_account_reachestimate(self):
        targeting = {"geo_locations": {"countries": ["US"]}, "age_min": 18}
        get, calls = _patched_get(200, {"data": {"users_lower_bound": 1}})
        with get:
            _client().get_reach_estimate(targeting=targeting)

        request = calls[0]
        assert request.url.path == "/v21.0/act_123/reachestimate"
        assert dict(request.url.params) == {"targeting_spec": json.dumps(targeting)}

    def test_unwraps_data_envelope(self):
        bounds = {"users_lower_bound": 100, "users_upper_bound": 300, "estimate_ready": True}
        get, _ = _patched_get(200, {"data": bounds})
        with get:
            assert _client().get_reach_estimate(targeting={}) == bounds

    def test_token_sent_as_bearer_header_not_in_url(self):
        get, calls = _patched_get(200, {"data": {}})
        with get:
            _client().get_reach_estimate(targeting={})

        request = calls[0]
        assert request.headers["Authorization"] == f"Bearer {_TOKEN}"
        assert _TOKEN not in str(request.url)

    def test_http_error_message_does_not_contain_token(self):
        get, _ = _patched_get(400, {"error": {"message": "(#100) bad param"}})
        with get:
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                _client().get_reach_estimate(targeting={})

        assert _TOKEN not in str(exc_info.value)


class TestGetAdAccount:
    def test_prefixes_bare_account_id(self):
        get, calls = _patched_get(200, {"id": "act_123"})
        with get:
            _client().get_ad_account()
        assert calls[0].url.path == "/v21.0/act_123"
