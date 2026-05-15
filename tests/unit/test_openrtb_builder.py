# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the OpenRTB carrier mapping builder.

Exercises the standard / contextual / agentic mapping path documented in
``docs/api/audience_plan_wire_format.md`` §9 and proposal §5.1 Step 4.

Bead: ar-8vzg.
"""

from __future__ import annotations

import logging

from ad_buyer.clients.openrtb_builder import (
    AGENTIC_USER_EXT_KEY,
    CONTENT_TAXONOMY_31_CATTAX,
    IAB_AUDIENCE_TAXONOMY_DATA_NAME,
    build_openrtb_audience_targeting,
)
from ad_buyer.models.audience_plan import (
    AudiencePlan,
    AudienceRef,
    ComplianceContext,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _standard(identifier: str, *, version: str = "1.1") -> AudienceRef:
    return AudienceRef(
        type="standard",
        identifier=identifier,
        taxonomy="iab-audience",
        version=version,
        source="explicit",
    )


def _contextual(identifier: str, *, version: str = "3.1") -> AudienceRef:
    return AudienceRef(
        type="contextual",
        identifier=identifier,
        taxonomy="iab-content",
        version=version,
        source="explicit",
    )


def _agentic(identifier: str, *, version: str = "draft-2026-01") -> AudienceRef:
    return AudienceRef(
        type="agentic",
        identifier=identifier,
        taxonomy="agentic-audiences",
        version=version,
        source="explicit",
        compliance_context=ComplianceContext(
            jurisdiction="US",
            consent_framework="IAB-TCFv2",
            consent_string_ref="tcf:CPxxxx",
        ),
    )


# ---------------------------------------------------------------------------
# 1. Standard primary -> user.data[].segment[].id
# ---------------------------------------------------------------------------


def test_standard_primary_emits_user_data_segment() -> None:
    plan = AudiencePlan(primary=_standard("3-7"))
    fragment = build_openrtb_audience_targeting(plan)

    assert "user" in fragment
    assert "site" not in fragment
    data = fragment["user"]["data"]
    assert isinstance(data, list) and len(data) == 1
    entry = data[0]
    assert entry["name"] == IAB_AUDIENCE_TAXONOMY_DATA_NAME
    assert entry["ext"] == {"taxonomy_version": "1.1"}
    assert entry["segment"] == [{"id": "3-7"}]


def test_standard_multiple_refs_collapse_into_single_data_entry() -> None:
    plan = AudiencePlan(
        primary=_standard("3-7"),
        constraints=[_standard("4-2")],
        extensions=[_standard("5-1")],
    )
    fragment = build_openrtb_audience_targeting(plan)

    data = fragment["user"]["data"]
    assert len(data) == 1
    seg_ids = [s["id"] for s in data[0]["segment"]]
    assert seg_ids == ["3-7", "4-2", "5-1"]
    assert data[0]["ext"]["taxonomy_version"] == "1.1"


# ---------------------------------------------------------------------------
# 2. Contextual constraint -> site.cat + cattax=7
# ---------------------------------------------------------------------------


def test_contextual_constraint_emits_site_cat_and_cattax_7() -> None:
    plan = AudiencePlan(
        primary=_standard("3-7"),
        constraints=[_contextual("IAB1-2")],
    )
    fragment = build_openrtb_audience_targeting(plan)

    assert "site" in fragment
    assert fragment["site"]["cat"] == ["IAB1-2"]
    assert fragment["site"]["cattax"] == CONTENT_TAXONOMY_31_CATTAX
    assert fragment["site"]["cattax"] == 7  # the explicit OpenRTB enum value


def test_contextual_only_plan_emits_only_site() -> None:
    plan = AudiencePlan(primary=_contextual("IAB1-2"))
    fragment = build_openrtb_audience_targeting(plan)
    assert "user" not in fragment
    assert fragment["site"]["cat"] == ["IAB1-2"]
    assert fragment["site"]["cattax"] == 7


# ---------------------------------------------------------------------------
# 3. Agentic extension with feature flag enabled
# ---------------------------------------------------------------------------


def test_agentic_extension_emitted_when_flag_enabled() -> None:
    plan = AudiencePlan(
        primary=_standard("3-7"),
        extensions=[_agentic("emb://buyer.example.com/auto-converters-q1")],
    )
    fragment = build_openrtb_audience_targeting(plan, enable_agentic_ext=True)

    user_ext = fragment["user"]["ext"]
    assert AGENTIC_USER_EXT_KEY in user_ext
    refs = user_ext[AGENTIC_USER_EXT_KEY]["refs"]
    assert len(refs) == 1
    entry = refs[0]
    assert entry["identifier"] == "emb://buyer.example.com/auto-converters-q1"
    assert entry["version"] == "draft-2026-01"
    assert entry["source"] == "explicit"
    cc = entry["compliance_context"]
    assert cc["jurisdiction"] == "US"
    assert cc["consent_framework"] == "IAB-TCFv2"
    assert cc["consent_string_ref"] == "tcf:CPxxxx"


# ---------------------------------------------------------------------------
# 4. Agentic extension with feature flag disabled (default)
# ---------------------------------------------------------------------------


def test_agentic_extension_dropped_when_flag_disabled(
    caplog: object,
) -> None:
    plan = AudiencePlan(
        primary=_standard("3-7"),
        extensions=[_agentic("emb://buyer.example.com/auto-converters-q1")],
    )
    # caplog is the pytest fixture; declare the type loosely to keep the
    # signature simple. mypy/strict typecheckers may want LogCaptureFixture.
    caplog.set_level(logging.WARNING)  # type: ignore[attr-defined]

    fragment = build_openrtb_audience_targeting(plan)  # default: flag off

    # Standard primary still emitted.
    assert fragment["user"]["data"][0]["segment"] == [{"id": "3-7"}]
    # Agentic extension NOT emitted.
    assert "ext" not in fragment["user"]
    # Warning logged citing the flag.
    warning_messages = [r.message for r in caplog.records  # type: ignore[attr-defined]
                        if r.levelno >= logging.WARNING]
    assert any(
        "enable_agentic_openrtb_ext" in m for m in warning_messages
    ), f"expected flag-disabled warning, got: {warning_messages}"


def test_agentic_only_plan_with_flag_off_returns_empty_user_block() -> None:
    plan = AudiencePlan(primary=_agentic("emb://buyer.example.com/q1"))
    fragment = build_openrtb_audience_targeting(plan)  # flag off
    # Agentic-only with flag off -> nothing to emit.
    assert fragment == {}


# ---------------------------------------------------------------------------
# 5. Multi-role plan: all three paths emitted simultaneously
# ---------------------------------------------------------------------------


def test_multi_role_plan_emits_all_three_paths() -> None:
    plan = AudiencePlan(
        primary=_standard("3-7"),
        constraints=[_contextual("IAB1-2")],
        extensions=[_agentic("emb://buyer.example.com/lookalikes")],
    )
    fragment = build_openrtb_audience_targeting(plan, enable_agentic_ext=True)

    # Standard.
    assert fragment["user"]["data"][0]["segment"] == [{"id": "3-7"}]
    # Contextual.
    assert fragment["site"]["cat"] == ["IAB1-2"]
    assert fragment["site"]["cattax"] == 7
    # Agentic.
    assert (
        fragment["user"]["ext"][AGENTIC_USER_EXT_KEY]["refs"][0]["identifier"]
        == "emb://buyer.example.com/lookalikes"
    )


# ---------------------------------------------------------------------------
# 6. Exclusions: documented chosen behavior
# ---------------------------------------------------------------------------


def test_standard_exclusions_emit_segment_with_exclude_ext_flag() -> None:
    """Documented behavior: standard exclusions emit segments with
    ``ext.exclude=true``. OpenRTB has no first-class exclusion slot;
    sellers MAY honor the namespaced ext flag.
    """
    plan = AudiencePlan(
        primary=_standard("3-7"),
        exclusions=[_standard("3-12")],
    )
    fragment = build_openrtb_audience_targeting(plan)

    segments = fragment["user"]["data"][0]["segment"]
    ids_to_segments = {s["id"]: s for s in segments}
    assert "3-7" in ids_to_segments
    assert "3-12" in ids_to_segments
    # Primary has no exclude flag.
    assert "ext" not in ids_to_segments["3-7"]
    # Exclusion carries ext.exclude=true.
    assert ids_to_segments["3-12"]["ext"] == {"exclude": True}


def test_contextual_exclusions_dropped_with_warning(
    caplog: object,
) -> None:
    """Documented behavior: contextual exclusions are dropped because
    ``site.cat`` has no exclusion semantics. A structured warning is logged.
    """
    plan = AudiencePlan(
        primary=_contextual("IAB1-2"),
        exclusions=[_contextual("IAB99-99")],
    )
    caplog.set_level(logging.WARNING)  # type: ignore[attr-defined]

    fragment = build_openrtb_audience_targeting(plan)

    # Only the positive contextual ref appears.
    assert fragment["site"]["cat"] == ["IAB1-2"]
    warning_messages = [r.message for r in caplog.records  # type: ignore[attr-defined]
                        if r.levelno >= logging.WARNING]
    assert any(
        "site.cat" in m or "exclusion" in m.lower() for m in warning_messages
    ), f"expected dropped-contextual-exclusion warning, got: {warning_messages}"


# ---------------------------------------------------------------------------
# 7. Empty / minimal plans
# ---------------------------------------------------------------------------


def test_minimal_plan_with_only_primary() -> None:
    plan = AudiencePlan(primary=_standard("3-7"))
    fragment = build_openrtb_audience_targeting(plan)
    assert fragment == {
        "user": {
            "data": [
                {
                    "name": IAB_AUDIENCE_TAXONOMY_DATA_NAME,
                    "ext": {"taxonomy_version": "1.1"},
                    "segment": [{"id": "3-7"}],
                }
            ]
        }
    }


def test_resolved_ref_carries_confidence_in_segment_value() -> None:
    """Resolved refs carry confidence; the builder threads it onto
    ``segment.value`` per OpenRTB convention for fuzzy-matched segments."""
    plan = AudiencePlan(
        primary=AudienceRef(
            type="standard",
            identifier="3-7",
            taxonomy="iab-audience",
            version="1.1",
            source="resolved",
            confidence=0.83,
        )
    )
    fragment = build_openrtb_audience_targeting(plan)
    seg = fragment["user"]["data"][0]["segment"][0]
    assert seg["id"] == "3-7"
    assert seg["value"] == "0.83"
