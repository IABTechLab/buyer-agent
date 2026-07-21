# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Deterministic validation + clamp boundary for LLM-sourced recommendations.

Channel-specialist crews return their inventory picks as free text. Before
any numeric field from that output can influence a money/state decision --
the CPM ceiling and per-line budget handed to the booking orchestrator, or
the total fed to the spend ceiling -- it must cross THIS boundary:

1. Schema-validate the shape (the item must be a JSON object; numeric fields
   must be coercible to numbers, otherwise the item is rejected).
2. CLAMP numeric fields to deterministic bounds:
     * cpm         -> clamped to [0, max_cpm]   (campaign CPM ceiling)
     * cost        -> clamped to [0, max_cost]   (per-line budget ceiling)
     * impressions -> clamped to >= 0
   An out-of-bounds LLM value is clamped down (or, if malformed, the item is
   rejected); it is never trusted as-is.

This complements ``booking/spend_ceiling.py``. The spend ceiling is the final
hard gate that REJECTS an over-budget commit at execution time; this boundary
CLAMPS the per-line numbers the LLM proposed at parse time so that an inflated
CPM cannot itself become the ``max_cpm`` ceiling that is later passed to the
orchestrator (the flow derives the orchestrator's ceiling from the approved
recommendation's own cpm/cost, so an unclamped inflated value would be self-
authorizing).

Bead ar-1ow7 (EP-4.3).
"""

import logging
import math
from dataclasses import dataclass

from pydantic import ValidationError

from ..models.flow_state import ProductRecommendation

logger = logging.getLogger(__name__)

__all__ = ["RecommendationBounds", "validate_and_clamp_recommendation"]


@dataclass(frozen=True)
class RecommendationBounds:
    """Deterministic bounds an LLM recommendation is clamped to.

    Attributes:
        max_cpm: Campaign CPM ceiling. A parsed cpm above this is clamped
            down to it. None disables the cpm clamp (no ceiling supplied).
        max_cost: Per-line cost ceiling (typically the channel's allocated
            budget, falling back to the campaign budget). A parsed cost above
            this is clamped down to it. None disables the cost clamp.
    """

    max_cpm: float | None = None
    max_cost: float | None = None


def _coerce_nonneg_float(value: object) -> float | None:
    """Coerce ``value`` to a finite, non-negative float.

    Returns None when the value cannot be interpreted as a real number
    (the item is then rejected). A negative value is clamped up to 0.0.
    """
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return max(0.0, result)


def _coerce_nonneg_int(value: object) -> int | None:
    """Coerce ``value`` to a non-negative int (accepts float-like inputs).

    Returns None when the value cannot be interpreted as a whole number.
    A negative value is clamped up to 0.
    """
    coerced = _coerce_nonneg_float(value)
    if coerced is None:
        return None
    return int(coerced)


def validate_and_clamp_recommendation(
    item: object,
    channel: str,
    bounds: RecommendationBounds,
) -> ProductRecommendation | None:
    """Validate and clamp one raw LLM recommendation into a typed model.

    This is the single boundary every LLM-sourced recommendation crosses
    before its numbers can reach a booking primitive.

    Args:
        item: A single parsed item from the crew's JSON output. Anything
            that is not a dict is rejected.
        channel: The channel the recommendation belongs to.
        bounds: Deterministic ceilings to clamp numeric fields to.

    Returns:
        A validated ``ProductRecommendation`` with clamped numbers, or None
        when the item is malformed and must be rejected (never booked).
    """
    if not isinstance(item, dict):
        logger.warning("Rejecting non-object recommendation from crew output: %r", item)
        return None

    cpm = _coerce_nonneg_float(item.get("cpm", 0))
    cost = _coerce_nonneg_float(item.get("cost", 0))
    impressions = _coerce_nonneg_int(item.get("impressions", 0))

    if cpm is None or cost is None or impressions is None:
        logger.warning(
            "Rejecting malformed recommendation (uncoercible numeric field): "
            "cpm=%r impressions=%r cost=%r",
            item.get("cpm"),
            item.get("impressions"),
            item.get("cost"),
        )
        return None

    # Clamp cpm to the campaign ceiling. An LLM proposing a CPM above the
    # buyer's max cannot raise the ceiling it will later be booked against.
    if bounds.max_cpm is not None and cpm > bounds.max_cpm:
        logger.warning(
            "Clamping recommendation cpm %.4f down to max_cpm %.4f (channel=%s)",
            cpm,
            bounds.max_cpm,
            channel,
        )
        cpm = bounds.max_cpm

    # Clamp per-line cost to the budget ceiling. The final aggregate spend is
    # still hard-gated by enforce_spend_ceiling at execution time.
    if bounds.max_cost is not None and cost > bounds.max_cost:
        logger.warning(
            "Clamping recommendation cost %.2f down to budget ceiling %.2f (channel=%s)",
            cost,
            bounds.max_cost,
            channel,
        )
        cost = bounds.max_cost

    try:
        return ProductRecommendation(
            product_id=str(item.get("product_id", "unknown")),
            product_name=str(item.get("product_name", "Unknown Product")),
            publisher=str(item.get("publisher", "Unknown")),
            channel=channel,
            format=item.get("format"),
            impressions=impressions,
            cpm=cpm,
            cost=cost,
            rationale=item.get("rationale"),
        )
    except ValidationError:
        # Defensive: the clamps above already guarantee the ge=0 invariants,
        # so this only trips on an unexpected field-shape problem.
        logger.warning("Rejecting recommendation that failed model validation: %r", item)
        return None
