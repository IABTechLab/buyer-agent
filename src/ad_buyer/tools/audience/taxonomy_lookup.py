# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Taxonomy Lookup Tool - Resolve identifiers against vendored IAB taxonomies.

Pure local lookup; no network access. Used by the Audience Planner agent
during the "classify intent" phase of its reasoning loop (proposal §5.5
step 1) to map raw `target_audience` strings into typed `AudienceRef`s.
"""

from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...data.taxonomy_loader import lookup


class TaxonomyLookupInput(BaseModel):
    """Input schema for the taxonomy lookup tool."""

    taxonomy: str = Field(
        description=(
            "Taxonomy to query: 'iab-audience' (Audience Taxonomy 1.1), "
            "'iab-content' (Content Taxonomy 3.1), or 'agentic-audiences' "
            "(IAB Agentic Audiences spec)."
        )
    )
    identifier: str = Field(
        description=(
            "The unique ID to resolve. For static taxonomies this is the "
            "Unique ID column value (e.g. '3-7' or '150'); for agentic this "
            "is an embedding URI."
        )
    )


class TaxonomyLookupTool(BaseTool):
    """Resolve a taxonomy identifier against vendored IAB data.

    Returns a structured row with name, parent, and tier path when found,
    or a structured "not found" response when the identifier doesn't
    resolve. Used by the Audience Planner to verify that human-supplied
    or LLM-suggested IDs actually exist in the named taxonomy before
    they're packed into an `AudienceRef`.
    """

    name: str = "taxonomy_lookup"
    description: str = (
        "Resolve an IAB taxonomy ID against vendored taxonomies. "
        "Inputs: taxonomy ('iab-audience' | 'iab-content' | 'agentic-audiences') "
        "and identifier (Unique ID or embedding URI). Returns the matching "
        "entry's name, parent, and tier path, or a not-found response. "
        "No network access -- all data is local."
    )
    args_schema: Type[BaseModel] = TaxonomyLookupInput

    def _run(self, taxonomy: str, identifier: str) -> str:
        """Execute the lookup and format the result for the agent."""

        entry = lookup(taxonomy, identifier)
        if entry is None:
            return self._format_not_found(taxonomy, identifier)
        return self._format_entry(entry)

    @staticmethod
    def _format_entry(entry: dict) -> str:
        """Format a found entry as agent-readable text."""

        # Agentic entries are stubs (validation deferred); render them
        # differently so the agent doesn't treat them as static rows.
        if entry.get("validation") == "deferred":
            return (
                f"AGENTIC REF (validation deferred)\n"
                f"  identifier: {entry.get('id')}\n"
                f"  taxonomy: {entry.get('taxonomy')}\n"
                f"  spec_version: {entry.get('spec_version')}\n"
                f"  note: {entry.get('note')}"
            )

        tier_path = " | ".join(entry.get("tiers") or []) or "(no tier path)"
        lines = [
            "FOUND",
            f"  id: {entry.get('id')}",
            f"  name: {entry.get('name') or '(unnamed)'}",
            f"  parent_id: {entry.get('parent_id') or '(none)'}",
            f"  tier_1: {entry.get('tier_1') or '(none)'}",
            f"  taxonomy: {entry.get('taxonomy')}",
            f"  tier_path: {tier_path}",
        ]
        if entry.get("extension_notes"):
            lines.append(f"  extension_notes: {entry['extension_notes']}")
        if entry.get("_version_mismatch"):
            mismatch = entry["_version_mismatch"]
            lines.append(
                f"  WARNING: requested v{mismatch['requested']} but "
                f"vendored v{mismatch['vendored']}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_not_found(taxonomy: str, identifier: str) -> str:
        """Format a not-found response as agent-readable text."""

        valid_taxonomies = {"iab-audience", "iab-content", "agentic-audiences"}
        if taxonomy not in valid_taxonomies:
            return (
                f"NOT_FOUND\n"
                f"  reason: unknown taxonomy {taxonomy!r}\n"
                f"  valid_taxonomies: {sorted(valid_taxonomies)}"
            )
        return (
            f"NOT_FOUND\n"
            f"  taxonomy: {taxonomy}\n"
            f"  identifier: {identifier}\n"
            f"  reason: identifier does not resolve in this taxonomy"
        )
