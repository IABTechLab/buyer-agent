# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the typed AudienceRef + AudiencePlan models.

bead: ar-50cm
"""

import pytest
from pydantic import ValidationError

from ad_buyer.models.audience_plan import (
    AudiencePlan,
    AudienceRef,
    ComplianceContext,
)


# ---------------------------------------------------------------------------
# AudienceRef construction + validators
# ---------------------------------------------------------------------------


def test_standard_ref_minimal_fields() -> None:
    ref = AudienceRef(
        type="standard",
        identifier="3-7",
        taxonomy="iab-audience",
        version="1.1",
        source="explicit",
    )
    assert ref.type == "standard"
    assert ref.confidence is None
    assert ref.compliance_context is None


def test_contextual_ref_with_resolved_confidence() -> None:
    ref = AudienceRef(
        type="contextual",
        identifier="150",
        taxonomy="iab-content",
        version="3.1",
        source="resolved",
        confidence=0.92,
    )
    assert ref.source == "resolved"
    assert ref.confidence == 0.92


def test_agentic_ref_requires_compliance_context() -> None:
    """Per proposal §5.2, agentic refs MUST carry compliance_context."""

    with pytest.raises(ValidationError) as excinfo:
        AudienceRef(
            type="agentic",
            identifier="emb://example.com/aud/x",
            taxonomy="agentic-audiences",
            version="draft-2026-01",
            source="explicit",
        )
    assert "compliance_context" in str(excinfo.value)


def test_agentic_ref_with_compliance_context_ok() -> None:
    ref = AudienceRef(
        type="agentic",
        identifier="emb://example.com/aud/x",
        taxonomy="agentic-audiences",
        version="draft-2026-01",
        source="explicit",
        compliance_context=ComplianceContext(
            jurisdiction="US",
            consent_framework="IAB-TCFv2",
            consent_string_ref="tcf:CPxxxx",
        ),
    )
    assert ref.compliance_context is not None
    assert ref.compliance_context.jurisdiction == "US"


def test_explicit_ref_rejects_confidence() -> None:
    """Explicit refs should not carry a confidence score."""

    with pytest.raises(ValidationError) as excinfo:
        AudienceRef(
            type="standard",
            identifier="3-7",
            taxonomy="iab-audience",
            version="1.1",
            source="explicit",
            confidence=0.99,
        )
    assert "confidence" in str(excinfo.value)


def test_invalid_type_rejected() -> None:
    with pytest.raises(ValidationError):
        AudienceRef(
            type="bogus",  # type: ignore[arg-type]
            identifier="x",
            taxonomy="iab-audience",
            version="1.1",
            source="explicit",
        )


def test_confidence_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        AudienceRef(
            type="contextual",
            identifier="150",
            taxonomy="iab-content",
            version="3.1",
            source="resolved",
            confidence=1.5,
        )


# ---------------------------------------------------------------------------
# AudiencePlan: id computation + stability
# ---------------------------------------------------------------------------


def _std_ref(identifier: str = "3-7") -> AudienceRef:
    return AudienceRef(
        type="standard",
        identifier=identifier,
        taxonomy="iab-audience",
        version="1.1",
        source="explicit",
    )


def _ctx_ref(identifier: str = "150") -> AudienceRef:
    return AudienceRef(
        type="contextual",
        identifier=identifier,
        taxonomy="iab-content",
        version="3.1",
        source="resolved",
        confidence=0.9,
    )


def test_plan_minimal_primary_only() -> None:
    plan = AudiencePlan(primary=_std_ref())
    assert plan.schema_version == "1"
    assert plan.audience_plan_id.startswith("sha256:")
    assert len(plan.audience_plan_id) == len("sha256:") + 64
    assert plan.constraints == []
    assert plan.extensions == []
    assert plan.exclusions == []


def test_plan_id_is_deterministic() -> None:
    """Two plans with identical content produce identical hashes."""

    plan_a = AudiencePlan(
        primary=_std_ref(),
        constraints=[_ctx_ref()],
    )
    plan_b = AudiencePlan(
        primary=_std_ref(),
        constraints=[_ctx_ref()],
    )
    assert plan_a.audience_plan_id == plan_b.audience_plan_id


def test_plan_id_stable_across_field_construction_order() -> None:
    """Pydantic field-construction order does not affect the hash.

    The canonicalizer sorts dict keys before hashing, so two plans built
    with the same content but different keyword-arg orders must hash to
    the same id.
    """

    plan_a = AudiencePlan(
        primary=_std_ref(),
        constraints=[_ctx_ref("150")],
        extensions=[],
        exclusions=[],
    )
    plan_b = AudiencePlan(
        exclusions=[],
        extensions=[],
        constraints=[_ctx_ref("150")],
        primary=_std_ref(),
    )
    assert plan_a.audience_plan_id == plan_b.audience_plan_id


def test_plan_id_changes_with_content() -> None:
    """Changing any role's content changes the hash."""

    base = AudiencePlan(primary=_std_ref("3-7"))
    different = AudiencePlan(primary=_std_ref("3-8"))
    assert base.audience_plan_id != different.audience_plan_id


def test_plan_id_changes_with_constraint_addition() -> None:
    base = AudiencePlan(primary=_std_ref())
    with_constraint = AudiencePlan(
        primary=_std_ref(),
        constraints=[_ctx_ref()],
    )
    assert base.audience_plan_id != with_constraint.audience_plan_id


def test_plan_id_unaffected_by_rationale() -> None:
    """Rationale is narrative; it doesn't change WHO is being targeted."""

    plan_a = AudiencePlan(primary=_std_ref(), rationale="version one")
    plan_b = AudiencePlan(primary=_std_ref(), rationale="totally different prose")
    assert plan_a.audience_plan_id == plan_b.audience_plan_id


def test_plan_id_unaffected_by_schema_version() -> None:
    """Schema version is a meta concern, not part of plan content identity."""

    plan_a = AudiencePlan(primary=_std_ref(), schema_version="1")
    plan_b = AudiencePlan(primary=_std_ref(), schema_version="2")
    assert plan_a.audience_plan_id == plan_b.audience_plan_id


def test_plan_id_sensitive_to_role_membership() -> None:
    """Same ref in different roles yields different plans (intersect vs union)."""

    primary = _std_ref()
    other = _ctx_ref()

    as_constraint = AudiencePlan(primary=primary, constraints=[other])
    as_extension = AudiencePlan(primary=primary, extensions=[other])
    assert as_constraint.audience_plan_id != as_extension.audience_plan_id


def test_explicit_id_is_honored() -> None:
    """Reconstructing a plan from a frozen snapshot preserves its hash."""

    frozen_id = "sha256:" + "0" * 64
    plan = AudiencePlan(
        primary=_std_ref(),
        audience_plan_id=frozen_id,
    )
    assert plan.audience_plan_id == frozen_id


def test_compute_id_matches_auto_populated_id() -> None:
    plan = AudiencePlan(primary=_std_ref(), constraints=[_ctx_ref()])
    assert plan.audience_plan_id == plan.compute_id()


def test_full_plan_serializable_round_trip() -> None:
    """A plan with all four roles round-trips through model_dump/parse."""

    primary = _std_ref()
    plan = AudiencePlan(
        primary=primary,
        constraints=[_ctx_ref()],
        extensions=[
            AudienceRef(
                type="agentic",
                identifier="emb://buyer.example/aud/q1",
                taxonomy="agentic-audiences",
                version="draft-2026-01",
                source="explicit",
                compliance_context=ComplianceContext(
                    jurisdiction="US",
                    consent_framework="IAB-TCFv2",
                ),
            ),
        ],
        exclusions=[_std_ref("3-12")],
        rationale="primary auto intenders, narrowed by automotive content",
    )
    dumped = plan.model_dump()
    restored = AudiencePlan(**dumped)
    assert restored.audience_plan_id == plan.audience_plan_id
    assert len(restored.exclusions) == 1
    assert restored.extensions[0].compliance_context is not None
