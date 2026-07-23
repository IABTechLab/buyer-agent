"""Tests for the buyer UI shell and API explorer."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from ad_buyer.config.settings import Settings
from ad_buyer.interfaces.api import main as api_module
from ad_buyer.interfaces.api.buyer_ui import _clean_proxy_path, _query_param_pairs


def _client() -> TestClient:
    return TestClient(api_module.app)


def _make_settings(api_key: str = "") -> Settings:
    return Settings.model_construct(
        api_key=api_key,
        anthropic_api_key="",
        iab_server_url="http://localhost:8001",
        seller_endpoints="",
        opendirect_base_url="http://localhost:3000/api/v2.1",
        opendirect_token=None,
        opendirect_api_key=None,
        default_llm_model="anthropic/claude-sonnet-4-5-20250929",
        manager_llm_model="anthropic/claude-opus-4-20250514",
        llm_temperature=0.3,
        llm_max_tokens=4096,
        database_url="sqlite:///./ad_buyer.db",
        redis_url=None,
        crew_memory_enabled=True,
        crew_verbose=True,
        crew_max_iterations=15,
        cors_allowed_origins="",
        environment="development",
        log_level="INFO",
    )


def _patch_settings(api_key: str):
    return patch.object(api_module, "settings", _make_settings(api_key))


def test_buyer_shell_links_to_docs_and_generated_client():
    response = _client().get("/buyer")

    assert response.status_code == 200
    assert "Ad Buyer Agent" in response.text
    assert 'href="/docs"' in response.text
    assert 'href="/redoc"' in response.text
    assert 'href="/openapi.json"' in response.text
    assert 'src="/buyer/openapi-client.js"' in response.text
    assert "Buyer API Explorer" in response.text


def test_buyer_openapi_client_exports_operations():
    response = _client().get("/buyer/openapi-client.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert "window.buyerOpenApi" in response.text
    assert '"path":"/health"' in response.text
    assert "window.buyerApiClient" in response.text


def test_buyer_proxy_calls_app_relative_endpoint():
    response = _client().post(
        "/buyer/api/proxy",
        json={"method": "GET", "path": "/health"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "version": "1.0.0"}


def test_buyer_proxy_forwards_api_key_to_protected_endpoints():
    with _patch_settings("test-secret-key"):
        unauthorized = _client().post(
            "/buyer/api/proxy",
            json={"method": "GET", "path": "/bookings"},
        )
        authorized = _client().post(
            "/buyer/api/proxy",
            json={
                "method": "GET",
                "path": "/bookings",
                "headers": {"x-api-key": "test-secret-key"},
            },
        )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200


def test_clean_proxy_path_rejects_absolute_and_recursive_paths():
    assert _clean_proxy_path("health") == "/health"

    for path in ("https://example.test/health", "//example.test/health", "/buyer/api/proxy"):
        try:
            _clean_proxy_path(path)
        except Exception as exc:  # noqa: BLE001 - FastAPI raises HTTPException here
            assert getattr(exc, "status_code") == 400
        else:
            raise AssertionError(f"Expected {path} to be rejected")


def test_query_param_pairs_preserves_repeated_values():
    assert _query_param_pairs({"tag": ["a", "b"], "limit": "2"}) == [
        ("tag", "a"),
        ("tag", "b"),
        ("limit", "2"),
    ]
