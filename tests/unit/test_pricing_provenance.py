# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for CPM hallucination fix — Layer 2b: pricing provenance tracking.

Bead: ar-r76d (child of epic ar-rrgw)

These tests verify that every pricing value in the buyer agent carries
a provenance source (`pricing_source`), and that the system refuses to
produce a CPM when the seller has not provided one.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from ad_buyer.booking.pricing import PricingCalculator, PricingResult, PricingSource
from ad_buyer.booking.quote_normalizer import NormalizedQuote, QuoteNormalizer
from ad_buyer.models.buyer_identity import AccessTier, BuyerContext, BuyerIdentity
from ad_buyer.models.deals import PricingInfo, ProductInfo, QuoteResponse, TermsInfo


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agency_identity():
    """Agency-tier identity for testing."""
    return BuyerIdentity(
        seat_id="ttd-seat-100",
        agency_id="omnicom-200",
        agency_name="OMD",
    )


@pytest.fixture
def agency_context(agency_identity):
    """Agency buyer context."""
    return BuyerContext(identity=agency_identity, is_authenticated=True)


# ---------------------------------------------------------------------------
# 1. PricingSource enum exists on PricingResult
# ---------------------------------------------------------------------------


class TestPricingSourceEnum:
    """PricingResult must carry a pricing_source field with provenance."""

    def test_pricing_source_seller_quoted(self):
        """PricingResult from a valid price has pricing_source=seller_quoted."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=20.0,
            tier=AccessTier.AGENCY,
            tier_discount=10.0,
        )
        assert result.pricing_source == PricingSource.SELLER_QUOTED

    def test_pricing_source_negotiated(self):
        """PricingResult from successful negotiation has pricing_source=negotiated."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=20.0,
            tier=AccessTier.AGENCY,
            tier_discount=10.0,
            target_cpm=17.0,
            can_negotiate=True,
            negotiation_enabled=True,
        )
        assert result.pricing_source == PricingSource.NEGOTIATED

    def test_pricing_source_unavailable_when_base_price_none(self):
        """PricingCalculator with base_price=None returns pricing_source=unavailable."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=None,
            tier=AccessTier.AGENCY,
            tier_discount=10.0,
        )
        assert result.pricing_source == PricingSource.UNAVAILABLE
        assert result.base_price is None
        assert result.final_price is None

    def test_pricing_source_enum_values(self):
        """PricingSource enum has exactly the expected values."""
        assert PricingSource.SELLER_QUOTED.value == "seller_quoted"
        assert PricingSource.NEGOTIATED.value == "negotiated"
        assert PricingSource.UNAVAILABLE.value == "unavailable"


# ---------------------------------------------------------------------------
# 2. PricingCalculator refuses to compute when base_price is None
# ---------------------------------------------------------------------------


class TestPricingCalculatorNullGuard:
    """PricingCalculator must refuse to compute when base_price is None."""

    def test_none_base_price_returns_unavailable_result(self):
        """calculate() with base_price=None returns an unavailable PricingResult."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=None,
            tier=AccessTier.PUBLIC,
            tier_discount=0.0,
        )
        assert result.pricing_source == PricingSource.UNAVAILABLE
        assert result.base_price is None
        assert result.tiered_price is None
        assert result.final_price is None
        assert result.tier_discount == 0.0
        assert result.volume_discount == 0.0

    def test_none_base_price_with_volume_returns_unavailable(self):
        """calculate() with base_price=None and volume still returns unavailable."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=None,
            tier=AccessTier.AGENCY,
            tier_discount=10.0,
            volume=5_000_000,
        )
        assert result.pricing_source == PricingSource.UNAVAILABLE
        assert result.final_price is None

    def test_valid_base_price_still_works(self):
        """calculate() with a valid base_price still produces correct pricing."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=20.0,
            tier=AccessTier.AGENCY,
            tier_discount=10.0,
        )
        assert result.pricing_source == PricingSource.SELLER_QUOTED
        assert result.base_price == 20.0
        assert result.tiered_price == 18.0
        assert result.final_price == 18.0


# ---------------------------------------------------------------------------
# 3. PricingInfo model — base_cpm and final_cpm are Optional[float]
# ---------------------------------------------------------------------------


class TestPricingInfoOptional:
    """PricingInfo.base_cpm and final_cpm must be Optional[float]."""

    def test_pricing_info_accepts_none_base_cpm(self):
        """PricingInfo can be created with base_cpm=None."""
        info = PricingInfo(base_cpm=None, final_cpm=None)
        assert info.base_cpm is None
        assert info.final_cpm is None

    def test_pricing_info_with_values_still_works(self):
        """PricingInfo with float values still works."""
        info = PricingInfo(base_cpm=20.0, final_cpm=18.0)
        assert info.base_cpm == 20.0
        assert info.final_cpm == 18.0

    def test_pricing_info_mixed_none_and_value(self):
        """PricingInfo with base_cpm=None but final_cpm set is valid."""
        info = PricingInfo(base_cpm=None, final_cpm=10.0)
        assert info.base_cpm is None
        assert info.final_cpm == 10.0


# ---------------------------------------------------------------------------
# 4. QuoteNormalizer — short-circuit guard for None pricing
# ---------------------------------------------------------------------------


def _make_quote(
    *,
    quote_id: str = "q-001",
    seller_id: str = "seller-a",
    base_cpm: float | None = 10.0,
    final_cpm: float | None = 10.0,
    fill_rate: float | None = None,
) -> QuoteResponse:
    """Helper to build a QuoteResponse for testing."""
    from ad_buyer.models.deals import AvailabilityInfo

    availability = None
    if fill_rate is not None:
        availability = AvailabilityInfo(
            inventory_available=True,
            estimated_fill_rate=fill_rate,
        )

    return QuoteResponse(
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
            impressions=500_000,
            flight_start="2026-04-01",
            flight_end="2026-04-30",
        ),
        availability=availability,
        seller_id=seller_id,
        buyer_tier="agency",
    )


class TestQuoteNormalizerNullPricing:
    """QuoteNormalizer must short-circuit when quote.pricing.final_cpm is None.

    The arithmetic in normalize_quote() (lines 173-179) would crash on None
    if not guarded. This tests the short-circuit guard.
    """

    def test_null_final_cpm_does_not_crash(self):
        """normalize_quote with final_cpm=None should NOT raise TypeError."""
        normalizer = QuoteNormalizer()
        quote = _make_quote(final_cpm=None, base_cpm=None)
        # Must not raise — should return an NormalizedQuote with
        # pricing_source indicating unavailable
        result = normalizer.normalize_quote(quote, deal_type="PD")
        assert result is not None

    def test_null_final_cpm_returns_unavailable_marker(self):
        """normalize_quote with final_cpm=None returns a NormalizedQuote
        flagged as unpriced."""
        normalizer = QuoteNormalizer()
        quote = _make_quote(final_cpm=None, base_cpm=None)
        result = normalizer.normalize_quote(quote, deal_type="PD")
        # The NormalizedQuote should have pricing_source=unavailable
        assert result.pricing_source == "unavailable"

    def test_null_final_cpm_raw_cpm_is_none(self):
        """normalize_quote with final_cpm=None sets raw_cpm to None."""
        normalizer = QuoteNormalizer()
        quote = _make_quote(final_cpm=None, base_cpm=None)
        result = normalizer.normalize_quote(quote, deal_type="PD")
        assert result.raw_cpm is None

    def test_priced_quote_still_normalizes(self):
        """A normally priced quote still normalizes correctly."""
        normalizer = QuoteNormalizer()
        quote = _make_quote(final_cpm=12.0, base_cpm=15.0)
        result = normalizer.normalize_quote(quote, deal_type="PD")
        assert result.raw_cpm == 12.0
        assert result.effective_cpm == 12.0
        assert result.pricing_source == "seller_quoted"

    def test_compare_quotes_filters_unpriced(self):
        """compare_quotes with a mix of priced and unpriced quotes handles
        both gracefully — unpriced quotes are excluded from ranking."""
        normalizer = QuoteNormalizer()
        quotes = [
            (_make_quote(quote_id="q-priced", seller_id="seller-a",
                         final_cpm=10.0, base_cpm=12.0), "PD"),
            (_make_quote(quote_id="q-unpriced", seller_id="seller-b",
                         final_cpm=None, base_cpm=None), "PD"),
        ]
        ranked = normalizer.compare_quotes(quotes)
        # Unpriced quotes should be separated from the ranked list
        priced = [q for q in ranked if q.pricing_source != "unavailable"]
        unpriced = [q for q in ranked if q.pricing_source == "unavailable"]
        assert len(priced) == 1
        assert priced[0].quote_id == "q-priced"
        assert len(unpriced) == 1
        assert unpriced[0].quote_id == "q-unpriced"


# ---------------------------------------------------------------------------
# 5. multi_seller.py — handle unpriced quotes gracefully
# ---------------------------------------------------------------------------


class TestMultiSellerUnpricedQuotes:
    """Multi-seller orchestration must handle a mix of priced and unpriced quotes."""

    @pytest.mark.asyncio
    async def test_evaluate_and_rank_filters_unpriced(self):
        """evaluate_and_rank should handle unpriced quotes without crashing."""
        from ad_buyer.orchestration.multi_seller import (
            MultiSellerOrchestrator,
            SellerQuoteResult,
        )

        mock_registry = AsyncMock()
        mock_factory = MagicMock()
        normalizer = QuoteNormalizer()

        orchestrator = MultiSellerOrchestrator(
            registry_client=mock_registry,
            deals_client_factory=mock_factory,
            quote_normalizer=normalizer,
            quote_timeout=5.0,
        )

        # One priced, one unpriced
        quote_results = [
            SellerQuoteResult(
                seller_id="seller-a",
                seller_url="http://seller-a.example.com",
                quote=_make_quote(
                    quote_id="q-priced",
                    seller_id="seller-a",
                    final_cpm=10.0,
                    base_cpm=12.0,
                ),
                deal_type="PD",
                error=None,
            ),
            SellerQuoteResult(
                seller_id="seller-b",
                seller_url="http://seller-b.example.com",
                quote=_make_quote(
                    quote_id="q-unpriced",
                    seller_id="seller-b",
                    final_cpm=None,
                    base_cpm=None,
                ),
                deal_type="PD",
                error=None,
            ),
        ]

        # Must not crash
        ranked = await orchestrator.evaluate_and_rank(quote_results)
        # Should have at least the priced quote
        priced = [q for q in ranked if q.pricing_source != "unavailable"]
        assert len(priced) >= 1
        assert priced[0].quote_id == "q-priced"


# ---------------------------------------------------------------------------
# 6. get_pricing tool — guard null pricing
# ---------------------------------------------------------------------------


class TestGetPricingToolNullGuard:
    """get_pricing tool must guard against null pricing from products."""

    @pytest.mark.asyncio
    async def test_no_base_price_shows_unavailable(self):
        """Product with no basePrice should show pricing as unavailable."""
        from ad_buyer.tools.dsp.get_pricing import GetPricingTool

        mock_client = MagicMock()
        mock_client.get_product = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "id": "prod-001",
                    "name": "Premium CTV",
                    "channel": "ctv",
                    # No basePrice
                },
            )
        )

        agency_identity = BuyerIdentity(
            seat_id="ttd-seat-100",
            agency_id="omnicom-200",
            agency_name="OMD",
        )
        buyer_context = BuyerContext(identity=agency_identity, is_authenticated=True)

        tool = GetPricingTool(client=mock_client, buyer_context=buyer_context)
        result = await tool._arun(product_id="prod-001")

        # Should NOT show $0.00 pricing
        assert "$0.00" not in result
        # Should indicate pricing is unavailable
        assert "unavailable" in result.lower() or "no pricing" in result.lower()

    @pytest.mark.asyncio
    async def test_none_base_price_shows_unavailable(self):
        """Product with basePrice=None should show pricing as unavailable."""
        from ad_buyer.tools.dsp.get_pricing import GetPricingTool

        mock_client = MagicMock()
        mock_client.get_product = AsyncMock(
            return_value=MagicMock(
                success=True,
                data={
                    "id": "prod-002",
                    "name": "Premium Display",
                    "basePrice": None,
                },
            )
        )

        agency_identity = BuyerIdentity(
            seat_id="ttd-seat-100",
            agency_id="omnicom-200",
            agency_name="OMD",
        )
        buyer_context = BuyerContext(identity=agency_identity, is_authenticated=True)

        tool = GetPricingTool(client=mock_client, buyer_context=buyer_context)
        result = await tool._arun(product_id="prod-002")

        assert "$0.00" not in result
        assert "unavailable" in result.lower() or "no pricing" in result.lower()


# ---------------------------------------------------------------------------
# 7. QuoteFlowClient — pricing_source propagation
# ---------------------------------------------------------------------------


class TestQuoteFlowPricingSource:
    """QuoteFlowClient must propagate pricing_source from PricingCalculator."""

    def test_get_pricing_with_price_has_seller_quoted(self, agency_context):
        """get_pricing with valid price returns PricingResult with seller_quoted."""
        from ad_buyer.booking.quote_flow import QuoteFlowClient

        client = QuoteFlowClient(
            buyer_context=agency_context,
            seller_base_url="http://localhost:5000",
        )
        product = {"id": "prod-001", "name": "Test", "basePrice": 20.0}
        result = client.get_pricing(product)
        assert result is not None
        assert result.pricing_source == PricingSource.SELLER_QUOTED

    def test_get_pricing_without_price_returns_unavailable(self, agency_context):
        """get_pricing with no price returns None (unchanged from 2a)."""
        from ad_buyer.booking.quote_flow import QuoteFlowClient

        client = QuoteFlowClient(
            buyer_context=agency_context,
            seller_base_url="http://localhost:5000",
        )
        product = {"id": "prod-001", "name": "Test"}
        result = client.get_pricing(product)
        # Layer 2a already returns None; the pricing_source is tracked
        # at PricingCalculator level
        assert result is None

    def test_build_deal_data_no_price_returns_none(self, agency_context):
        """build_deal_data with no pricing returns None (no CPM populated)."""
        from ad_buyer.booking.quote_flow import QuoteFlowClient

        client = QuoteFlowClient(
            buyer_context=agency_context,
            seller_base_url="http://localhost:5000",
        )
        product = {"id": "prod-001", "name": "Test"}
        result = client.build_deal_data(product)
        assert result is None


# ---------------------------------------------------------------------------
# 8. unified_client.py — guard null pricing in get_pricing
# ---------------------------------------------------------------------------


class TestUnifiedClientPricingGuard:
    """unified_client.get_pricing must guard against null base price."""

    @pytest.mark.asyncio
    async def test_get_pricing_no_base_price_no_crash(self):
        """get_pricing on a product with no basePrice should not crash
        and should not populate fabricated pricing."""
        from ad_buyer.clients.unified_client import UnifiedClient, Protocol

        client = UnifiedClient(
            base_url="http://localhost:5000",
            buyer_identity=BuyerIdentity(
                seat_id="ttd-seat-100",
                agency_id="omnicom-200",
                agency_name="OMD",
            ),
        )

        # Mock the get_product call to return a product with no price
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.data = {
            "id": "prod-001",
            "name": "Premium CTV",
            # No basePrice
        }
        client.get_product = AsyncMock(return_value=mock_result)

        result = await client.get_pricing("prod-001")

        # Should succeed but not have fabricated pricing
        assert result.success
        pricing_data = result.data.get("pricing", {})
        # Should NOT have a non-zero base_price from a fabricated fallback
        if pricing_data:
            assert pricing_data.get("pricing_source") == "unavailable"
