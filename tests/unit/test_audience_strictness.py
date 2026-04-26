# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the AudienceStrictness policy field on CampaignBrief.

bead: ar-fe0h (proposal §5.7)

`audience_strictness` controls how the buyer's pre-flight degradation
logic responds when a seller can't honor part of the AudiencePlan.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from ad_buyer.models.audience_plan import AudienceStrictness
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
        "target_audience": ["3-7"],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_strictness_defaults():
    s = AudienceStrictness()
    assert s.primary == "required"
    assert s.constraints == "preferred"
    assert s.extensions == "optional"
    assert s.agentic == "optional"


def test_brief_has_default_strictness():
    brief = CampaignBrief(**_minimal_brief())
    assert isinstance(brief.audience_strictness, AudienceStrictness)
    assert brief.audience_strictness.primary == "required"
    assert brief.audience_strictness.constraints == "preferred"
    assert brief.audience_strictness.extensions == "optional"
    assert brief.audience_strictness.agentic == "optional"


# ---------------------------------------------------------------------------
# Per-role overrides
# ---------------------------------------------------------------------------


def test_brief_accepts_strictness_override_dict():
    brief = CampaignBrief(
        **_minimal_brief(
            audience_strictness={
                "primary": "required",
                "constraints": "required",
                "extensions": "required",
                "agentic": "required",
            }
        )
    )
    s = brief.audience_strictness
    assert s.primary == "required"
    assert s.constraints == "required"
    assert s.extensions == "required"
    assert s.agentic == "required"


def test_brief_accepts_strictness_partial_override():
    brief = CampaignBrief(
        **_minimal_brief(
            audience_strictness={"agentic": "required"}
        )
    )
    s = brief.audience_strictness
    # Overridden field
    assert s.agentic == "required"
    # Other fields keep defaults
    assert s.primary == "required"
    assert s.constraints == "preferred"
    assert s.extensions == "optional"


def test_strictness_rejects_unknown_level():
    with pytest.raises(ValidationError):
        AudienceStrictness(primary="mandatory")  # type: ignore[arg-type]


def test_strictness_rejects_unknown_role():
    # Pydantic rejects extra fields by default for the strictness model.
    s = AudienceStrictness(**{"primary": "required"})  # known field works
    assert s.primary == "required"


def test_brief_strictness_serializes_round_trip():
    brief = CampaignBrief(
        **_minimal_brief(
            audience_strictness={
                "primary": "preferred",
                "constraints": "optional",
                "extensions": "optional",
                "agentic": "required",
            }
        )
    )
    payload = brief.model_dump(mode="json")
    assert payload["audience_strictness"]["primary"] == "preferred"
    assert payload["audience_strictness"]["agentic"] == "required"
    # Re-parse
    rehydrated = CampaignBrief(**payload)
    assert rehydrated.audience_strictness.agentic == "required"
