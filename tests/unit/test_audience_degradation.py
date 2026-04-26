# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for `degrade_plan_for_seller` (proposal §5.7 layer 2).

Covers the ten scenarios called out in the bead scope:
  1. agentic ref dropped when `agentic.supported=False`
  2. extensions dropped when `supports_extensions=False`
  3. constraints dropped when `supports_constraints=False`
  4. exclusions dropped when `supports_exclusions=False`
  5. contextual version mismatch -> drop with "needs IAB Mapper"
  6. standard version mismatch -> drop with "version mismatch"
  7. multi-degradation: agentic + extensions + version mismatch all dropped
  8. all-supported plan -> returns unchanged, empty log
  9. degraded plan still has valid primary -> returns plan
  10. plan has no primary after degradation -> raises CannotFulfillPlan

Plus targeted tests for `synthesize_capabilities_from_unsupported`, which
underpins the retry-on-rejection path in §12 part B.

Bead: ar-0w48.
"""

from __future__ import annotations

import pytest

from ad_buyer.models.audience_plan import (
    AudiencePlan,
    AudienceRef,
    ComplianceContext,
)
from ad_buyer.orchestration.audience_degradation import (
    CannotFulfillPlan,
    DegradationLogEntry,
    SellerAudienceCapabilities,
    _AgenticFlag,
    _MaxRefsPerRole,
    degrade_plan_for_seller,
    synthesize_capabilities_from_unsupported,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _standard(identifier: str = "3-7", version: str = "1.1") -> AudienceRef:
    return AudienceRef(
        type="standard",
        identifier=identifier,
        taxonomy="iab-audience",
        version=version,
        source="explicit",
    )


def _contextual(
    identifier: str = "IAB1-2", version: str = "3.1"
) -> AudienceRef:
    return AudienceRef(
        type="contextual",
        identifier=identifier,
        taxonomy="iab-content",
        version=version,
        source="explicit",
    )


def _agentic(identifier: str = "emb://buyer.example.com/x") -> AudienceRef:
    return AudienceRef(
        type="agentic",
        identifier=identifier,
        taxonomy="agentic-audiences",
        version="draft-2026-01",
        source="explicit",
        compliance_context=ComplianceContext(
            jurisdiction="US",
            consent_framework="IAB-TCFv2",
        ),
    )


def _full_caps(
    *,
    agentic_supported: bool = True,
    supports_constraints: bool = True,
    supports_extensions: bool = True,
    supports_exclusions: bool = True,
    standard_versions: list[str] | None = None,
    contextual_versions: list[str] | None = None,
    max_constraints: int = 5,
    max_extensions: int = 5,
    max_exclusions: int = 5,
) -> SellerAudienceCapabilities:
    """Build a maximally-capable seller, then turn off individual axes."""

    return SellerAudienceCapabilities(
        schema_version="1",
        standard_taxonomy_versions=standard_versions or ["1.1"],
        contextual_taxonomy_versions=contextual_versions or ["3.1"],
        agentic=_AgenticFlag(supported=agentic_supported),
        supports_constraints=supports_constraints,
        supports_extensions=supports_extensions,
        supports_exclusions=supports_exclusions,
        max_refs_per_role=_MaxRefsPerRole(
            primary=1,
            constraints=max_constraints,
            extensions=max_extensions,
            exclusions=max_exclusions,
        ),
    )


def _paths(log) -> list[str]:
    return [entry.path for entry in log]


def _reasons(log) -> list[str]:
    return [entry.reason for entry in log]


# ---------------------------------------------------------------------------
# Scenario 1: agentic dropped when agentic.supported=False
# ---------------------------------------------------------------------------


class TestAgenticDropping:
    def test_agentic_extension_dropped(self):
        plan = AudiencePlan(
            primary=_standard(),
            extensions=[_agentic()],
        )
        caps = _full_caps(agentic_supported=False)

        degraded, log = degrade_plan_for_seller(plan, caps)

        assert degraded.extensions == []
        assert len(log) == 1
        assert log[0].path == "extensions[0]"
        assert "agentic refs not supported" in log[0].reason
        assert log[0].action == "dropped"
        assert log[0].original_ref is not None
        assert log[0].original_ref["type"] == "agentic"

    def test_agentic_constraint_dropped(self):
        plan = AudiencePlan(
            primary=_standard(),
            constraints=[_agentic()],
        )
        caps = _full_caps(agentic_supported=False)

        degraded, log = degrade_plan_for_seller(plan, caps)

        assert degraded.constraints == []
        assert log[0].path == "constraints[0]"
        assert "agentic" in log[0].reason

    def test_agentic_primary_raises_cannot_fulfill(self):
        # Per proposal: dropping the primary is fatal.
        plan = AudiencePlan(primary=_agentic())
        caps = _full_caps(agentic_supported=False)

        with pytest.raises(CannotFulfillPlan) as exc_info:
            degrade_plan_for_seller(plan, caps)

        assert "Primary ref dropped" in str(exc_info.value)
        # The log on the exception still records what happened.
        assert exc_info.value.log
        assert exc_info.value.log[0].path == "primary"


# ---------------------------------------------------------------------------
# Scenarios 2/3/4: role gates
# ---------------------------------------------------------------------------


class TestRoleGates:
    def test_extensions_dropped_when_unsupported(self):
        plan = AudiencePlan(
            primary=_standard(),
            extensions=[_standard("3-1"), _contextual("IAB1-3")],
        )
        caps = _full_caps(supports_extensions=False)

        degraded, log = degrade_plan_for_seller(plan, caps)

        assert degraded.extensions == []
        # One log entry per extension dropped, preserving order.
        assert _paths(log) == ["extensions[0]", "extensions[1]"]
        assert all("extensions not supported" in r for r in _reasons(log))

    def test_constraints_dropped_when_unsupported(self):
        plan = AudiencePlan(
            primary=_standard(),
            constraints=[_contextual("IAB1-2"), _contextual("IAB1-3")],
        )
        caps = _full_caps(supports_constraints=False)

        degraded, log = degrade_plan_for_seller(plan, caps)

        assert degraded.constraints == []
        assert _paths(log) == ["constraints[0]", "constraints[1]"]
        assert all("constraints not supported" in r for r in _reasons(log))

    def test_exclusions_dropped_when_unsupported(self):
        plan = AudiencePlan(
            primary=_standard(),
            exclusions=[_standard("3-12")],
        )
        caps = _full_caps(supports_exclusions=False)

        degraded, log = degrade_plan_for_seller(plan, caps)

        assert degraded.exclusions == []
        assert _paths(log) == ["exclusions[0]"]
        assert "exclusions not supported" in log[0].reason


# ---------------------------------------------------------------------------
# Scenarios 5/6: taxonomy version mismatches
# ---------------------------------------------------------------------------


class TestVersionMismatches:
    def test_contextual_version_mismatch_logs_iab_mapper_hint(self):
        plan = AudiencePlan(
            primary=_standard(),
            constraints=[_contextual("IAB1-2", version="2.0")],
        )
        # Seller only speaks 3.1, not 2.0.
        caps = _full_caps(contextual_versions=["3.1"])

        degraded, log = degrade_plan_for_seller(plan, caps)

        assert degraded.constraints == []
        assert log[0].path == "constraints[0]"
        assert "needs IAB Mapper" in log[0].reason
        assert "'2.0'" in log[0].reason
        assert log[0].action == "dropped"

    def test_standard_version_mismatch_logs_version_mismatch(self):
        plan = AudiencePlan(
            primary=_standard(version="1.1"),
            extensions=[_standard("3-1", version="2.0")],
        )
        # Seller only speaks 1.1, not 2.0.
        caps = _full_caps(standard_versions=["1.1"])

        degraded, log = degrade_plan_for_seller(plan, caps)

        assert degraded.extensions == []
        assert log[0].path == "extensions[0]"
        assert "version mismatch" in log[0].reason
        assert "'2.0'" in log[0].reason
        assert log[0].action == "dropped"


# ---------------------------------------------------------------------------
# Scenario 7: multi-degradation
# ---------------------------------------------------------------------------


class TestMultiDegradation:
    def test_three_axes_at_once(self):
        plan = AudiencePlan(
            primary=_standard(),
            constraints=[_contextual("IAB1-2", version="2.0")],
            extensions=[
                _agentic(),
                _standard("3-1"),
            ],
        )
        # Seller: no agentic, no extensions, only Content Tax 3.1 (so 2.0 fails).
        caps = _full_caps(
            agentic_supported=False,
            supports_extensions=False,
            contextual_versions=["3.1"],
        )

        degraded, log = degrade_plan_for_seller(plan, caps)

        # Constraint with bad version -> dropped.
        assert degraded.constraints == []
        # Extensions wholesale dropped (role gate fires before per-ref).
        assert degraded.extensions == []
        # Primary preserved.
        assert degraded.primary.identifier == "3-7"
        # At least 3 entries: 1 constraint version mismatch + 2 extension drops.
        assert len(log) >= 3
        # Verify each axis represented.
        joined = "\n".join(_reasons(log))
        assert "needs IAB Mapper" in joined
        assert "extensions not supported" in joined


# ---------------------------------------------------------------------------
# Scenarios 8/9: happy path
# ---------------------------------------------------------------------------


class TestNoDegradation:
    def test_all_supported_plan_returns_unchanged(self):
        plan = AudiencePlan(
            primary=_standard(),
            constraints=[_contextual("IAB1-2")],
            extensions=[_agentic()],
            exclusions=[_standard("3-12")],
        )
        caps = _full_caps()

        degraded, log = degrade_plan_for_seller(plan, caps)

        assert log == []
        assert degraded.primary.identifier == plan.primary.identifier
        assert len(degraded.constraints) == 1
        assert len(degraded.extensions) == 1
        assert len(degraded.exclusions) == 1

    def test_degraded_plan_recomputes_id_when_content_changes(self):
        plan = AudiencePlan(
            primary=_standard(),
            extensions=[_agentic(), _standard("3-1")],
        )
        # Drop only agentic, keep the standard extension.
        caps = _full_caps(agentic_supported=False)

        degraded, log = degrade_plan_for_seller(plan, caps)

        assert len(degraded.extensions) == 1
        assert degraded.extensions[0].type == "standard"
        # id must be recomputed because content changed.
        assert degraded.audience_plan_id != plan.audience_plan_id
        assert degraded.audience_plan_id.startswith("sha256:")
        # Log captures the agentic drop.
        assert len(log) == 1


class TestPrimaryStillValid:
    def test_primary_kept_when_seller_supports_it(self):
        plan = AudiencePlan(
            primary=_contextual("IAB1-2"),
            extensions=[_agentic()],
        )
        caps = _full_caps(agentic_supported=False)

        degraded, log = degrade_plan_for_seller(plan, caps)

        assert degraded.primary.identifier == "IAB1-2"
        assert degraded.primary.type == "contextual"
        # Only the agentic extension was dropped.
        assert len(log) == 1


# ---------------------------------------------------------------------------
# Scenario 10: primary lost -> CannotFulfillPlan
# ---------------------------------------------------------------------------


class TestPrimaryLost:
    def test_no_primary_after_degradation_raises(self):
        plan = AudiencePlan(
            primary=_contextual("IAB1-2", version="2.0"),
            extensions=[_standard("3-1")],
        )
        # Seller can only speak Content 3.1 -- primary 2.0 is dropped.
        caps = _full_caps(contextual_versions=["3.1"])

        with pytest.raises(CannotFulfillPlan) as exc_info:
            degrade_plan_for_seller(plan, caps)

        assert exc_info.value.log
        assert exc_info.value.log[0].path == "primary"
        assert "needs IAB Mapper" in exc_info.value.log[0].reason


# ---------------------------------------------------------------------------
# Cardinality cap tests
# ---------------------------------------------------------------------------


class TestCardinalityCaps:
    def test_constraints_trimmed_to_max(self):
        plan = AudiencePlan(
            primary=_standard(),
            constraints=[
                _contextual(f"IAB1-{i}") for i in range(1, 6)  # 5 constraints
            ],
        )
        # Seller accepts only 2 constraints.
        caps = _full_caps(max_constraints=2)

        degraded, log = degrade_plan_for_seller(plan, caps)

        assert len(degraded.constraints) == 2
        # Three excess refs were dropped; their indices match positions in
        # the original list (2, 3, 4).
        excess_paths = _paths(log)
        assert "constraints[2]" in excess_paths
        assert "constraints[3]" in excess_paths
        assert "constraints[4]" in excess_paths
        for entry in log:
            assert "max_refs_per_role.constraints=2" in entry.reason


# ---------------------------------------------------------------------------
# Original plan is not mutated
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_original_plan_unchanged(self):
        plan = AudiencePlan(
            primary=_standard(),
            extensions=[_agentic()],
        )
        original_id = plan.audience_plan_id
        original_ext_count = len(plan.extensions)
        caps = _full_caps(agentic_supported=False)

        degraded, _ = degrade_plan_for_seller(plan, caps)

        # The buyer's original plan object stays put (audit trail).
        assert plan.audience_plan_id == original_id
        assert len(plan.extensions) == original_ext_count
        # The degraded plan is a different object with different content.
        assert degraded is not plan
        assert degraded.audience_plan_id != original_id


# ---------------------------------------------------------------------------
# DegradationLogEntry shape sanity
# ---------------------------------------------------------------------------


class TestDegradationLogEntry:
    def test_entry_serializes_to_dict(self):
        entry = DegradationLogEntry(
            path="extensions[0]",
            reason="agentic refs not supported by seller",
            original_ref={"type": "agentic", "identifier": "emb://x"},
            action="dropped",
        )
        dumped = entry.model_dump()
        assert dumped["path"] == "extensions[0]"
        assert dumped["original_ref"]["type"] == "agentic"
        assert dumped["action"] == "dropped"


# ---------------------------------------------------------------------------
# synthesize_capabilities_from_unsupported (drives the retry path)
# ---------------------------------------------------------------------------


class TestSynthesizeCapabilities:
    def test_role_not_supported_flips_role_gate(self):
        unsupported = [
            {
                "path": "extensions[0]",
                "reason": "extensions not supported by this seller",
            }
        ]
        caps = synthesize_capabilities_from_unsupported(unsupported)
        assert caps.supports_extensions is False
        # Other gates remain at their default (constraints supported).
        assert caps.supports_constraints is True

    def test_agentic_rejection_flips_agentic_flag(self):
        unsupported = [
            {
                "path": "extensions[0].taxonomy",
                "reason": "agentic refs not supported by this seller",
            }
        ]
        caps = synthesize_capabilities_from_unsupported(unsupported)
        assert caps.agentic.supported is False

    def test_contextual_version_rejection_clears_version_list(self):
        unsupported = [
            {
                "path": "primary.taxonomy",
                "reason": "contextual taxonomy version '2.0' not supported",
            }
        ]
        caps = synthesize_capabilities_from_unsupported(unsupported)
        assert caps.contextual_taxonomy_versions == []

    def test_standard_version_rejection_clears_version_list(self):
        unsupported = [
            {
                "path": "primary.taxonomy",
                "reason": "standard taxonomy version '2.0' not supported",
            }
        ]
        caps = synthesize_capabilities_from_unsupported(unsupported)
        assert caps.standard_taxonomy_versions == []

    def test_unrecognized_reason_left_alone(self):
        unsupported = [
            {"path": "primary.taxonomy", "reason": "blargh"},
        ]
        caps = synthesize_capabilities_from_unsupported(unsupported)
        # Defaults preserved.
        assert caps.supports_constraints is True
        assert caps.standard_taxonomy_versions == ["1.1"]

    def test_base_caps_respected(self):
        base = SellerAudienceCapabilities(
            standard_taxonomy_versions=["1.1", "1.2"],
            supports_extensions=True,
        )
        unsupported = [
            {
                "path": "extensions[0]",
                "reason": "extensions not supported by this seller",
            }
        ]
        caps = synthesize_capabilities_from_unsupported(unsupported, base=base)
        assert caps.supports_extensions is False  # downgrade applied
        # Other base settings preserved.
        assert caps.standard_taxonomy_versions == ["1.1", "1.2"]
