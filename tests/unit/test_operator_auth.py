# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Operator-key authentication for the buyer-agent control plane.

Covers:
(a) Hashed operator keys in SQLite; CLI bootstrap mints OPERATOR role.
(b) ``require_operator_key``: anonymous → 401, invalid → 401, operator → ok.
(c) HTTP cannot mint the first operator key without an existing credential.
(d) Additional operator keys via POST /auth/api-keys/operator with operator auth.
(e) Deprecated settings.api_key shim when no DB keys exist.
(f) MCP ``_deny_unless_operator`` allows stdio, denies HTTP without key.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ad_buyer.auth.factory import get_operator_key_service, reset_operator_key_service
from ad_buyer.config.settings import get_settings
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


class TestMcpOperatorGate:
    def test_stdio_allows_without_key(self):
        from ad_buyer.interfaces.mcp_server import _deny_unless_operator

        with patch("ad_buyer.interfaces.mcp_server.mcp.get_context") as ctx:
            ctx.side_effect = RuntimeError("no http")
            assert _deny_unless_operator() is None

    def test_http_without_key_denied(self):
        from ad_buyer.interfaces.mcp_server import _deny_unless_operator

        request = MagicMock()
        request.headers = {}
        ctx = MagicMock()
        ctx.request_context.request = request
        with patch("ad_buyer.interfaces.mcp_server.mcp.get_context", return_value=ctx):
            denied = _deny_unless_operator()
        assert denied is not None
        body = json.loads(denied)
        assert body["error"] == "authentication_required"
        assert "create-operator-key" in body["detail"]
