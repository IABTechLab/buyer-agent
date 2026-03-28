# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Trace-level integration tests for DealBookingFlow timing and sequencing.

Runs the full async booking pipeline with lightweight crew mocks and
captures structured log output to verify:

  1. Portfolio crew runs and returns valid JSON allocations.
  2. All four research branches complete BEFORE consolidation fires.
  3. The job dict reaches ``awaiting_approval`` (never premature).
  4. ``budget_allocations`` and ``recommendations`` are populated after kickoff.

Run with:
    cd /Users/dnicol/IAB\ Agents/buyer-agent
    uv run pytest -s -v tests/integration/test_flow_timing_trace.py
"""

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from unittest.mock import AsyncMock

from ad_buyer.clients.opendirect_client import OpenDirectClient
from ad_buyer.flows.deal_booking_flow import DealBookingFlow
from ad_buyer.models.flow_state import ExecutionStatus


# ---------------------------------------------------------------------------
# Shared mock payloads
# ---------------------------------------------------------------------------

_ALLOCATION_JSON = json.dumps({
    "branding":    {"budget": 40000, "percentage": 40, "rationale": "Awareness via display"},
    "ctv":         {"budget": 25000, "percentage": 25, "rationale": "Premium video reach"},
    "performance": {"budget": 25000, "percentage": 25, "rationale": "Conversion remarketing"},
    "mobile_app":  {"budget": 10000, "percentage": 10, "rationale": "App install push"},
})

# One recommendation per channel (product_id must differ per crew)
def _rec_json(channel: str) -> str:
    return json.dumps([{
        "product_id":   f"prod_{channel}_001",
        "product_name": f"Test {channel} product",
        "publisher":    "Test Publisher",
        "impressions":  100_000,
        "cpm":          10.0,
        "cost":         1_000.0,
    }])


def _mock_crew(result: str) -> MagicMock:
    """Return a MagicMock whose kickoff() and kickoff_async() both return *result*."""
    m = MagicMock()
    m.kickoff.return_value = result
    m.kickoff_async = AsyncMock(return_value=result)
    return m


_BRIEF: dict[str, Any] = {
    "name":            "Trace Test Campaign",
    "objectives":      ["brand_awareness", "reach"],
    "budget":          100_000,
    "start_date":      "2025-06-01",
    "end_date":        "2025-06-30",
    "target_audience": {"age": "25-54", "geo": ["US"]},
    "kpis":            {"viewability": 70},
}


# ---------------------------------------------------------------------------
# Fixture: capture log records in order
# ---------------------------------------------------------------------------

class _OrderedCapture(logging.Handler):
    """Lightweight handler that stores formatted records in insertion order."""

    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        self.records.append(record)

    def messages(self) -> list[str]:
        return [r.getMessage() for r in self.records]

    def find_index(self, pattern: str) -> int:
        """Return index of first message matching *pattern*, or -1."""
        rx = re.compile(pattern)
        for i, msg in enumerate(self.messages()):
            if rx.search(msg):
                return i
        return -1


@pytest.fixture()
def log_capture() -> _OrderedCapture:
    """Attach a capturing handler to the flow logger for the duration of the test."""
    handler = _OrderedCapture()
    handler.setLevel(logging.DEBUG)

    # Capture both the flow logger and the root logger so crew output is included
    flow_logger = logging.getLogger("ad_buyer.flows.deal_booking_flow")
    flow_logger.addHandler(handler)
    flow_logger.setLevel(logging.DEBUG)

    yield handler

    flow_logger.removeHandler(handler)


# ---------------------------------------------------------------------------
# Helper: patch all crew factories
# ---------------------------------------------------------------------------

def _patch_all_crews():
    """Context manager that patches every crew factory used by the flow."""
    return patch.multiple(
        "ad_buyer.flows.deal_booking_flow",
        create_portfolio_crew=MagicMock(return_value=_mock_crew(_ALLOCATION_JSON)),
        create_branding_crew=MagicMock(return_value=_mock_crew(_rec_json("branding"))),
        create_ctv_crew=MagicMock(return_value=_mock_crew(_rec_json("ctv"))),
        create_mobile_crew=MagicMock(return_value=_mock_crew(_rec_json("mobile_app"))),
        create_performance_crew=MagicMock(return_value=_mock_crew(_rec_json("performance"))),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFlowTimingSequence:
    """Verify the event ordering and final state of a full kickoff_async run."""

    @pytest.mark.asyncio
    async def test_kickoff_reaches_awaiting_approval(self, log_capture):
        """Flow should reach AWAITING_APPROVAL with all channels populated."""
        client = OpenDirectClient(base_url="http://fake.seller")
        flow = DealBookingFlow(client, campaign_brief=_BRIEF)

        with _patch_all_crews():
            await flow.kickoff_async()

        # ── Status ──────────────────────────────────────────────────────────
        assert flow.state.execution_status == ExecutionStatus.AWAITING_APPROVAL, (
            f"Expected AWAITING_APPROVAL, got {flow.state.execution_status}. "
            f"Errors: {flow.state.errors}"
        )

    @pytest.mark.asyncio
    async def test_all_four_channels_allocated(self, log_capture):
        """All four channels should have a budget allocation after kickoff."""
        client = OpenDirectClient(base_url="http://fake.seller")
        flow = DealBookingFlow(client, campaign_brief=_BRIEF)

        with _patch_all_crews():
            await flow.kickoff_async()

        allocs = flow.state.budget_allocations
        assert len(allocs) == 4, f"Expected 4 allocations, got {len(allocs)}: {list(allocs)}"
        for ch in ("branding", "ctv", "performance", "mobile_app"):
            assert ch in allocs, f"Missing allocation for channel: {ch}"
            assert allocs[ch].budget > 0, f"Zero budget for channel: {ch}"

    @pytest.mark.asyncio
    async def test_all_four_channels_recommended(self, log_capture):
        """Each channel should have at least one recommendation after kickoff."""
        client = OpenDirectClient(base_url="http://fake.seller")
        flow = DealBookingFlow(client, campaign_brief=_BRIEF)

        with _patch_all_crews():
            await flow.kickoff_async()

        recs = flow.state.channel_recommendations
        assert len(recs) == 4, f"Expected recs for 4 channels, got {list(recs)}"
        for ch in ("branding", "ctv", "performance", "mobile_app"):
            assert ch in recs, f"No recommendations for channel: {ch}"
            assert len(recs[ch]) > 0, f"Empty recommendations for channel: {ch}"

        pending = flow.state.pending_approvals
        assert len(pending) == 4, f"Expected 4 pending approvals, got {len(pending)}"

    @pytest.mark.asyncio
    async def test_research_completes_before_consolidation(self, log_capture):
        """All research END log lines must appear before consolidate ENTERED."""
        client = OpenDirectClient(base_url="http://fake.seller")
        flow = DealBookingFlow(client, campaign_brief=_BRIEF)

        with _patch_all_crews():
            await flow.kickoff_async()

        msgs = log_capture.messages()

        # Print the full trace for inspection
        print("\n━━━ Captured log sequence ━━━")
        for i, m in enumerate(msgs):
            print(f"  [{i:02d}] {m}")
        print("━━━ End trace ━━━\n")

        consolidate_idx = log_capture.find_index(r"consolidate_recommendations ENTERED")
        assert consolidate_idx >= 0, (
            "consolidate_recommendations ENTERED log line not found.\n"
            f"All messages: {msgs}"
        )

        # Every research END log must appear before consolidation
        for channel in ("branding", "ctv", "mobile", "performance"):
            end_idx = log_capture.find_index(rf"research_{channel}.*END")
            assert end_idx >= 0, (
                f"research_{channel} END log line not found.\n"
                f"All messages: {msgs}"
            )
            assert end_idx < consolidate_idx, (
                f"research_{channel} END (idx={end_idx}) appeared AFTER "
                f"consolidate_recommendations ENTERED (idx={consolidate_idx}).\n"
                f"This means consolidation fired before all research completed!\n"
                f"Full message list:\n" + "\n".join(f"  [{i}] {m}" for i, m in enumerate(msgs))
            )

    @pytest.mark.asyncio
    async def test_no_premature_awaiting_approval(self, log_capture):
        """consolidate_recommendations must fire exactly once (not per-research-branch).

        CrewAI caches method references at decoration time so we cannot spy via
        instance attribute replacement.  Instead we count "ENTERED" log lines and
        check the flow state after kickoff_async completes.
        """
        client = OpenDirectClient(base_url="http://fake.seller")
        flow = DealBookingFlow(client, campaign_brief=_BRIEF)

        with _patch_all_crews():
            await flow.kickoff_async()

        msgs = log_capture.messages()
        entered_count = sum(
            1 for m in msgs if re.search(r"consolidate_recommendations ENTERED", m)
        )

        assert entered_count == 1, (
            f"consolidate_recommendations ENTERED appeared {entered_count} times "
            f"(expected exactly 1 — if 4, and_() is being treated as or_()).\n"
            f"Full log:\n" + "\n".join(f"  {m}" for m in msgs)
        )

        # Verify final state is correct after consolidation
        assert flow.state.execution_status == ExecutionStatus.AWAITING_APPROVAL
        assert len(flow.state.pending_approvals) == 4, (
            f"Expected 4 pending approvals, got {len(flow.state.pending_approvals)}"
        )


class TestFullJobPipeline:
    """Test the complete _run_booking_flow background task with the jobs dict."""

    @pytest.mark.asyncio
    async def test_run_booking_flow_job_reaches_awaiting_approval(self):
        """_run_booking_flow should write awaiting_approval to the jobs dict."""
        from ad_buyer.interfaces.api.main import _run_booking_flow, jobs
        from ad_buyer.interfaces.api.main import BookingRequest, CampaignBrief

        job_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        jobs[job_id] = {
            "status": "pending",
            "progress": 0.0,
            "brief": _BRIEF,
            "auto_approve": False,
            "budget_allocations": {},
            "recommendations": [],
            "booked_lines": [],
            "errors": [],
            "created_at": now,
            "updated_at": now,
        }

        request = BookingRequest(
            brief=CampaignBrief(**_BRIEF),
            auto_approve=False,
        )

        with _patch_all_crews():
            await _run_booking_flow(job_id, request)

        job = jobs[job_id]

        print(f"\n━━━ Job final state ━━━")
        print(f"  status:           {job['status']}")
        print(f"  progress:         {job['progress']}")
        print(f"  budget_allocs:    {list(job['budget_allocations'])}")
        print(f"  recommendations:  {len(job['recommendations'])}")
        print(f"  errors:           {job['errors']}")
        print("━━━━━━━━━━━━━━━━━━━━━━━\n")

        assert job["status"] == "awaiting_approval", (
            f"Expected 'awaiting_approval', got {job['status']!r}.\n"
            f"Errors: {job['errors']}"
        )
        assert job["progress"] == 0.9
        assert len(job["budget_allocations"]) == 4
        assert len(job["recommendations"]) == 4

        # Clean up
        del jobs[job_id]

    @pytest.mark.asyncio
    async def test_run_booking_flow_auto_approve_completes(self):
        """With auto_approve=True the job should reach 'completed'."""
        from ad_buyer.interfaces.api.main import _run_booking_flow, jobs
        from ad_buyer.interfaces.api.main import BookingRequest, CampaignBrief

        job_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        jobs[job_id] = {
            "status": "pending",
            "progress": 0.0,
            "brief": _BRIEF,
            "auto_approve": True,
            "budget_allocations": {},
            "recommendations": [],
            "booked_lines": [],
            "errors": [],
            "created_at": now,
            "updated_at": now,
        }

        request = BookingRequest(
            brief=CampaignBrief(**_BRIEF),
            auto_approve=True,
        )

        with _patch_all_crews():
            await _run_booking_flow(job_id, request)

        job = jobs[job_id]

        print(f"\n━━━ Auto-approve job final state ━━━")
        print(f"  status:       {job['status']}")
        print(f"  booked_lines: {len(job['booked_lines'])}")
        print(f"  errors:       {job['errors']}")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

        assert job["status"] == "completed", (
            f"Expected 'completed' with auto_approve, got {job['status']!r}.\n"
            f"Errors: {job['errors']}"
        )
        assert len(job["booked_lines"]) == 4

        # Clean up
        del jobs[job_id]


class TestPortfolioCrew:
    """Unit-level tests for the portfolio crew (sequential process)."""

    def test_portfolio_crew_uses_sequential_process(self):
        """Portfolio crew must use Process.sequential (not hierarchical)."""
        from crewai import Process
        from ad_buyer.crews.portfolio_crew import create_portfolio_crew

        client = OpenDirectClient(base_url="http://fake.seller")
        crew = create_portfolio_crew(client, _BRIEF)

        assert crew.process == Process.sequential, (
            f"Expected Process.sequential to avoid tool_use/tool_result mismatch, "
            f"got {crew.process}"
        )

    def test_portfolio_manager_disallows_delegation(self):
        """Portfolio Manager must have allow_delegation=False in sequential mode."""
        from ad_buyer.agents.level1.portfolio_manager import create_portfolio_manager

        manager = create_portfolio_manager()
        assert manager.allow_delegation is False, (
            "Portfolio Manager has allow_delegation=True which causes "
            "ask_question_to_coworker tool_use errors on retry"
        )

    def test_portfolio_crew_has_one_task(self):
        """Portfolio crew should have exactly one task: budget allocation.

        The former channel_coordination_task was removed because kickoff()
        returns the *last* task's output, so having two tasks meant the
        budget JSON was never captured by _parse_allocations.
        """
        from ad_buyer.crews.portfolio_crew import create_portfolio_crew

        client = OpenDirectClient(base_url="http://fake.seller")
        crew = create_portfolio_crew(client, _BRIEF)

        assert len(crew.tasks) == 1, f"Expected 1 task, got {len(crew.tasks)}"

    def test_portfolio_crew_result_parsed_into_allocations(self):
        """_parse_allocations should correctly parse the portfolio crew JSON output."""
        client = OpenDirectClient(base_url="http://fake.seller")
        flow = DealBookingFlow(client, campaign_brief=_BRIEF)

        result = flow._parse_allocations(_ALLOCATION_JSON)

        assert "branding" in result
        assert result["branding"]["budget"] == 40_000
        assert result["ctv"]["budget"] == 25_000
        assert result["performance"]["budget"] == 25_000
        assert result["mobile_app"]["budget"] == 10_000
