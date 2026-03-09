# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for API key authentication middleware."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ad_buyer.config.settings import Settings
from ad_buyer.interfaces.api import main as api_module


def _client() -> TestClient:
    """Create a test client for the app."""
    return TestClient(api_module.app)


def _make_settings(api_key: str = "") -> Settings:
    """Create a Settings instance with the given api_key."""
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
    """Replace the settings object on the api module."""
    return patch.object(api_module, "settings", _make_settings(api_key))


class TestApiKeyAuthEnabled:
    """Tests when api_key is configured (auth required)."""

    def test_health_no_auth_required(self):
        """Health endpoint should be accessible without API key."""
        with _patch_settings("test-secret-key"):
            response = _client().get("/health")
        assert response.status_code == 200

    def test_missing_api_key_returns_401(self):
        """Requests without X-API-Key header should get 401."""
        with _patch_settings("test-secret-key"):
            response = _client().get("/bookings")
        assert response.status_code == 401
        assert "api key" in response.json()["detail"].lower()

    def test_wrong_api_key_returns_401(self):
        """Requests with wrong API key should get 401."""
        with _patch_settings("test-secret-key"):
            response = _client().get(
                "/bookings",
                headers={"X-API-Key": "wrong-key"},
            )
        assert response.status_code == 401

    def test_valid_api_key_succeeds(self):
        """Requests with correct API key should succeed."""
        with _patch_settings("test-secret-key"):
            response = _client().get(
                "/bookings",
                headers={"X-API-Key": "test-secret-key"},
            )
        assert response.status_code == 200

    def test_post_endpoint_requires_auth(self):
        """POST endpoints also require API key."""
        with _patch_settings("test-secret-key"):
            response = _client().post("/products/search", json={"limit": 5})
        assert response.status_code == 401

    def test_post_endpoint_with_valid_key(self):
        """POST endpoints work with valid API key."""
        with _patch_settings("test-secret-key"):
            response = _client().post(
                "/products/search",
                json={"limit": 5},
                headers={"X-API-Key": "test-secret-key"},
            )
        # May fail due to missing backend, but should not be 401
        assert response.status_code != 401


class TestApiKeyAuthDisabled:
    """Tests when api_key is empty/not set (auth disabled)."""

    def test_no_api_key_configured_allows_access(self):
        """When api_key is empty, requests should succeed without header."""
        with _patch_settings(""):
            response = _client().get("/bookings")
        assert response.status_code == 200

    def test_health_still_works(self):
        """Health endpoint works when auth is disabled."""
        with _patch_settings(""):
            response = _client().get("/health")
        assert response.status_code == 200
