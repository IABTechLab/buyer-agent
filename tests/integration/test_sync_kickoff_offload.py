# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Regression tests: sync CrewAI Flow.kickoff() must not
block the FastAPI event loop.

CrewAI's sync ``Flow.kickoff()`` internally does::

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(ctx.run, asyncio.run, _run_flow()).result()

The final ``.result()`` blocks the calling thread until the inner thread
finishes — so calling ``flow.kickoff()`` directly from a FastAPI
``async def`` handler blocks the entire event loop for the duration of
the run.

The fix is to wrap the call with ``asyncio.to_thread`` so the
``.result()`` runs on a worker thread instead of the event loop thread.
We deliberately do NOT use ``await flow.kickoff_async()`` — the buyer's
Flow ``@start`` / ``@listen`` step methods are themselves sync (they
call ``crew.kickoff()`` directly), so awaiting kickoff_async from the
event loop just runs those sync steps in the loop thread anyway.

These tests guard against anyone reverting to an un-offloaded sync
``kickoff()`` / ``approve_all()`` in async contexts by asserting the
calls execute on a worker thread, not on ``MainThread``.
"""

import pytest

from ad_buyer.flows.deal_booking_flow import DealBookingFlow
from ad_buyer.interfaces.api.main import (
    BookingRequest,
    CampaignBrief,
    _run_booking_flow,
    jobs,
)
from ad_buyer.time_utils import utc_now


def _seed_job(job_id: str) -> None:
    jobs[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "progress": 0.0,
        "errors": [],
        "budget_allocations": {},
        "recommendations": [],
        "booked_lines": [],
        "updated_at": utc_now().isoformat(),
        "created_at": utc_now().isoformat(),
    }


def _sample_brief() -> CampaignBrief:
    return CampaignBrief(
        name="sync-kickoff regression",
        objectives=["awareness"],
        budget=50000,
        start_date="2026-07-01",
        end_date="2026-07-31",
        target_audience={"geo": ["US"]},
    )


class TestRunBookingFlowOffloadsKickoff:
    """``api.main._run_booking_flow`` must offload sync CrewAI work so
    the FastAPI event loop stays responsive."""

    @pytest.mark.asyncio
    async def test_kickoff_runs_on_worker_thread(self, monkeypatch):
        """``flow.kickoff()`` (sync) must execute on a worker thread,
        never on ``MainThread``. ``asyncio.to_thread`` provides this
        offload; calling ``flow.kickoff()`` directly from the async
        background task would run it on the event-loop thread and
        block every other request."""

        import threading

        kickoff_threads: list[str] = []

        def fake_kickoff(self, *args, **kwargs):
            kickoff_threads.append(threading.current_thread().name)
            return {}

        monkeypatch.setattr(DealBookingFlow, "kickoff", fake_kickoff)

        job_id = "test-sync-kickoff-thread"
        _seed_job(job_id)
        try:
            await _run_booking_flow(
                job_id,
                BookingRequest(brief=_sample_brief(), auto_approve=False),
            )

            assert kickoff_threads, "flow.kickoff should have been called"
            assert "MainThread" not in kickoff_threads[0], (
                f"flow.kickoff ran on {kickoff_threads[0]} — sync-kickoff "
                "regression: should be offloaded via asyncio.to_thread"
            )
            assert jobs[job_id]["status"] != "failed", (
                f"Job failed unexpectedly: errors={jobs[job_id]['errors']}"
            )
        finally:
            jobs.pop(job_id, None)

    @pytest.mark.asyncio
    async def test_approve_all_runs_on_worker_thread(self, monkeypatch):
        """When ``auto_approve=True``, ``approve_all`` is sync CrewAI
        work and must be offloaded via ``asyncio.to_thread`` for the
        same reason as ``kickoff``."""

        import threading

        approve_threads: list[str] = []

        def fake_kickoff(self, *args, **kwargs):
            return {}

        def fake_approve_all(self):
            approve_threads.append(threading.current_thread().name)
            return {"status": "success", "booked_lines": []}

        monkeypatch.setattr(DealBookingFlow, "kickoff", fake_kickoff)
        monkeypatch.setattr(DealBookingFlow, "approve_all", fake_approve_all)

        job_id = "test-sync-approve-all-thread"
        _seed_job(job_id)
        try:
            await _run_booking_flow(
                job_id,
                BookingRequest(brief=_sample_brief(), auto_approve=True),
            )

            assert approve_threads, "approve_all should have been called"
            assert "MainThread" not in approve_threads[0], (
                f"approve_all ran on {approve_threads[0]} — sync-kickoff "
                "regression: should be offloaded via asyncio.to_thread"
            )
        finally:
            jobs.pop(job_id, None)


class TestSourceLevelAsyncContract:
    """Source-level assertions for async-context call sites.

    These guard against silent reverts to sync ``kickoff()`` /
    unwrapped ``approve_all()`` inside ``async def`` functions. They are
    cheaper than full mocking for sites whose runtime invocation needs
    extensive client/flow stubbing.
    """

    @staticmethod
    def _calls_in_async_functions(filename: str) -> dict[str, list[str]]:
        """Walk a source file and return, for each ``async def`` function,
        the list of attribute-call names invoked synchronously (i.e.
        not via ``await ...`` and not via ``asyncio.to_thread(...)``).
        """
        import ast
        import pathlib

        path = pathlib.Path(filename)
        tree = ast.parse(path.read_text())

        results: dict[str, list[str]] = {}

        class _Visitor(ast.NodeVisitor):
            def __init__(self):
                self._async_stack: list[str] = []

            def visit_AsyncFunctionDef(self, node):
                self._async_stack.append(node.name)
                results.setdefault(node.name, [])
                self.generic_visit(node)
                self._async_stack.pop()

            def visit_FunctionDef(self, node):
                # Skip nested sync defs — they have their own loop semantics.
                pass

            def visit_Await(self, node):
                # Anything under an Await is fine — don't descend.
                pass

            def visit_Call(self, node):
                if self._async_stack and isinstance(node.func, ast.Attribute):
                    results[self._async_stack[-1]].append(node.func.attr)
                self.generic_visit(node)

        _Visitor().visit(tree)
        return results

    def test_run_booking_flow_no_sync_kickoff(self):
        from ad_buyer.interfaces.api import main as api_main

        calls = self._calls_in_async_functions(api_main.__file__)
        booking_calls = calls.get("_run_booking_flow", [])
        assert "kickoff" not in booking_calls, (
            "_run_booking_flow contains a sync .kickoff() call — offload "
            "regression. Wrap with ``await asyncio.to_thread(flow.kickoff)``."
        )

    def test_approve_all_recommendations_endpoint_no_sync_approve(self):
        from ad_buyer.interfaces.api import main as api_main

        calls = self._calls_in_async_functions(api_main.__file__)
        endpoint_calls = calls.get("approve_all_recommendations", [])
        assert "approve_all" not in endpoint_calls, (
            "approve_all_recommendations endpoint contains a sync "
            ".approve_all() call — offload regression. Wrap with "
            "``await asyncio.to_thread(flow.approve_all)``."
        )
