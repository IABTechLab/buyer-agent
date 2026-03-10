# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Integration tests: API endpoint integration.

Tests the FastAPI endpoints with httpx AsyncClient and ASGITransport,
verifying request routing, authentication middleware, job lifecycle,
and error propagation from API through to business logic.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport

from ad_buyer.config.settings import Settings
from ad_buyer.interfaces.api import main as api_module
from ad_buyer.interfaces.api.main import app, jobs


def _make_settings(api_key: str = "") -> Settings:
    """Create a Settings instance for testing."""
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


class TestBookingEndpointLifecycle:
    """Tests the full booking job lifecycle via API."""

    @pytest.mark.asyncio
    async def test_create_booking_returns_job_id(self):
        """POST /bookings should return a job_id and pending status."""
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.object(api_module, "settings", _make_settings("")):
                response = await client.post(
                    "/bookings",
                    json={
                        "brief": {
                            "name": "Test Campaign",
                            "objectives": ["awareness"],
                            "budget": 50000,
                            "start_date": "2025-03-01",
                            "end_date": "2025-03-31",
                            "target_audience": {"geo": ["US"]},
                        },
                        "auto_approve": False,
                    },
                )

        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "pending"

        # Clean up job from global state
        jobs.pop(data["job_id"], None)

    @pytest.mark.asyncio
    async def test_get_booking_status_after_creation(self):
        """GET /bookings/{job_id} should return the job status."""
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.object(api_module, "settings", _make_settings("")):
                # Create a booking
                create_resp = await client.post(
                    "/bookings",
                    json={
                        "brief": {
                            "name": "Test Campaign",
                            "objectives": ["reach"],
                            "budget": 25000,
                            "start_date": "2025-04-01",
                            "end_date": "2025-04-30",
                            "target_audience": {"age": "18-34"},
                        },
                        "auto_approve": False,
                    },
                )
                job_id = create_resp.json()["job_id"]

                # Query status
                status_resp = await client.get(f"/bookings/{job_id}")

        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert status_data["job_id"] == job_id
        # Status should be pending or running (background task may or may not have started)
        assert status_data["status"] in ("pending", "running", "failed", "completed")

        jobs.pop(job_id, None)

    @pytest.mark.asyncio
    async def test_nonexistent_job_returns_404(self):
        """GET /bookings/{bad_id} should return 404."""
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.object(api_module, "settings", _make_settings("")):
                response = await client.get("/bookings/nonexistent-job-id")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_bookings_empty(self):
        """GET /bookings should return empty list when no jobs exist."""
        # Ensure jobs dict is clean
        saved_jobs = dict(jobs)
        jobs.clear()

        try:
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                with patch.object(api_module, "settings", _make_settings("")):
                    response = await client.get("/bookings")

            assert response.status_code == 200
            data = response.json()
            assert data["jobs"] == []
            assert data["total"] == 0
        finally:
            jobs.update(saved_jobs)


class TestApiAuthIntegration:
    """Tests authentication middleware with actual API requests."""

    @pytest.mark.asyncio
    async def test_auth_enabled_rejects_unauthenticated(self):
        """When api_key is set, unauthenticated requests should get 401."""
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.object(api_module, "settings", _make_settings("my-secret")):
                response = await client.get("/bookings")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_auth_enabled_accepts_valid_key(self):
        """When api_key is set, requests with correct key should succeed."""
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.object(api_module, "settings", _make_settings("my-secret")):
                response = await client.get(
                    "/bookings",
                    headers={"X-API-Key": "my-secret"},
                )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_health_bypasses_auth(self):
        """Health endpoint should bypass authentication."""
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.object(api_module, "settings", _make_settings("my-secret")):
                response = await client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_auth_disabled_allows_all_requests(self):
        """When api_key is empty, all requests should succeed without headers."""
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.object(api_module, "settings", _make_settings("")):
                response = await client.get("/bookings")

        assert response.status_code == 200


class TestApiValidationIntegration:
    """Tests input validation across the API boundary."""

    @pytest.mark.asyncio
    async def test_invalid_brief_missing_fields(self):
        """POST /bookings with missing required fields should return 422."""
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.object(api_module, "settings", _make_settings("")):
                response = await client.post(
                    "/bookings",
                    json={
                        "brief": {
                            "name": "Incomplete",
                            # Missing objectives, budget, dates, audience
                        },
                        "auto_approve": False,
                    },
                )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_budget_zero(self):
        """POST /bookings with zero budget should return 422."""
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.object(api_module, "settings", _make_settings("")):
                response = await client.post(
                    "/bookings",
                    json={
                        "brief": {
                            "name": "Zero Budget",
                            "objectives": ["reach"],
                            "budget": 0,
                            "start_date": "2025-03-01",
                            "end_date": "2025-03-31",
                            "target_audience": {"geo": ["US"]},
                        },
                        "auto_approve": False,
                    },
                )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_date_format(self):
        """POST /bookings with bad date format should return 422."""
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.object(api_module, "settings", _make_settings("")):
                response = await client.post(
                    "/bookings",
                    json={
                        "brief": {
                            "name": "Bad Dates",
                            "objectives": ["reach"],
                            "budget": 50000,
                            "start_date": "March 1 2025",  # Wrong format
                            "end_date": "2025-03-31",
                            "target_audience": {"geo": ["US"]},
                        },
                        "auto_approve": False,
                    },
                )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_approve_wrong_status_returns_400(self):
        """Approving a job not in awaiting_approval status should return 400."""
        # Manually insert a job in 'running' state
        from datetime import datetime

        job_id = "test-job-running"
        jobs[job_id] = {
            "status": "running",
            "progress": 0.5,
            "brief": {"name": "Test"},
            "auto_approve": False,
            "budget_allocations": {},
            "recommendations": [],
            "booked_lines": [],
            "errors": [],
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }

        try:
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                with patch.object(api_module, "settings", _make_settings("")):
                    response = await client.post(
                        f"/bookings/{job_id}/approve",
                        json={"approved_product_ids": ["prod_001"]},
                    )

            assert response.status_code == 400
        finally:
            jobs.pop(job_id, None)
