# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Supply path analysis tool for DealJockey.

Scores supply paths on four dimensions -- transparency, fee, trust, and
performance -- using configurable weights from a supply_path_template.
Powers price comparison and migration recommendations by ranking
alternative supply paths.

Usage:
    store = DealStore("sqlite:///./ad_buyer.db")
    store.connect()

    tool = AnalyzeSupplyPathTool(deal_store=store)

    # Score a single path from raw data
    result = tool._run(
        supply_path_data_json='{"schain": {...}, "fee_estimate": 0.12, ...}',
    )

    # Score using a deal already in the library
    result = tool._run(deal_id="deal-uuid-here")

    # Score with custom weights from a template
    result = tool._run(
        deal_id="deal-uuid-here",
        supply_path_template_id="template-uuid",
    )

    # Compare multiple paths
    result = tool._run(
        supply_path_data_json='[{path1}, {path2}]',
    )
"""

import json
import logging
from typing import Any, Optional

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...storage.deal_store import DealStore

logger = logging.getLogger(__name__)

# Default scoring weights per the acceptance criteria
DEFAULT_WEIGHTS = {
    "transparency": 0.25,
    "fee": 0.35,
    "trust": 0.20,
    "performance": 0.20,
}


# -- Input schema -----------------------------------------------------------


class AnalyzeSupplyPathInput(BaseModel):
    """Input schema for AnalyzeSupplyPathTool."""

    deal_id: Optional[str] = Field(
        default=None,
        description=(
            "ID of a deal in the deal library. Supply chain and "
            "performance data will be pulled from the store."
        ),
    )
    supply_path_data_json: Optional[str] = Field(
        default=None,
        description=(
            "JSON string with supply path data. Can be a single object "
            "or an array of objects for comparison. Each object may have: "
            "schain (object with 'complete' int and 'nodes' array), "
            "fee_estimate (float 0-1, fraction of total cost), "
            "seller_reputation (object with trust_score, verified, "
            "sellers_json_listed), performance (object with fill_rate, "
            "win_rate, avg_effective_cpm, impressions_delivered, "
            "performance_trend)."
        ),
    )
    supply_path_template_id: Optional[str] = Field(
        default=None,
        description=(
            "ID of a supply_path_template whose scoring_weights to use. "
            "If omitted, default weights are applied: "
            "transparency=0.25, fee=0.35, trust=0.20, performance=0.20."
        ),
    )


# -- Scoring functions -------------------------------------------------------


def _score_transparency(path_data: dict[str, Any]) -> float:
    """Score supply path transparency based on schain completeness.

    Factors:
    - schain.complete flag (0 or 1): major contributor
    - Number of nodes: more nodes = more transparency into the path
    - Node quality: presence of hp (hosted publishing) flags

    Args:
        path_data: Supply path data dict.

    Returns:
        Score between 0.0 and 1.0.
    """
    schain = path_data.get("schain", {})
    if not schain:
        return 0.0

    score = 0.0

    # schain.complete flag is the biggest factor (0.5 of transparency score)
    complete = schain.get("complete", 0)
    if complete:
        score += 0.50

    # Nodes present (0.3 of transparency score)
    nodes = schain.get("nodes", [])
    if isinstance(nodes, str):
        try:
            nodes = json.loads(nodes)
        except (json.JSONDecodeError, TypeError):
            nodes = []

    if nodes:
        # Having at least one node is good; more nodes show full path
        node_count = len(nodes)
        # Diminishing returns: 1 node = 0.20, 2 nodes = 0.25, 3+ = 0.30
        if node_count >= 3:
            score += 0.30
        elif node_count == 2:
            score += 0.25
        elif node_count == 1:
            score += 0.20

        # Node quality: hp flags (0.2 of transparency score)
        hp_count = sum(1 for n in nodes if n.get("hp"))
        if nodes:
            hp_ratio = hp_count / len(nodes)
            score += 0.20 * hp_ratio

    return min(score, 1.0)


def _score_fee(path_data: dict[str, Any]) -> float:
    """Score supply path on estimated intermediary costs.

    Lower fees = higher score. fee_estimate is expected as a fraction
    (0.0 to 1.0) representing the share of total cost taken by
    intermediaries.

    Args:
        path_data: Supply path data dict.

    Returns:
        Score between 0.0 and 1.0.
    """
    fee_estimate = path_data.get("fee_estimate")

    if fee_estimate is None:
        # No fee data: return midpoint (uncertain)
        return 0.50

    # Clamp to valid range
    fee_estimate = max(0.0, min(1.0, float(fee_estimate)))

    # Linear inverse: 0% fees -> 1.0 score, 100% fees -> 0.0 score
    return 1.0 - fee_estimate


def _score_trust(path_data: dict[str, Any]) -> float:
    """Score seller reputation and trust signals.

    Factors:
    - trust_score: direct numeric trust rating (0.4 weight)
    - verified: seller is verified/known (0.3 weight)
    - sellers_json_listed: listed in sellers.json (0.3 weight)

    Args:
        path_data: Supply path data dict.

    Returns:
        Score between 0.0 and 1.0.
    """
    reputation = path_data.get("seller_reputation", {})
    if not reputation:
        return 0.0

    score = 0.0

    # Direct trust score (0.4 of trust score)
    trust_val = reputation.get("trust_score")
    if trust_val is not None:
        score += 0.40 * max(0.0, min(1.0, float(trust_val)))

    # Verified status (0.3 of trust score)
    if reputation.get("verified"):
        score += 0.30

    # sellers.json listing (0.3 of trust score)
    if reputation.get("sellers_json_listed"):
        score += 0.30

    return min(score, 1.0)


def _score_performance(path_data: dict[str, Any]) -> float:
    """Score historical delivery performance metrics.

    Factors:
    - fill_rate: fraction of bid requests that receive a bid (0.35 weight)
    - win_rate: fraction of bids that win the auction (0.35 weight)
    - performance_trend: IMPROVING/STABLE/DECLINING (0.30 weight)

    Args:
        path_data: Supply path data dict.

    Returns:
        Score between 0.0 and 1.0.
    """
    perf = path_data.get("performance", {})
    if not perf:
        return 0.0

    score = 0.0
    has_data = False

    # Fill rate (0.35 of performance score)
    fill_rate = perf.get("fill_rate")
    if fill_rate is not None:
        has_data = True
        score += 0.35 * max(0.0, min(1.0, float(fill_rate)))

    # Win rate (0.35 of performance score)
    win_rate = perf.get("win_rate")
    if win_rate is not None:
        has_data = True
        score += 0.35 * max(0.0, min(1.0, float(win_rate)))

    # Performance trend (0.30 of performance score)
    trend = perf.get("performance_trend", "").upper()
    if trend:
        has_data = True
        trend_scores = {
            "IMPROVING": 1.0,
            "STABLE": 0.7,
            "DECLINING": 0.3,
            "NO_DATA": 0.0,
        }
        score += 0.30 * trend_scores.get(trend, 0.0)

    if not has_data:
        return 0.0

    return min(score, 1.0)


def _score_single_path(
    path_data: dict[str, Any],
    weights: dict[str, float],
) -> dict[str, Any]:
    """Score a single supply path on all four dimensions.

    Args:
        path_data: Supply path data dict.
        weights: Scoring weights for each dimension.

    Returns:
        Dict with 'scores' (per-dimension), 'composite', and 'weights'.
    """
    scores = {
        "transparency": round(_score_transparency(path_data), 4),
        "fee": round(_score_fee(path_data), 4),
        "trust": round(_score_trust(path_data), 4),
        "performance": round(_score_performance(path_data), 4),
    }

    composite = sum(
        scores[dim] * weights[dim]
        for dim in ("transparency", "fee", "trust", "performance")
    )

    return {
        "scores": scores,
        "composite": round(composite, 4),
        "weights": weights,
    }


def _generate_recommendation(
    composite: float,
    scores: dict[str, float],
) -> str:
    """Generate a human-readable recommendation based on scores.

    Args:
        composite: Overall composite score (0-1).
        scores: Per-dimension scores.

    Returns:
        Recommendation string.
    """
    # Overall assessment
    if composite >= 0.80:
        assessment = "Excellent supply path."
    elif composite >= 0.60:
        assessment = "Good supply path with some areas for improvement."
    elif composite >= 0.40:
        assessment = "Moderate supply path; review recommended."
    elif composite >= 0.20:
        assessment = "Below-average supply path; consider alternatives."
    else:
        assessment = "Poor supply path; migration recommended."

    # Identify weak dimensions (below 0.4)
    weak_dims = [dim for dim, val in scores.items() if val < 0.4]
    # Identify strong dimensions (above 0.7)
    strong_dims = [dim for dim, val in scores.items() if val >= 0.7]

    parts = [assessment]

    if strong_dims:
        parts.append(f"Strengths: {', '.join(strong_dims)}.")

    if weak_dims:
        parts.append(f"Weaknesses: {', '.join(weak_dims)}.")

    return " ".join(parts)


def _build_path_from_deal(
    deal: dict[str, Any],
    perf_cache: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Build a supply path data dict from a deal record and its performance cache.

    Args:
        deal: Deal dict from DealStore.get_deal().
        perf_cache: Performance cache dict, or None.

    Returns:
        Supply path data dict suitable for scoring.
    """
    # Build schain from deal fields
    schain_nodes_raw = deal.get("schain_nodes")
    nodes = []
    if schain_nodes_raw:
        try:
            nodes = json.loads(schain_nodes_raw) if isinstance(schain_nodes_raw, str) else schain_nodes_raw
        except (json.JSONDecodeError, TypeError):
            pass

    schain = {
        "complete": deal.get("schain_complete", 0) or 0,
        "nodes": nodes,
    }

    # Fee estimate from fee_transparency field
    fee_estimate = deal.get("fee_transparency")

    # Seller reputation from deal fields
    seller_reputation = {}
    seller_type = deal.get("seller_type")
    if seller_type:
        # Direct publishers are generally more trusted
        trust_base = 0.80 if seller_type == "PUBLISHER" else 0.50
        seller_reputation["trust_score"] = trust_base

    sellers_json_url = deal.get("sellers_json_url")
    seller_reputation["sellers_json_listed"] = bool(sellers_json_url)
    seller_reputation["verified"] = bool(deal.get("is_direct"))

    # Performance from cache
    performance = {}
    if perf_cache:
        for field in ("fill_rate", "win_rate", "avg_effective_cpm",
                      "impressions_delivered", "performance_trend"):
            val = perf_cache.get(field)
            if val is not None:
                performance[field] = val

    result: dict[str, Any] = {"schain": schain}
    if fee_estimate is not None:
        result["fee_estimate"] = fee_estimate
    if seller_reputation:
        result["seller_reputation"] = seller_reputation
    if performance:
        result["performance"] = performance

    return result


# -- AnalyzeSupplyPathTool ---------------------------------------------------


class AnalyzeSupplyPathTool(BaseTool):
    """Score and rank supply paths using weighted multi-dimensional analysis.

    Evaluates supply paths on four dimensions:
    - **Transparency**: schain completeness and node detail
    - **Fee**: estimated intermediary cost (lower is better)
    - **Trust**: seller reputation, verification, sellers.json listing
    - **Performance**: historical fill rate, win rate, delivery trend

    Weights are loaded from a supply_path_template (if provided) or
    default to: transparency=0.25, fee=0.35, trust=0.20, performance=0.20.

    Accepts either raw supply path data (single or array for comparison)
    or a deal_id to pull data from the deal library.

    Returns JSON with per-dimension scores, composite score, ranking
    (if multiple paths), and a recommendation.
    """

    name: str = "analyze_supply_path"
    description: str = (
        "Score and rank supply paths on transparency, fee, trust, and "
        "performance dimensions. Accepts raw supply path data or a deal_id. "
        "Optionally uses a supply_path_template for custom scoring weights. "
        "Returns per-dimension scores, composite score, ranking for multiple "
        "paths, and a recommendation."
    )
    args_schema: type[BaseModel] = AnalyzeSupplyPathInput
    deal_store: Any = Field(exclude=True)

    def _run(
        self,
        deal_id: Optional[str] = None,
        supply_path_data_json: Optional[str] = None,
        supply_path_template_id: Optional[str] = None,
    ) -> str:
        """Analyze supply path(s) and return scored results as JSON.

        Args:
            deal_id: Look up supply chain data from a deal in the store.
            supply_path_data_json: Raw supply path data as JSON string.
                Can be a single object or an array of objects.
            supply_path_template_id: Template ID for custom scoring weights.

        Returns:
            JSON string with scores, composite, weights, recommendation,
            and ranking (if multiple paths).
        """
        # Validate inputs: need at least one of deal_id or supply_path_data_json
        if not deal_id and not supply_path_data_json:
            return json.dumps({
                "error": (
                    "Either 'deal_id' or 'supply_path_data_json' is required. "
                    "Provide a deal ID to pull data from the library, or "
                    "raw supply path data as JSON."
                ),
            })

        # Load weights from template or use defaults
        weights = dict(DEFAULT_WEIGHTS)
        warning = None

        if supply_path_template_id:
            template = self.deal_store.get_supply_path_template(
                supply_path_template_id
            )
            if template is None:
                warning = (
                    f"Supply path template '{supply_path_template_id}' not found. "
                    f"Using default weights."
                )
                logger.warning(warning)
            else:
                weights_raw = template.get("scoring_weights")
                if weights_raw:
                    try:
                        loaded = (
                            json.loads(weights_raw)
                            if isinstance(weights_raw, str)
                            else weights_raw
                        )
                        # Validate required keys present
                        required = {"transparency", "fee", "trust", "performance"}
                        if required.issubset(set(loaded.keys())):
                            weights = {k: loaded[k] for k in required}
                        else:
                            warning = (
                                "Template weights missing required keys. "
                                "Using default weights."
                            )
                    except (json.JSONDecodeError, TypeError):
                        warning = (
                            "Failed to parse template scoring_weights. "
                            "Using default weights."
                        )

        # Build path data list
        paths: list[dict[str, Any]] = []

        if deal_id:
            deal = self.deal_store.get_deal(deal_id)
            if deal is None:
                return json.dumps({
                    "error": f"Deal not found: {deal_id}",
                })
            perf_cache = self.deal_store.get_performance_cache(deal_id)
            path_data = _build_path_from_deal(deal, perf_cache)
            paths.append(path_data)
        else:
            # Parse supply_path_data_json
            try:
                parsed = json.loads(supply_path_data_json)
            except (json.JSONDecodeError, TypeError) as exc:
                return json.dumps({
                    "error": f"Invalid JSON in supply_path_data_json: {exc}",
                })

            if isinstance(parsed, list):
                paths = parsed
            elif isinstance(parsed, dict):
                # Validate: must have at least schain
                if not parsed.get("schain"):
                    return json.dumps({
                        "error": (
                            "Supply path data must contain at least a 'schain' "
                            "object with 'complete' and 'nodes' fields."
                        ),
                    })
                paths = [parsed]
            else:
                return json.dumps({
                    "error": "supply_path_data_json must be a JSON object or array.",
                })

        if not paths:
            return json.dumps({
                "error": "No supply path data to analyze.",
            })

        # Score each path
        results = []
        for path_data in paths:
            scored = _score_single_path(path_data, weights)
            scored["recommendation"] = _generate_recommendation(
                scored["composite"], scored["scores"]
            )
            results.append(scored)

        # Build response
        if len(results) == 1:
            response = results[0]
        else:
            # Multiple paths: build ranking
            ranking = sorted(
                [
                    {"path_index": i, "composite": r["composite"]}
                    for i, r in enumerate(results)
                ],
                key=lambda x: x["composite"],
                reverse=True,
            )

            # Use the best path's scores for the top-level response
            best_idx = ranking[0]["path_index"]
            response = results[best_idx].copy()
            response["ranking"] = ranking
            response["all_results"] = results

        if warning:
            response["warning"] = warning

        return json.dumps(response)
