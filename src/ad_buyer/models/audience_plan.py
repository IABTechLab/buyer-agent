# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Typed audience reference and plan models for the buyer's Audience Planner.

Implements the composable overlay model defined in
`docs/proposals/AUDIENCE_PLANNER_3TYPE_EXTENSION_2026-04-25.md` §5.2.

A campaign carries one primary audience plus zero or more constraint,
extension, or exclusion audiences. Each is an `AudienceRef` carrying its
type (standard / contextual / agentic), taxonomy, version, and identifier.

This module is additive: it does not replace the legacy `AudiencePlan`
in `models/ucp.py`. Wiring the new shape through the pipeline is a
follow-up bead (see proposal §6 row 4+).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# Type aliases for readability and to keep Literal definitions in one place.
AudienceType = Literal["standard", "contextual", "agentic"]
AudienceSource = Literal["explicit", "resolved", "inferred"]
StrictnessLevel = Literal["required", "preferred", "optional"]

# Migration logger -- emits a structured INFO record every time a legacy
# `list[str]` audience field is rewritten to the new AudiencePlan shape.
# Consumed by the audit-trail surface (proposal §13a) once that lands.
_MIGRATION_LOGGER = logging.getLogger("ad_buyer.audience.migration")


class ComplianceContext(BaseModel):
    """Consent regime accompanying an audience reference.

    Embeddings minted under different consent frameworks are not
    interchangeable -- the regime is part of the reference's identity.
    Required for `type=agentic` refs; optional for standard/contextual.
    """

    jurisdiction: str = Field(
        ...,
        description="Jurisdiction code, e.g. 'US', 'EU', 'GLOBAL'",
    )
    consent_framework: str = Field(
        ...,
        description="Consent framework: 'IAB-TCFv2', 'GPP', 'advertiser-1p', 'none'",
    )
    consent_string_ref: str | None = Field(
        default=None,
        description="Opaque pointer to the consent string (not the raw string)",
    )
    attestation: str | None = Field(
        default=None,
        description="Hash or signature carrying any required attestation",
    )
    embedding_provenance: Literal[
        "local_buyer", "advertiser_supplied", "hosted_external", "mock"
    ] | None = Field(
        default=None,
        description=(
            "Provenance of the embedding bytes (E2-7 Gap 6). Populated by "
            "UCPClient.create_query_embedding_with_provenance per the locked "
            "EMBEDDING_MODE strategy in docs/decisions/EMBEDDING_STRATEGY_2026-04-25.md."
        ),
    )

    model_config = {"populate_by_name": True}


class AudienceRef(BaseModel):
    """A single audience reference within an `AudiencePlan`.

    The `type` field discriminates the meaning of `identifier`:
    - standard: IAB Audience Taxonomy ID (e.g. "3-7")
    - contextual: IAB Content Taxonomy ID (e.g. "IAB1-2")
    - agentic: embedding URI (e.g. "emb://buyer.example.com/audiences/x")
    """

    type: AudienceType = Field(
        ...,
        description="Audience type: 'standard', 'contextual', or 'agentic'",
    )
    identifier: str = Field(
        ...,
        description="ID for standard/contextual; URI for agentic",
    )
    taxonomy: str = Field(
        ...,
        description="'iab-audience' | 'iab-content' | 'agentic-audiences'",
    )
    version: str = Field(
        ...,
        description="Taxonomy version, e.g. '1.1', '3.1', 'draft-2026-01'",
    )
    source: AudienceSource = Field(
        ...,
        description="Provenance: 'explicit', 'resolved', or 'inferred'",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Match confidence; set when source is resolved/inferred",
    )
    compliance_context: ComplianceContext | None = Field(
        default=None,
        description="Consent context; required when type='agentic'",
    )

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _validate_compliance_for_agentic(self) -> AudienceRef:
        """Agentic refs MUST carry a compliance_context.

        Standard/contextual refs may omit it (consent is usually
        attached at the campaign level for those types).
        """

        if self.type == "agentic" and self.compliance_context is None:
            raise ValueError(
                "AudienceRef.compliance_context is required when type='agentic'"
            )
        return self

    @model_validator(mode="after")
    def _validate_confidence_provenance(self) -> AudienceRef:
        """Explicit refs should not carry a confidence score.

        confidence is meaningful only for 'resolved' / 'inferred' refs.
        """

        if self.source == "explicit" and self.confidence is not None:
            raise ValueError(
                "AudienceRef.confidence must be None when source='explicit'"
            )
        return self


def _canonicalize(obj: Any) -> Any:
    """Recursively sort dict keys for stable hashing.

    Lists keep their order (the order of refs within a role is meaningful;
    the planner's choice of order in `constraints` is part of its rationale).
    Dicts get keys sorted so internal field ordering does not affect the hash.
    """

    if isinstance(obj, dict):
        return {k: _canonicalize(obj[k]) for k in sorted(obj.keys())}
    if isinstance(obj, list):
        return [_canonicalize(x) for x in obj]
    return obj


class AudiencePlan(BaseModel):
    """Composable audience plan emitted by the Audience Planner agent.

    Carries one primary audience plus any number of constraint, extension,
    and exclusion audiences. The `audience_plan_id` is a content hash that
    both buyer and seller can recompute to verify they're looking at the
    same plan (see proposal §5.1, Step 2).

    Note: This model is additive alongside `models/ucp.AudiencePlan` -- the
    legacy plan carries free-text demographics and embedding state; this
    one carries typed taxonomy refs. Subsequent beads wire this new shape
    through `CampaignPlan` / `InventoryRequirements` / `DealBookingRequest`.
    """

    schema_version: str = Field(
        default="1",
        description="Schema version; bumped on breaking changes",
    )
    audience_plan_id: str = Field(
        default="",
        description="sha256 hash of canonicalized plan content; computed by compute_id()",
    )
    primary: AudienceRef = Field(
        ...,
        description="The primary audience for the campaign",
    )
    constraints: list[AudienceRef] = Field(
        default_factory=list,
        description="Refs that intersect with primary (precision)",
    )
    extensions: list[AudienceRef] = Field(
        default_factory=list,
        description="Refs that union with primary (reach)",
    )
    exclusions: list[AudienceRef] = Field(
        default_factory=list,
        description="Refs subtracted from the assembled set (negative audiences)",
    )
    rationale: str = Field(
        default="",
        description="Human-readable explanation including any degradation log",
    )

    model_config = {"populate_by_name": True}

    def _content_for_hash(self) -> dict[str, Any]:
        """Build the canonical dict that defines the plan's identity.

        Excludes `audience_plan_id` itself (the hash is over content, not
        over the hash field), `schema_version` (bumping the schema is not a
        plan content change), and `rationale` (the planner's narrative does
        not change WHO is being targeted).
        """

        roles = {
            "primary": self.primary.model_dump(mode="json"),
            "constraints": [r.model_dump(mode="json") for r in self.constraints],
            "extensions": [r.model_dump(mode="json") for r in self.extensions],
            "exclusions": [r.model_dump(mode="json") for r in self.exclusions],
        }
        return _canonicalize(roles)

    def compute_id(self) -> str:
        """Compute the sha256-prefixed content hash for this plan.

        Stable across reorderings of dict keys (Pydantic field order does
        not affect the result). NOT stable across reorderings of list
        items within a role -- planner-chosen order is significant.
        """

        canonical = self._content_for_hash()
        payload = json.dumps(
            canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        return f"sha256:{digest}"

    @model_validator(mode="after")
    def _populate_id_if_blank(self) -> AudiencePlan:
        """Auto-fill `audience_plan_id` when not supplied.

        Callers may pass an explicit id (e.g., when reconstructing a frozen
        snapshot from the wire) -- in that case we honor it. When blank, we
        compute the canonical hash from the plan's content.
        """

        if not self.audience_plan_id:
            # Avoid recursion on assignment by using object.__setattr__ via
            # Pydantic's internal mechanism: directly assign the field.
            object.__setattr__(self, "audience_plan_id", self.compute_id())
        return self


# ---------------------------------------------------------------------------
# Audience strictness policy (proposal §5.7)
# ---------------------------------------------------------------------------


class AudienceStrictness(BaseModel):
    """Per-role policy controlling buyer-side degradation behavior.

    When a seller does not support a portion of the AudiencePlan (e.g. the
    extensions list, or an agentic ref), the buyer's pre-flight degradation
    logic consults this policy to decide whether to drop, prompt, or refuse.

    Defaults follow proposal §5.7's recommended sane defaults:
      primary=required, constraints=preferred, extensions=optional, agentic=optional.
    """

    primary: StrictnessLevel = Field(
        default="required",
        description="Strictness for the primary ref (default: required)",
    )
    constraints: StrictnessLevel = Field(
        default="preferred",
        description="Strictness for constraint refs (default: preferred)",
    )
    extensions: StrictnessLevel = Field(
        default="optional",
        description="Strictness for extension refs (default: optional)",
    )
    agentic: StrictnessLevel = Field(
        default="optional",
        description="Strictness for agentic refs in any role (default: optional)",
    )

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Legacy migration shim (list[str] -> AudiencePlan)
# ---------------------------------------------------------------------------


# Sentinel identifier used when a legacy row had no audience entries at all.
# We cannot drop the campaign -- some pipelines guard on the presence of an
# audience plan, but a fully-empty list should not crash. The sentinel makes
# the lossy-conversion case visible and searchable in audit trails.
LEGACY_UNSPECIFIED_IDENTIFIER = "legacy:unspecified"


def is_legacy_list_shape(value: Any) -> bool:
    """Return True if `value` looks like the old `list[str]` audience shape.

    The new wire shape is a dict (or AudiencePlan); legacy SQLite rows store
    a JSON list of strings. A list of dicts is rejected (it would indicate
    a malformed input, not legacy data).
    """

    if not isinstance(value, list):
        return False
    if not value:
        return True
    return all(isinstance(item, str) for item in value)


def migrate_legacy_audience_list(
    legacy: list[str], *, source_context: str = "unspecified"
) -> AudiencePlan:
    """Convert a legacy `list[str]` audience field into a new `AudiencePlan`.

    Locked default policy (per ar-fe0h scope):
      - First item -> primary, type=standard, taxonomy=iab-audience,
        version=1.1, source=inferred (we never had explicit type info on the
        legacy field, so we cannot honestly mark it `explicit`).
      - Remaining items -> extensions, same shape.
      - constraints, exclusions empty.
      - rationale = "Migrated from legacy list[str]".
      - Empty list -> raise ValueError (the brief schema currently rejects
        empty audience and we preserve that behavior).

    Args:
        legacy: The legacy list of segment-id strings.
        source_context: Free-text label identifying the call site (e.g.
            "campaign_brief.target_audience") used in the audit log.

    Returns:
        A populated `AudiencePlan` with auto-computed `audience_plan_id`.

    Raises:
        ValueError: when the input is empty.
    """

    if not legacy:
        raise ValueError(
            "Cannot migrate empty legacy audience list to AudiencePlan: "
            "the brief schema requires at least one audience entry. "
            "Provide an explicit AudiencePlan or a non-empty list[str]."
        )

    primary = AudienceRef(
        type="standard",
        identifier=legacy[0],
        taxonomy="iab-audience",
        version="1.1",
        source="inferred",
        confidence=None,
    )
    extensions = [
        AudienceRef(
            type="standard",
            identifier=item,
            taxonomy="iab-audience",
            version="1.1",
            source="inferred",
            confidence=None,
        )
        for item in legacy[1:]
    ]
    plan = AudiencePlan(
        primary=primary,
        constraints=[],
        extensions=extensions,
        exclusions=[],
        rationale="Migrated from legacy list[str]",
    )

    # Structured log entry; downstream audit-trail surface (§13a) consumes it.
    _MIGRATION_LOGGER.info(
        "legacy audience list migrated to AudiencePlan",
        extra={
            "audience_migration": {
                "source_context": source_context,
                "legacy_input": list(legacy),
                "audience_plan_id": plan.audience_plan_id,
                "primary_identifier": plan.primary.identifier,
                "extension_count": len(plan.extensions),
                "policy": "first->primary, rest->extensions, source=inferred",
            }
        },
    )
    return plan


def coerce_audience_field(value: Any, *, source_context: str = "unspecified") -> Any:
    """Best-effort coercion for `target_audience` field input.

    Behavior:
      - If `value` is None, an `AudiencePlan` instance, or a dict, return as-is
        (Pydantic will validate the shape).
      - If `value` looks like the legacy `list[str]` form, migrate it via the
        locked default policy and return the `AudiencePlan`.
      - Otherwise return as-is and let downstream validation raise.

    This is a thin wrapper that keeps `model_validator(mode='before')` blocks
    in consumer models compact and consistent.
    """

    if value is None:
        return value
    if isinstance(value, AudiencePlan):
        return value
    if isinstance(value, dict):
        return value
    if is_legacy_list_shape(value):
        # Empty list intentionally raises here so callers see the policy.
        return migrate_legacy_audience_list(value, source_context=source_context)
    return value


# ---------------------------------------------------------------------------
# Brief-ingestion validation: Content Taxonomy 2.x -> 3.x deletions
# ---------------------------------------------------------------------------


def validate_content_taxonomy_version(plan: AudiencePlan) -> list[dict[str, Any]]:
    """Return a list of validation issues for content-taxonomy refs in `plan`.

    IAB Content Taxonomy 3.x is non-backwards-compatible with 2.x: some IDs
    were deleted entirely. A brief that arrives with a Contextual ref pinned
    to a pre-3.x version (or a 3.x ID that no longer resolves locally) needs
    to be remapped via the IAB Mapper tool before it can be matched against
    sellers running the modern taxonomy.

    This function does NOT call IAB Mapper -- that's a separate bead. It
    returns a structured issues list that the brief-ingestion entry point
    can attach to its error response.

    Each issue dict carries:
      - role: 'primary' | 'constraints' | 'extensions' | 'exclusions'
      - index: position within that role's list (0 for primary)
      - identifier: the offending ID
      - taxonomy: the ref's taxonomy
      - version: the ref's version
      - reason: short human-readable description
      - suggestion: action hint pointing to IAB Mapper
    """

    issues: list[dict[str, Any]] = []

    # Try to import the loader; fall back to None when the data dir is absent.
    try:
        from ..data.taxonomy_loader import lookup as _taxonomy_lookup
    except Exception:  # noqa: BLE001 - tolerate missing data in odd test envs.
        _taxonomy_lookup = None  # type: ignore[assignment]

    def _check(role: str, index: int, ref: AudienceRef) -> None:
        if ref.taxonomy != "iab-content":
            return

        # Policy: any version not starting with "3." for iab-content is a
        # 2.x-or-earlier ref needing the IAB Mapper. This catches both the
        # "version=2.0" and "version=" (blank/unset) cases.
        if not ref.version.startswith("3."):
            issues.append(
                {
                    "role": role,
                    "index": index,
                    "identifier": ref.identifier,
                    "taxonomy": ref.taxonomy,
                    "version": ref.version,
                    "reason": (
                        f"Content Taxonomy {ref.version!r} is pre-3.x. "
                        "Some IDs were deleted in 3.x; this ref must be "
                        "remapped before it can be matched."
                    ),
                    "suggestion": (
                        "Run the IAB Mapper migration tool "
                        "(https://iabtechlab.com/standards/iab-content-taxonomy/) "
                        f"to remap identifier {ref.identifier!r} from "
                        f"{ref.version} to 3.1, then resubmit the brief."
                    ),
                }
            )
            return

        # 3.x ref: best-effort lookup against the vendored 3.1 table. A miss
        # here suggests the ID was deleted or never existed.
        if _taxonomy_lookup is None:
            return
        try:
            entry = _taxonomy_lookup(ref.taxonomy, ref.identifier, ref.version)
        except Exception:  # noqa: BLE001 - loader errors must not block the brief.
            return
        if entry is None:
            issues.append(
                {
                    "role": role,
                    "index": index,
                    "identifier": ref.identifier,
                    "taxonomy": ref.taxonomy,
                    "version": ref.version,
                    "reason": (
                        f"Identifier {ref.identifier!r} not found in "
                        f"vendored Content Taxonomy {ref.version}. "
                        "The ID may have been deleted between 2.x and 3.x."
                    ),
                    "suggestion": (
                        "Run the IAB Mapper migration tool to discover the "
                        "3.x replacement, then resubmit the brief."
                    ),
                }
            )

    _check("primary", 0, plan.primary)
    for i, r in enumerate(plan.constraints):
        _check("constraints", i, r)
    for i, r in enumerate(plan.extensions):
        _check("extensions", i, r)
    for i, r in enumerate(plan.exclusions):
        _check("exclusions", i, r)

    return issues


class ContentTaxonomyMigrationRequired(ValueError):
    """Raised when a brief carries pre-3.x or unresolved Content Taxonomy refs.

    Carries the structured issue list as `.issues` so callers can render a
    specific UI/error response without re-parsing a string.
    """

    def __init__(self, issues: list[dict[str, Any]]) -> None:
        self.issues = issues
        if not issues:
            msg = "Content Taxonomy migration required (no specific issues)"
        else:
            heads = [
                f"{i['role']}[{i['index']}] id={i['identifier']!r} "
                f"version={i['version']!r}"
                for i in issues
            ]
            msg = (
                "Brief carries Content Taxonomy refs that need IAB Mapper "
                "migration before ingestion: " + "; ".join(heads)
            )
        super().__init__(msg)
