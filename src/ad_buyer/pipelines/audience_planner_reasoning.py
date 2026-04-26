# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Audience Planner reasoning loop (proposal §5.5).

This module owns the pure-Python core of the Audience Planner reasoning
loop. It takes a `CampaignBrief` and emits an `AudiencePlan` (primary +
constraints + extensions + rationale) following the six-phase loop
described in proposal §5.5:

  1. Classify intent  -- resolve free-text against vendored taxonomies.
  2. Pick primary     -- standard / contextual / agentic, by signal.
  3. Add constraints  -- when KPI signals precision (CPA, ROAS, CPC, CTR).
  4. Add extensions   -- when KPI signals reach (CPM, GRP, REACH objective).
  5. Validate         -- discovery + coverage; gracefully degrade on outage.
  6. Emit plan        -- with multi-line human-readable rationale.

The reasoning is deterministic Python so the unit tests in
`tests/unit/test_audience_planner_reasoning.py` can pin concrete behavior
without spinning up CrewAI. The orchestration shell in
`audience_planner_step.py` wraps this module and (in a later bead) may
invoke a CrewAI Task only for free-form rationale prose; the
*classification + role assignment* logic lives here, intentionally
testable without an LLM.

Hard rules from the proposal:

- Anything the planner ADDS to a user-supplied plan must carry
  `source="inferred"` so the audit trail distinguishes user-attributed
  from agent-attributed refs (proposal §5.2).
- An explicit primary (source=`explicit`) is NEVER mutated -- the
  planner can only enrich around it.
- Validation phase MUST degrade gracefully when discovery is
  unavailable (sellers aren't audience-aware until §8/§9/§11). The
  rationale records the degradation rather than crashing.
- `audience_strictness` from the brief is carried forward into the
  plan's metadata (encoded into the rationale prefix here; downstream
  beads can promote to a structured field).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..data.taxonomy_loader import lookup as taxonomy_lookup
from ..models.audience_plan import (
    AudiencePlan,
    AudienceRef,
    AudienceStrictness,
    ComplianceContext,
)

if TYPE_CHECKING:
    from ..models.campaign_brief import CampaignBrief
    from ..tools.audience.audience_discovery import AudienceDiscoveryTool
    from ..tools.audience.coverage_estimation import CoverageEstimationTool
    from ..tools.audience.embedding_mint import EmbeddingMintTool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal-detection vocabularies
# ---------------------------------------------------------------------------
#
# Tiny lexicons that drive intent classification when free-text strings
# arrive on `target_audience`. We deliberately keep these short and
# discoverable (rather than reaching for a real NLP pipeline) so the
# behavior is auditable from a unit test. Add new tokens here when a
# brief in the wild surfaces a class we want to classify; do NOT reach
# for fuzzy/ML methods inside this module -- that belongs in a real
# embedding pass (Epic 2).

# Demographic / intent-driven tokens => prefer Standard primary.
_DEMOGRAPHIC_TOKENS = {
    "men", "women", "male", "female",
    "kids", "children", "parent", "parents",
    "millennials", "gen z", "gen x", "boomers", "seniors",
    "household", "households",
    "intender", "intenders", "in-market", "in market",
    "demographic", "age", "income",
}

# Content-adjacent tokens => prefer Contextual primary.
_CONTEXTUAL_TOKENS = {
    "content", "adjacent", "alongside", "next to",
    "premium", "automotive content", "automotive blog",
    "news", "sports", "lifestyle", "category",
    "context", "contextual",
}

# First-party / lookalike tokens => prefer Agentic primary.
_AGENTIC_TOKENS = {
    "our converters", "our customers", "our buyers",
    "lookalike", "look-alike", "look alike",
    "first-party", "first party", "1p data", "1p audience",
    "previous campaign", "last campaign", "past campaign",
    "crm", "advertiser data", "advertiser-supplied",
    "high-ltv", "high ltv", "ltv lookalike",
}


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class _Candidate:
    """A single candidate ref produced by the classify-intent phase.

    Carries enough context for later phases (pick-primary, add-constraints/
    extensions) to decide what to do with the candidate without re-running
    the taxonomy lookup. `score` is a tiny self-confidence in [0, 1] used
    only for ordering candidates of the same type.
    """

    type: str  # "standard" | "contextual" | "agentic"
    identifier: str
    taxonomy: str
    version: str
    name: str = ""
    tier_1: str | None = None
    score: float = 0.5
    raw_token: str = ""

    def to_ref(
        self,
        *,
        source: str = "resolved",
        confidence: float | None = None,
        compliance_context: ComplianceContext | None = None,
    ) -> AudienceRef:
        """Materialize a typed `AudienceRef` from this candidate."""

        return AudienceRef(
            type=self.type,  # type: ignore[arg-type]
            identifier=self.identifier,
            taxonomy=self.taxonomy,
            version=self.version,
            source=source,  # type: ignore[arg-type]
            confidence=confidence if confidence is not None else self.score,
            compliance_context=compliance_context,
        )


@dataclass
class ClassificationResult:
    """Bundle of candidates produced by the classify phase.

    `unmatched_tokens` are free-text fragments that didn't resolve in
    either static taxonomy; the agentic phase mints embedding refs from
    these (or from explicit advertiser-1p tokens).
    """

    standard: list[_Candidate] = field(default_factory=list)
    contextual: list[_Candidate] = field(default_factory=list)
    agentic_seeds: list[str] = field(default_factory=list)
    unmatched_tokens: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.standard
            or self.contextual
            or self.agentic_seeds
            or self.unmatched_tokens
        )


@dataclass
class ReasoningResult:
    """Output of `run_audience_reasoning`.

    Attributes:
        plan: The composed `AudiencePlan`, or None when the brief had
            no audience signals at all and the planner produced nothing
            usable (callers surface "needs human review" in that branch).
        rationale_lines: List of human-readable rationale lines, in
            order. The plan's `rationale` is the joined string; this
            list is exposed for tests and audit-trail consumers.
        discovery_available: True when validation phase succeeded; False
            when degradation kicked in.
    """

    plan: AudiencePlan | None
    rationale_lines: list[str]
    discovery_available: bool = True


# ---------------------------------------------------------------------------
# Phase 1: Classify intent
# ---------------------------------------------------------------------------


def _normalize_tokens(text: str) -> list[str]:
    """Split free-text into lowercased tokens for matching."""

    return [t.strip() for t in re.split(r"[\s,;/|]+", text.lower()) if t.strip()]


def _classify_token(token: str) -> tuple[str | None, str]:
    """Bucket a free-text token into a coarse audience type.

    Returns (bucket, normalized_token):
      bucket is one of "standard" | "contextual" | "agentic" | None.
      None means the token was uninformative.
    """

    norm = token.lower().strip()
    if not norm:
        return None, ""

    # Agentic checks first -- multi-word phrases must beat single-word
    # demographic tokens that may appear inside them ("our converters"
    # contains "our" but is agentic, not demographic).
    for phrase in _AGENTIC_TOKENS:
        if phrase in norm:
            return "agentic", norm

    for phrase in _CONTEXTUAL_TOKENS:
        if phrase in norm:
            return "contextual", norm

    for phrase in _DEMOGRAPHIC_TOKENS:
        # Whole-word match for short tokens to avoid e.g. "men" matching
        # "supplement". Phrase matches are substring-OK.
        if " " in phrase:
            if phrase in norm:
                return "standard", norm
        else:
            if re.search(rf"\b{re.escape(phrase)}\b", norm):
                return "standard", norm

    return None, norm


def classify_intent(brief: CampaignBrief) -> ClassificationResult:
    """Phase 1: Classify the brief's audience signals.

    Walks all free-text sources on the brief (current target_audience
    refs that came from legacy migration, plus the brief's `description`
    and `notes` fields), resolving each token against vendored
    taxonomies where possible and bucketing the rest.

    Per proposal §5.5 step 1, this phase is the only one that touches
    the static taxonomies; downstream phases work over `_Candidate`
    objects.

    Important: literal taxonomy-id matching is only attempted on
    refs that came in already typed (not on prose tokens) -- a free-text
    "25" is the number 25, not Audience Taxonomy ID "25". Free-text
    tokens feed primary-type biasing only.
    """

    result = ClassificationResult()
    seen_identifiers: set[tuple[str, str]] = set()  # (type, identifier)

    def _add_candidate(c: _Candidate) -> None:
        key = (c.type, c.identifier)
        if key in seen_identifiers:
            return
        seen_identifiers.add(key)
        if c.type == "standard":
            result.standard.append(c)
        elif c.type == "contextual":
            result.contextual.append(c)

    # 1a. If the brief carries a typed plan, the existing primary +
    #     constraints + extensions are themselves classification signals.
    if brief.target_audience is not None:
        plan = brief.target_audience
        for ref in [plan.primary, *plan.constraints, *plan.extensions]:
            cand = _candidate_from_ref(ref)
            if cand is not None:
                _add_candidate(cand)
            elif ref.type == "agentic":
                result.agentic_seeds.append(ref.identifier)

    # 1b. Walk free-text on the brief itself. We do NOT attempt literal
    # taxonomy-id matches against prose tokens -- "25" inside an English
    # phrase is not Audience Taxonomy entry "25". Prose only feeds
    # primary-type biasing.
    text_sources: list[str] = []
    if brief.description:
        text_sources.append(brief.description)
    if brief.notes:
        text_sources.append(brief.notes)

    agentic_phrases: list[str] = []
    for src in text_sources:
        for token in _normalize_tokens(src):
            bucket, norm = _classify_token(token)
            if bucket is None:
                if norm:
                    result.unmatched_tokens.append(norm)
                continue
            # We don't synthesize candidates for free-text demographic /
            # contextual tokens -- without a confident taxonomy match we
            # can't pick an ID. They feed primary-selection biasing
            # instead via _classify_token's bucket. Stash them on
            # `unmatched_tokens` so the bias logic can see them but
            # downstream phases don't try to materialize a ref.
            if bucket == "agentic":
                agentic_phrases.append(norm)
            else:
                result.unmatched_tokens.append(norm)

        # Also scan for multi-word agentic / contextual phrases that the
        # per-token split would miss.
        src_lower = src.lower()
        for phrase in _AGENTIC_TOKENS:
            if phrase in src_lower and phrase not in agentic_phrases:
                agentic_phrases.append(phrase)

    # Stash agentic phrases as seed text for the embedding mint phase.
    for phrase in agentic_phrases:
        if phrase not in result.agentic_seeds:
            result.agentic_seeds.append(phrase)

    return result


def _candidate_from_ref(ref: AudienceRef) -> _Candidate | None:
    """Materialize a `_Candidate` from an existing `AudienceRef`.

    Used when the brief already carries a typed plan -- we treat the
    user-supplied refs as classification signals so any planner-added
    enrichment is consistent with what the user already chose.

    Returns None for agentic refs (they're tracked separately as seeds)
    or refs whose identifier doesn't resolve in the vendored taxonomy
    (the bias is still recorded via the type, but we can't carry an
    invalid candidate forward).
    """

    if ref.type == "agentic":
        return None
    taxonomy = "iab-audience" if ref.type == "standard" else "iab-content"
    entry = taxonomy_lookup(taxonomy, ref.identifier, ref.version)
    if entry is None:
        return None
    return _Candidate(
        type=ref.type,
        identifier=entry["id"],
        taxonomy=taxonomy,
        version=ref.version,
        name=entry.get("name") or "",
        tier_1=entry.get("tier_1"),
        score=ref.confidence if ref.confidence is not None else 1.0,
        raw_token=ref.identifier,
    )


# ---------------------------------------------------------------------------
# Phase 2: Pick primary
# ---------------------------------------------------------------------------


def _bias_from_text(brief: CampaignBrief) -> dict[str, float]:
    """Score bias toward each of the three types from brief free text."""

    bias = {"standard": 0.0, "contextual": 0.0, "agentic": 0.0}
    text = " ".join(filter(None, [brief.description or "", brief.notes or ""])).lower()

    for phrase in _AGENTIC_TOKENS:
        if phrase in text:
            bias["agentic"] += 1.0
    for phrase in _CONTEXTUAL_TOKENS:
        if phrase in text:
            bias["contextual"] += 1.0
    for phrase in _DEMOGRAPHIC_TOKENS:
        if " " in phrase:
            if phrase in text:
                bias["standard"] += 1.0
        elif re.search(rf"\b{re.escape(phrase)}\b", text):
            bias["standard"] += 1.0

    return bias


def pick_primary(
    brief: CampaignBrief,
    classification: ClassificationResult,
) -> tuple[AudienceRef | None, str, str]:
    """Phase 2: Decide which type owns the primary slot.

    Heuristic (proposal §5.5 step 2):
      - Demographic / intent-driven brief -> Standard.
      - Content-adjacent brief -> Contextual.
      - First-party-driven brief -> Agentic (mock embedding minted).

    Returns `(primary_ref, chosen_type, why)` where `why` is a short
    rationale phrase for the rationale block. `primary_ref` is None when
    no primary could be picked; the caller decides what to do.

    Note: this function does NOT mint agentic embeddings (that requires
    the EmbeddingMintTool); it returns the chosen type and the seed text
    so the orchestrator step can mint the ref with the tool injected.
    """

    bias = _bias_from_text(brief)

    # Bias from existing plan (if any) -- a brief that already includes
    # a Standard primary leans toward keeping Standard primary.
    if brief.target_audience is not None:
        bias[brief.target_audience.primary.type] += 2.0

    # Bias from candidate counts (more standard candidates -> stronger
    # standard signal; ditto contextual).
    bias["standard"] += min(len(classification.standard), 3) * 0.5
    bias["contextual"] += min(len(classification.contextual), 3) * 0.5
    bias["agentic"] += min(len(classification.agentic_seeds), 3) * 0.5

    # Decide. Tie-breaking: standard > contextual > agentic (the boring
    # safe default for unclear briefs).
    chosen = max(bias, key=lambda k: (bias[k], -["standard", "contextual", "agentic"].index(k)))

    if chosen == "standard" and classification.standard:
        cand = classification.standard[0]
        return cand.to_ref(source="resolved", confidence=cand.score), "standard", (
            f"primary=Standard (id={cand.identifier} {cand.name!r}); "
            "demographic / intent-driven brief"
        )

    if chosen == "contextual" and classification.contextual:
        cand = classification.contextual[0]
        return cand.to_ref(source="resolved", confidence=cand.score), "contextual", (
            f"primary=Contextual (id={cand.identifier} {cand.name!r}); "
            "content-adjacent brief"
        )

    if chosen == "agentic" and classification.agentic_seeds:
        # The caller will mint via EmbeddingMintTool; we return None for
        # the ref but signal the choice via the type.
        return None, "agentic", (
            f"primary=Agentic (seed={classification.agentic_seeds[0]!r}); "
            "first-party / lookalike-driven brief"
        )

    # Fallbacks: pick whatever we have.
    if classification.standard:
        cand = classification.standard[0]
        return cand.to_ref(source="resolved", confidence=cand.score), "standard", (
            f"primary=Standard (id={cand.identifier}, fallback)"
        )
    if classification.contextual:
        cand = classification.contextual[0]
        return cand.to_ref(source="resolved", confidence=cand.score), "contextual", (
            f"primary=Contextual (id={cand.identifier}, fallback)"
        )
    if classification.agentic_seeds:
        return None, "agentic", (
            f"primary=Agentic (seed={classification.agentic_seeds[0]!r}, fallback)"
        )

    return None, "none", "no usable audience signals found"


# ---------------------------------------------------------------------------
# Phase 3 / 4: Constraints (precision) vs Extensions (reach)
# ---------------------------------------------------------------------------


# KPIs that signal precision vs. reach. Maps brief-level KPI metric to
# the role we should populate.
_PRECISION_KPIS = {"CPC", "CPCV", "ROAS", "CTR"}
_REACH_KPIS = {"CPM", "GRP", "VCR"}


def _kpi_orientation(brief: CampaignBrief) -> str:
    """Returns "precision" | "reach" | "balanced" based on KPIs + objective."""

    metrics = {kpi.metric.value for kpi in brief.kpis}
    has_precision = bool(metrics & _PRECISION_KPIS)
    has_reach = bool(metrics & _REACH_KPIS)

    # Objective is the tiebreaker -- if KPIs are silent or balanced.
    obj_value = brief.objective.value
    if obj_value == "REACH":
        return "reach"
    if obj_value in {"CONVERSION", "CONSIDERATION"}:
        if has_reach and not has_precision:
            return "reach"
        return "precision"
    if obj_value == "AWARENESS":
        if has_precision and not has_reach:
            return "precision"
        return "reach"

    if has_precision and not has_reach:
        return "precision"
    if has_reach and not has_precision:
        return "reach"
    return "balanced"


def add_constraints(
    primary_type: str,
    classification: ClassificationResult,
    *,
    used_identifiers: set[tuple[str, str]],
) -> tuple[list[AudienceRef], list[str]]:
    """Phase 3: Add narrowing constraints when KPI is precision.

    Heuristic: if primary is Standard, prefer a Contextual constraint
    (intersect demographic with content adjacency). If primary is
    Contextual, prefer a Standard demographic constraint.

    Returns (constraint_refs, rationale_lines). Refs are tagged
    source=`inferred` since the planner is adding them.
    """

    refs: list[AudienceRef] = []
    rationale: list[str] = []

    if primary_type == "standard":
        for cand in classification.contextual:
            if (cand.type, cand.identifier) in used_identifiers:
                continue
            refs.append(cand.to_ref(source="inferred", confidence=cand.score))
            used_identifiers.add((cand.type, cand.identifier))
            rationale.append(
                f"constraint=Contextual {cand.identifier} ({cand.name!r}) -- "
                "narrows Standard primary with content adjacency for precision"
            )
            break  # one constraint is enough; demo-scope decision.
    elif primary_type == "contextual":
        for cand in classification.standard:
            if (cand.type, cand.identifier) in used_identifiers:
                continue
            refs.append(cand.to_ref(source="inferred", confidence=cand.score))
            used_identifiers.add((cand.type, cand.identifier))
            rationale.append(
                f"constraint=Standard {cand.identifier} ({cand.name!r}) -- "
                "narrows Contextual primary with demographic precision"
            )
            break
    elif primary_type == "agentic":
        # Tighten an Agentic primary with a Standard demographic if
        # available, to cap the lookalike to a sensible base population.
        for cand in classification.standard:
            if (cand.type, cand.identifier) in used_identifiers:
                continue
            refs.append(cand.to_ref(source="inferred", confidence=cand.score))
            used_identifiers.add((cand.type, cand.identifier))
            rationale.append(
                f"constraint=Standard {cand.identifier} ({cand.name!r}) -- "
                "anchors Agentic primary in a portable demographic"
            )
            break

    return refs, rationale


def add_extensions(
    brief: CampaignBrief,
    primary_type: str,
    classification: ClassificationResult,
    *,
    used_identifiers: set[tuple[str, str]],
    embedding_mint_tool: EmbeddingMintTool | None = None,
) -> tuple[list[AudienceRef], list[str]]:
    """Phase 4: Add broadening extensions when KPI is reach.

    Heuristic: if KPI is reach, mint an Agentic lookalike extension when
    the brief carries advertiser-1p signals; otherwise add a broader
    Standard tier-1 category (the parent of any standard candidate).

    Returns (extension_refs, rationale_lines), all source=`inferred`.
    """

    refs: list[AudienceRef] = []
    rationale: list[str] = []

    # Try Agentic lookalike if we have a seed and a mint tool.
    if classification.agentic_seeds and embedding_mint_tool is not None:
        seed = classification.agentic_seeds[0]
        try:
            ref = embedding_mint_tool.mint(
                name=f"{brief.advertiser_id}-lookalike",
                description=seed,
            )
            # We need source=inferred (mint tool emits source=inferred
                # already, but be defensive in case that ever changes).
            if ref.source != "inferred":
                ref = ref.model_copy(update={"source": "inferred"})
            key = (ref.type, ref.identifier)
            if key not in used_identifiers:
                refs.append(ref)
                used_identifiers.add(key)
                rationale.append(
                    f"extension=Agentic {ref.identifier[:24]}... (mint from "
                    f"{seed!r}) -- broadens reach via lookalike"
                )
        except Exception as exc:  # noqa: BLE001 - mint can fail in odd envs
            logger.warning(
                "audience_planner_reasoning: embedding mint failed; skipping",
                exc_info=exc,
            )

    # Otherwise (or in addition) add a broader Standard candidate not yet
    # used: prefer one whose tier_1 differs from the primary's, to add
    # genuine breadth rather than a near-duplicate.
    for cand in classification.standard:
        if (cand.type, cand.identifier) in used_identifiers:
            continue
        refs.append(cand.to_ref(source="inferred", confidence=cand.score))
        used_identifiers.add((cand.type, cand.identifier))
        rationale.append(
            f"extension=Standard {cand.identifier} ({cand.name!r}) -- "
            "broadens reach via additional demographic"
        )
        break

    if not refs:
        rationale.append(
            "no extensions added -- no broader candidates available"
        )

    return refs, rationale


# ---------------------------------------------------------------------------
# Phase 5: Validate (discovery + coverage)
# ---------------------------------------------------------------------------


def validate_plan(
    plan_refs: dict[str, Any],
    *,
    discovery_tool: AudienceDiscoveryTool | None = None,
    coverage_tool: CoverageEstimationTool | None = None,
) -> tuple[bool, list[str]]:
    """Phase 5: Run discovery + coverage tools; degrade gracefully.

    Returns (discovery_available, rationale_lines).

    The validation step is a soft gate: if the discovery tool succeeds
    we record the available capabilities count; if it raises (the
    expected case in this bead, since seller endpoints aren't
    audience-aware until §8/§9/§11), we record the degradation in the
    rationale and continue.
    """

    rationale: list[str] = []
    discovery_available = True

    if discovery_tool is None:
        rationale.append(
            "validation: discovery tool not provided; reach not validated "
            "(graceful degradation -- §8/§9/§11 will activate seller-side "
            "audience awareness)"
        )
        return False, rationale

    # Run discovery against a sentinel "mock" endpoint -- the tool's mock
    # branch returns a stable capability set in this bead, which is
    # enough for the validation step to record "we tried and got X".
    try:
        # The tool's _run is sync; we invoke directly.
        discovery_result = discovery_tool._run(
            seller_endpoint="http://mock.local/capabilities",
        )
    except Exception as exc:  # noqa: BLE001 - tolerate tool flakiness
        rationale.append(
            f"validation: discovery raised {type(exc).__name__}; reach not "
            "validated (graceful degradation per §5.5 step 5)"
        )
        return False, rationale

    if not discovery_result or "Error" in discovery_result[:64]:
        rationale.append(
            "validation: discovery returned no useful response; reach not "
            "validated (graceful degradation)"
        )
        discovery_available = False

    if coverage_tool is not None and discovery_available:
        try:
            targeting = {
                "primary_id": plan_refs.get("primary_identifier"),
                "primary_type": plan_refs.get("primary_type"),
            }
            coverage_tool._run(targeting=targeting)
            rationale.append(
                "validation: discovery + coverage estimates ran successfully"
            )
        except Exception as exc:  # noqa: BLE001 - tolerate tool flakiness
            rationale.append(
                f"validation: coverage tool raised {type(exc).__name__}; "
                "reach estimate skipped"
            )
    elif discovery_available:
        rationale.append(
            "validation: discovery ran; coverage tool not provided"
        )

    return discovery_available, rationale


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _strictness_prefix(strictness: AudienceStrictness) -> str:
    """One-line prefix that records the audience_strictness policy.

    Carries the policy forward into the rationale so downstream beads
    (§12 buyer-side degradation; §13a audit trail) can read it without
    threading another field through the wire.
    """

    return (
        f"[strictness primary={strictness.primary} "
        f"constraints={strictness.constraints} "
        f"extensions={strictness.extensions} "
        f"agentic={strictness.agentic}]"
    )


def run_audience_reasoning(
    brief: CampaignBrief,
    *,
    discovery_tool: AudienceDiscoveryTool | None = None,
    coverage_tool: CoverageEstimationTool | None = None,
    embedding_mint_tool: EmbeddingMintTool | None = None,
) -> ReasoningResult:
    """Run the six-phase Audience Planner reasoning loop.

    This is the pure-Python core. The CrewAI shell in
    `audience_planner_step.py` wraps it; tests call this function
    directly without spinning up an LLM.

    Args:
        brief: Validated `CampaignBrief`.
        discovery_tool: Optional `AudienceDiscoveryTool` for phase 5.
            When None, validation degrades gracefully and records that
            in the rationale.
        coverage_tool: Optional `CoverageEstimationTool`.
        embedding_mint_tool: Optional `EmbeddingMintTool` for minting
            agentic refs. When None, agentic primaries fall back to
            Standard / Contextual; agentic extensions are skipped.

    Returns:
        `ReasoningResult` carrying the composed plan (or None when no
        signals could be classified) and the rationale lines.
    """

    rationale_lines: list[str] = [_strictness_prefix(brief.audience_strictness)]

    # Brief carries no audience signals at all -- short-circuit. The
    # planner cannot invent a primary out of thin air; this branch
    # surfaces "needs human review" in the rationale so a downstream
    # reviewer can attach an explicit plan and re-run.
    brief_audience = getattr(brief, "target_audience", None)
    brief_description = getattr(brief, "description", None)
    brief_notes = getattr(brief, "notes", None)
    if brief_audience is None and not brief_description and not brief_notes:
        rationale_lines.append(
            "no target_audience and no advertiser context on brief; "
            "needs human review"
        )
        return ReasoningResult(
            plan=None,
            rationale_lines=rationale_lines,
            discovery_available=False,
        )

    classification = classify_intent(brief)

    # Decide on the primary.
    primary_ref: AudienceRef | None = None
    primary_type: str = "none"

    if brief_audience is not None:
        # The brief carries a primary from either explicit user input
        # OR legacy migration (source=inferred). Either way, we PRESERVE
        # it verbatim -- the planner does not second-guess the primary
        # the brief asked for. Enrichment happens around it.
        primary_ref = brief_audience.primary
        primary_type = primary_ref.type
        if primary_ref.source == "explicit":
            rationale_lines.append(
                f"primary=preserved (explicit {primary_type} "
                f"{primary_ref.identifier})"
            )
        else:
            rationale_lines.append(
                f"primary=preserved (inferred {primary_type} "
                f"{primary_ref.identifier} from migration / brief)"
            )
    else:
        # No brief plan at all -- compose from classification.
        if classification.is_empty():
            rationale_lines.append(
                "no audience signals classified from advertiser context; "
                "needs human review"
            )
            return ReasoningResult(
                plan=None,
                rationale_lines=rationale_lines,
                discovery_available=False,
            )

        primary_ref, primary_type, why = pick_primary(brief, classification)
        rationale_lines.append(why)

        # Mint an agentic primary if that's the chosen type and we have
        # a tool to do it.
        if primary_type == "agentic" and primary_ref is None:
            if embedding_mint_tool is not None and classification.agentic_seeds:
                seed = classification.agentic_seeds[0]
                try:
                    minted = embedding_mint_tool.mint(
                        name=f"{brief.advertiser_id}-primary",
                        description=seed,
                    )
                    primary_ref = minted
                    rationale_lines.append(
                        f"primary minted from seed {seed!r} -> "
                        f"{minted.identifier[:32]}..."
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "audience_planner_reasoning: agentic mint failed",
                        exc_info=exc,
                    )

            if primary_ref is None:
                # Fallback: use whatever standard/contextual candidate
                # we have so the plan is still buildable.
                if classification.standard:
                    cand = classification.standard[0]
                    primary_ref = cand.to_ref(source="resolved", confidence=cand.score)
                    primary_type = "standard"
                    rationale_lines.append(
                        f"primary=Standard fallback {cand.identifier} "
                        "(agentic mint unavailable)"
                    )
                elif classification.contextual:
                    cand = classification.contextual[0]
                    primary_ref = cand.to_ref(source="resolved", confidence=cand.score)
                    primary_type = "contextual"
                    rationale_lines.append(
                        f"primary=Contextual fallback {cand.identifier} "
                        "(agentic mint unavailable)"
                    )

    if primary_ref is None:
        rationale_lines.append(
            "could not compose primary ref; needs human review"
        )
        return ReasoningResult(
            plan=None,
            rationale_lines=rationale_lines,
            discovery_available=False,
        )

    used_identifiers: set[tuple[str, str]] = {(primary_ref.type, primary_ref.identifier)}

    # Carry forward any explicit constraints/extensions verbatim.
    explicit_constraints: list[AudienceRef] = []
    explicit_extensions: list[AudienceRef] = []
    explicit_exclusions: list[AudienceRef] = []
    if brief.target_audience is not None:
        for r in brief.target_audience.constraints:
            explicit_constraints.append(r)
            used_identifiers.add((r.type, r.identifier))
        for r in brief.target_audience.extensions:
            explicit_extensions.append(r)
            used_identifiers.add((r.type, r.identifier))
        for r in brief.target_audience.exclusions:
            explicit_exclusions.append(r)
            used_identifiers.add((r.type, r.identifier))

    # Phases 3 and 4: orient by KPI, then enrich.
    orientation = _kpi_orientation(brief)
    rationale_lines.append(
        f"KPI orientation: {orientation} (objective={brief.objective.value})"
    )

    inferred_constraints: list[AudienceRef] = []
    inferred_extensions: list[AudienceRef] = []

    if orientation in {"precision", "balanced"}:
        inferred_constraints, lines = add_constraints(
            primary_type, classification, used_identifiers=used_identifiers
        )
        rationale_lines.extend(lines)

    if orientation in {"reach", "balanced"}:
        inferred_extensions, lines = add_extensions(
            brief,
            primary_type,
            classification,
            used_identifiers=used_identifiers,
            embedding_mint_tool=embedding_mint_tool,
        )
        rationale_lines.extend(lines)

    # Phase 5: validate. Degrade gracefully when tools missing.
    discovery_available, val_lines = validate_plan(
        {
            "primary_identifier": primary_ref.identifier,
            "primary_type": primary_ref.type,
        },
        discovery_tool=discovery_tool,
        coverage_tool=coverage_tool,
    )
    rationale_lines.extend(val_lines)

    # Compose final plan.
    constraints = explicit_constraints + inferred_constraints
    extensions = explicit_extensions + inferred_extensions

    plan = AudiencePlan(
        primary=primary_ref,
        constraints=constraints,
        extensions=extensions,
        exclusions=explicit_exclusions,
        rationale="\n".join(rationale_lines),
    )

    return ReasoningResult(
        plan=plan,
        rationale_lines=rationale_lines,
        discovery_available=discovery_available,
    )
