# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for inbound operator API key authentication on REST routes."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ad_buyer.auth.factory import get_operator_key_service, reset_operator_key_service
from ad_buyer.config.settings import get_settings
from ad_buyer.interfaces.api import main as api_module
from ad_buyer.models.api_key import OperatorApiKeyCreateRequest


@pytest.fixture
def auth_db(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'api_auth.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("API_KEY", "")
    get_settings.cache_clear()
    reset_operator_key_service()
    yield db_url
    reset_operator_key_service()
    get_settings.cache_clear()


def _client() -> TestClient:
    return TestClient(api_module.app)


def _mint_key(db_url: str) -> str:
    svc = get_operator_key_service(db_url, force_new=True)
    return svc.create_operator_key(OperatorApiKeyCreateRequest(label="test")).api_key


class TestOperatorAuthRequired:
    def test_health_no_auth_required(self, auth_db):
        response = _client().get("/health")
        assert response.status_code == 200

    def test_missing_api_key_returns_401(self, auth_db):
        _mint_key(auth_db)
        response = _client().get("/bookings")
        assert response.status_code == 401

    def test_wrong_api_key_returns_401(self, auth_db):
        _mint_key(auth_db)
        response = _client().get(
            "/bookings",
            headers={"X-Api-Key": "wrong-key"},
        )
        assert response.status_code == 401

    def test_valid_operator_key_succeeds(self, auth_db):
        raw = _mint_key(auth_db)
        response = _client().get(
            "/bookings",
            headers={"X-Api-Key": raw},
        )
        assert response.status_code == 200

    def test_post_endpoint_requires_auth(self, auth_db):
        _mint_key(auth_db)
        response = _client().post("/products/search", json={"limit": 5})
        assert response.status_code == 401

    def test_post_endpoint_with_valid_key(self, auth_db):
        raw = _mint_key(auth_db)
        with patch.object(api_module, "ProductSearchTool", create=True):
            response = _client().post(
                "/products/search",
                json={"limit": 5},
                headers={"X-Api-Key": raw},
            )
        assert response.status_code != 401


class TestNoCredentials:
    def test_protected_route_requires_auth_even_without_env_key(self, auth_db):
        """Empty API_KEY and no DB keys still require authentication."""
        get_operator_key_service(auth_db, force_new=True)
        response = _client().get("/bookings")
        assert response.status_code == 401

    def test_health_still_works(self, auth_db):
        response = _client().get("/health")
        assert response.status_code == 200
