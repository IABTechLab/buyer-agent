# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Buyer-side capability negotiation: `degrade_plan_for_seller`.

Implements proposal §5.7 layer 2 (graceful degradation) and the retry-side
of layer 3 (forward-compatible structured rejection):

> degrade_plan_for_seller(plan, caps):
>   if not caps.agentic.supported:
>     drop refs of type=agentic from plan, log entry per drop
>   if not caps.supports_extensions:
>     drop all extensions, log
>   if not caps.supports_constraints:
>     drop all constraints, log
>   if not caps.supports_exclusions:
>     drop all exclusions, log
>   if caps.contextual_taxonomy_versions doesn't include plan's contextual
>       ref version:
>     log "needs IAB Mapper" -- drop or attempt mapping (drop for now)
>   if caps.standard_taxonomy_versions doesn't include plan's standard ref
>       version:
>     log "version mismatch" -- drop or warn
>   if remaining plan has no primary:
>     raise CannotFulfillPlan(reason)
>   return degraded_plan, log

The function takes a plan and a capabilities object and returns a degraded
plan plus a structured `DegradationLog` (list of entries) the orchestrator
uses for the audit trail (§13a) and rationale append. Composes with bead
§13's pre-flight integration (the two together implement full capability
negotiation per §5.7).

A second helper, `synthesize_capabilities_from_unsupported`, derives a
downgraded `SellerAudienceCapabilities` from the seller's structured
`{"error": "audience_plan_unsupported", "unsupported": [...]}` rejection.
The orchestrator's retry-on-rejection path uses it to figure out which
parts of the plan to drop before retrying once.

Bead: ar-0w48 (proposal §5.7 layer 2 + §6 row 12).
"""

from __future__ import annotations

import copy
import re
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field

from ..models.audience_plan import AudiencePlan, AudienceRef

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

DegradationAction = Literal["dropped", "warned", "mapped"]


class DegradationLogEntry(BaseModel):
    """Structured record of a single degradation action.

    Fields:
        path: JSON-path-ish location in the original plan (e.g. "primary",
            "extensions[0]", "constraints[2]"). Mirrors the seller's
            `audience_plan_unsupported.unsupported[].path` shape so the audit
            trail can correlate buyer-side drops with seller-side rejections.
        reason: Short human-readable description. Surfaced into the plan's
            rationale and into the audit-trail surface (§13a).
        original_ref: The ref that was dropped/warned, captured as a JSON
            dict so the audit trail stays self-contained even after the plan
            object is mutated.
        action: One of "dropped" (removed from the plan), "warned" (kept
            in the plan but flagged in the log), or "mapped" (rewritten,
            reserved for IAB Mapper integration which is a separate bead).
    """

    path: str = Field(..., description="JSON-path-ish location in the plan")
    reason: str = Field(..., description="Short human-readable explanation")
    original_ref: dict[str, Any] | None = Field(
        default=None,
        description="The original ref dict, when the entry refers to one",
    )
    action: DegradationAction = Field(
        default="dropped",
        description="Outcome: 'dropped' | 'warned' | 'mapped'",
    )

    model_config = {"populate_by_name": True}


# DegradationLog is a simple list alias rather than a wrapper class -- callers
# treat it as a sequence and the entries are what matters.
DegradationLog = list[DegradationLogEntry]


class CannotFulfillPlan(ValueError):
    """Raised when degradation strips the plan's primary ref.

    Per proposal §5.7: "if remaining plan has no primary: raise". The buyer
    cannot meaningfully proceed without a primary -- the seller would have
    nothing to match against. Carries the `DegradationLog` so callers can
    surface what was stripped before the failure.
    """

    def __init__(self, reason: str, log: DegradationLog | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.log: DegradationLog = log or []


# ---------------------------------------------------------------------------
# Buyer-side capability mirror
# ---------------------------------------------------------------------------
#
# The seller's authoritative capability shape lives in
# `ad_seller/models/audience_capabilities.py:CapabilityAudienceBlock`. We do
# not import that across repos -- the buyer reads the seller's JSON on the
# wire and parses into this model. Field names match the seller's so the
# wire shape round-trips without translation.


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
    bead §13, or a synthesized downgrade in the retry path of this bead).

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
            max_refs_per_role=_MaxRefsPerRole(
                primary=1, constraints=0, extensions=0, exclusions=0
            ),
        )


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------


def _ref_dump(ref: AudienceRef) -> dict[str, Any]:
    """Serialize a ref to a JSON-safe dict for the audit log.

    Uses `model_dump(mode="json")` so embedded models (ComplianceContext)
    flatten correctly and the entry is self-contained.
    """

    return ref.model_dump(mode="json")


def _ref_supports_taxonomy(
    ref: AudienceRef, capabilities: SellerAudienceCapabilities
) -> tuple[bool, str | None, str]:
    """Check a ref against the seller's taxonomy-version capabilities.

    Returns (ok, reason, classification):
      - ok=True, reason=None when the ref's taxonomy/version is supported.
      - ok=False, reason=<msg>, classification="needs IAB Mapper" for
        contextual refs whose version isn't listed.
      - ok=False, reason=<msg>, classification="version mismatch" for
        standard refs whose version isn't listed.

    Agentic refs are handled separately by the caller (the agentic-support
    flag is a single boolean, not a version list).
    """

    if ref.type == "standard":
        if not ref.version:
            return True, None, ""
        if ref.version in capabilities.standard_taxonomy_versions:
            return True, None, ""
        reason = (
            f"version mismatch: standard taxonomy version {ref.version!r} "
            f"not supported by seller (supports "
            f"{sorted(capabilities.standard_taxonomy_versions)})"
        )
        return False, reason, "version mismatch"

    if ref.type == "contextual":
        if not ref.version:
            return True, None, ""
        if ref.version in capabilities.contextual_taxonomy_versions:
            return True, None, ""
        reason = (
            f"needs IAB Mapper: contextual taxonomy version {ref.version!r} "
            f"not supported by seller (supports "
            f"{sorted(capabilities.contextual_taxonomy_versions)})"
        )
        return False, reason, "needs IAB Mapper"

    # Agentic: the caller already handled the top-level supported flag.
    return True, None, ""


def _filter_refs(
    refs: list[AudienceRef],
    *,
    role: str,
    capabilities: SellerAudienceCapabilities,
    log: DegradationLog,
) -> list[AudienceRef]:
    """Apply per-ref taxonomy/agentic checks to a list of refs.

    Returns the kept refs. Drops are appended to `log` with the appropriate
    reason. Used for primary (single-element list) and the three multi-ref
    roles (constraints / extensions / exclusions).
    """

    kept: list[AudienceRef] = []
    for idx, ref in enumerate(refs):
        path = role if len(refs) == 1 and role == "primary" else f"{role}[{idx}]"

        # Agentic refs first: the top-level flag is the gate.
        if ref.type == "agentic" and not capabilities.agentic.supported:
            log.append(
                DegradationLogEntry(
                    path=path,
                    reason="agentic refs not supported by seller",
                    original_ref=_ref_dump(ref),
                    action="dropped",
                )
            )
            continue

        ok, reason, _ = _ref_supports_taxonomy(ref, capabilities)
        if not ok:
            log.append(
                DegradationLogEntry(
                    path=path,
                    reason=reason or "taxonomy/version not supported",
                    original_ref=_ref_dump(ref),
                    action="dropped",
                )
            )
            continue

        kept.append(ref)
    return kept


def _drop_role_unsupported(
    refs: list[AudienceRef],
    *,
    role: str,
    log: DegradationLog,
) -> list[AudienceRef]:
    """Drop every ref in a role the seller doesn't honor.

    One log entry per ref so the audit trail keeps full provenance for what
    was stripped. Returns an empty list (the role is being zeroed out).
    """

    for idx, ref in enumerate(refs):
        log.append(
            DegradationLogEntry(
                path=f"{role}[{idx}]",
                reason=f"{role} not supported by seller",
                original_ref=_ref_dump(ref),
                action="dropped",
            )
        )
    return []


def _trim_to_max(
    refs: list[AudienceRef],
    *,
    role: str,
    max_for_role: int,
    log: DegradationLog,
) -> list[AudienceRef]:
    """Trim a role's ref list to the seller's per-role cardinality cap.

    Excess refs are dropped from the tail (planner-chosen order is
    significant: primaries first, then narrowing constraints, then
    extensions in priority order). Each excess drop logs its own entry.
    """

    if len(refs) <= max_for_role:
        return refs
    dropped = refs[max_for_role:]
    for offset, ref in enumerate(dropped):
        idx = max_for_role + offset
        log.append(
            DegradationLogEntry(
                path=f"{role}[{idx}]",
                reason=(
                    f"max_refs_per_role.{role}={max_for_role} exceeded "
                    f"(plan had {len(refs)} refs)"
                ),
                original_ref=_ref_dump(ref),
                action="dropped",
            )
        )
    return refs[:max_for_role]


def degrade_plan_for_seller(
    plan: AudiencePlan,
    capabilities: SellerAudienceCapabilities,
) -> tuple[AudiencePlan, DegradationLog]:
    """Strip a plan to fit a seller's capability declaration.

    Per proposal §5.7 layer 2. Walks the plan in role order
    (primary -> constraints -> extensions -> exclusions) and applies the
    seller's flags:

    - Agentic refs are dropped wholesale when `agentic.supported=False`.
    - `supports_extensions=False` zeros out the extensions list.
    - `supports_constraints=False` zeros out the constraints list.
    - `supports_exclusions=False` zeros out the exclusions list.
    - Per-ref taxonomy/version mismatches are dropped with the appropriate
      classification ("needs IAB Mapper" for contextual, "version mismatch"
      for standard).
    - Per-role cardinality caps are enforced after taxonomy filtering.
    - If the primary survives all of the above, returns the degraded plan
      plus a structured log of what was stripped. Otherwise raises
      `CannotFulfillPlan`.

    The original plan is not mutated -- a fresh `AudiencePlan` is returned.
    The new plan's `audience_plan_id` is recomputed from the degraded
    content (the plan's identity moves with its content; the seller will
    receive and log the new hash).

    Args:
        plan: The buyer's `AudiencePlan` to degrade.
        capabilities: The seller's capability declaration.

    Returns:
        A tuple of (degraded_plan, log). `log` is empty when no degradation
        was needed.

    Raises:
        CannotFulfillPlan: When degradation would strip the primary ref.
    """

    log: DegradationLog = []

    # ---- primary ----
    # Agentic primary is a special case: dropping it leaves the plan with no
    # primary at all, which is fatal.
    primary = plan.primary
    primary_kept = _filter_refs(
        [primary], role="primary", capabilities=capabilities, log=log
    )
    if not primary_kept:
        # The most recent log entry describes why the primary was dropped.
        last_reason = log[-1].reason if log else "primary ref unsupported"
        raise CannotFulfillPlan(
            reason=(
                f"Primary ref dropped during degradation: {last_reason}. "
                "Seller cannot fulfill this plan."
            ),
            log=log,
        )

    # ---- constraints ----
    if plan.constraints:
        if not capabilities.supports_constraints:
            constraints = _drop_role_unsupported(
                plan.constraints, role="constraints", log=log
            )
        else:
            constraints = _filter_refs(
                plan.constraints,
                role="constraints",
                capabilities=capabilities,
                log=log,
            )
            constraints = _trim_to_max(
                constraints,
                role="constraints",
                max_for_role=capabilities.max_refs_per_role.constraints,
                log=log,
            )
    else:
        constraints = []

    # ---- extensions ----
    if plan.extensions:
        if not capabilities.supports_extensions:
            extensions = _drop_role_unsupported(
                plan.extensions, role="extensions", log=log
            )
        else:
            extensions = _filter_refs(
                plan.extensions,
                role="extensions",
                capabilities=capabilities,
                log=log,
            )
            extensions = _trim_to_max(
                extensions,
                role="extensions",
                max_for_role=capabilities.max_refs_per_role.extensions,
                log=log,
            )
    else:
        extensions = []

    # ---- exclusions ----
    if plan.exclusions:
        if not capabilities.supports_exclusions:
            exclusions = _drop_role_unsupported(
                plan.exclusions, role="exclusions", log=log
            )
        else:
            exclusions = _filter_refs(
                plan.exclusions,
                role="exclusions",
                capabilities=capabilities,
                log=log,
            )
            exclusions = _trim_to_max(
                exclusions,
                role="exclusions",
                max_for_role=capabilities.max_refs_per_role.exclusions,
                log=log,
            )
    else:
        exclusions = []

    # Build the degraded plan. Reset audience_plan_id to "" so the model
    # validator recomputes it from the (potentially) changed content.
    degraded = AudiencePlan(
        schema_version=plan.schema_version,
        audience_plan_id="",
        primary=primary_kept[0],
        constraints=list(constraints),
        extensions=list(extensions),
        exclusions=list(exclusions),
        rationale=plan.rationale,
    )
    return degraded, log


# ---------------------------------------------------------------------------
# Synthesizing a downgraded capability from the seller's structured rejection
# ---------------------------------------------------------------------------


# Match "extensions[0]", "constraints[2]", etc. Keeps the role name and index.
_PATH_INDEXED = re.compile(r"^(?P<role>[a-z]+)(?:\[(?P<idx>\d+)\])?(?:\.[a-z_]+)?$")


def synthesize_capabilities_from_unsupported(
    unsupported: Iterable[dict[str, Any]],
    base: SellerAudienceCapabilities | None = None,
) -> SellerAudienceCapabilities:
    """Derive a downgraded `SellerAudienceCapabilities` from the seller's rejection.

    The retry-on-rejection path uses this when the buyer's pre-flight cache
    was stale or missing: it parses the seller's
    `{"error": "audience_plan_unsupported", "unsupported": [...]}` payload
    and figures out what to disable in the buyer's cap-mirror so a single
    pass of `degrade_plan_for_seller` lines the plan up with what the seller
    actually accepts.

    Conservative interpretation: if the seller rejects "extensions[0]" with
    reason "extensions not supported", we flip `supports_extensions=False`.
    If the seller rejects a contextual version, we drop that version from
    `contextual_taxonomy_versions`. Anything we can't classify, we leave
    alone -- the orchestrator's retry will surface a second rejection if
    we missed something.

    Args:
        unsupported: The list from the seller's structured error.
        base: Starting capabilities (e.g. the buyer's cached view of the
            seller). If None, starts from `legacy_default()` which is
            already maximally conservative.

    Returns:
        A `SellerAudienceCapabilities` with the relevant flags toggled off.
    """

    caps = (
        base.model_copy(deep=True) if base is not None
        else SellerAudienceCapabilities()
    )

    for entry in unsupported:
        path = (entry.get("path") or "").strip()
        reason = (entry.get("reason") or "").lower()

        match = _PATH_INDEXED.match(path)
        role = match.group("role") if match else ""

        # Role-level "not supported" rejections -> flip the role gate.
        if role == "extensions" and "not supported" in reason:
            caps.supports_extensions = False
            caps.max_refs_per_role.extensions = 0
            continue
        if role == "constraints" and "not supported" in reason:
            caps.supports_constraints = False
            caps.max_refs_per_role.constraints = 0
            continue
        if role == "exclusions" and "not supported" in reason:
            caps.supports_exclusions = False
            caps.max_refs_per_role.exclusions = 0
            continue

        # Agentic-specific rejection -> flip the agentic flag.
        if "agentic" in reason and "not supported" in reason:
            caps.agentic = _AgenticFlag(supported=False)
            continue

        # Version mismatches -> drop the offending version from the cap list.
        # The seller's reason text carries the version in quotes; we don't
        # try to parse it. We instead trim down to whatever the buyer's
        # base caps had MINUS the offending version, conservatively. Without
        # the version embedded in the rejection, the safest move is to
        # blank the list -- the next retry will hit the same rejection if
        # the seller still doesn't accept anything, and the orchestrator
        # will mark it incompatible.
        if "standard taxonomy version" in reason:
            caps.standard_taxonomy_versions = []
            continue
        if "contextual taxonomy version" in reason:
            caps.contextual_taxonomy_versions = []
            continue

    return caps


__all__ = [
    "CannotFulfillPlan",
    "DegradationAction",
    "DegradationLog",
    "DegradationLogEntry",
    "SellerAudienceCapabilities",
    "degrade_plan_for_seller",
    "synthesize_capabilities_from_unsupported",
]


# Quiet the "unused import" linter -- copy is exported for callers that want
# to vendor the deep-copy behavior outside this module.
_ = copy
