# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Buyer-side mirror of the seller's `CapabilityAudienceBlock`.

This is a pure data model with no dependency on either the `clients` or
`orchestration` layer. It lives under `models/` so both can import it at
module top level without creating a cycle:

- `clients.capability_client` parses seller `/.well-known/agent.json`
  responses into `SellerAudienceCapabilities`.
- `orchestration.audience_degradation` reads the same model when degrading
  a plan to fit a seller's declared capabilities.

Previously this model lived in `orchestration.audience_degradation`, which
forced `clients.capability_client` (a low-level client) to reach *up* into
the orchestration package via function-local deferred imports -- a layering
inversion. Hoisting the shared model to `models/` inverts the dependency so
it flows one way only: orchestration -> clients -> models.

The seller's authoritative capability shape lives in
`ad_seller/models/audience_capabilities.py:CapabilityAudienceBlock`. We do
not import that across repos -- the buyer reads the seller's JSON on the
wire and parses into this model. Field names match the seller's so the wire
shape round-trips without translation.

Part of EP-2.3: break the clients<->orchestration cycle.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class _AgenticFlag(BaseModel):
    """Buyer-side mirror of the seller's `AgenticCapabilityFlag`.

    Carries only the top-level "agentic supported at all" boolean. Per-package
    detail (signal types, embedding dim) is the seller's concern at booking
    time; the buyer only needs to know whether to keep agentic refs in the
    plan.
    """

    supported: bool = Field(default=False)


class _MaxRefsPerRole(BaseModel):
    """Buyer-side mirror of the seller's `MaxRefsPerRole`.

    Cardinality caps per role. The buyer trims ref lists to fit before
    sending the plan.
    """

    primary: int = Field(default=1, ge=0)
    constraints: int = Field(default=3, ge=0)
    extensions: int = Field(default=0, ge=0)
    exclusions: int = Field(default=0, ge=0)


class SellerAudienceCapabilities(BaseModel):
    """Buyer-side mirror of the seller's `CapabilityAudienceBlock`.

    Same JSON shape as the seller's authoritative model so capability
    discovery responses round-trip without translation. The buyer's
    `degrade_plan_for_seller` reads from this model only -- it doesn't care
    where the values came from (a real capability discovery response in
    §13, or a synthesized downgrade in the retry path).

    A seller that doesn't ship `audience_capabilities` at all is treated as
    legacy. Callers can construct a "legacy default" instance via
    `SellerAudienceCapabilities.legacy_default()`.
    """

    schema_version: str = Field(default="1")
    standard_taxonomy_versions: list[str] = Field(default_factory=lambda: ["1.1"])
    contextual_taxonomy_versions: list[str] = Field(default_factory=lambda: ["3.1"])
    agentic: _AgenticFlag = Field(default_factory=_AgenticFlag)
    supports_constraints: bool = Field(default=True)
    supports_extensions: bool = Field(default=False)
    supports_exclusions: bool = Field(default=False)
    max_refs_per_role: _MaxRefsPerRole = Field(default_factory=_MaxRefsPerRole)

    model_config = {"populate_by_name": True}

    @classmethod
    def legacy_default(cls) -> SellerAudienceCapabilities:
        """Return the safe-default for a seller that ships no capability block.

        Per proposal §5.7: "A seller that doesn't ship this field is treated
        as legacy: standard segments only, no constraints, no extensions, no
        exclusions, no agentic. That's the safe default."
        """

        return cls(
            schema_version="0",
            standard_taxonomy_versions=["1.1"],
            contextual_taxonomy_versions=[],
            agentic=_AgenticFlag(supported=False),
            supports_constraints=False,
            supports_extensions=False,
            supports_exclusions=False,
            max_refs_per_role=_MaxRefsPerRole(primary=1, constraints=0, extensions=0, exclusions=0),
        )


__all__ = [
    "SellerAudienceCapabilities",
]
