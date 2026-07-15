"""ar-ei0s: reject_global_agentic brief-ingestion validator tests."""

import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

import pytest

from ad_buyer.models.audience_plan import (
    AudiencePlan,
    AudienceRef,
    ComplianceContext,
    GlobalAgenticUnsupported,
    validate_no_global_agentic,
)


def _agentic_ref(jurisdiction: str = "US") -> AudienceRef:
    return AudienceRef(
        type="agentic",
        identifier="emb://test",
        taxonomy="agentic-audiences",
        version="draft-2026-01",
        source="explicit",
        confidence=None,
        compliance_context=ComplianceContext(
            jurisdiction=jurisdiction,
            consent_framework="IAB-TCFv2",
        ),
    )


def _standard_primary() -> AudienceRef:
    return AudienceRef(
        type="standard",
        identifier="3-7",
        taxonomy="iab-audience",
        version="1.1",
        source="explicit",
        confidence=None,
    )


class TestValidator:
    def test_no_agentic_refs_returns_no_issues(self):
        plan = AudiencePlan(
            schema_version="1",
            primary=_standard_primary(),
            constraints=[],
            extensions=[],
            exclusions=[],
            rationale="standard only",
        )
        assert validate_no_global_agentic(plan) == []

    def test_us_agentic_ref_passes(self):
        plan = AudiencePlan(
            schema_version="1",
            primary=_standard_primary(),
            constraints=[],
            extensions=[_agentic_ref("US")],
            exclusions=[],
            rationale="US agentic",
        )
        assert validate_no_global_agentic(plan) == []

    def test_global_agentic_ref_in_extensions_flagged(self):
        plan = AudiencePlan(
            schema_version="1",
            primary=_standard_primary(),
            constraints=[],
            extensions=[_agentic_ref("GLOBAL")],
            exclusions=[],
            rationale="global agentic",
        )
        issues = validate_no_global_agentic(plan)
        assert len(issues) == 1
        assert issues[0]["role"] == "extensions"
        assert issues[0]["jurisdiction"] == "GLOBAL"
        assert "GLOBAL" in issues[0]["reason"]

    def test_multiple_global_refs_all_flagged(self):
        plan = AudiencePlan(
            schema_version="1",
            primary=_standard_primary(),
            constraints=[_agentic_ref("GLOBAL")],
            extensions=[_agentic_ref("GLOBAL")],
            exclusions=[],
            rationale="multi-global",
        )
        issues = validate_no_global_agentic(plan)
        assert len(issues) == 2

    def test_global_standard_ref_not_flagged(self):
        # Standard refs CAN carry GLOBAL — they don't carry per-region
        # consent semantics.
        global_std = AudienceRef(
            type="standard",
            identifier="3-7",
            taxonomy="iab-audience",
            version="1.1",
            source="explicit",
            confidence=None,
            compliance_context=ComplianceContext(
                jurisdiction="GLOBAL",
                consent_framework="IAB-TCFv2",
            ),
        )
        plan = AudiencePlan(
            schema_version="1",
            primary=global_std,
            constraints=[],
            extensions=[],
            exclusions=[],
            rationale="global standard",
        )
        assert validate_no_global_agentic(plan) == []


class TestExceptionShape:
    def test_exception_carries_issues(self):
        issues = [{"role": "extensions", "index": 0, "identifier": "emb://x"}]
        exc = GlobalAgenticUnsupported(issues)
        assert exc.issues == issues
        assert "GLOBAL" in str(exc) or "Global" in str(exc).lower()


class TestBriefIngestion:
    """The validator wires into CampaignBrief.parse_target_audience."""

    def test_brief_with_global_agentic_rejected(self):
        from ad_buyer.models.campaign_brief import (
            CampaignBrief,
            ChannelAllocation,
        )

        plan_with_global_agentic = AudiencePlan(
            schema_version="1",
            primary=_standard_primary(),
            constraints=[],
            extensions=[_agentic_ref("GLOBAL")],
            exclusions=[],
            rationale="global agentic brief",
        )

        with pytest.raises((GlobalAgenticUnsupported, ValueError)) as excinfo:
            CampaignBrief(
                advertiser_id="adv_1",
                advertiser_name="Test",
                campaign_name="Test campaign",
                industry="auto",
                objective="awareness",
                total_budget=10000.0,
                currency="USD",
                flight_start="2026-05-01",
                flight_end="2026-06-30",
                target_audience=plan_with_global_agentic,
                channels=[ChannelAllocation(channel="DISPLAY", budget_pct=100)],
            )
        # Pydantic wraps custom validators in ValidationError, but the inner
        # exception type matches.
        assert "GLOBAL" in str(excinfo.value) or "global" in str(excinfo.value).lower()
