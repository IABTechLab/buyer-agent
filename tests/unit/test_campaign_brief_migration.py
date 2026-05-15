# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the legacy `list[str]` -> AudiencePlan migration shim.

bead: ar-fe0h (proposal §6 row 4)

The compat shim lives on `CampaignBrief` as a `model_validator(mode='before')`
and on `coerce_audience_field`, which is also used by the `_reconstruct_brief`
load-side shim to transparently upgrade legacy SQLite rows.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from ad_buyer.models.audience_plan import (
    AudiencePlan,
    AudienceRef,
    coerce_audience_field,
    is_legacy_list_shape,
    migrate_legacy_audience_list,
)
from ad_buyer.models.campaign_brief import CampaignBrief


def _minimal_brief(**overrides):
    today = date.today()
    base = {
        "advertiser_id": "adv-001",
        "campaign_name": "Test",
        "objective": "AWARENESS",
        "total_budget": 100_000.0,
        "currency": "USD",
        "flight_start": (today + timedelta(days=7)).isoformat(),
        "flight_end": (today + timedelta(days=37)).isoformat(),
        "channels": [{"channel": "CTV", "budget_pct": 100.0}],
        "target_audience": ["3-7", "3-8"],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# is_legacy_list_shape
# ---------------------------------------------------------------------------


def test_legacy_shape_detects_list_of_strings():
    assert is_legacy_list_shape(["3-7", "3-8"]) is True


def test_legacy_shape_detects_empty_list():
    assert is_legacy_list_shape([]) is True


def test_legacy_shape_rejects_dict():
    assert is_legacy_list_shape({"primary": {}}) is False


def test_legacy_shape_rejects_list_of_dicts():
    assert is_legacy_list_shape([{"id": "3-7"}]) is False


def test_legacy_shape_rejects_none():
    assert is_legacy_list_shape(None) is False


# ---------------------------------------------------------------------------
# migrate_legacy_audience_list — locked policy
# ---------------------------------------------------------------------------


def test_migrate_locked_policy_first_to_primary_rest_to_extensions():
    plan = migrate_legacy_audience_list(["3-7", "3-8", "3-9"])
    assert isinstance(plan, AudiencePlan)
    # Primary
    assert plan.primary.identifier == "3-7"
    assert plan.primary.type == "standard"
    assert plan.primary.taxonomy == "iab-audience"
    assert plan.primary.version == "1.1"
    assert plan.primary.source == "inferred"
    assert plan.primary.confidence is None
    assert plan.primary.compliance_context is None
    # Extensions preserve order
    assert [e.identifier for e in plan.extensions] == ["3-8", "3-9"]
    for ext in plan.extensions:
        assert ext.type == "standard"
        assert ext.taxonomy == "iab-audience"
        assert ext.version == "1.1"
        assert ext.source == "inferred"
    # constraints / exclusions empty by policy
    assert plan.constraints == []
    assert plan.exclusions == []
    # Rationale records the migration
    assert "legacy" in plan.rationale.lower()
    # Auto-computed plan id present
    assert plan.audience_plan_id.startswith("sha256:")


def test_migrate_single_item_has_no_extensions():
    plan = migrate_legacy_audience_list(["only-one"])
    assert plan.primary.identifier == "only-one"
    assert plan.extensions == []


def test_migrate_empty_list_raises():
    with pytest.raises(ValueError) as exc:
        migrate_legacy_audience_list([])
    assert "empty" in str(exc.value).lower()


def test_migrate_emits_structured_log(caplog):
    caplog.set_level(logging.INFO, logger="ad_buyer.audience.migration")
    plan = migrate_legacy_audience_list(
        ["3-7", "3-8"], source_context="test_emits"
    )
    records = [
        r for r in caplog.records if r.name == "ad_buyer.audience.migration"
    ]
    assert len(records) == 1
    payload = getattr(records[0], "audience_migration", None)
    assert payload is not None
    assert payload["source_context"] == "test_emits"
    assert payload["legacy_input"] == ["3-7", "3-8"]
    assert payload["audience_plan_id"] == plan.audience_plan_id
    assert payload["primary_identifier"] == "3-7"
    assert payload["extension_count"] == 1


# ---------------------------------------------------------------------------
# coerce_audience_field passthrough
# ---------------------------------------------------------------------------


def test_coerce_passthrough_for_none():
    assert coerce_audience_field(None) is None


def test_coerce_passthrough_for_audience_plan_instance():
    ref = AudienceRef(
        type="standard",
        identifier="3-7",
        taxonomy="iab-audience",
        version="1.1",
        source="explicit",
    )
    plan = AudiencePlan(primary=ref)
    out = coerce_audience_field(plan)
    assert out is plan


def test_coerce_passthrough_for_dict():
    payload = {
        "primary": {
            "type": "standard",
            "identifier": "3-7",
            "taxonomy": "iab-audience",
            "version": "1.1",
            "source": "explicit",
        }
    }
    out = coerce_audience_field(payload)
    # dicts pass through; pydantic validates them later
    assert out is payload


def test_coerce_migrates_list_of_strings():
    out = coerce_audience_field(["a", "b"])
    assert isinstance(out, AudiencePlan)
    assert out.primary.identifier == "a"
    assert out.extensions[0].identifier == "b"


# ---------------------------------------------------------------------------
# CampaignBrief integration — both new and legacy shapes validate
# ---------------------------------------------------------------------------


def test_brief_accepts_legacy_list_shape():
    brief = CampaignBrief(**_minimal_brief())
    assert isinstance(brief.target_audience, AudiencePlan)
    assert brief.target_audience.primary.identifier == "3-7"
    assert brief.target_audience.extensions[0].identifier == "3-8"


def test_brief_accepts_new_audience_plan_dict():
    plan_dict = {
        "primary": {
            "type": "standard",
            "identifier": "3-7",
            "taxonomy": "iab-audience",
            "version": "1.1",
            "source": "explicit",
        },
        "constraints": [],
        "extensions": [],
        "exclusions": [],
        "rationale": "Hand-authored plan",
    }
    brief = CampaignBrief(**_minimal_brief(target_audience=plan_dict))
    assert isinstance(brief.target_audience, AudiencePlan)
    assert brief.target_audience.primary.source == "explicit"
    assert brief.target_audience.rationale == "Hand-authored plan"


def test_brief_accepts_audience_plan_instance():
    plan = AudiencePlan(
        primary=AudienceRef(
            type="standard",
            identifier="3-7",
            taxonomy="iab-audience",
            version="1.1",
            source="explicit",
        )
    )
    brief = CampaignBrief(**_minimal_brief(target_audience=plan))
    assert brief.target_audience is plan or (
        isinstance(brief.target_audience, AudiencePlan)
        and brief.target_audience.audience_plan_id == plan.audience_plan_id
    )


def test_brief_omitting_target_audience_yields_none():
    data = _minimal_brief()
    del data["target_audience"]
    brief = CampaignBrief(**data)
    assert brief.target_audience is None


def test_brief_logs_legacy_conversion(caplog):
    caplog.set_level(logging.INFO, logger="ad_buyer.audience.migration")
    CampaignBrief(**_minimal_brief())
    records = [
        r for r in caplog.records if r.name == "ad_buyer.audience.migration"
    ]
    assert len(records) >= 1
    payload = getattr(records[0], "audience_migration", None)
    assert payload is not None
    assert payload["source_context"] == "campaign_brief.target_audience"
    assert payload["legacy_input"] == ["3-7", "3-8"]


def test_brief_legacy_empty_list_rejected():
    data = _minimal_brief(target_audience=[])
    with pytest.raises(ValidationError):
        CampaignBrief(**data)


# ---------------------------------------------------------------------------
# JSON round-trip: dump and reload yields equivalent plan
# ---------------------------------------------------------------------------


def test_brief_roundtrips_through_json():
    brief = CampaignBrief(**_minimal_brief())
    payload = brief.model_dump(mode="json")
    # Replace target_audience with the dict form (what we'd persist).
    serialized = json.dumps(payload["target_audience"])
    decoded = json.loads(serialized)
    rehydrated = AudiencePlan(**decoded)
    assert rehydrated.primary.identifier == "3-7"
    assert [e.identifier for e in rehydrated.extensions] == ["3-8"]
