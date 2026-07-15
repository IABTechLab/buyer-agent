# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the TaxonomyLookupTool CrewAI tool.

bead: ar-50cm
"""

from ad_buyer.tools.audience.taxonomy_lookup import (
    TaxonomyLookupInput,
    TaxonomyLookupTool,
)


def test_tool_metadata() -> None:
    tool = TaxonomyLookupTool()
    assert tool.name == "taxonomy_lookup"
    assert "vendored" in tool.description.lower()
    assert tool.args_schema is TaxonomyLookupInput


def test_tool_lookup_audience_hit() -> None:
    tool = TaxonomyLookupTool()
    out = tool._run(taxonomy="iab-audience", identifier="1")
    assert out.startswith("FOUND")
    assert "Demographic" in out
    assert "iab-audience" in out


def test_tool_lookup_content_hit() -> None:
    tool = TaxonomyLookupTool()
    out = tool._run(taxonomy="iab-content", identifier="150")
    assert out.startswith("FOUND")
    assert "Attractions" in out
    assert "iab-content" in out


def test_tool_lookup_miss_returns_structured_not_found() -> None:
    tool = TaxonomyLookupTool()
    out = tool._run(taxonomy="iab-audience", identifier="99999999")
    assert out.startswith("NOT_FOUND")
    assert "iab-audience" in out
    assert "99999999" in out


def test_tool_lookup_agentic_returns_deferred_stub() -> None:
    tool = TaxonomyLookupTool()
    out = tool._run(
        taxonomy="agentic-audiences",
        identifier="emb://buyer.example/aud/q1",
    )
    assert "AGENTIC" in out
    assert "deferred" in out.lower()
    assert "draft-2026-01" in out


def test_tool_unknown_taxonomy_reports_valid_taxonomies() -> None:
    tool = TaxonomyLookupTool()
    out = tool._run(taxonomy="bogus-taxonomy", identifier="1")
    assert out.startswith("NOT_FOUND")
    assert "unknown taxonomy" in out
    assert "iab-audience" in out
    assert "iab-content" in out
    assert "agentic-audiences" in out


def test_input_schema_validates_required_fields() -> None:
    """args_schema is a Pydantic model -- both fields required."""

    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TaxonomyLookupInput()  # type: ignore[call-arg]

    # Both fields supplied -> ok
    inp = TaxonomyLookupInput(taxonomy="iab-audience", identifier="1")
    assert inp.taxonomy == "iab-audience"
    assert inp.identifier == "1"
