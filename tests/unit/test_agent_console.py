"""Tests for the internal Agent Console."""

from fastapi.testclient import TestClient

from ad_buyer.interfaces.api import main as api_module
from tests.unit.test_api_auth import _patch_settings


def test_console_shell_and_generated_client_are_public_when_auth_is_enabled():
    """The shell can load docs/client assets without an API key."""
    with _patch_settings("test-secret-key"):
        client = TestClient(api_module.app)
        shell = client.get("/console")
        script = client.get("/console/openapi-client.js")

    assert shell.status_code == 200
    assert "Ad Buyer Agent Console" in shell.text
    assert "/docs" in shell.text
    assert script.status_code == 200
    assert "window.agentConsoleOpenApi" in script.text
    assert '"/health"' in script.text


def test_console_proxy_uses_app_auth_middleware_and_forwards_to_health():
    """The proxy is protected by normal auth and can call app-relative paths."""
    with _patch_settings("test-secret-key"):
        client = TestClient(api_module.app)
        missing_key = client.post(
            "/console/api/proxy",
            json={"method": "GET", "path": "/health"},
        )
        with_key = client.post(
            "/console/api/proxy",
            headers={"X-API-Key": "test-secret-key"},
            json={"method": "GET", "path": "/health"},
        )

    assert missing_key.status_code == 401
    assert with_key.status_code == 200
    assert with_key.json()["status"] == "healthy"


def test_console_proxy_rejects_external_urls():
    with _patch_settings(""):
        client = TestClient(api_module.app)
        response = client.post(
            "/console/api/proxy",
            json={"method": "GET", "path": "https://example.com/health"},
        )

    assert response.status_code == 400
    assert "app-relative" in response.json()["detail"]
