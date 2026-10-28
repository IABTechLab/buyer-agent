# Donated to IAB Tech Lab

"""Tests for the buyer OAuthTokenProvider (Req 6.6 / task 7.4)."""

import logging

import httpx
import pytest

from ad_buyer.auth.oauth_token_provider import OAuthTokenError, OAuthTokenProvider

_TOKEN_URL = "https://issuer.example/oauth2/token"
_SCOPE = "seller-agent/invoke"


class _Recorder:
    """Counts mint calls and lets a test flip the returned token / status."""

    def __init__(self):
        self.calls = 0
        self.token = "jwt-1"
        self.status = 200
        self.expires_in = 3600
        self.last_auth_header = None
        self.last_body = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.last_auth_header = request.headers.get("Authorization")
        self.last_body = request.content.decode()
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "invalid_client"})
        return httpx.Response(
            200,
            json={
                "access_token": self.token,
                "expires_in": self.expires_in,
                "token_type": "Bearer",
            },
        )


def _provider(recorder: _Recorder, **kw) -> OAuthTokenProvider:
    return OAuthTokenProvider(
        client_id="buyer-client",
        client_secret="buyer-secret",
        transport=httpx.MockTransport(recorder.handler),
        **kw,
    )


def test_mints_a_client_credentials_token():
    rec = _Recorder()
    p = _provider(rec)
    assert p.get_token(_TOKEN_URL, _SCOPE) == "jwt-1"
    assert rec.calls == 1
    # HTTP Basic client_id:secret, grant_type + scope in the form body.
    assert rec.last_auth_header.startswith("Basic ")
    assert "grant_type=client_credentials" in rec.last_body
    assert "scope=seller-agent" in rec.last_body


def test_reuses_cached_token_within_lifetime():
    rec = _Recorder()
    p = _provider(rec)
    p.get_token(_TOKEN_URL, _SCOPE)
    p.get_token(_TOKEN_URL, _SCOPE)  # cached — no second mint
    assert rec.calls == 1


def test_refreshes_before_expiry():
    """A token whose lifetime is within the skew window is re-minted."""
    rec = _Recorder()
    rec.expires_in = 10  # < _EXPIRY_SKEW_SECONDS → always considered stale
    p = _provider(rec)
    p.get_token(_TOKEN_URL, _SCOPE)
    rec.token = "jwt-2"
    assert p.get_token(_TOKEN_URL, _SCOPE) == "jwt-2"
    assert rec.calls == 2


def test_reactive_reminting_after_invalidate():
    rec = _Recorder()
    p = _provider(rec)
    assert p.get_token(_TOKEN_URL, _SCOPE) == "jwt-1"
    p.invalidate(_TOKEN_URL, _SCOPE)  # simulate a downstream 401
    rec.token = "jwt-2"
    assert p.get_token(_TOKEN_URL, _SCOPE) == "jwt-2"
    assert rec.calls == 2


def test_force_bypasses_cache():
    rec = _Recorder()
    p = _provider(rec)
    p.get_token(_TOKEN_URL, _SCOPE)
    p.get_token(_TOKEN_URL, _SCOPE, force=True)
    assert rec.calls == 2


def test_distinct_cache_keys_per_scope():
    rec = _Recorder()
    p = _provider(rec)
    p.get_token(_TOKEN_URL, "scope-a")
    p.get_token(_TOKEN_URL, "scope-b")
    assert rec.calls == 2  # different scope → separate mint


def test_unconfigured_raises():
    p = OAuthTokenProvider(client_id="", client_secret="")
    assert p.configured is False
    with pytest.raises(OAuthTokenError):
        p.get_token(_TOKEN_URL, _SCOPE)


def test_missing_token_endpoint_raises():
    rec = _Recorder()
    p = _provider(rec)
    with pytest.raises(OAuthTokenError):
        p.get_token("", _SCOPE)


def test_http_error_raises_and_hides_body():
    rec = _Recorder()
    rec.status = 401
    p = _provider(rec)
    with pytest.raises(OAuthTokenError) as ei:
        p.get_token(_TOKEN_URL, _SCOPE)
    assert "401" in str(ei.value)
    assert "invalid_client" not in str(ei.value)  # body not echoed


def test_never_logs_the_secret(caplog):
    rec = _Recorder()
    p = _provider(rec)
    with caplog.at_level(logging.DEBUG):
        p.get_token(_TOKEN_URL, _SCOPE)
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "buyer-secret" not in joined
