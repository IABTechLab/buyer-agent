# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for Linear TV models (Option C hybrid approach).

Covers LinearTVParams, LinearTVQuoteDetails, CancellationTerms,
MakegoodRequest, CancellationRequest, and extensions to
QuoteRequest/QuoteResponse/PricingInfo/TermsInfo for linear TV support.

Tests written first (TDD) per bead buyer-6io.
"""

import pytest

from ad_buyer.models.linear_tv import (
    CancellationRequest,
    CancellationTerms,
    LinearTVParams,
    LinearTVQuoteDetails,
    MakegoodRequest,
)
from ad_buyer.models.deals import (
    PricingInfo,
    QuoteRequest,
    QuoteResponse,
    TermsInfo,
)


# ---------------------------------------------------------------------------
# LinearTVParams tests
# ---------------------------------------------------------------------------


class TestLinearTVParams:
    """Test the LinearTVParams model (nested in QuoteRequest.linear_tv)."""

    def test_minimal_params(self):
        """LinearTVParams with only required field (target_demo)."""
        params = LinearTVParams(target_demo="A18-49")
        assert params.target_demo == "A18-49"
        assert params.spot_length == 30  # default
        assert params.measurement_currency == "nielsen"  # default
        assert params.rotation == "ros"  # default
        assert params.grps_requested is None
        assert params.dayparts is None
        assert params.networks is None
        assert params.dmas is None
        assert params.target_cpp is None

    def test_full_params(self):
        """LinearTVParams with all fields populated."""
        params = LinearTVParams(
            target_demo="A25-54",
            grps_requested=200,
            dayparts=["primetime", "late_night"],
            networks=["NBC", "CBS", "ESPN"],
            dmas=["501", "803"],
            spot_length=60,
            target_cpp=45000.0,
            measurement_currency="comscore",
            rotation="fixed",
        )
        assert params.target_demo == "A25-54"
        assert params.grps_requested == 200
        assert params.dayparts == ["primetime", "late_night"]
        assert params.networks == ["NBC", "CBS", "ESPN"]
        assert params.dmas == ["501", "803"]
        assert params.spot_length == 60
        assert params.target_cpp == 45000.0
        assert params.measurement_currency == "comscore"
        assert params.rotation == "fixed"

    def test_national_buy_no_dmas(self):
        """National buy has no DMAs specified (None means national)."""
        params = LinearTVParams(target_demo="HH")
        assert params.dmas is None

    def test_spot_length_options(self):
        """Spot length accepts standard values: 15, 30, 60."""
        for length in [15, 30, 60]:
            params = LinearTVParams(target_demo="A18-49", spot_length=length)
            assert params.spot_length == length

    def test_rotation_options(self):
        """Rotation accepts standard values."""
        for rotation in ["ros", "fixed", "program_specific"]:
            params = LinearTVParams(target_demo="A18-49", rotation=rotation)
            assert params.rotation == rotation

    def test_measurement_currency_options(self):
        """Measurement currency accepts supported values."""
        for currency in ["nielsen", "comscore", "videoamp"]:
            params = LinearTVParams(
                target_demo="A18-49", measurement_currency=currency
            )
            assert params.measurement_currency == currency

    def test_serialization_excludes_none(self):
        """model_dump(exclude_none=True) omits unset optional fields."""
        params = LinearTVParams(target_demo="A18-49", grps_requested=100)
        data = params.model_dump(exclude_none=True)
        assert "target_demo" in data
        assert "grps_requested" in data
        assert "dayparts" not in data
        assert "networks" not in data
        assert "dmas" not in data
        assert "target_cpp" not in data

    def test_target_demo_required(self):
        """target_demo is required."""
        with pytest.raises(Exception):
            LinearTVParams()


# ---------------------------------------------------------------------------
# CancellationTerms tests
# ---------------------------------------------------------------------------


class TestCancellationTerms:
    """Test the CancellationTerms model."""

    def test_basic_cancellation(self):
        """Basic scatter cancellation terms."""
        terms = CancellationTerms(
            notice_days=14,
            cancellable_pct=1.0,
        )
        assert terms.notice_days == 14
        assert terms.cancellable_pct == 1.0
        assert terms.deadline is None
        assert terms.force_majeure is True  # default

    def test_upfront_cancellation(self):
        """Upfront cancellation with partial cancel and deadline."""
        terms = CancellationTerms(
            notice_days=90,
            cancellable_pct=0.5,
            deadline="2026-09-30",
            force_majeure=True,
        )
        assert terms.notice_days == 90
        assert terms.cancellable_pct == 0.5
        assert terms.deadline == "2026-09-30"


# ---------------------------------------------------------------------------
# LinearTVQuoteDetails tests
# ---------------------------------------------------------------------------


class TestLinearTVQuoteDetails:
    """Test the LinearTVQuoteDetails model (nested in QuoteResponse.linear_tv)."""

    def test_full_quote_details(self):
        """LinearTVQuoteDetails with all fields."""
        details = LinearTVQuoteDetails(
            target_demo="A18-49",
            estimated_grps=200.0,
            estimated_rating=5.2,
            cpp=45000.0,
            dayparts=["primetime"],
            networks=["NBC", "CBS"],
            spots_per_week=10,
            total_spots=40,
            spot_length=30,
            measurement_currency="nielsen",
            audience_estimate={
                "demo": "A18-49",
                "universe": 130000000,
                "impressions_equiv": 65000000,
            },
        )
        assert details.target_demo == "A18-49"
        assert details.estimated_grps == 200.0
        assert details.estimated_rating == 5.2
        assert details.cpp == 45000.0
        assert details.dayparts == ["primetime"]
        assert details.networks == ["NBC", "CBS"]
        assert details.spots_per_week == 10
        assert details.total_spots == 40
        assert details.spot_length == 30
        assert details.measurement_currency == "nielsen"
        assert details.audience_estimate["universe"] == 130000000

    def test_optional_cancellation_terms(self):
        """Cancellation terms are optional."""
        details = LinearTVQuoteDetails(
            target_demo="A18-49",
            estimated_grps=100.0,
            estimated_rating=3.0,
            cpp=30000.0,
            dayparts=["daytime"],
            networks=["CBS"],
            spots_per_week=5,
            total_spots=20,
            spot_length=30,
            measurement_currency="nielsen",
            audience_estimate={"demo": "A18-49", "universe": 130000000},
        )
        assert details.cancellation_terms is None
        assert details.makegood_policy is None

    def test_with_cancellation_and_makegood(self):
        """Quote details with cancellation terms and makegood policy."""
        details = LinearTVQuoteDetails(
            target_demo="A25-54",
            estimated_grps=300.0,
            estimated_rating=6.5,
            cpp=55000.0,
            dayparts=["primetime", "late_night"],
            networks=["NBC"],
            spots_per_week=15,
            total_spots=60,
            spot_length=30,
            measurement_currency="nielsen",
            audience_estimate={
                "demo": "A25-54",
                "universe": 120000000,
                "impressions_equiv": 90000000,
            },
            cancellation_terms=CancellationTerms(
                notice_days=14,
                cancellable_pct=1.0,
            ),
            makegood_policy="standard",
        )
        assert details.cancellation_terms.notice_days == 14
        assert details.makegood_policy == "standard"


# ---------------------------------------------------------------------------
# QuoteRequest extensions for linear TV
# ---------------------------------------------------------------------------


class TestQuoteRequestLinearTV:
    """Test QuoteRequest with media_type and linear_tv extensions."""

    def test_default_media_type_is_digital(self):
        """Default media_type is 'digital' for backward compat."""
        req = QuoteRequest(product_id="ctv-premium-sports", deal_type="PD")
        assert req.media_type == "digital"

    def test_linear_tv_media_type(self):
        """QuoteRequest with media_type='linear_tv' and linear_tv params."""
        req = QuoteRequest(
            product_id="linear-primetime-nbc",
            deal_type="scatter",
            media_type="linear_tv",
            flight_start="2026-04-01",
            flight_end="2026-04-30",
            linear_tv=LinearTVParams(
                target_demo="A18-49",
                grps_requested=200,
                dayparts=["primetime"],
                networks=["NBC"],
                dmas=["501"],
                target_cpp=50000.0,
            ),
        )
        assert req.media_type == "linear_tv"
        assert req.linear_tv is not None
        assert req.linear_tv.target_demo == "A18-49"
        assert req.linear_tv.grps_requested == 200

    def test_digital_request_no_linear_tv(self):
        """Digital request has no linear_tv params."""
        req = QuoteRequest(
            product_id="ctv-premium-sports",
            deal_type="PD",
            impressions=5000000,
            target_cpm=28.00,
        )
        assert req.linear_tv is None
        assert req.media_type == "digital"

    def test_linear_tv_serialization(self):
        """Linear TV request serializes correctly with nested params."""
        req = QuoteRequest(
            product_id="linear-primetime-cbs",
            deal_type="scatter",
            media_type="linear_tv",
            linear_tv=LinearTVParams(
                target_demo="A25-54",
                grps_requested=150,
                dayparts=["primetime"],
            ),
        )
        data = req.model_dump(exclude_none=True)
        assert data["media_type"] == "linear_tv"
        assert data["linear_tv"]["target_demo"] == "A25-54"
        assert data["linear_tv"]["grps_requested"] == 150


# ---------------------------------------------------------------------------
# QuoteResponse extensions for linear TV
# ---------------------------------------------------------------------------


class TestQuoteResponseLinearTV:
    """Test QuoteResponse with media_type and linear_tv extensions."""

    def test_digital_response_backward_compat(self):
        """Existing digital QuoteResponse still works (backward compat)."""
        resp = QuoteResponse(
            quote_id="qt-abc123",
            status="available",
            product={"product_id": "ctv-premium", "name": "Premium CTV"},
            pricing={
                "base_cpm": 35.0,
                "final_cpm": 28.26,
                "pricing_model": "cpm",
            },
            terms={"impressions": 5000000},
        )
        assert resp.media_type == "digital"
        assert resp.linear_tv is None

    def test_linear_tv_response(self):
        """QuoteResponse with linear TV details."""
        resp = QuoteResponse(
            quote_id="qt-ltv-001",
            status="available",
            product={
                "product_id": "linear-primetime-nbc",
                "name": "NBC Primetime",
            },
            pricing={
                "base_cpm": 0.0,
                "final_cpm": 0.0,
                "pricing_model": "cpp",
                "base_cpp": 50000.0,
                "final_cpp": 45000.0,
            },
            terms={
                "grps": 200,
                "guaranteed_grps": 180,
                "target_demo": "A18-49",
            },
            media_type="linear_tv",
            linear_tv={
                "target_demo": "A18-49",
                "estimated_grps": 200.0,
                "estimated_rating": 5.2,
                "cpp": 45000.0,
                "dayparts": ["primetime"],
                "networks": ["NBC"],
                "spots_per_week": 10,
                "total_spots": 40,
                "spot_length": 30,
                "measurement_currency": "nielsen",
                "audience_estimate": {
                    "demo": "A18-49",
                    "universe": 130000000,
                },
            },
        )
        assert resp.media_type == "linear_tv"
        assert resp.linear_tv is not None
        assert resp.linear_tv.cpp == 45000.0
        assert resp.linear_tv.estimated_grps == 200.0


# ---------------------------------------------------------------------------
# PricingInfo extensions for CPP
# ---------------------------------------------------------------------------


class TestPricingInfoCPP:
    """Test PricingInfo with CPP pricing model."""

    def test_existing_cpm_pricing_unchanged(self):
        """Existing CPM pricing continues to work."""
        pricing = PricingInfo(base_cpm=35.0, final_cpm=28.26)
        assert pricing.pricing_model == "cpm"
        assert pricing.base_cpp is None
        assert pricing.final_cpp is None

    def test_cpp_pricing(self):
        """CPP pricing model with base_cpp and final_cpp."""
        pricing = PricingInfo(
            base_cpm=0.0,
            final_cpm=0.0,
            pricing_model="cpp",
            base_cpp=50000.0,
            final_cpp=45000.0,
        )
        assert pricing.pricing_model == "cpp"
        assert pricing.base_cpp == 50000.0
        assert pricing.final_cpp == 45000.0

    def test_hybrid_pricing(self):
        """Hybrid pricing with both CPM and CPP."""
        pricing = PricingInfo(
            base_cpm=25.0,
            final_cpm=22.0,
            pricing_model="hybrid",
            base_cpp=40000.0,
            final_cpp=36000.0,
        )
        assert pricing.pricing_model == "hybrid"
        assert pricing.base_cpm == 25.0
        assert pricing.base_cpp == 40000.0


# ---------------------------------------------------------------------------
# TermsInfo extensions for linear TV
# ---------------------------------------------------------------------------


class TestTermsInfoLinearTV:
    """Test TermsInfo with linear TV extensions."""

    def test_existing_digital_terms_unchanged(self):
        """Existing digital terms work without GRP fields."""
        terms = TermsInfo(impressions=5000000, flight_start="2026-04-01")
        assert terms.grps is None
        assert terms.guaranteed_grps is None
        assert terms.target_demo is None

    def test_linear_tv_terms(self):
        """Terms with GRP-based volume and audience guarantee."""
        terms = TermsInfo(
            grps=200,
            guaranteed_grps=180,
            target_demo="A18-49",
            flight_start="2026-04-01",
            flight_end="2026-04-30",
        )
        assert terms.grps == 200
        assert terms.guaranteed_grps == 180
        assert terms.target_demo == "A18-49"


# ---------------------------------------------------------------------------
# MakegoodRequest tests
# ---------------------------------------------------------------------------


class TestMakegoodRequest:
    """Test the MakegoodRequest model for POST /deals/{id}/makegoods."""

    def test_makegood_request(self):
        """MakegoodRequest with all fields."""
        req = MakegoodRequest(
            shortfall_grps=30.0,
            original_daypart="primetime",
            target_demo="A18-49",
            preferred_dayparts=["primetime", "late_night"],
            notes="Q1 underdelivery on NBC Thursday",
        )
        assert req.shortfall_grps == 30.0
        assert req.original_daypart == "primetime"
        assert req.target_demo == "A18-49"
        assert req.preferred_dayparts == ["primetime", "late_night"]
        assert req.notes == "Q1 underdelivery on NBC Thursday"

    def test_makegood_request_minimal(self):
        """MakegoodRequest with only required fields."""
        req = MakegoodRequest(
            shortfall_grps=15.0,
            original_daypart="daytime",
            target_demo="A25-54",
        )
        assert req.preferred_dayparts is None
        assert req.notes is None


# ---------------------------------------------------------------------------
# CancellationRequest tests
# ---------------------------------------------------------------------------


class TestCancellationRequest:
    """Test the CancellationRequest for POST /deals/{id}/cancel."""

    def test_full_cancellation(self):
        """Full cancellation request (100%)."""
        req = CancellationRequest(
            cancel_pct=1.0,
            reason="Campaign budget cut",
        )
        assert req.cancel_pct == 1.0
        assert req.reason == "Campaign budget cut"
        assert req.effective_date is None

    def test_partial_cancellation(self):
        """Partial cancellation (30%)."""
        req = CancellationRequest(
            cancel_pct=0.3,
            reason="Reduced Q2 budget",
            effective_date="2026-06-01",
        )
        assert req.cancel_pct == 0.3
        assert req.effective_date == "2026-06-01"


# ---------------------------------------------------------------------------
# CPM <-> CPP conversion utility tests
# ---------------------------------------------------------------------------


class TestCPMCPPConversion:
    """Test CPM to CPP conversion for cross-media comparison."""

    def test_cpp_to_cpm(self):
        """Convert CPP to CPM equivalent.

        Formula: CPM = CPP * 1000 / (universe * target_pct / 1000)
        Simplified: CPM = (CPP / universe_per_grp) * 1000
        Where universe_per_grp = universe * 0.01 (1 GRP = 1% of universe)
        So: CPM = CPP / (universe * 0.01) * 1000 = CPP * 100000 / universe
        """
        from ad_buyer.models.linear_tv import cpp_to_cpm

        # A18-49 universe ~130M, CPP = $50,000
        # 1 GRP reaches 1% of 130M = 1,300,000 people
        # CPM = $50,000 / 1,300,000 * 1000 = $38.46
        cpm = cpp_to_cpm(cpp=50000.0, universe_size=130000000)
        assert abs(cpm - 38.46) < 0.01

    def test_cpm_to_cpp(self):
        """Convert CPM to CPP equivalent."""
        from ad_buyer.models.linear_tv import cpm_to_cpp

        # Reverse: CPP = CPM * universe * 0.01 / 1000 = CPM * universe / 100000
        cpp = cpm_to_cpp(cpm=38.46, universe_size=130000000)
        assert abs(cpp - 50000.0) < 100  # within $100

    def test_roundtrip_conversion(self):
        """CPP -> CPM -> CPP should roundtrip."""
        from ad_buyer.models.linear_tv import cpp_to_cpm, cpm_to_cpp

        original_cpp = 45000.0
        universe = 120000000
        cpm = cpp_to_cpm(cpp=original_cpp, universe_size=universe)
        recovered_cpp = cpm_to_cpp(cpm=cpm, universe_size=universe)
        assert abs(recovered_cpp - original_cpp) < 0.01
