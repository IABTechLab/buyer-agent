# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Operator-key authentication for the buyer-agent control plane.

Covers:
(a) Hashed operator keys in SQLite; CLI bootstrap mints OPERATOR role.
(b) ``require_operator_key``: anonymous → 401, invalid → 401, operator → ok.
(c) HTTP cannot mint the first operator key without an existing credential.
(d) Additional operator keys via POST /auth/api-keys/operator with operator auth.
(e) Deprecated settings.api_key shim when no operator key has ever been minted.
(f) MCP ``_deny_unless_operator`` fails closed: stdio is trusted only without
    an HTTP transport mounted; unexpected SDK errors deny.
"""

from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ad_buyer.auth.factory import get_operator_key_service, reset_operator_key_service
from ad_buyer.config.settings import get_settings
from ad_buyer.interfaces import mcp_server
from ad_buyer.interfaces.api import main as api_module
from ad_buyer.models.api_key import (
    ApiKeyRole,
    OperatorApiKeyCreateRequest,
    generate_api_key,
    hash_api_key,
)


@pytest.fixture
def operator_db(tmp_path, monkeypatch):
    """Point settings at an isolated SQLite DB and reset caches."""
    db_url = f"sqlite:///{tmp_path / 'operator_keys.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("API_KEY", "")
    get_settings.cache_clear()
    reset_operator_key_service()
    yield db_url
    reset_operator_key_service()
    get_settings.cache_clear()


@pytest.fixture
def operator_service(operator_db):
    """Fresh OperatorKeyService bound to the isolated DB."""
    return get_operator_key_service(operator_db, force_new=True)


def _mint(service, label: str = "Primary operator") -> str:
    resp = service.create_operator_key(OperatorApiKeyCreateRequest(label=label))
    return resp.api_key


class TestOperatorKeyService:
    def test_create_and_validate(self, operator_service):
        raw = _mint(operator_service)
        assert raw.startswith("abk_live_")
        record = operator_service.validate_key(raw)
        assert record is not None
        assert record.role == ApiKeyRole.OPERATOR
        assert record.use_count == 1

    def test_plaintext_not_stored(self, operator_service):
        raw = _mint(operator_service)
        rows = operator_service._store.list_all()
        assert len(rows) == 1
        assert rows[0].key_hash == hash_api_key(raw)
        assert raw not in rows[0].key_prefix_hint

    def test_duplicate_label_rejected(self, operator_service):
        _mint(operator_service, "ops")
        with pytest.raises(ValueError, match="already exists"):
            _mint(operator_service, "ops")

    def test_delete_frees_label(self, operator_service):
        _mint(operator_service, "ops")
        operator_service.delete_operator_key(label="ops")
        raw = _mint(operator_service, "ops")
        assert operator_service.validate_key(raw) is not None

    def test_label_claim_is_atomic(self, operator_service):
        """Concurrent mints must not both claim the same label."""
        results: list[str] = []
        errors: list[Exception] = []

        def mint() -> None:
            try:
                results.append(_mint(operator_service, "race"))
            except ValueError as exc:
                errors.append(exc)

        threads = [threading.Thread(target=mint) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 1, f"expected one winner, got {len(results)}"
        assert len(errors) == 7
        active = [i for i in operator_service.list_operator_keys() if i.label == "race"]
        assert len(active) == 1

    def test_revoked_key_raises(self, operator_service):
        raw = _mint(operator_service, "ops")
        info = operator_service.list_operator_keys()[0]
        operator_service.revoke_key(info.key_id)
        with pytest.raises(ValueError, match="revoked"):
            operator_service.validate_key(raw)


class TestCliBootstrap:
    def test_create_operator_key_command(self, operator_db):
        from ad_buyer.interfaces.cli.main import create_operator_key

        create_operator_key(label="cli-boot", expires_in_days=None)

        svc = get_operator_key_service(operator_db, force_new=True)
        keys = svc.list_operator_keys()
        assert len(keys) == 1
        assert keys[0].label == "cli-boot"

    def test_quiet_prints_only_the_key(self, operator_db, capsys):
        """--quiet is what run-demo.sh and the smoke tests capture."""
        from ad_buyer.interfaces.cli.main import create_operator_key

        create_operator_key(label="scripted", expires_in_days=None, quiet=True)

        out = capsys.readouterr().out.strip()
        assert out.startswith("abk_live_")
        assert out.count("\n") == 0

        svc = get_operator_key_service(operator_db, force_new=True)
        assert svc.validate_key(out) is not None

    def test_delete_missing_args_exits(self):
        import typer

        from ad_buyer.interfaces.cli.main import delete_operator_key

        with pytest.raises(typer.Exit) as exc:
            delete_operator_key(label=None, key_id=None)
        assert exc.value.exit_code == 1


class TestHttpOperatorAuth:
    @pytest.fixture
    def client_and_key(self, operator_service):
        raw = _mint(operator_service)
        return TestClient(api_module.app), raw

    def test_health_public(self, client_and_key):
        client, _ = client_and_key
        assert client.get("/health").status_code == 200

    def test_bookings_anonymous_401(self, client_and_key):
        client, _ = client_and_key
        assert client.get("/bookings").status_code == 401

    def test_bookings_with_operator_key(self, client_and_key):
        client, raw = client_and_key
        resp = client.get("/bookings", headers={"X-Api-Key": raw})
        assert resp.status_code == 200

    def test_bookings_with_bearer(self, client_and_key):
        client, raw = client_and_key
        resp = client.get("/bookings", headers={"Authorization": f"Bearer {raw}"})
        assert resp.status_code == 200

    def test_wrong_key_401(self, client_and_key):
        client, _ = client_and_key
        resp = client.get("/bookings", headers={"X-Api-Key": generate_api_key()})
        assert resp.status_code == 401

    def test_http_cannot_bootstrap_first_key(self, operator_db):
        """POST /auth/api-keys/operator without existing operator → 401."""
        get_operator_key_service(operator_db, force_new=True)
        client = TestClient(api_module.app)
        resp = client.post(
            "/auth/api-keys/operator",
            json={"label": "should-fail"},
        )
        assert resp.status_code == 401

    def test_http_mints_additional_operator_key(self, client_and_key):
        client, raw = client_and_key
        resp = client.post(
            "/auth/api-keys/operator",
            json={"label": "secondary"},
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "operator"
        assert body["api_key"].startswith("abk_live_")
        assert "warning" in body

    def test_list_and_revoke(self, client_and_key):
        client, raw = client_and_key
        listed = client.get("/auth/api-keys", headers={"X-Api-Key": raw})
        assert listed.status_code == 200
        keys = listed.json()["keys"]
        assert len(keys) >= 1
        key_id = keys[0]["key_id"]
        revoked = client.delete(
            f"/auth/api-keys/{key_id}",
            headers={"X-Api-Key": raw},
        )
        assert revoked.status_code == 200
        assert revoked.json()["revoked"] is True


class TestLegacyApiKeyShim:
    def test_env_api_key_accepted_when_no_db_keys(self, tmp_path, monkeypatch):
        db_url = f"sqlite:///{tmp_path / 'legacy.db'}"
        monkeypatch.setenv("DATABASE_URL", db_url)
        monkeypatch.setenv("API_KEY", "legacy-secret")
        get_settings.cache_clear()
        reset_operator_key_service()
        get_operator_key_service(db_url, force_new=True)

        client = TestClient(api_module.app)
        assert client.get("/bookings").status_code == 401
        resp = client.get("/bookings", headers={"X-Api-Key": "legacy-secret"})
        assert resp.status_code == 200

        reset_operator_key_service()
        get_settings.cache_clear()

    def test_env_api_key_ignored_once_db_keys_exist(self, tmp_path, monkeypatch):
        db_url = f"sqlite:///{tmp_path / 'legacy2.db'}"
        monkeypatch.setenv("DATABASE_URL", db_url)
        monkeypatch.setenv("API_KEY", "legacy-secret")
        get_settings.cache_clear()
        reset_operator_key_service()
        svc = get_operator_key_service(db_url, force_new=True)
        _mint(svc)

        client = TestClient(api_module.app)
        resp = client.get("/bookings", headers={"X-Api-Key": "legacy-secret"})
        assert resp.status_code == 401

        reset_operator_key_service()
        get_settings.cache_clear()

    def test_revoking_every_key_does_not_reopen_shim(self, tmp_path, monkeypatch):
        """Regression: the shim keys off any row ever, not active rows.

        Gating on *active* keys let an operator downgrade the control plane
        back to plaintext env auth by revoking every hashed key.
        """
        db_url = f"sqlite:///{tmp_path / 'legacy3.db'}"
        monkeypatch.setenv("DATABASE_URL", db_url)
        monkeypatch.setenv("API_KEY", "legacy-secret")
        get_settings.cache_clear()
        reset_operator_key_service()
        svc = get_operator_key_service(db_url, force_new=True)
        _mint(svc)
        for info in svc.list_operator_keys(include_inactive=True):
            svc.revoke_key(info.key_id)
        assert svc.has_active_operator_keys() is False
        assert svc.has_any_operator_keys() is True

        client = TestClient(api_module.app)
        resp = client.get("/bookings", headers={"X-Api-Key": "legacy-secret"})
        assert resp.status_code == 401

        reset_operator_key_service()
        get_settings.cache_clear()

    def test_revoked_key_401_does_not_leak_key_state(self, operator_service):
        """A revoked key must not be distinguishable from an unknown key."""
        raw = _mint(operator_service, "to-revoke")
        info = operator_service.list_operator_keys()[0]
        operator_service.revoke_key(info.key_id)

        client = TestClient(api_module.app)
        revoked = client.get("/bookings", headers={"X-Api-Key": raw})
        unknown = client.get("/bookings", headers={"X-Api-Key": generate_api_key()})

        assert revoked.status_code == unknown.status_code == 401
        assert revoked.json()["detail"] == unknown.json()["detail"]
        assert info.key_id not in revoked.text


class TestMcpOperatorGate:
    """The gate must fail CLOSED: only affirmatively-local calls go unchecked.

    FastMCP swallows the context-var LookupError in ``get_context()`` and then
    ``Context.request_context`` raises ValueError, so those two are the SDK's
    documented "no MCP request in flight" signals and mean a direct in-process
    call. A stdio tool call carries a request context whose ``request`` is
    None. Anything else — an unexpected exception, an unreadable request
    object — must deny rather than un-gate all 42 tools.
    """

    @pytest.fixture(autouse=True)
    def _no_http_transport(self, monkeypatch):
        """Default to a process that has not mounted an MCP HTTP transport."""
        monkeypatch.setattr(mcp_server, "_http_transport_mounted", False)

    def _ctx(self, request):
        ctx = MagicMock()
        ctx.request_context.request = request
        return ctx

    @pytest.mark.parametrize(
        "signal",
        [
            ValueError("Context is not available outside of a request"),
            LookupError("no context var"),
        ],
        ids=["value_error", "lookup_error"],
    )
    def test_in_process_call_allowed_without_key(self, signal):
        """The documented no-request signals mean a direct Python call."""
        ctx = MagicMock()
        type(ctx).request_context = property(lambda _self: (_ for _ in ()).throw(signal))
        with patch("ad_buyer.interfaces.mcp_server.mcp.get_context", return_value=ctx):
            assert mcp_server._deny_unless_operator() is None

    def test_stdio_allows_without_key(self):
        """stdio tool calls have a request context whose request is None."""
        with patch("ad_buyer.interfaces.mcp_server.mcp.get_context", return_value=self._ctx(None)):
            assert mcp_server._deny_unless_operator() is None

    def test_unexpected_sdk_error_denies(self):
        """An unrecognized SDK failure must not be read as trusted local access."""
        with patch("ad_buyer.interfaces.mcp_server.mcp.get_context") as ctx:
            ctx.side_effect = RuntimeError("FastMCP internals changed")
            denied = mcp_server._deny_unless_operator()
        assert denied is not None
        assert json.loads(denied)["error"] == "authentication_required"

    def test_unexpected_request_context_error_denies(self):
        """A new exception type from request_context denies rather than trusts."""
        ctx = MagicMock()
        type(ctx).request_context = property(
            lambda _self: (_ for _ in ()).throw(RuntimeError("shape changed"))
        )
        with patch("ad_buyer.interfaces.mcp_server.mcp.get_context", return_value=ctx):
            denied = mcp_server._deny_unless_operator()
        assert denied is not None
        assert json.loads(denied)["error"] == "authentication_required"

    def test_unrecognized_request_object_denies(self):
        """A request we cannot read headers from is denied, not trusted."""
        with patch(
            "ad_buyer.interfaces.mcp_server.mcp.get_context", return_value=self._ctx(object())
        ):
            denied = mcp_server._deny_unless_operator()
        assert denied is not None
        assert json.loads(denied)["error"] == "authentication_required"

    def test_stdio_denied_when_http_transport_mounted(self, monkeypatch):
        """A server process cannot be serving stdio, so that claim is denied."""
        monkeypatch.setattr(mcp_server, "_http_transport_mounted", True)
        with patch("ad_buyer.interfaces.mcp_server.mcp.get_context", return_value=self._ctx(None)):
            denied = mcp_server._deny_unless_operator()
        assert denied is not None
        assert json.loads(denied)["error"] == "authentication_required"

    def test_mount_mcp_marks_http_transport(self, monkeypatch):
        """mount_mcp flips the flag that turns off the trusted-stdio path."""
        monkeypatch.setattr(mcp_server, "_http_transport_mounted", False)
        app = FastAPI()
        mcp_server.mount_mcp(app)
        assert mcp_server._http_transport_mounted is True

    def test_http_without_key_denied(self):
        request = MagicMock()
        request.headers = {}
        ctx = MagicMock()
        ctx.request_context.request = request
        with patch("ad_buyer.interfaces.mcp_server.mcp.get_context", return_value=ctx):
            denied = mcp_server._deny_unless_operator()
        assert denied is not None
        body = json.loads(denied)
        assert body["error"] == "authentication_required"
        assert "create-operator-key" in body["detail"]

    def test_http_with_operator_key_allowed(self, operator_service):
        raw = _mint(operator_service, "mcp-http")
        request = MagicMock()
        request.headers = {"x-api-key": raw}
        ctx = MagicMock()
        ctx.request_context.request = request
        with patch("ad_buyer.interfaces.mcp_server.mcp.get_context", return_value=ctx):
            assert mcp_server._deny_unless_operator() is None
