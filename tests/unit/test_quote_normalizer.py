# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for QuoteNormalizer -- normalizes quotes from different sellers
for apples-to-apples comparison.

Covers:
- Single quote normalization (PG, PD, PA deal types)
- Fee estimation from supply chain data
- Minimum spend requirement handling
- Multi-quote comparison and ranking
- Edge cases: missing data, zero CPM, identical scores
"""

import pytest

from ad_buyer.booking.quote_normalizer import (
    NormalizedQuote,
    QuoteNormalizer,
    SupplyPathInfo,
)
from ad_buyer.models.deals import (
    AvailabilityInfo,
    PricingInfo,
    ProductInfo,
    QuoteResponse,
    TermsInfo,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_quote(
    *,
    quote_id: str = "q-001",
    seller_id: str = "seller-a",
    deal_type: str = "PD",
    base_cpm: float = 10.0,
    final_cpm: float = 10.0,
    impressions: int | None = 500_000,
    flight_start: str | None = "2026-04-01",
    flight_end: str | None = "2026-04-30",
    fill_rate: float | None = None,
    minimum_spend: float | None = None,
) -> QuoteResponse:
    """Helper to build a QuoteResponse with sensible defaults."""
    availability = None
    if fill_rate is not None:
        availability = AvailabilityInfo(
            inventory_available=True,
            estimated_fill_rate=fill_rate,
        )

    quote = QuoteResponse(
        quote_id=quote_id,
        status="available",
        product=ProductInfo(
            product_id=f"prod-{seller_id}",
            name=f"Package from {seller_id}",
        ),
        pricing=PricingInfo(
            base_cpm=base_cpm,
            final_cpm=final_cpm,
        ),
        terms=TermsInfo(
            impressions=impressions,
            flight_start=flight_start,
            flight_end=flight_end,
            guaranteed=(deal_type == "PG"),
        ),
        availability=availability,
        seller_id=seller_id,
        buyer_tier="agency",
    )
    # Attach deal_type as extra context that the normalizer needs.
    # The QuoteResponse model itself doesn't have a deal_type field,
    # so we pass it alongside the quote via the normalizer API.
    return quote


@pytest.fixture
def normalizer() -> QuoteNormalizer:
    """Default normalizer with no supply-path data."""
    return QuoteNormalizer()


@pytest.fixture
def normalizer_with_supply_paths() -> QuoteNormalizer:
    """Normalizer pre-loaded with supply-path fee data."""
    supply_paths = {
        "seller-a": SupplyPathInfo(
            seller_id="seller-a",
            intermediary_fee_pct=5.0,
            tech_fee_cpm=0.50,
        ),
        "seller-b": SupplyPathInfo(
            seller_id="seller-b",
            intermediary_fee_pct=12.0,
            tech_fee_cpm=1.00,
        ),
    }
    return QuoteNormalizer(supply_paths=supply_paths)


# ---------------------------------------------------------------------------
# NormalizedQuote dataclass tests
# ---------------------------------------------------------------------------


class TestNormalizedQuoteModel:
    """Verify the NormalizedQuote data structure."""

    def test_required_fields(self):
        """NormalizedQuote has all specified fields."""
        nq = NormalizedQuote(
            seller_id="seller-a",
            quote_id="q-001",
            raw_cpm=10.0,
            effective_cpm=11.5,
            deal_type="PD",
            fee_estimate=1.5,
            minimum_spend=0.0,
            score=87.5,
        )
        assert nq.seller_id == "seller-a"
        assert nq.quote_id == "q-001"
        assert nq.raw_cpm == 10.0
        assert nq.effective_cpm == 11.5
        assert nq.deal_type == "PD"
        assert nq.fee_estimate == 1.5
        assert nq.minimum_spend == 0.0
        assert nq.score == 87.5

    def test_optional_fill_rate(self):
        """fill_rate_estimate is optional and defaults to None."""
        nq = NormalizedQuote(
            seller_id="seller-a",
            quote_id="q-001",
            raw_cpm=10.0,
            effective_cpm=10.0,
            deal_type="PG",
            fee_estimate=0.0,
            minimum_spend=0.0,
            score=100.0,
        )
        assert nq.fill_rate_estimate is None

    def test_fill_rate_present(self):
        """fill_rate_estimate can be set."""
        nq = NormalizedQuote(
            seller_id="seller-a",
            quote_id="q-001",
            raw_cpm=10.0,
            effective_cpm=10.0,
            deal_type="PG",
            fee_estimate=0.0,
            minimum_spend=0.0,
            score=100.0,
            fill_rate_estimate=0.95,
        )
        assert nq.fill_rate_estimate == 0.95


# ---------------------------------------------------------------------------
# Single-quote normalization
# ---------------------------------------------------------------------------


class TestNormalizeSingleQuote:
    """Test normalize_quote() for individual quotes."""

    def test_preferred_deal_no_fees(self, normalizer: QuoteNormalizer):
        """PD quote with no supply-path data returns raw CPM as effective."""
        quote = _make_quote(deal_type="PD", final_cpm=12.0)
        result = normalizer.normalize_quote(quote, deal_type="PD")

        assert result.raw_cpm == 12.0
        assert result.deal_type == "PD"
        assert result.fee_estimate == 0.0
        # With no fees, effective CPM equals raw CPM
        assert result.effective_cpm == 12.0

    def test_pg_deal_type_premium(self, normalizer: QuoteNormalizer):
        """PG quotes carry no premium (they already have guaranteed pricing)."""
        quote = _make_quote(deal_type="PG", final_cpm=15.0)
        result = normalizer.normalize_quote(quote, deal_type="PG")

        assert result.deal_type == "PG"
        assert result.raw_cpm == 15.0
        # PG has guaranteed delivery -- no deal-type adjustment needed
        assert result.effective_cpm == 15.0

    def test_pa_deal_type_adjustment(self, normalizer: QuoteNormalizer):
        """PA (private auction) floor CPMs are adjusted upward since actual
        clearing price is typically higher than the floor."""
        quote = _make_quote(deal_type="PA", final_cpm=8.0)
        result = normalizer.normalize_quote(quote, deal_type="PA")

        assert result.deal_type == "PA"
        assert result.raw_cpm == 8.0
        # PA effective CPM should be higher than raw due to auction markup
        assert result.effective_cpm > result.raw_cpm

    def test_supply_path_fees_applied(
        self, normalizer_with_supply_paths: QuoteNormalizer
    ):
        """When supply-path data exists, fees are added to effective CPM."""
        quote = _make_quote(
            seller_id="seller-a", deal_type="PD", final_cpm=10.0
        )
        result = normalizer_with_supply_paths.normalize_quote(
            quote, deal_type="PD"
        )

        assert result.raw_cpm == 10.0
        # seller-a: 5% intermediary + $0.50 tech fee
        # fee = 10.0 * 0.05 + 0.50 = 1.0
        assert result.fee_estimate == pytest.approx(1.0, abs=0.01)
        assert result.effective_cpm == pytest.approx(11.0, abs=0.01)

    def test_high_fee_seller(
        self, normalizer_with_supply_paths: QuoteNormalizer
    ):
        """High-fee seller has higher effective CPM even with same raw CPM."""
        quote = _make_quote(
            seller_id="seller-b", deal_type="PD", final_cpm=10.0
        )
        result = normalizer_with_supply_paths.normalize_quote(
            quote, deal_type="PD"
        )

        # seller-b: 12% intermediary + $1.00 tech fee
        # fee = 10.0 * 0.12 + 1.00 = 2.20
        assert result.fee_estimate == pytest.approx(2.20, abs=0.01)
        assert result.effective_cpm == pytest.approx(12.20, abs=0.01)

    def test_minimum_spend_from_terms(self, normalizer: QuoteNormalizer):
        """Minimum spend is computed from guaranteed impressions * CPM."""
        quote = _make_quote(
            deal_type="PG", final_cpm=20.0, impressions=1_000_000
        )
        result = normalizer.normalize_quote(
            quote, deal_type="PG", minimum_spend=10_000.0
        )

        assert result.minimum_spend == 10_000.0

    def test_minimum_spend_default_zero(self, normalizer: QuoteNormalizer):
        """If no minimum spend provided, defaults to 0."""
        quote = _make_quote(deal_type="PD", final_cpm=10.0)
        result = normalizer.normalize_quote(quote, deal_type="PD")

        assert result.minimum_spend == 0.0

    def test_fill_rate_from_availability(self, normalizer: QuoteNormalizer):
        """Fill rate from availability data is included in NormalizedQuote."""
        quote = _make_quote(deal_type="PD", final_cpm=10.0, fill_rate=0.85)
        result = normalizer.normalize_quote(quote, deal_type="PD")

        assert result.fill_rate_estimate == 0.85

    def test_seller_id_propagated(self, normalizer: QuoteNormalizer):
        """Seller ID from the quote is carried through."""
        quote = _make_quote(seller_id="seller-xyz", deal_type="PD")
        result = normalizer.normalize_quote(quote, deal_type="PD")

        assert result.seller_id == "seller-xyz"

    def test_quote_id_propagated(self, normalizer: QuoteNormalizer):
        """Quote ID from the quote is carried through."""
        quote = _make_quote(quote_id="q-unique-123", deal_type="PD")
        result = normalizer.normalize_quote(quote, deal_type="PD")

        assert result.quote_id == "q-unique-123"

    def test_unknown_seller_no_supply_path(
        self, normalizer_with_supply_paths: QuoteNormalizer
    ):
        """A seller not in supply_paths gets zero fee estimate."""
        quote = _make_quote(
            seller_id="seller-unknown", deal_type="PD", final_cpm=10.0
        )
        result = normalizer_with_supply_paths.normalize_quote(
            quote, deal_type="PD"
        )

        assert result.fee_estimate == 0.0
        assert result.effective_cpm == 10.0


# ---------------------------------------------------------------------------
# Multi-quote comparison
# ---------------------------------------------------------------------------


class TestCompareQuotes:
    """Test compare_quotes() ranking logic."""

    def test_lower_effective_cpm_ranks_higher(
        self, normalizer: QuoteNormalizer
    ):
        """Quotes with lower effective CPM rank higher (lower is better)."""
        quotes = [
            (
                _make_quote(
                    quote_id="q-expensive",
                    seller_id="seller-a",
                    deal_type="PD",
                    final_cpm=20.0,
                ),
                "PD",
            ),
            (
                _make_quote(
                    quote_id="q-cheap",
                    seller_id="seller-b",
                    deal_type="PD",
                    final_cpm=10.0,
                ),
                "PD",
            ),
        ]
        ranked = normalizer.compare_quotes(quotes)

        assert len(ranked) == 2
        assert ranked[0].quote_id == "q-cheap"
        assert ranked[1].quote_id == "q-expensive"
        # Higher score = better
        assert ranked[0].score > ranked[1].score

    def test_pg_vs_pd_same_cpm(self, normalizer: QuoteNormalizer):
        """PG (guaranteed) scores higher than PD at the same CPM because
        guaranteed delivery is more valuable."""
        quotes = [
            (
                _make_quote(
                    quote_id="q-pd",
                    seller_id="seller-a",
                    deal_type="PD",
                    final_cpm=10.0,
                ),
                "PD",
            ),
            (
                _make_quote(
                    quote_id="q-pg",
                    seller_id="seller-b",
                    deal_type="PG",
                    final_cpm=10.0,
                ),
                "PG",
            ),
        ]
        ranked = normalizer.compare_quotes(quotes)

        # PG should rank higher due to guaranteed delivery bonus
        assert ranked[0].quote_id == "q-pg"

    def test_fee_aware_ranking(
        self, normalizer_with_supply_paths: QuoteNormalizer
    ):
        """A seller with lower raw CPM but higher fees may rank lower."""
        quotes = [
            # seller-a: raw $11, fee 5% + $0.50 => eff $12.05
            (
                _make_quote(
                    quote_id="q-a",
                    seller_id="seller-a",
                    deal_type="PD",
                    final_cpm=11.0,
                ),
                "PD",
            ),
            # seller-b: raw $11, fee 12% + $1.00 => eff $13.32
            (
                _make_quote(
                    quote_id="q-b",
                    seller_id="seller-b",
                    deal_type="PD",
                    final_cpm=11.0,
                ),
                "PD",
            ),
        ]
        ranked = normalizer_with_supply_paths.compare_quotes(quotes)

        # seller-a should rank higher (lower effective CPM)
        assert ranked[0].quote_id == "q-a"
        assert ranked[0].effective_cpm < ranked[1].effective_cpm

    def test_empty_list_returns_empty(self, normalizer: QuoteNormalizer):
        """Comparing an empty list returns an empty list."""
        ranked = normalizer.compare_quotes([])
        assert ranked == []

    def test_single_quote_returns_single(self, normalizer: QuoteNormalizer):
        """A single quote still gets normalized and returned."""
        quotes = [
            (
                _make_quote(
                    quote_id="q-only",
                    seller_id="seller-a",
                    deal_type="PD",
                    final_cpm=10.0,
                ),
                "PD",
            ),
        ]
        ranked = normalizer.compare_quotes(quotes)

        assert len(ranked) == 1
        assert ranked[0].quote_id == "q-only"

    def test_fill_rate_affects_ranking(self, normalizer: QuoteNormalizer):
        """A quote with better fill rate scores higher when CPMs are close."""
        quotes = [
            (
                _make_quote(
                    quote_id="q-low-fill",
                    seller_id="seller-a",
                    deal_type="PD",
                    final_cpm=10.0,
                    fill_rate=0.40,
                ),
                "PD",
            ),
            (
                _make_quote(
                    quote_id="q-high-fill",
                    seller_id="seller-b",
                    deal_type="PD",
                    final_cpm=10.0,
                    fill_rate=0.95,
                ),
                "PD",
            ),
        ]
        ranked = normalizer.compare_quotes(quotes)

        assert ranked[0].quote_id == "q-high-fill"

    def test_ranked_list_all_have_scores(self, normalizer: QuoteNormalizer):
        """Every quote in the ranked list has a score between 0 and 100."""
        quotes = [
            (
                _make_quote(
                    quote_id=f"q-{i}",
                    seller_id=f"seller-{i}",
                    deal_type="PD",
                    final_cpm=10.0 + i,
                ),
                "PD",
            )
            for i in range(5)
        ]
        ranked = normalizer.compare_quotes(quotes)

        assert len(ranked) == 5
        for nq in ranked:
            assert 0.0 <= nq.score <= 100.0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and error handling."""

    def test_zero_cpm_quote(self, normalizer: QuoteNormalizer):
        """A quote with zero CPM should normalize without error."""
        quote = _make_quote(deal_type="PD", final_cpm=0.0, base_cpm=0.0)
        result = normalizer.normalize_quote(quote, deal_type="PD")

        assert result.raw_cpm == 0.0
        assert result.effective_cpm == 0.0

    def test_no_availability_data(self, normalizer: QuoteNormalizer):
        """Quote with no availability info normalizes with None fill rate."""
        quote = _make_quote(deal_type="PD", final_cpm=10.0, fill_rate=None)
        result = normalizer.normalize_quote(quote, deal_type="PD")

        assert result.fill_rate_estimate is None

    def test_no_impressions_in_terms(self, normalizer: QuoteNormalizer):
        """Quote with no impressions in terms still normalizes."""
        quote = _make_quote(
            deal_type="PD", final_cpm=10.0, impressions=None
        )
        result = normalizer.normalize_quote(quote, deal_type="PD")

        assert result.raw_cpm == 10.0

    def test_no_seller_id(self, normalizer: QuoteNormalizer):
        """Quote with None seller_id uses 'unknown' as seller_id."""
        quote = _make_quote(deal_type="PD", final_cpm=10.0)
        quote.seller_id = None
        result = normalizer.normalize_quote(quote, deal_type="PD")

        assert result.seller_id == "unknown"

    def test_pa_with_supply_path_fees(
        self, normalizer_with_supply_paths: QuoteNormalizer
    ):
        """PA quote with supply path fees compounds both adjustments."""
        quote = _make_quote(
            seller_id="seller-a", deal_type="PA", final_cpm=10.0
        )
        result = normalizer_with_supply_paths.normalize_quote(
            quote, deal_type="PA"
        )

        # PA markup applied to raw CPM, then fees added
        assert result.effective_cpm > 10.0
        assert result.fee_estimate > 0.0
        assert result.deal_type == "PA"

    def test_very_high_cpm(self, normalizer: QuoteNormalizer):
        """Extremely high CPM normalizes correctly."""
        quote = _make_quote(deal_type="PG", final_cpm=500.0)
        result = normalizer.normalize_quote(quote, deal_type="PG")

        assert result.raw_cpm == 500.0
        assert result.effective_cpm == 500.0


# ---------------------------------------------------------------------------
# SupplyPathInfo model tests
# ---------------------------------------------------------------------------


class TestSupplyPathInfo:
    """Verify SupplyPathInfo data structure."""

    def test_required_fields(self):
        """SupplyPathInfo has required fields."""
        sp = SupplyPathInfo(
            seller_id="seller-a",
            intermediary_fee_pct=5.0,
            tech_fee_cpm=0.50,
        )
        assert sp.seller_id == "seller-a"
        assert sp.intermediary_fee_pct == 5.0
        assert sp.tech_fee_cpm == 0.50

    def test_defaults(self):
        """Defaults to zero fees."""
        sp = SupplyPathInfo(seller_id="seller-a")
        assert sp.intermediary_fee_pct == 0.0
        assert sp.tech_fee_cpm == 0.0
