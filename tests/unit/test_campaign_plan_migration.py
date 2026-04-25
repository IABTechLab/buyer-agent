# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for CampaignPlan.target_audience migration to AudiencePlan.

bead: ar-fe0h (proposal §6 row 4)

CampaignPlan now carries `target_audience: AudiencePlan | None`. Going
through `CampaignPipeline.ingest_brief` -> `plan_campaign` should yield
a CampaignPlan whose `target_audience` is the typed AudiencePlan that
was migrated from the legacy `list[str]` brief input.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import date, timedelta
from typing import Any

import pytest

from ad_buyer.models.audience_plan import AudiencePlan
from ad_buyer.models.state_machine import CampaignStatus
from ad_buyer.pipelines.campaign_pipeline import CampaignPipeline, CampaignPlan


class _FakeStore:
    """Mini in-memory CampaignStore used only by the migration tests."""

    def __init__(self) -> None:
        self._campaigns: dict[str, dict[str, Any]] = {}

    def create_campaign(self, brief: dict[str, Any]) -> str:
        cid = str(uuid.uuid4())
        # Mirror the production schema: target_audience is a JSON TEXT col.
        self._campaigns[cid] = {
            "campaign_id": cid,
            "advertiser_id": brief["advertiser_id"],
            "campaign_name": brief["campaign_name"],
            "status": CampaignStatus.DRAFT.value,
            "total_budget": brief["total_budget"],
            "currency": brief.get("currency", "USD"),
            "flight_start": brief["flight_start"],
            "flight_end": brief["flight_end"],
            "channels": brief.get("channels"),
            "target_audience": brief.get("target_audience"),
        }
        return cid

    def get_campaign(self, cid: str) -> dict[str, Any] | None:
        return self._campaigns.get(cid)

    def start_planning(self, cid: str) -> None:
        self._campaigns[cid]["status"] = CampaignStatus.PLANNING.value


def _brief_with_legacy_audience():
    today = date.today()
    return {
        "advertiser_id": "adv-001",
        "campaign_name": "Migration Test",
        "objective": "AWARENESS",
        "total_budget": 100_000.0,
        "currency": "USD",
        "flight_start": (today + timedelta(days=7)).isoformat(),
        "flight_end": (today + timedelta(days=37)).isoformat(),
        "channels": [{"channel": "CTV", "budget_pct": 100.0}],
        "target_audience": ["legacy-seg-A", "legacy-seg-B"],
    }


def _brief_with_typed_plan():
    today = date.today()
    return {
        "advertiser_id": "adv-001",
        "campaign_name": "Migration Test",
        "objective": "AWARENESS",
        "total_budget": 100_000.0,
        "currency": "USD",
        "flight_start": (today + timedelta(days=7)).isoformat(),
        "flight_end": (today + timedelta(days=37)).isoformat(),
        "channels": [{"channel": "CTV", "budget_pct": 100.0}],
        "target_audience": {
            "primary": {
                "type": "standard",
                "identifier": "explicit-seg",
                "taxonomy": "iab-audience",
                "version": "1.1",
                "source": "explicit",
            },
        },
    }


# ---------------------------------------------------------------------------
# CampaignPlan dataclass
# ---------------------------------------------------------------------------


def test_campaign_plan_default_target_audience_is_none():
    plan = CampaignPlan(
        campaign_id="c-1",
        channel_plans=[],
        total_budget=1000.0,
        flight_start="2026-05-01",
        flight_end="2026-06-30",
    )
    assert plan.target_audience is None


def test_campaign_plan_accepts_audience_plan_instance():
    from ad_buyer.models.audience_plan import AudienceRef

    ap = AudiencePlan(
        primary=AudienceRef(
            type="standard",
            identifier="X",
            taxonomy="iab-audience",
            version="1.1",
            source="explicit",
        )
    )
    plan = CampaignPlan(
        campaign_id="c-1",
        channel_plans=[],
        total_budget=1000.0,
        flight_start="2026-05-01",
        flight_end="2026-06-30",
        target_audience=ap,
    )
    assert plan.target_audience is ap


# ---------------------------------------------------------------------------
# Pipeline integration: legacy brief -> typed plan
# ---------------------------------------------------------------------------


def test_pipeline_ingests_legacy_brief_and_plan_carries_typed_audience():
    """Legacy `list[str]` brief flows through to a typed CampaignPlan.

    This exercises the full path:
      brief (list[str]) -> ingest -> SQLite TEXT (AudiencePlan dict)
                        -> plan_campaign -> CampaignPlan.target_audience
    """
    store = _FakeStore()
    # Stub orchestrator -- not called in the plan_campaign stage we test.
    pipeline = CampaignPipeline(store=store, orchestrator=None)  # type: ignore[arg-type]
    brief_data = _brief_with_legacy_audience()

    cid = asyncio.run(pipeline.ingest_brief(brief_data))
    plan = asyncio.run(pipeline.plan_campaign(cid))

    assert isinstance(plan, CampaignPlan)
    assert isinstance(plan.target_audience, AudiencePlan)
    assert plan.target_audience.primary.identifier == "legacy-seg-A"
    assert len(plan.target_audience.extensions) == 1
    assert plan.target_audience.extensions[0].identifier == "legacy-seg-B"
    # source=inferred per the locked migration policy
    assert plan.target_audience.primary.source == "inferred"


def test_pipeline_persists_audience_plan_dict_to_sqlite_column():
    """The SQLite TEXT column receives the new AudiencePlan dict shape.

    Old rows that already hold list[str] keep working because the load-side
    shim in `_reconstruct_brief` re-applies the migration; new rows persist
    the typed shape so subsequent loads avoid the shim entirely.
    """
    store = _FakeStore()
    pipeline = CampaignPipeline(store=store, orchestrator=None)  # type: ignore[arg-type]
    brief_data = _brief_with_legacy_audience()

    cid = asyncio.run(pipeline.ingest_brief(brief_data))
    raw = store.get_campaign(cid)
    assert raw is not None
    audience_text = raw["target_audience"]
    # Should be JSON string of an AudiencePlan dict, not list[str].
    decoded = json.loads(audience_text)
    assert isinstance(decoded, dict)
    assert "primary" in decoded
    assert decoded["primary"]["identifier"] == "legacy-seg-A"


def test_pipeline_typed_plan_passthrough():
    """A brief that already carries the typed dict shape reaches the plan."""
    store = _FakeStore()
    pipeline = CampaignPipeline(store=store, orchestrator=None)  # type: ignore[arg-type]
    brief_data = _brief_with_typed_plan()

    cid = asyncio.run(pipeline.ingest_brief(brief_data))
    plan = asyncio.run(pipeline.plan_campaign(cid))
    assert isinstance(plan.target_audience, AudiencePlan)
    assert plan.target_audience.primary.identifier == "explicit-seg"
    assert plan.target_audience.primary.source == "explicit"


# ---------------------------------------------------------------------------
# Reconstruct path: legacy SQLite row -> AudiencePlan
# ---------------------------------------------------------------------------


def test_reconstruct_brief_handles_legacy_list_text():
    """A SQLite row carrying a JSON list[str] still reconstructs.

    Simulates the lazy-migration scenario: an old row written before this
    bead landed. `_reconstruct_brief` runs the shim and yields a brief
    with a typed AudiencePlan.
    """
    store = _FakeStore()
    pipeline = CampaignPipeline(store=store, orchestrator=None)  # type: ignore[arg-type]
    today = date.today()
    legacy_row = {
        "campaign_id": "old-campaign",
        "advertiser_id": "adv-001",
        "campaign_name": "Old Campaign",
        "status": CampaignStatus.DRAFT.value,
        "total_budget": 100_000.0,
        "currency": "USD",
        "flight_start": (today + timedelta(days=7)).isoformat(),
        "flight_end": (today + timedelta(days=37)).isoformat(),
        "channels": json.dumps([{"channel": "CTV", "budget_pct": 100.0}]),
        # Legacy: TEXT column held a JSON list of strings.
        "target_audience": json.dumps(["seg-1", "seg-2", "seg-3"]),
    }
    brief = pipeline._reconstruct_brief(legacy_row)  # noqa: SLF001
    assert brief.target_audience is not None
    assert isinstance(brief.target_audience, AudiencePlan)
    assert brief.target_audience.primary.identifier == "seg-1"
    assert [e.identifier for e in brief.target_audience.extensions] == [
        "seg-2",
        "seg-3",
    ]


def test_reconstruct_brief_handles_new_dict_text():
    store = _FakeStore()
    pipeline = CampaignPipeline(store=store, orchestrator=None)  # type: ignore[arg-type]
    today = date.today()
    plan_dict = {
        "primary": {
            "type": "standard",
            "identifier": "new-seg",
            "taxonomy": "iab-audience",
            "version": "1.1",
            "source": "explicit",
        },
    }
    new_row = {
        "campaign_id": "new-campaign",
        "advertiser_id": "adv-001",
        "campaign_name": "New Campaign",
        "status": CampaignStatus.DRAFT.value,
        "total_budget": 100_000.0,
        "currency": "USD",
        "flight_start": (today + timedelta(days=7)).isoformat(),
        "flight_end": (today + timedelta(days=37)).isoformat(),
        "channels": json.dumps([{"channel": "CTV", "budget_pct": 100.0}]),
        "target_audience": json.dumps(plan_dict),
    }
    brief = pipeline._reconstruct_brief(new_row)  # noqa: SLF001
    assert brief.target_audience is not None
    assert brief.target_audience.primary.identifier == "new-seg"
    assert brief.target_audience.primary.source == "explicit"


def test_reconstruct_brief_handles_empty_legacy_list():
    """An empty legacy list becomes target_audience=None on reconstruction.

    Different from the ingestion path (which rejects fresh empty lists);
    we don't want to crash on legacy rows that may have been seeded with
    `'[]'` defaults from the SQLite DEFAULT clause.
    """
    store = _FakeStore()
    pipeline = CampaignPipeline(store=store, orchestrator=None)  # type: ignore[arg-type]
    today = date.today()
    row = {
        "campaign_id": "empty-campaign",
        "advertiser_id": "adv-001",
        "campaign_name": "Empty Campaign",
        "status": CampaignStatus.DRAFT.value,
        "total_budget": 100_000.0,
        "currency": "USD",
        "flight_start": (today + timedelta(days=7)).isoformat(),
        "flight_end": (today + timedelta(days=37)).isoformat(),
        "channels": json.dumps([{"channel": "CTV", "budget_pct": 100.0}]),
        "target_audience": json.dumps([]),
    }
    brief = pipeline._reconstruct_brief(row)  # noqa: SLF001
    assert brief.target_audience is None
