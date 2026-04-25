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
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# Type aliases for readability and to keep Literal definitions in one place.
AudienceType = Literal["standard", "contextual", "agentic"]
AudienceSource = Literal["explicit", "resolved", "inferred"]


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
