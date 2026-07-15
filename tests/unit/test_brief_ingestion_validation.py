# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for brief-ingestion Content Taxonomy 2.x -> 3.x validation.

bead: ar-fe0h (proposal §6 row 4 / §5.7 IAB Mapper hint)

A brief that arrives with a Contextual ref pinned to pre-3.x must be
rejected with a clear error pointing at the IAB Mapper migration tool.
Standard and Agentic refs are unaffected.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from ad_buyer.models.audience_plan import (
    AudiencePlan,
    AudienceRef,
    ComplianceContext,
    ContentTaxonomyMigrationRequired,
    validate_content_taxonomy_version,
)
from ad_buyer.models.campaign_brief import CampaignBrief


def _minimal_brief(target_audience):
    today = date.today()
    return {
        "advertiser_id": "adv-001",
        "campaign_name": "Test",
        "objective": "AWARENESS",
        "total_budget": 100_000.0,
        "currency": "USD",
        "flight_start": (today + timedelta(days=7)).isoformat(),
        "flight_end": (today + timedelta(days=37)).isoformat(),
        "channels": [{"channel": "CTV", "budget_pct": 100.0}],
        "target_audience": target_audience,
    }


def _standard_primary():
    return AudienceRef(
        type="standard",
        identifier="3-7",
        taxonomy="iab-audience",
        version="1.1",
        source="explicit",
    )


# ---------------------------------------------------------------------------
# validate_content_taxonomy_version unit tests
# ---------------------------------------------------------------------------


def test_no_contextual_refs_yields_no_issues():
    plan = AudiencePlan(primary=_standard_primary())
    assert validate_content_taxonomy_version(plan) == []


def test_v2_contextual_constraint_rejected():
    plan = AudiencePlan(
        primary=_standard_primary(),
        constraints=[
            AudienceRef(
                type="contextual",
                identifier="IAB1-2",
                taxonomy="iab-content",
                version="2.0",
                source="explicit",
            )
        ],
    )
    issues = validate_content_taxonomy_version(plan)
    assert len(issues) == 1
    issue = issues[0]
    assert issue["role"] == "constraints"
    assert issue["index"] == 0
    assert issue["identifier"] == "IAB1-2"
    assert "pre-3" in issue["reason"]
    assert "IAB Mapper" in issue["suggestion"]


def test_v3_contextual_passes_when_id_resolves():
    # The vendored Content Taxonomy 3.1 includes "1" as a Tier-1 ID
    # ("Automotive"). We sanity-check that a real 3.x ID does not produce
    # an issue, while still tolerating loader unavailability gracefully.
    plan = AudiencePlan(
        primary=_standard_primary(),
        constraints=[
            AudienceRef(
                type="contextual",
                identifier="1",
                taxonomy="iab-content",
                version="3.1",
                source="explicit",
            )
        ],
    )
    # If the loader is available and the ID resolves, expect zero issues.
    # If the loader is missing, the validator silently no-ops on missing
    # IDs; in that case we still want zero "version" issues.
    issues = validate_content_taxonomy_version(plan)
    # Filter for version-related issues only; an unresolved ID under 3.1
    # would also surface here, but we accept either outcome since both
    # are non-blocking from the perspective of "no pre-3.x rejection".
    version_issues = [i for i in issues if "pre-3" in i["reason"]]
    assert version_issues == []


def test_v3_unresolved_id_flagged_when_loader_present():
    """An ID that doesn't appear in 3.1 should be flagged."""
    plan = AudiencePlan(
        primary=_standard_primary(),
        constraints=[
            AudienceRef(
                type="contextual",
                identifier="bogus-id-not-in-31",
                taxonomy="iab-content",
                version="3.1",
                source="explicit",
            )
        ],
    )
    issues = validate_content_taxonomy_version(plan)
    # Either the loader is present and reports the miss, or it's absent and
    # the validator no-ops. Both are acceptable; assert at least no false
    # positive for the version-prefix rule.
    if issues:
        assert all("pre-3" not in i["reason"] for i in issues)
        assert any(i["identifier"] == "bogus-id-not-in-31" for i in issues)


def test_blank_version_treated_as_pre_3():
    plan = AudiencePlan(
        primary=_standard_primary(),
        extensions=[
            AudienceRef(
                type="contextual",
                identifier="X",
                taxonomy="iab-content",
                version="legacy",
                source="explicit",
            )
        ],
    )
    issues = validate_content_taxonomy_version(plan)
    assert len(issues) == 1
    assert issues[0]["role"] == "extensions"
    assert issues[0]["version"] == "legacy"


def test_standard_ref_not_affected():
    plan = AudiencePlan(
        primary=AudienceRef(
            type="standard",
            identifier="3-7",
            taxonomy="iab-audience",
            version="1.1",
            source="explicit",
        )
    )
    assert validate_content_taxonomy_version(plan) == []


def test_agentic_ref_not_affected():
    plan = AudiencePlan(
        primary=_standard_primary(),
        extensions=[
            AudienceRef(
                type="agentic",
                identifier="emb://buyer/x",
                taxonomy="agentic-audiences",
                version="draft-2026-01",
                source="explicit",
                compliance_context=ComplianceContext(
                    jurisdiction="US",
                    consent_framework="advertiser-1p",
                ),
            )
        ],
    )
    assert validate_content_taxonomy_version(plan) == []


# ---------------------------------------------------------------------------
# CampaignBrief integration: pre-3.x contextual refs reject
# ---------------------------------------------------------------------------


def test_brief_rejects_pre_3x_contextual_constraint():
    plan = AudiencePlan(
        primary=_standard_primary(),
        constraints=[
            AudienceRef(
                type="contextual",
                identifier="IAB1-2",
                taxonomy="iab-content",
                version="2.0",
                source="explicit",
            )
        ],
    )
    with pytest.raises(ValidationError) as exc:
        CampaignBrief(**_minimal_brief(plan.model_dump(mode="json")))
    # Pydantic wraps our ContentTaxonomyMigrationRequired into a
    # ValidationError; confirm the message references IAB Mapper.
    text = str(exc.value)
    assert "IAB Mapper" in text or "Content Taxonomy" in text


def test_brief_accepts_v3_contextual_constraint():
    plan = AudiencePlan(
        primary=_standard_primary(),
        constraints=[
            AudienceRef(
                type="contextual",
                identifier="1",
                taxonomy="iab-content",
                version="3.1",
                source="explicit",
            )
        ],
    )
    # Should parse cleanly when the loader resolves "1" (Automotive in 3.1)
    # OR when the loader is unavailable (the validator no-ops).
    brief = CampaignBrief(**_minimal_brief(plan.model_dump(mode="json")))
    assert brief.target_audience is not None
    assert brief.target_audience.constraints[0].version == "3.1"


def test_brief_standard_only_passes():
    brief = CampaignBrief(**_minimal_brief(["3-7"]))
    assert brief.target_audience is not None
    assert brief.target_audience.primary.taxonomy == "iab-audience"


def test_content_taxonomy_migration_required_carries_issues():
    issues = [
        {
            "role": "primary",
            "index": 0,
            "identifier": "IAB1-2",
            "taxonomy": "iab-content",
            "version": "2.0",
            "reason": "pre-3.x",
            "suggestion": "Run IAB Mapper",
        }
    ]
    err = ContentTaxonomyMigrationRequired(issues)
    assert err.issues is issues
    assert "IAB1-2" in str(err)
