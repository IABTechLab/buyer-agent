# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the campaign booking application service (EP-2.2).

Exercises ``ad_buyer.services.booking_service`` directly: job-record
construction, DealStore persistence, the DealBookingFlow execution
handoff (with the sync-kickoff worker-thread offload), and approval
execution -- happy paths and edge cases.

bead: ar-22w1
"""

from __future__ import annotations

import threading

import pytest

from ad_buyer.services import booking_service
from ad_buyer.storage.deal_store import DealStore

DB_URL = "sqlite:///:memory:"


@pytest.fixture
def store():
    s = DealStore(DB_URL)
    s.connect()
    yield s
    s.disconnect()


def _brief() -> dict:
    return {
        "name": "Test Campaign",
        "objectives": ["awareness"],
        "budget": 50000,
        "start_date": "2026-07-01",
        "end_date": "2026-07-31",
        "target_audience": {"geo": ["US"]},
    }


# ---------------------------------------------------------------------------
# Job record + persistence
# ---------------------------------------------------------------------------


class TestNewJobRecord:
    def test_shape(self):
        job = booking_service.new_job_record(_brief(), auto_approve=True)
        assert job["status"] == "pending"
        assert job["progress"] == 0.0
        assert job["auto_approve"] is True
        assert job["brief"]["name"] == "Test Campaign"
        assert job["created_at"] == job["updated_at"]
        for key in ("budget_allocations", "recommendations", "booked_lines", "errors"):
            assert key in job


class TestPersistJob:
    def test_writes_to_store(self, store):
        job = booking_service.new_job_record(_brief(), auto_approve=False)
        job["status"] = "running"
        job["progress"] = 0.5
        booking_service.persist_job(store, "job-001", job)

        stored = store.get_job("job-001")
        assert stored is not None
        assert stored["status"] == "running"
        assert stored["progress"] == 0.5
        assert stored["brief"]["name"] == "Test Campaign"

    def test_none_store_is_noop(self):
        # Must not raise.
        booking_service.persist_job(None, "job-x", {"status": "pending"})

    def test_upserts(self, store):
        job = booking_service.new_job_record(_brief(), auto_approve=False)
        booking_service.persist_job(store, "job-002", job)
        job["status"] = "completed"
        job["progress"] = 1.0
        booking_service.persist_job(store, "job-002", job)
        stored = store.get_job("job-002")
        assert stored["status"] == "completed"
        assert stored["progress"] == 1.0


# ---------------------------------------------------------------------------
# Flow execution
# ---------------------------------------------------------------------------


class _FakeState:
    def __init__(self):
        self.errors: list = []
        self.budget_allocations: dict = {}
        self.pending_approvals: list = []
        self.booked_lines: list = []


class _FakeFlow:
    """Stand-in for DealBookingFlow that records the kickoff thread."""

    def __init__(self, *args, **kwargs):
        self.state = _FakeState()
        self.kickoff_thread: str | None = None
        self.approve_all_thread: str | None = None

    def kickoff(self):
        self.kickoff_thread = threading.current_thread().name
        return {}

    def approve_all(self):
        self.approve_all_thread = threading.current_thread().name
        return {"status": "success"}

    def approve_recommendations(self, ids):
        return {"status": "success", "booked": len(ids), "total_cost": 0}


@pytest.mark.asyncio
class TestExecuteBooking:
    async def test_holds_for_approval(self, store, monkeypatch):
        created: list[_FakeFlow] = []

        def _factory(*args, **kwargs):
            f = _FakeFlow()
            created.append(f)
            return f

        monkeypatch.setattr(booking_service, "DealBookingFlow", _factory)

        job = booking_service.new_job_record(_brief(), auto_approve=False)
        await booking_service.execute_booking(
            "j1",
            job,
            _brief(),
            auto_approve=False,
            client=object(),
            store=store,
            persist=lambda jid, j: booking_service.persist_job(store, jid, j),
        )

        assert job["status"] == "awaiting_approval"
        assert job["_flow"] is created[0]
        # buyer-1g4: kickoff must run off the main thread.
        assert created[0].kickoff_thread is not None
        assert "MainThread" not in created[0].kickoff_thread
        # Persisted snapshot exists.
        assert store.get_job("j1")["status"] == "awaiting_approval"

    async def test_auto_approve_completes(self, store, monkeypatch):
        created: list[_FakeFlow] = []
        monkeypatch.setattr(
            booking_service,
            "DealBookingFlow",
            lambda *a, **k: created.append(_FakeFlow()) or created[-1],
        )

        job = booking_service.new_job_record(_brief(), auto_approve=True)
        await booking_service.execute_booking(
            "j2",
            job,
            _brief(),
            auto_approve=True,
            client=object(),
            store=store,
            persist=lambda jid, j: booking_service.persist_job(store, jid, j),
        )

        assert job["status"] == "completed"
        assert created[0].approve_all_thread is not None
        assert "MainThread" not in created[0].approve_all_thread

    async def test_failure_is_recorded(self, store, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("flow blew up")

        monkeypatch.setattr(booking_service, "DealBookingFlow", _boom)

        job = booking_service.new_job_record(_brief(), auto_approve=False)
        await booking_service.execute_booking(
            "j3",
            job,
            _brief(),
            auto_approve=False,
            client=object(),
            store=store,
            persist=lambda jid, j: booking_service.persist_job(store, jid, j),
        )

        assert job["status"] == "failed"
        assert any("flow blew up" in e for e in job["errors"])


@pytest.mark.asyncio
class TestApproval:
    async def test_approve_all(self, store):
        job = booking_service.new_job_record(_brief(), auto_approve=False)
        job["_flow"] = _FakeFlow()
        result = await booking_service.approve_all(
            "j4", job, store=store, persist=lambda jid, j: None
        )
        assert result["status"] == "success"
        assert job["status"] == "completed"
        assert job["progress"] == 1.0

    async def test_approve_specific(self, store):
        job = booking_service.new_job_record(_brief(), auto_approve=False)
        job["_flow"] = _FakeFlow()
        result = await booking_service.approve(
            "j5", job, ["p1", "p2"], store=store, persist=lambda jid, j: None
        )
        assert result["status"] == "success"
        assert result["approved_count"] == 2
        assert job["status"] == "completed"
