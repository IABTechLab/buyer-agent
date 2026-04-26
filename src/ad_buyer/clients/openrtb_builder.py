# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""OpenRTB carrier mapping for `AudiencePlan` (impression-time wire shape).

Per proposal §5.1 Step 4 / §6 row 15 / wire-format spec §9, once a deal is
booked the buyer issues OpenRTB bid requests carrying the audience semantics
in three slots:

| Audience type | OpenRTB carrier |
|---------------|-----------------|
| `standard`    | ``user.data[].segment[].id`` (with ``data.name="IAB_Taxonomy"`` and ``data.ext.taxonomy_version``) |
| `contextual`  | ``site.cat`` + ``site.cattax = 7`` (Content Taxonomy 3.1 enum) |
| `agentic`     | ``user.ext.iab_agentic_audiences.refs[]`` (namespaced extension; feature-flagged) |

The agentic extension is **temporary**: until IAB ratifies an OpenRTB
extension shape, this is the namespaced key we emit. A 90-day dual-emit
migration policy applies once IAB ratifies a key (see wire-format spec §9
"90-day dual-emit migration policy"). The agentic emission is gated by the
``enable_agentic_openrtb_ext`` setting (default off) so deployments that do
not want to ship the temporary key can disable it without a code change.

This module is a pure function of the plan: it does NOT mutate the plan,
issue any HTTP, or read any global state outside the explicit
``enable_agentic_ext`` argument.

Bead: ar-8vzg (proposal §6 row 15).
"""

from __future__ import annotations

import logging
from typing import Any

from ad_buyer.models.audience_plan import AudiencePlan, AudienceRef

logger = logging.getLogger(__name__)

# OpenRTB 2.6 enum value for IAB Content Taxonomy 3.1 in `site.cattax` /
# `app.cattax` / `content.cattax`. See OpenRTB 2.6 spec, "Category Taxonomies".
CONTENT_TAXONOMY_31_CATTAX = 7

# Constant `data.name` we emit on the user.data[] entry carrying standard refs.
# Sellers parse on this name to identify IAB Audience Taxonomy segments.
IAB_AUDIENCE_TAXONOMY_DATA_NAME = "IAB_Taxonomy"

# Namespaced key for the temporary agentic extension on `user.ext`. When IAB
# ratifies an extension shape, the buyer dual-emits both this key and the
# ratified one for 90 days, then drops this key (wire-format spec §9).
AGENTIC_USER_EXT_KEY = "iab_agentic_audiences"


def _collect_role(refs: list[AudienceRef], role: str) -> list[tuple[AudienceRef, str]]:
    """Tag each ref with its role name for downstream exclusion handling."""

    return [(r, role) for r in refs]


def _ref_to_segment(ref: AudienceRef, *, role: str) -> dict[str, Any]:
    """Build a single ``user.data[].segment[]`` entry from a standard ref.

    Exclusions get an ``ext.exclude=true`` flag on the segment. OpenRTB does
    not have a first-class "exclude this segment" slot, so we use a
    namespaced ext flag which sellers MAY honor. Callers that prefer to omit
    exclusions entirely can filter them out before calling the builder.
    """

    seg: dict[str, Any] = {"id": ref.identifier}
    if ref.confidence is not None:
        # Carry confidence through where present (resolved/inferred refs).
        seg["value"] = str(ref.confidence)
    if role == "exclusions":
        seg["ext"] = {"exclude": True}
    return seg


def _ref_to_agentic_ext_entry(ref: AudienceRef) -> dict[str, Any]:
    """Build a single entry for ``user.ext.iab_agentic_audiences.refs[]``.

    Carries the four fields per the wire-format spec §9 example:
    ``identifier``, ``version``, ``source``, ``compliance_context``. Pydantic
    has already validated that agentic refs carry a compliance_context.
    """

    cc = ref.compliance_context
    cc_payload = cc.model_dump(mode="json") if cc is not None else None
    return {
        "identifier": ref.identifier,
        "version": ref.version,
        "source": ref.source,
        "compliance_context": cc_payload,
    }


def build_openrtb_audience_targeting(
    plan: AudiencePlan,
    *,
    enable_agentic_ext: bool = False,
) -> dict[str, Any]:
    """Translate an `AudiencePlan` into OpenRTB v2.6 wire fragments.

    Returns a dict with up to two top-level keys -- ``user`` and ``site`` --
    each of which is a fragment that the caller merges into a full
    ``BidRequest``. The builder does not assemble a full bid request because
    the rest (imp, deal_id, currency, etc.) is supplied by the campaign
    runtime, not the audience plan.

    Mapping summary (proposal §5.1 Step 4):

    - ``standard`` refs (any role) -> ``user.data[]`` group named
      ``"IAB_Taxonomy"`` with one segment per ref. Exclusions are emitted as
      segments with ``ext.exclude=true``.
    - ``contextual`` refs (any role) -> appended to ``site.cat[]`` with
      ``site.cattax=7``. Exclusions are dropped with a structured warning
      log entry (OpenRTB has no contextual-exclusion slot).
    - ``agentic`` refs -> ``user.ext.iab_agentic_audiences.refs[]`` IFF the
      feature flag is enabled. When disabled, the agentic refs are dropped
      with a structured warning log entry citing the flag.

    Args:
        plan: The `AudiencePlan` to translate.
        enable_agentic_ext: Feature flag -- when False (default), agentic
            refs are NOT emitted to the wire. This protects deployments that
            do not want to ship the temporary namespaced key while IAB's
            ratified shape is still pending.

    Returns:
        A dict with ``"user"`` and/or ``"site"`` keys containing the OpenRTB
        fragments. Empty dict if the plan has no refs in any of the three
        OpenRTB carrier slots (e.g. an agentic-only plan with the flag off).

    Notes on exclusions:
        OpenRTB does not have first-class exclusion semantics for any of the
        three carriers. Our chosen behavior:
        - standard: emit segment with ``ext.exclude=true`` (ad-hoc; sellers
          MAY honor).
        - contextual: drop, log warning. ``site.cat`` is positive-only.
        - agentic: emit with ``compliance_context`` intact, no exclusion
          flag (the agentic spec does not yet define exclusion semantics).
        Sellers that need exclusion-aware OpenRTB handling should consult
        the booking-time `AudiencePlan` snapshot (which carries the full
        `exclusions[]` list with full fidelity).
    """

    # Walk the plan once, collecting refs by type with their role context.
    standard_refs: list[tuple[AudienceRef, str]] = []
    contextual_refs: list[tuple[AudienceRef, str]] = []
    agentic_refs: list[tuple[AudienceRef, str]] = []

    for role_name in ("primary", "constraints", "extensions", "exclusions"):
        if role_name == "primary":
            role_refs = [plan.primary]
        else:
            role_refs = list(getattr(plan, role_name))
        for ref in role_refs:
            if ref.type == "standard":
                standard_refs.append((ref, role_name))
            elif ref.type == "contextual":
                contextual_refs.append((ref, role_name))
            elif ref.type == "agentic":
                agentic_refs.append((ref, role_name))

    fragment: dict[str, Any] = {}

    # --- Standard refs -> user.data[].segment[] ---
    if standard_refs:
        # Group all standard refs under a single data entry with the IAB
        # taxonomy name. Use the first ref's version as the taxonomy version
        # in ext (all standard refs in a campaign should share a version --
        # the planner enforces this; if mixed, we annotate with the first).
        version = standard_refs[0][0].version
        segments = [_ref_to_segment(r, role=role) for r, role in standard_refs]
        user_data = {
            "name": IAB_AUDIENCE_TAXONOMY_DATA_NAME,
            "ext": {"taxonomy_version": version},
            "segment": segments,
        }
        fragment.setdefault("user", {})["data"] = [user_data]

    # --- Contextual refs -> site.cat + cattax ---
    if contextual_refs:
        positive = [(r, role) for r, role in contextual_refs if role != "exclusions"]
        excluded = [(r, role) for r, role in contextual_refs if role == "exclusions"]
        if excluded:
            # OpenRTB has no contextual-exclusion slot. Dropping is the
            # honest behavior; surface it via a structured warning so the
            # audit trail can pick it up (proposal §13a).
            logger.warning(
                "openrtb_builder dropping contextual exclusions: "
                "OpenRTB site.cat has no exclusion semantics",
                extra={
                    "openrtb_drop": {
                        "reason": "site_cat_has_no_exclusion_semantics",
                        "dropped_count": len(excluded),
                        "dropped_identifiers": [r.identifier for r, _ in excluded],
                    }
                },
            )
        if positive:
            cats = [r.identifier for r, _ in positive]
            fragment.setdefault("site", {})
            fragment["site"]["cat"] = cats
            fragment["site"]["cattax"] = CONTENT_TAXONOMY_31_CATTAX

    # --- Agentic refs -> user.ext.iab_agentic_audiences.refs[] ---
    if agentic_refs:
        if not enable_agentic_ext:
            logger.warning(
                "openrtb_builder skipping agentic refs: "
                "enable_agentic_openrtb_ext flag disabled",
                extra={
                    "openrtb_drop": {
                        "reason": "agentic_ext_feature_flag_disabled",
                        "dropped_count": len(agentic_refs),
                        "dropped_identifiers": [r.identifier for r, _ in agentic_refs],
                    }
                },
            )
        else:
            entries = [_ref_to_agentic_ext_entry(r) for r, _ in agentic_refs]
            user = fragment.setdefault("user", {})
            user_ext = user.setdefault("ext", {})
            user_ext[AGENTIC_USER_EXT_KEY] = {"refs": entries}

    return fragment


__all__ = [
    "AGENTIC_USER_EXT_KEY",
    "CONTENT_TAXONOMY_31_CATTAX",
    "IAB_AUDIENCE_TAXONOMY_DATA_NAME",
    "build_openrtb_audience_targeting",
]
