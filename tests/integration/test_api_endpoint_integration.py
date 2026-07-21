# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Integration tests: API endpoint integration.

Tests the FastAPI endpoints with httpx AsyncClient and ASGITransport,
verifying request routing, authentication middleware, job lifecycle,
and error propagation from API through to business logic.

Execution-boundary note: POST /bookings schedules
``_run_booking_flow`` as a Starlette background task, which ASGITransport
awaits as part of the response cycle -- so, since the and_() trigger fix
(c98ba1d), every lifecycle test that creates a booking genuinely ran
``DealBookingFlow.kickoff()`` and its full research crews (real paid LLM
calls when ANTHROPIC_API_KEY is exported). The module-level autouse
fixture below mocks the flow construction at the ``booking_service`` seam
(the same seam tests/unit/test_booking_service.py uses), so these tests
exercise the real API + booking_service state machine -- job creation,
status polling, awaiting_approval, approval, error propagation -- without
ever spinning a crew. A sentinel on the real ``DealBookingFlow.kickoff``
fails loudly if any future wiring change reopens the leak.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from httpx import ASGITransport

from ad_buyer.config.settings import Settings
from ad_buyer.flows.deal_booking_flow import DealBookingFlow
from ad_buyer.interfaces.api import main as api_module
from ad_buyer.interfaces.api.main import app, jobs
from ad_buyer.models.flow_state import (
    BookedLine,
    ChannelAllocation,
    ProductRecommendation,
)
from ad_buyer.services import booking_service


class _FakeBookingFlow:
    """Crew-free DealBookingFlow stand-in for API lifecycle tests.

    Mirrors the flow surface ``booking_service`` touches: ``kickoff()``
    populates typed state (allocations + pending approvals), the approval
    methods book the approved subset as real ``BookedLine`` models, and
    ``state.errors`` feeds the service's error propagation.
    """

    def __init__(self, client, store=None, orchestrator=None, campaign_brief=None, **kwargs):
        self._brief = dict(campaign_brief or {})
        self.state = SimpleNamespace(
            errors=[],
            budget_allocations={},
            pending_approvals=[],
            booked_lines=[],
        )

    def kickoff(self):
        budget = float(self._brief.get("budget", 0) or 0)
        self.state.budget_allocations = {
            "branding": ChannelAllocation(
                channel="branding",
                budget=budget,
                percentage=100.0,
                rationale="mocked research",
            )
        }
        self.state.pending_approvals = [
            ProductRecommendation(
                product_id="prod-mock-1",
                product_name="Mock Homepage Takeover",
                publisher="MockPub",
                channel="branding",
                impressions=1_000_000,
                cpm=15.0,
                cost=15_000.0,
            )
        ]
        return {}

    def _book(self, approved_ids: list[str]) -> dict:
        booked = []
        for rec in self.state.pending_approvals:
            if rec.product_id in approved_ids:
                rec.status = "approved"
                booked.append(
                    BookedLine(
                        deal_id=f"DEAL-{rec.product_id}",
                        product_id=rec.product_id,
                        product_name=rec.product_name,
                        channel=rec.channel,
                        impressions=rec.impressions,
                        cpm=rec.cpm,
                        cost=rec.cost,
                        booking_status="booked",
                        booked_at=datetime.now(UTC),
                    )
                )
            else:
                rec.status = "rejected"
        self.state.booked_lines = booked
        return {
            "status": "success" if booked else "failed",
            "booked": len(booked),
            "total_cost": sum(b.cost for b in booked),
        }

    def approve_all(self):
        return self._book([r.product_id for r in self.state.pending_approvals])

    def approve_recommendations(self, approved_ids):
        return self._book(list(approved_ids))


@pytest.fixture(autouse=True)
def _seal_execution_boundary(monkeypatch):
    """Mock the crew-execution boundary for every test in this module.

    ``booking_service.execute_booking`` constructs its flow via the
    module-global ``DealBookingFlow`` name, so patching that name swaps in
    the crew-free fake while the real service state machine still runs.
    The sentinel on the real class guarantees that no wiring change can
    silently reintroduce real crew runs (and paid LLM calls) here.
    """

    def _sentinel_kickoff(self, *args, **kwargs):
        raise AssertionError(
            "Real DealBookingFlow.kickoff() invoked from an API lifecycle test -- "
            "this spins full research crews (paid LLM calls with a key in env). "
            "The execution boundary must stay mocked."
        )

    monkeypatch.setattr(DealBookingFlow, "kickoff", _sentinel_kickoff)
    monkeypatch.setattr(booking_service, "DealBookingFlow", _FakeBookingFlow)


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
        # Status should be any valid job state (background task may or may not have started;
        # awaiting_approval is valid when auto_approve=False and flow ran to completion)
        assert status_data["status"] in (
            "pending",
            "running",
            "failed",
            "completed",
            "awaiting_approval",
        )

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


class TestBookingApprovalLifecycle:
    """Full job state machine through the API with the crew boundary mocked.

    ASGITransport awaits Starlette background tasks as part of the response
    cycle, so by the time POST /bookings returns the (mocked) flow has run
    and the job state is deterministic.
    """

    @staticmethod
    def _brief_payload(auto_approve: bool = False) -> dict:
        return {
            "brief": {
                "name": "Lifecycle Campaign",
                "objectives": ["awareness"],
                "budget": 50000,
                "start_date": "2025-05-01",
                "end_date": "2025-05-31",
                "target_audience": {"geo": ["US"]},
            },
            "auto_approve": auto_approve,
        }

    @pytest.mark.asyncio
    async def test_job_reaches_awaiting_approval_with_recommendations(self):
        """auto_approve=False: research holds for approval; pollers see recs."""
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.object(api_module, "settings", _make_settings("")):
                create_resp = await client.post("/bookings", json=self._brief_payload())
                job_id = create_resp.json()["job_id"]
                status_resp = await client.get(f"/bookings/{job_id}")

        try:
            data = status_resp.json()
            assert data["status"] == "awaiting_approval"
            assert data["progress"] == 0.9
            assert [r["product_id"] for r in data["recommendations"]] == ["prod-mock-1"]
            assert data["budget_allocations"]["branding"]["budget"] == 50000
            assert data["booked_lines"] == []
        finally:
            jobs.pop(job_id, None)

    @pytest.mark.asyncio
    async def test_approve_all_completes_job_and_books_lines(self):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.object(api_module, "settings", _make_settings("")):
                create_resp = await client.post("/bookings", json=self._brief_payload())
                job_id = create_resp.json()["job_id"]
                approve_resp = await client.post(f"/bookings/{job_id}/approve-all")
                status_resp = await client.get(f"/bookings/{job_id}")

        try:
            assert approve_resp.status_code == 200
            assert approve_resp.json()["status"] == "success"
            data = status_resp.json()
            assert data["status"] == "completed"
            assert data["progress"] == 1.0
            assert [b["product_id"] for b in data["booked_lines"]] == ["prod-mock-1"]
        finally:
            jobs.pop(job_id, None)

    @pytest.mark.asyncio
    async def test_approve_specific_books_only_approved(self):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.object(api_module, "settings", _make_settings("")):
                create_resp = await client.post("/bookings", json=self._brief_payload())
                job_id = create_resp.json()["job_id"]
                approve_resp = await client.post(
                    f"/bookings/{job_id}/approve",
                    json={"approved_product_ids": ["prod-mock-1"]},
                )
                status_resp = await client.get(f"/bookings/{job_id}")

        try:
            assert approve_resp.status_code == 200
            body = approve_resp.json()
            assert body["status"] == "success"
            assert body["approved_count"] == 1
            assert body["booked"] == 1
            assert status_resp.json()["status"] == "completed"
        finally:
            jobs.pop(job_id, None)

    @pytest.mark.asyncio
    async def test_auto_approve_completes_without_manual_approval(self):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.object(api_module, "settings", _make_settings("")):
                create_resp = await client.post(
                    "/bookings", json=self._brief_payload(auto_approve=True)
                )
                job_id = create_resp.json()["job_id"]
                status_resp = await client.get(f"/bookings/{job_id}")

        try:
            data = status_resp.json()
            assert data["status"] == "completed"
            assert data["progress"] == 1.0
            assert [b["product_id"] for b in data["booked_lines"]] == ["prod-mock-1"]
        finally:
            jobs.pop(job_id, None)

    @pytest.mark.asyncio
    async def test_flow_construction_failure_propagates_to_job_errors(self, monkeypatch):
        """An execution failure must surface as a failed job with the error."""

        def _boom(*args, **kwargs):
            raise RuntimeError("research crew exploded")

        monkeypatch.setattr(booking_service, "DealBookingFlow", _boom)

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.object(api_module, "settings", _make_settings("")):
                create_resp = await client.post("/bookings", json=self._brief_payload())
                job_id = create_resp.json()["job_id"]
                status_resp = await client.get(f"/bookings/{job_id}")

        try:
            data = status_resp.json()
            assert data["status"] == "failed"
            assert any("research crew exploded" in e for e in data["errors"])
        finally:
            jobs.pop(job_id, None)

    @pytest.mark.asyncio
    async def test_flow_state_errors_surface_in_status(self, monkeypatch):
        """Errors recorded on flow.state during kickoff reach pollers."""

        class _ErroringFlow(_FakeBookingFlow):
            def kickoff(self):
                result = super().kickoff()
                self.state.errors.append("Branding research failed: seller 502")
                return result

        monkeypatch.setattr(booking_service, "DealBookingFlow", _ErroringFlow)

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with patch.object(api_module, "settings", _make_settings("")):
                create_resp = await client.post("/bookings", json=self._brief_payload())
                job_id = create_resp.json()["job_id"]
                status_resp = await client.get(f"/bookings/{job_id}")

        try:
            data = status_resp.json()
            assert data["status"] == "awaiting_approval"
            assert any("Branding research failed" in e for e in data["errors"])
        finally:
            jobs.pop(job_id, None)


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
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
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
