# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the vendored IAB taxonomy loader.

bead: ar-50cm
"""

import pytest

from ad_buyer.data.taxonomy_loader import (
    ValidationResult,
    load_audience_taxonomy,
    load_content_taxonomy,
    lookup,
    reset_caches,
    taxonomy_lock_hash,
    validate_ref,
)
from ad_buyer.models.audience_plan import AudienceRef, ComplianceContext


# ---------------------------------------------------------------------------
# Loader basics
# ---------------------------------------------------------------------------


def test_audience_taxonomy_loads_expected_row_count() -> None:
    """Audience Taxonomy 1.1 ships ~1558 data rows (1559 total lines).

    The vendored TSV has one header row, so the data dict counts the
    rows minus that header.
    """

    table = load_audience_taxonomy()
    # Header + 1558 data rows = 1559 lines in the TSV file.
    assert len(table) == 1558


def test_content_taxonomy_loads_expected_row_count() -> None:
    """Content Taxonomy 3.1 ships 704 data rows (706 total lines).

    The vendored TSV has two header rows (group + column names), so the
    data dict counts rows minus those two headers.
    """

    table = load_content_taxonomy()
    assert len(table) == 704


def test_audience_table_is_cached() -> None:
    a = load_audience_taxonomy()
    b = load_audience_taxonomy()
    assert a is b  # same dict instance


def test_content_table_is_cached() -> None:
    a = load_content_taxonomy()
    b = load_content_taxonomy()
    assert a is b


def test_reset_caches_forces_reload() -> None:
    a = load_audience_taxonomy()
    reset_caches()
    b = load_audience_taxonomy()
    assert a is not b
    assert len(a) == len(b)


# ---------------------------------------------------------------------------
# Lookup hits and misses
# ---------------------------------------------------------------------------


def test_audience_lookup_hit_returns_entry() -> None:
    entry = lookup("iab-audience", "1")
    assert entry is not None
    assert entry["id"] == "1"
    # Per the TSV: row 2 is "1 / Demographic / Demographic"
    assert entry["name"] == "Demographic"
    assert entry["tier_1"] == "Demographic"


def test_audience_lookup_with_parent() -> None:
    """Row 3: id=2, parent_id=1, Tier 1=Demographic, Tier 2=Age Range."""

    entry = lookup("iab-audience", "2")
    assert entry is not None
    assert entry["parent_id"] == "1"
    assert entry["tier_1"] == "Demographic"
    assert "Age Range" in entry["tiers"]


def test_audience_lookup_miss_returns_none() -> None:
    assert lookup("iab-audience", "99999999") is None


def test_content_lookup_hit_returns_entry() -> None:
    """Row 3 of Content TSV: id=150, name=Attractions, Tier 1=Attractions."""

    entry = lookup("iab-content", "150")
    assert entry is not None
    assert entry["id"] == "150"
    assert entry["name"] == "Attractions"
    assert entry["tier_1"] == "Attractions"


def test_content_lookup_with_parent() -> None:
    """Row 4: id=151, parent=150, name=Amusement and Theme Parks."""

    entry = lookup("iab-content", "151")
    assert entry is not None
    assert entry["parent_id"] == "150"
    assert entry["name"] == "Amusement and Theme Parks"


def test_content_lookup_miss_returns_none() -> None:
    assert lookup("iab-content", "99999999") is None


def test_unknown_taxonomy_returns_none() -> None:
    assert lookup("not-a-taxonomy", "anything") is None


def test_agentic_lookup_returns_stub() -> None:
    """The agentic taxonomy is not a static table; the loader returns a
    stub so callers know the ref must be resolved against capability
    advertisement instead."""

    entry = lookup("agentic-audiences", "emb://example.com/aud/foo")
    assert entry is not None
    assert entry["validation"] == "deferred"
    assert entry["taxonomy"] == "agentic-audiences"
    assert entry["spec_version"] == "draft-2026-01"


def test_lookup_version_mismatch_annotates_entry() -> None:
    """Looking up with an unexpected version flags but doesn't fail."""

    entry = lookup("iab-audience", "1", version="0.9")
    assert entry is not None
    assert "_version_mismatch" in entry
    assert entry["_version_mismatch"]["requested"] == "0.9"
    assert entry["_version_mismatch"]["vendored"] == "1.1"


# ---------------------------------------------------------------------------
# Lock-file hash exposure
# ---------------------------------------------------------------------------


def test_taxonomy_lock_hash_audience() -> None:
    h = taxonomy_lock_hash("audience")
    # Pinned in data/taxonomies/taxonomies.lock.json.
    assert h == "0216547402f3dc028f5ec1bb78c648eed68a81ea3b3b94862e6f6caa9db3ad3b"


def test_taxonomy_lock_hash_content() -> None:
    h = taxonomy_lock_hash("content")
    assert h == "7212cdc496ba347a03e703b1932bdcdd4fd29089b058f4edeb4d3da1f1222ea7"


def test_taxonomy_lock_hash_agentic() -> None:
    h = taxonomy_lock_hash("agentic")
    assert h == "1b3266f92f478b738da701d8923980b7067f70a59d18cfa76c54ecfb5d6301b9"


def test_taxonomy_lock_hash_unknown_raises() -> None:
    with pytest.raises(KeyError):
        taxonomy_lock_hash("not-a-taxonomy")


# ---------------------------------------------------------------------------
# AudienceRef validation against the vendored taxonomies
# ---------------------------------------------------------------------------


def test_validate_ref_standard_hit() -> None:
    ref = AudienceRef(
        type="standard",
        identifier="1",
        taxonomy="iab-audience",
        version="1.1",
        source="explicit",
    )
    result = validate_ref(ref)
    assert isinstance(result, ValidationResult)
    assert result.valid is True
    assert result.matched_entry is not None


def test_validate_ref_standard_miss() -> None:
    ref = AudienceRef(
        type="standard",
        identifier="99999999",
        taxonomy="iab-audience",
        version="1.1",
        source="explicit",
    )
    result = validate_ref(ref)
    assert result.valid is False
    assert "not found" in result.reason


def test_validate_ref_taxonomy_mismatches_type() -> None:
    """A standard ref pointing at iab-content is invalid by construction."""

    ref = AudienceRef(
        type="standard",
        identifier="150",
        taxonomy="iab-content",  # wrong for type=standard
        version="3.1",
        source="explicit",
    )
    result = validate_ref(ref)
    assert result.valid is False
    assert "does not match" in result.reason


def test_validate_ref_agentic_returns_deferred() -> None:
    """Agentic refs validate structurally; resolution is deferred."""

    ref = AudienceRef(
        type="agentic",
        identifier="emb://buyer.example/aud/q1-converters",
        taxonomy="agentic-audiences",
        version="draft-2026-01",
        source="explicit",
        compliance_context=ComplianceContext(
            jurisdiction="US",
            consent_framework="IAB-TCFv2",
        ),
    )
    result = validate_ref(ref)
    assert result.valid is True
    assert "deferred" in result.reason
