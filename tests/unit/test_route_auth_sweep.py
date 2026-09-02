# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Every REST route is gated unless explicitly allowlisted as public.

Per-route auth tests only cover the handful of routes someone remembered to
write a test for, so a new (or accidentally un-gated) route can ship with the
suite green. This sweep walks ``app.routes`` and asserts an anonymous request
gets 401 for everything outside ``PUBLIC_PATHS``, which makes adding a route
a decision: gate it, or add it to the allowlist here and justify it in review.
"""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from ad_buyer.auth.factory import get_operator_key_service, reset_operator_key_service
from ad_buyer.config.settings import get_settings
from ad_buyer.interfaces.api import main as api_module
from ad_buyer.models.api_key import OperatorApiKeyCreateRequest

# Routes that must stay reachable without a credential.
PUBLIC_PATHS = {
    "/health",  # liveness/readiness probes (ECS, k8s, curl)
    "/openapi.json",  # schema for API explorers
    "/docs",  # Swagger UI
    "/docs/oauth2-redirect",
    "/redoc",  # ReDoc
}

# Methods that never carry auth semantics of their own.
_SKIPPED_METHODS = {"HEAD", "OPTIONS"}

_PATH_PARAM = re.compile(r"\{[^}]+\}")


@pytest.fixture
def gated_client(tmp_path, monkeypatch):
    """Client against a DB holding one operator key (so the shim is retired)."""
    db_url = f"sqlite:///{tmp_path / 'sweep.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("API_KEY", "")
    get_settings.cache_clear()
    reset_operator_key_service()
    svc = get_operator_key_service(db_url, force_new=True)
    raw = svc.create_operator_key(OperatorApiKeyCreateRequest(label="sweep")).api_key
    yield TestClient(api_module.app), raw
    reset_operator_key_service()
    get_settings.cache_clear()


def _sample_path(path: str) -> str:
    """Fill path params with a value that will never exist."""
    return _PATH_PARAM.sub("sweep-nonexistent", path)


def _protected_routes() -> list[tuple[str, str]]:
    """(method, path) for every non-allowlisted REST route on the app."""
    targets: list[tuple[str, str]] = []
    for route in api_module.app.routes:
        if not isinstance(route, APIRoute) or route.path in PUBLIC_PATHS:
            continue
        for method in sorted(route.methods - _SKIPPED_METHODS):
            targets.append((method, route.path))
    return targets


def test_sweep_covers_the_whole_route_table():
    """Guard the guard: an empty or tiny sweep would pass vacuously."""
    routes = _protected_routes()
    assert len(routes) >= 14, f"route sweep collected only {len(routes)} routes"


@pytest.mark.parametrize(("method", "path"), _protected_routes(), ids=lambda v: str(v))
def test_route_requires_operator_key(method, path, gated_client):
    """Anonymous callers get 401 on every non-public route."""
    client, _ = gated_client
    response = client.request(method, _sample_path(path), json={})
    assert response.status_code == 401, (
        f"{method} {path} answered {response.status_code} anonymously — "
        "gate it with Depends(require_operator_key) or add it to PUBLIC_PATHS"
    )


@pytest.mark.parametrize("path", sorted(PUBLIC_PATHS), ids=lambda v: str(v))
def test_public_paths_are_real_and_open(path, gated_client):
    """The allowlist may not accumulate stale or secretly-gated entries."""
    client, _ = gated_client
    response = client.get(path)
    assert response.status_code != 404, f"{path} is allowlisted but does not exist"
    assert response.status_code != 401, f"{path} is allowlisted but requires auth"


def test_gated_route_succeeds_with_operator_key(gated_client):
    """The sweep's 401s come from missing auth, not a broken app."""
    client, raw = gated_client
    assert client.get("/bookings", headers={"X-Api-Key": raw}).status_code == 200
