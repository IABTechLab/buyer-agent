# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Quote normalization for multi-seller deal comparison.

Different sellers return quotes with different deal types (PG, PD, PA),
fee structures, and minimum spends.  QuoteNormalizer computes an
"effective CPM" for each quote that accounts for:

  - Deal-type adjustment (PA floor CPMs are marked up to reflect
    expected clearing price)
  - Estimated intermediary fees from supply-path data when available
  - Minimum spend requirements

The effective CPM lets Campaign Automation rank sellers by true cost on
an apples-to-apples basis.

Reference: Campaign Automation Strategic Plan, Section 7.2
(Multi-Seller Deal Orchestration), step 5 -- Compare.
Bead: buyer-lae (blocks buyer-8ih).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models.deals import QuoteResponse

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SupplyPathInfo:
    """Fee data for a supply path (seller + intermediaries).

    Attributes:
        seller_id: Identifier of the seller.
        intermediary_fee_pct: Percentage fee taken by intermediaries
            (SSP take-rate, exchange fees, etc.).  Expressed as a
            percentage of CPM (e.g. 5.0 means 5%).
        tech_fee_cpm: Flat per-mille fee added by ad-tech platforms
            (data fees, verification fees, etc.) in currency units.
    """

    seller_id: str
    intermediary_fee_pct: float = 0.0
    tech_fee_cpm: float = 0.0


@dataclass
class NormalizedQuote:
    """A quote normalized for cross-seller comparison.

    Attributes:
        seller_id: Which seller provided this quote.
        quote_id: The original quote identifier.
        raw_cpm: The CPM as quoted by the seller (final_cpm from the
            quote response, after any tier/volume discounts the seller
            already applied).  None when pricing is unavailable.
        effective_cpm: The true cost-per-mille after deal-type
            adjustment and estimated fees.  None when pricing is
            unavailable.
        deal_type: Deal type string (PG, PD, PA).
        fee_estimate: Estimated intermediary + tech fees added to
            raw_cpm, in currency units per mille.
        minimum_spend: Minimum budget commitment for this deal.
        score: Composite ranking score (0-100, higher is better).
        fill_rate_estimate: Optional fill-rate from seller availability
            data, if provided.
        pricing_source: Provenance of the pricing value.  One of
            "seller_quoted", "negotiated", or "unavailable".
    """

    seller_id: str
    quote_id: str
    raw_cpm: float | None
    effective_cpm: float | None
    deal_type: str
    fee_estimate: float
    minimum_spend: float
    score: float
    fill_rate_estimate: float | None = None
    pricing_source: str = "seller_quoted"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# PA (Private Auction) floor CPMs typically clear 15-25% above the
# floor.  We use 20% as a conservative estimate for normalization.
_PA_CLEARING_MARKUP_PCT = 20.0

# Scoring weights (must sum to 1.0)
_WEIGHT_EFFECTIVE_CPM = 0.60
_WEIGHT_DEAL_TYPE = 0.20
_WEIGHT_FILL_RATE = 0.20

# Deal-type quality bonuses (out of 100 for the deal-type component).
# PG is most valuable (guaranteed delivery), PD next, PA least.
_DEAL_TYPE_SCORES: dict[str, float] = {
    "PG": 100.0,
    "PD": 70.0,
    "PA": 40.0,
}

# Default fill-rate assumption when the seller doesn't report one.
_DEFAULT_FILL_RATE = 0.70


# ---------------------------------------------------------------------------
# QuoteNormalizer
# ---------------------------------------------------------------------------


class QuoteNormalizer:
    """Normalizes and ranks quotes from different sellers.

    Usage::

        normalizer = QuoteNormalizer(supply_paths={
            "seller-a": SupplyPathInfo(seller_id="seller-a",
                                       intermediary_fee_pct=5.0,
                                       tech_fee_cpm=0.50),
        })

        nq = normalizer.normalize_quote(quote_response, deal_type="PD")

        ranked = normalizer.compare_quotes([
            (quote_a, "PD"),
            (quote_b, "PG"),
        ])
    """

    def __init__(
        self,
        supply_paths: dict[str, SupplyPathInfo] | None = None,
    ) -> None:
        """Initialize the normalizer.

        Args:
            supply_paths: Optional mapping of seller_id to supply-path
                fee information.  When provided, intermediary and tech
                fees are folded into the effective CPM.  When absent,
                fee_estimate will be zero for all quotes.
        """
        self._supply_paths: dict[str, SupplyPathInfo] = supply_paths or {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def normalize_quote(
        self,
        quote: QuoteResponse,
        deal_type: str,
        minimum_spend: float = 0.0,
    ) -> NormalizedQuote:
        """Normalize a single quote into a comparable form.

        Args:
            quote: Raw QuoteResponse from a seller.
            deal_type: Deal type string ("PG", "PD", or "PA").
            minimum_spend: Minimum budget commitment for this deal,
                in currency units.  Defaults to 0.

        Returns:
            NormalizedQuote with effective CPM and a preliminary score.
            When the quote has no pricing (final_cpm is None), returns
            an unpriced NormalizedQuote with pricing_source="unavailable".
        """
        raw_cpm = quote.pricing.final_cpm
        seller_id = quote.seller_id or "unknown"
        quote_id = quote.quote_id

        # Short-circuit: when final_cpm is None the seller has not
        # provided pricing.  Return an unpriced NormalizedQuote instead
        # of crashing on arithmetic with None.
        if raw_cpm is None:
            fill_rate: float | None = None
            if quote.availability and quote.availability.estimated_fill_rate is not None:
                fill_rate = quote.availability.estimated_fill_rate

            return NormalizedQuote(
                seller_id=seller_id,
                quote_id=quote_id,
                raw_cpm=None,
                effective_cpm=None,
                deal_type=deal_type,
                fee_estimate=0.0,
                minimum_spend=minimum_spend,
                score=0.0,
                fill_rate_estimate=fill_rate,
                pricing_source="unavailable",
            )

        # Step 1: Deal-type adjustment
        adjusted_cpm = self._apply_deal_type_adjustment(raw_cpm, deal_type)

        # Step 2: Supply-path fee estimation
        fee_estimate = self._estimate_fees(seller_id, raw_cpm)

        # Step 3: Effective CPM = adjusted CPM + fees
        effective_cpm = adjusted_cpm + fee_estimate

        # Step 4: Extract fill-rate if available
        fill_rate = None
        if quote.availability and quote.availability.estimated_fill_rate is not None:
            fill_rate = quote.availability.estimated_fill_rate

        # Step 5: Compute preliminary score (will be re-scored in
        # compare_quotes when we know the full set of quotes)
        score = self._score_quote(effective_cpm, deal_type, fill_rate)

        return NormalizedQuote(
            seller_id=seller_id,
            quote_id=quote_id,
            raw_cpm=raw_cpm,
            effective_cpm=effective_cpm,
            deal_type=deal_type,
            fee_estimate=fee_estimate,
            minimum_spend=minimum_spend,
            score=score,
            fill_rate_estimate=fill_rate,
            pricing_source="seller_quoted",
        )

    def compare_quotes(
        self,
        quotes: list[tuple[QuoteResponse, str]],
        minimum_spends: dict[str, float] | None = None,
    ) -> list[NormalizedQuote]:
        """Normalize and rank multiple quotes.

        Unpriced quotes (pricing_source="unavailable") are separated from
        the ranked set and appended at the end so they do not interfere
        with the relative scoring of priced quotes.

        Args:
            quotes: List of (QuoteResponse, deal_type) tuples.
            minimum_spends: Optional mapping of quote_id to minimum
                spend amounts.

        Returns:
            List of NormalizedQuote sorted by score descending
            (best quote first), with unpriced quotes appended at the end.
        """
        if not quotes:
            return []

        minimum_spends = minimum_spends or {}

        # Normalize all quotes
        priced: list[NormalizedQuote] = []
        unpriced: list[NormalizedQuote] = []
        for quote, deal_type in quotes:
            min_spend = minimum_spends.get(quote.quote_id, 0.0)
            nq = self.normalize_quote(quote, deal_type, minimum_spend=min_spend)
            if nq.pricing_source == "unavailable":
                unpriced.append(nq)
            else:
                priced.append(nq)

        # Re-score relative to the set (best effective CPM gets highest
        # CPM sub-score) — only for priced quotes
        if len(priced) > 1:
            self._rescore_relative(priced)

        # Sort priced by score descending (best first)
        priced.sort(key=lambda nq: nq.score, reverse=True)

        # Append unpriced quotes at the end
        return priced + unpriced

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_deal_type_adjustment(self, raw_cpm: float, deal_type: str) -> float:
        """Adjust CPM based on deal type.

        PG and PD: no adjustment (price is the price).
        PA: floor CPM is marked up by the expected clearing markup.
        """
        if deal_type == "PA":
            return raw_cpm * (1 + _PA_CLEARING_MARKUP_PCT / 100)
        return raw_cpm

    def _estimate_fees(self, seller_id: str, raw_cpm: float) -> float:
        """Estimate intermediary + tech fees for a seller.

        Returns fee amount in currency units per mille.
        """
        sp = self._supply_paths.get(seller_id)
        if sp is None:
            return 0.0

        intermediary_fee = raw_cpm * (sp.intermediary_fee_pct / 100)
        return intermediary_fee + sp.tech_fee_cpm

    def _score_quote(
        self,
        effective_cpm: float,
        deal_type: str,
        fill_rate: float | None,
    ) -> float:
        """Compute a composite score (0-100, higher = better).

        Components:
          - CPM sub-score (lower CPM = higher score)
          - Deal-type sub-score (PG > PD > PA)
          - Fill-rate sub-score (higher fill = higher score)
        """
        # CPM sub-score: map CPM to 0-100 using a reference range.
        # Assume CPMs between $0 and $50 span the full range.
        cpm_score = max(0.0, 100.0 - (effective_cpm * 2.0))
        cpm_score = min(100.0, cpm_score)

        # Deal-type sub-score
        dt_score = _DEAL_TYPE_SCORES.get(deal_type, 50.0)

        # Fill-rate sub-score
        fr = fill_rate if fill_rate is not None else _DEFAULT_FILL_RATE
        fr_score = fr * 100.0  # 0-100

        # Weighted composite
        composite = (
            _WEIGHT_EFFECTIVE_CPM * cpm_score
            + _WEIGHT_DEAL_TYPE * dt_score
            + _WEIGHT_FILL_RATE * fr_score
        )
        return round(min(100.0, max(0.0, composite)), 2)

    def _rescore_relative(self, quotes: list[NormalizedQuote]) -> None:
        """Re-score quotes relative to the best in the set.

        Instead of using an absolute CPM scale, the best effective CPM
        in the set gets a CPM sub-score of 100, and others are scaled
        relative to it.  This gives better differentiation within a
        competitive set.

        Mutates the quotes in place.
        """
        effective_cpms = [nq.effective_cpm for nq in quotes]
        min_cpm = min(effective_cpms)
        max_cpm = max(effective_cpms)
        cpm_range = max_cpm - min_cpm

        for nq in quotes:
            # CPM sub-score: best CPM = 100, worst = 0
            if cpm_range > 0:
                cpm_score = 100.0 * (1.0 - (nq.effective_cpm - min_cpm) / cpm_range)
            else:
                cpm_score = 100.0

            dt_score = _DEAL_TYPE_SCORES.get(nq.deal_type, 50.0)

            fr = nq.fill_rate_estimate if nq.fill_rate_estimate is not None else _DEFAULT_FILL_RATE
            fr_score = fr * 100.0

            composite = (
                _WEIGHT_EFFECTIVE_CPM * cpm_score
                + _WEIGHT_DEAL_TYPE * dt_score
                + _WEIGHT_FILL_RATE * fr_score
            )
            nq.score = round(min(100.0, max(0.0, composite)), 2)
