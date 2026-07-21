"""Tests for DealBookingFlow._extract_allocations.

The portfolio crew has two tasks: budget allocation and channel coordination.
``crew.kickoff()`` returns a CrewOutput whose top-level ``raw`` reflects the
LAST task (channel coordination), not the first. Prior to the fix the
flow used ``str(result)`` and did a naive ``find('{')..rfind('}')`` extract,
which parsed the wrong task's JSON and produced empty budget_allocations.

These tests pin the new behaviour: we must read from ``tasks_output[0]``
(the budget allocation task), preferring ``pydantic`` then ``json_dict``,
falling back to a JSON block in ``raw``, and finally falling back to the
default split (with an error appended to flow.state.errors).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from ad_buyer.crews.portfolio_crew import BudgetAllocationOutput, _ChannelAllocationOut
from ad_buyer.flows.deal_booking_flow import DealBookingFlow


def _flow() -> DealBookingFlow:
    """Make a DealBookingFlow with the minimal state needed for extraction tests."""
    flow = DealBookingFlow(MagicMock(), campaign_brief={"budget": 100_000})
    return flow


def _crew_output_with(first_task: SimpleNamespace) -> SimpleNamespace:
    """Build a fake CrewOutput that has the given object at tasks_output[0]."""
    return SimpleNamespace(tasks_output=[first_task], raw="")


def test_extract_from_pydantic_output() -> None:
    """When the first task carries a typed BudgetAllocationOutput, use it."""
    flow = _flow()
    pyd = BudgetAllocationOutput(
        branding=_ChannelAllocationOut(budget=40_000, percentage=40, rationale="brand"),
        ctv=_ChannelAllocationOut(budget=20_000, percentage=20, rationale="ctv"),
        performance=_ChannelAllocationOut(budget=40_000, percentage=40, rationale="perf"),
    )
    result = _crew_output_with(SimpleNamespace(pydantic=pyd, json_dict=None, raw=""))

    allocations = flow._extract_allocations(result)

    assert allocations["branding"]["budget"] == 40_000
    assert allocations["ctv"]["budget"] == 20_000
    assert allocations["performance"]["budget"] == 40_000
    assert allocations["mobile_app"]["budget"] == 0
    assert flow.state.errors == []


def test_extract_from_json_dict_when_pydantic_missing() -> None:
    """Fall through to json_dict when pydantic is None."""
    flow = _flow()
    json_dict = {
        "branding": {"budget": 50_000, "percentage": 50, "rationale": "all-in branding"},
        "ctv": {"budget": 50_000, "percentage": 50, "rationale": "ctv"},
    }
    result = _crew_output_with(SimpleNamespace(pydantic=None, json_dict=json_dict, raw=""))

    allocations = flow._extract_allocations(result)

    assert allocations["branding"]["budget"] == 50_000
    assert allocations["ctv"]["budget"] == 50_000
    assert flow.state.errors == []


def test_extract_from_raw_text_when_others_missing() -> None:
    """If pydantic and json_dict are absent, parse a JSON block from raw text."""
    flow = _flow()
    raw = (
        "Here's the allocation:\n"
        '{"branding": {"budget": 60000, "percentage": 60, "rationale": "x"},'
        ' "performance": {"budget": 40000, "percentage": 40, "rationale": "y"}}'
    )
    result = _crew_output_with(SimpleNamespace(pydantic=None, json_dict=None, raw=raw))

    allocations = flow._extract_allocations(result)

    assert allocations["branding"]["budget"] == 60_000
    assert allocations["performance"]["budget"] == 40_000


def test_extract_falls_back_to_default_when_all_paths_fail() -> None:
    """When typed output is empty/zero and raw is garbage, fall back + log error."""
    flow = _flow()
    # Pydantic present but all-zero (e.g., the LLM emitted an empty object).
    pyd = BudgetAllocationOutput()
    result = _crew_output_with(SimpleNamespace(pydantic=pyd, json_dict=None, raw="no json here"))

    allocations = flow._extract_allocations(result)

    # Default split should be applied: 40/40/20 across branding/performance/ctv.
    assert allocations["branding"]["budget"] == 40_000
    assert allocations["performance"]["budget"] == 40_000
    assert allocations["ctv"]["budget"] == 20_000
    assert allocations["mobile_app"]["budget"] == 0
    # And an error should be surfaced for diagnostic visibility.
    assert any("portfolio crew" in e.lower() for e in flow.state.errors)


def test_extract_ignores_wrong_schema_from_last_task() -> None:
    """Regression test for the extraction bug itself.

    Before the fix the code did str(result) which captured the LAST task's
    output (channel coordination, different schema). This test pins that
    we never read from anywhere but tasks_output[0].
    """
    flow = _flow()
    # Mimic the buggy situation: top-level raw has the channel-coordination
    # schema (objectives/targeting_priorities), no budget keys anywhere.
    wrong_schema_raw = (
        '{"branding": {"objectives": ["awareness"], "targeting_priorities": ["18-34"]}}'
    )
    # tasks_output[0] has the RIGHT data via pydantic.
    pyd = BudgetAllocationOutput(
        branding=_ChannelAllocationOut(budget=30_000, percentage=30, rationale="brand"),
        ctv=_ChannelAllocationOut(budget=70_000, percentage=70, rationale="ctv"),
    )
    result = SimpleNamespace(
        tasks_output=[SimpleNamespace(pydantic=pyd, json_dict=None, raw="")],
        raw=wrong_schema_raw,
    )

    allocations = flow._extract_allocations(result)

    # Confirm we used tasks_output[0], not the top-level raw.
    assert allocations["branding"]["budget"] == 30_000
    assert allocations["ctv"]["budget"] == 70_000
    # Wrong-schema "objectives" key must NOT appear.
    assert "objectives" not in allocations["branding"]
