# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for PricingCalculator - extracted pricing logic.

These tests verify that the PricingCalculator produces identical results
to the duplicated pricing logic previously in:
- unified_client.py (get_pricing, request_deal)
- tools/dsp/request_deal.py (_create_deal_response)
- tools/dsp/get_pricing.py (_format_pricing)
"""

import pytest

from ad_buyer.booking.pricing import PricingCalculator, PricingResult
from ad_buyer.models.buyer_identity import AccessTier


class TestPricingCalculatorTierDiscounts:
    """Test tier-based discount calculations."""

    def test_public_tier_no_discount(self):
        """Public tier gets 0% discount."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=20.0,
            tier=AccessTier.PUBLIC,
            tier_discount=0.0,
        )
        assert result.tiered_price == 20.0
        assert result.tier_discount == 0.0

    def test_seat_tier_5_percent_discount(self):
        """Seat tier gets 5% discount."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=20.0,
            tier=AccessTier.SEAT,
            tier_discount=5.0,
        )
        assert result.tiered_price == 19.0
        assert result.tier_discount == 5.0

    def test_agency_tier_10_percent_discount(self):
        """Agency tier gets 10% discount."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=20.0,
            tier=AccessTier.AGENCY,
            tier_discount=10.0,
        )
        assert result.tiered_price == 18.0
        assert result.tier_discount == 10.0

    def test_advertiser_tier_15_percent_discount(self):
        """Advertiser tier gets 15% discount."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=20.0,
            tier=AccessTier.ADVERTISER,
            tier_discount=15.0,
        )
        assert result.tiered_price == 17.0
        assert result.tier_discount == 15.0


class TestPricingCalculatorVolumeDiscounts:
    """Test volume discount calculations."""

    def test_no_volume_discount_below_5m(self):
        """No volume discount under 5 million impressions."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=20.0,
            tier=AccessTier.AGENCY,
            tier_discount=10.0,
            volume=4_999_999,
        )
        assert result.volume_discount == 0.0
        assert result.final_price == 18.0

    def test_5_percent_volume_discount_at_5m(self):
        """5% volume discount at 5 million impressions for agency tier."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=20.0,
            tier=AccessTier.AGENCY,
            tier_discount=10.0,
            volume=5_000_000,
        )
        assert result.volume_discount == 5.0
        assert result.final_price == pytest.approx(17.1)

    def test_10_percent_volume_discount_at_10m(self):
        """10% volume discount at 10 million impressions for agency tier."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=20.0,
            tier=AccessTier.AGENCY,
            tier_discount=10.0,
            volume=10_000_000,
        )
        assert result.volume_discount == 10.0
        assert result.final_price == 16.2

    def test_advertiser_volume_discount_at_5m(self):
        """Advertiser tier also gets volume discounts."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=20.0,
            tier=AccessTier.ADVERTISER,
            tier_discount=15.0,
            volume=5_000_000,
        )
        assert result.volume_discount == 5.0
        assert result.final_price == 16.15

    def test_public_tier_no_volume_discount_even_at_10m(self):
        """Public tier does NOT get volume discounts regardless of volume."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=20.0,
            tier=AccessTier.PUBLIC,
            tier_discount=0.0,
            volume=10_000_000,
        )
        assert result.volume_discount == 0.0
        assert result.final_price == 20.0

    def test_seat_tier_no_volume_discount_even_at_10m(self):
        """Seat tier does NOT get volume discounts regardless of volume."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=20.0,
            tier=AccessTier.SEAT,
            tier_discount=5.0,
            volume=10_000_000,
        )
        assert result.volume_discount == 0.0
        assert result.final_price == 19.0

    def test_no_volume_discount_when_volume_is_none(self):
        """No volume discount when volume is not provided."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=20.0,
            tier=AccessTier.AGENCY,
            tier_discount=10.0,
            volume=None,
        )
        assert result.volume_discount == 0.0
        assert result.final_price == 18.0


class TestPricingCalculatorNegotiation:
    """Test negotiation logic."""

    def test_negotiation_accepted_above_floor(self):
        """Target CPM is accepted when above floor price (90% of tiered)."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=20.0,
            tier=AccessTier.AGENCY,
            tier_discount=10.0,
            target_cpm=17.0,
            can_negotiate=True,
            negotiation_enabled=True,
        )
        assert result.final_price == 17.0

    def test_negotiation_countered_at_floor(self):
        """Target CPM below floor results in counter at floor price."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=20.0,
            tier=AccessTier.AGENCY,
            tier_discount=10.0,
            target_cpm=15.0,
            can_negotiate=True,
            negotiation_enabled=True,
        )
        assert result.final_price == 16.2

    def test_negotiation_not_available_when_cannot_negotiate(self):
        """Negotiation skipped when buyer cannot negotiate."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=20.0,
            tier=AccessTier.SEAT,
            tier_discount=5.0,
            target_cpm=15.0,
            can_negotiate=False,
            negotiation_enabled=True,
        )
        assert result.final_price == 19.0

    def test_negotiation_not_available_when_product_not_negotiable(self):
        """Negotiation skipped when product doesn't support negotiation."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=20.0,
            tier=AccessTier.AGENCY,
            tier_discount=10.0,
            target_cpm=15.0,
            can_negotiate=True,
            negotiation_enabled=False,
        )
        assert result.final_price == 18.0


class TestPricingCalculatorEdgeCases:
    """Test edge cases and data integrity."""

    def test_zero_base_price_produces_zero_output(self):
        """Zero base price correctly produces zero pricing output."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=0,
            tier=AccessTier.AGENCY,
            tier_discount=10.0,
        )
        assert result.tiered_price == 0.0
        assert result.final_price == 0.0

    def test_pricing_result_fields(self):
        """Verify all PricingResult fields are populated."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=25.0,
            tier=AccessTier.ADVERTISER,
            tier_discount=15.0,
            volume=10_000_000,
        )
        assert result.base_price == 25.0
        assert result.tier == AccessTier.ADVERTISER
        assert result.tier_discount == 15.0
        assert result.volume_discount == 10.0
        assert result.tiered_price == 21.25
        assert result.final_price == 19.125
        assert result.requested_volume == 10_000_000

    def test_combined_tier_and_volume_discount(self):
        """Combined tier + volume discount matches existing behavior."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=30.0,
            tier=AccessTier.ADVERTISER,
            tier_discount=15.0,
            volume=7_000_000,
        )
        assert result.tiered_price == 25.5
        assert result.volume_discount == 5.0
        assert result.final_price == pytest.approx(24.225)

    def test_rounding_final_price(self):
        """PricingResult provides rounded final price for deal creation."""
        calc = PricingCalculator()
        result = calc.calculate(
            base_price=30.0,
            tier=AccessTier.ADVERTISER,
            tier_discount=15.0,
            volume=7_000_000,
        )
        assert round(result.final_price, 2) == 24.22
