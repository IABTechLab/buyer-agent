# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Taxonomy loader for vendored IAB taxonomies.

Reads the TSV files vendored under `data/taxonomies/` and exposes a
typed lookup surface for the Audience Planner. No network access; all
data is local.

The two TSVs have different schemas:

  Audience Taxonomy 1.1  (1 header row + 1558 data rows):
    [unused, "Unique ID", "Parent ID", "Condensed Name (...)",
     "Tier 1", "Tier 2", "Tier 3", "Tier 4", "Tier 5", "Tier 6",
     "*Extension Notes"]

  Content Taxonomy 3.1  (2 header rows + 704 data rows):
    Row 1 (group): "Relational ID System", "", "",
                   "Content Taxonomy v3.1 Tiered Categories", ""...
    Row 2 (cols):  "Unique ID", "Parent", "Name",
                   "Tier 1", "Tier 2", "Tier 3", "Tier 4", ""

Both are normalized to a common internal `TaxonomyEntry` dict shape so
downstream callers (TaxonomyLookupTool, planner heuristics) don't have
to care about the source format.

Lock metadata (versions + sha256) lives in `data/taxonomies/taxonomies.lock.json`
and is read via `taxonomy_lock_hash()` for capability advertisement
(see proposal §5.7 layer 1, bead ar-50cm + ar-XXX seller capability bead).
"""

from __future__ import annotations

import csv
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.audience_plan import AudienceRef


# Resolve the repo-root data dir from this module's location.
# This file lives at: <repo>/src/ad_buyer/data/taxonomy_loader.py
# Taxonomies live at: <repo>/data/taxonomies/
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[3]
_TAXONOMIES_DIR = _REPO_ROOT / "data" / "taxonomies"

_AUDIENCE_TSV = _TAXONOMIES_DIR / "audience-1.1" / "Audience Taxonomy 1.1.tsv"
_CONTENT_TSV = _TAXONOMIES_DIR / "content-3.1" / "Content Taxonomy 3.1.tsv"
_LOCK_FILE = _TAXONOMIES_DIR / "taxonomies.lock.json"


# Module-level caches; protected by a lock so multi-threaded callers
# (CrewAI tools may run concurrently) don't double-load the TSVs.
_audience_cache: dict[str, dict] | None = None
_content_cache: dict[str, dict] | None = None
_lock_cache: dict | None = None
_cache_lock = threading.Lock()


@dataclass(frozen=True)
class ValidationResult:
    """Result of validating an `AudienceRef` against vendored taxonomies."""

    valid: bool
    reason: str
    matched_entry: dict | None = None


def _load_audience_tsv(path: Path) -> dict[str, dict]:
    """Parse the Audience Taxonomy TSV into id-keyed entries.

    Skips the single header row; entries keyed by "Unique ID" string.
    """

    out: dict[str, dict] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        rows = list(reader)
    if not rows:
        return out
    # Header row index 0; data rows start at 1.
    for row in rows[1:]:
        # Pad short rows so indexing is safe.
        cells = row + [""] * (11 - len(row)) if len(row) < 11 else row
        unique_id = cells[1].strip()
        if not unique_id:
            continue
        tiers = [c.strip() for c in cells[4:10] if c.strip()]
        out[unique_id] = {
            "id": unique_id,
            "parent_id": cells[2].strip() or None,
            "name": cells[3].strip(),
            "tiers": tiers,
            "tier_1": cells[4].strip() or None,
            "extension_notes": cells[10].strip() if len(cells) > 10 else "",
            "taxonomy": "iab-audience",
        }
    return out


def _load_content_tsv(path: Path) -> dict[str, dict]:
    """Parse the Content Taxonomy TSV into id-keyed entries.

    Skips the two header rows; entries keyed by "Unique ID" string.
    """

    out: dict[str, dict] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        rows = list(reader)
    if len(rows) < 2:
        return out
    # Header rows are indices 0 and 1; data rows start at 2.
    for row in rows[2:]:
        cells = row + [""] * (8 - len(row)) if len(row) < 8 else row
        unique_id = cells[0].strip()
        if not unique_id:
            continue
        tiers = [c.strip() for c in cells[3:7] if c.strip()]
        out[unique_id] = {
            "id": unique_id,
            "parent_id": cells[1].strip() or None,
            "name": cells[2].strip(),
            "tiers": tiers,
            "tier_1": cells[3].strip() or None,
            "extension_notes": cells[7].strip() if len(cells) > 7 else "",
            "taxonomy": "iab-content",
        }
    return out


def _load_lock() -> dict:
    """Load taxonomies.lock.json once per process."""

    global _lock_cache
    with _cache_lock:
        if _lock_cache is None:
            with _LOCK_FILE.open(encoding="utf-8") as fh:
                _lock_cache = json.load(fh)
        return _lock_cache


def load_audience_taxonomy() -> dict[str, dict]:
    """Return the IAB Audience Taxonomy 1.1 keyed by Unique ID.

    Cached per process. Subsequent calls return the same dict.
    """

    global _audience_cache
    with _cache_lock:
        if _audience_cache is None:
            _audience_cache = _load_audience_tsv(_AUDIENCE_TSV)
        return _audience_cache


def load_content_taxonomy() -> dict[str, dict]:
    """Return the IAB Content Taxonomy 3.1 keyed by Unique ID.

    Cached per process. Subsequent calls return the same dict.
    """

    global _content_cache
    with _cache_lock:
        if _content_cache is None:
            _content_cache = _load_content_tsv(_CONTENT_TSV)
        return _content_cache


def lookup(taxonomy: str, identifier: str, version: str | None = None) -> dict | None:
    """Resolve an identifier within a named taxonomy.

    Args:
        taxonomy: 'iab-audience' | 'iab-content' | 'agentic-audiences'.
        identifier: Unique ID for static taxonomies; URI for agentic.
        version: Optional version pin; mismatches are tolerated but logged
            in the returned entry under '_version_mismatch' for upstream
            callers to surface in degradation logs.

    Returns:
        The taxonomy entry dict, or None when not found. For
        'agentic-audiences', returns a stub entry indicating that
        agentic refs are not validated against a static table.
    """

    if taxonomy == "iab-audience":
        table = load_audience_taxonomy()
        entry = table.get(identifier)
        if entry is None:
            return None
        result = dict(entry)
        if version and version != _load_lock()["audience"]["version"]:
            result["_version_mismatch"] = {
                "requested": version,
                "vendored": _load_lock()["audience"]["version"],
            }
        return result

    if taxonomy == "iab-content":
        table = load_content_taxonomy()
        entry = table.get(identifier)
        if entry is None:
            return None
        result = dict(entry)
        if version and version != _load_lock()["content"]["version"]:
            result["_version_mismatch"] = {
                "requested": version,
                "vendored": _load_lock()["content"]["version"],
            }
        return result

    if taxonomy == "agentic-audiences":
        # The agentic taxonomy isn't a static table -- it's a spec describing
        # how embedding URIs are exchanged. We return a stub indicating the
        # ref must be validated against capability advertisement instead.
        return {
            "id": identifier,
            "taxonomy": "agentic-audiences",
            "validation": "deferred",
            "note": (
                "Agentic refs are not validated against a static table. "
                "Consult capability advertisement (proposal §5.7 layer 1)."
            ),
            "spec_version": _load_lock()["agentic"]["version"],
        }

    return None


def validate_ref(ref: AudienceRef) -> ValidationResult:
    """Confirm an `AudienceRef`'s identifier resolves in its taxonomy.

    For agentic refs, validation is structural only -- the loader cannot
    verify whether the embedding URI dereferences. The downstream UCP
    client handles that.
    """

    expected_taxonomies = {
        "standard": "iab-audience",
        "contextual": "iab-content",
        "agentic": "agentic-audiences",
    }
    expected = expected_taxonomies.get(ref.type)
    if expected is None:
        return ValidationResult(
            valid=False,
            reason=f"unknown ref.type={ref.type!r}",
        )
    if ref.taxonomy != expected:
        return ValidationResult(
            valid=False,
            reason=(
                f"ref.taxonomy={ref.taxonomy!r} does not match "
                f"ref.type={ref.type!r} (expected {expected!r})"
            ),
        )
    entry = lookup(ref.taxonomy, ref.identifier, ref.version)
    if entry is None:
        return ValidationResult(
            valid=False,
            reason=(
                f"identifier {ref.identifier!r} not found in "
                f"{ref.taxonomy} v{ref.version}"
            ),
        )
    if ref.type == "agentic":
        # Agentic loader returns a stub, not a real validation.
        return ValidationResult(
            valid=True,
            reason="agentic ref structurally valid; resolution deferred",
            matched_entry=entry,
        )
    return ValidationResult(
        valid=True,
        reason="ok",
        matched_entry=entry,
    )


def taxonomy_lock_hash(taxonomy_name: str) -> str:
    """Return the sha256 from the lock file for a given taxonomy.

    Args:
        taxonomy_name: 'audience' | 'content' | 'agentic'.

    Returns:
        The sha256 hex digest as recorded in `taxonomies.lock.json`.

    Raises:
        KeyError: when `taxonomy_name` is not present in the lock file.
    """

    lock = _load_lock()
    return lock[taxonomy_name]["sha256"]


def reset_caches() -> None:
    """Clear the per-process caches (test helper).

    Production code should not call this; the caches are immutable from
    the loader's perspective. Tests use it to verify reload behavior.
    """

    global _audience_cache, _content_cache, _lock_cache
    with _cache_lock:
        _audience_cache = None
        _content_cache = None
        _lock_cache = None
