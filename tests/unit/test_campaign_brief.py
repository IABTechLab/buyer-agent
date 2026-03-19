# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the campaign brief JSON schema and parser.

Tests the CampaignBrief Pydantic model and the parse_campaign_brief
function that validates raw JSON/dict input and returns a structured
CampaignBrief object.

bead: buyer-80k
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from ad_buyer.models.campaign_brief import (
    ApprovalConfig,
    ApprovalStage,
    BrandSafety,
    CampaignBrief,
    CampaignObjective,
    ChannelAllocation,
    ChannelType,
    FrequencyCap,
    GeoTarget,
    GeoType,
    KPI,
    KPIMetric,
    PacingModel,
    parse_campaign_brief,
)


# ---------------------------------------------------------------------------
# Helpers — reusable minimal valid brief data
# ---------------------------------------------------------------------------


def _minimal_brief_data() -> dict:
    """Return the smallest valid campaign brief as a dict."""
    return {
        "advertiser_id": "adv-rivian-001",
        "campaign_name": "Rivian R2 Launch",
        "objective": "AWARENESS",
        "total_budget": 500000.00,
        "currency": "USD",
        "flight_start": str(date.today() + timedelta(days=7)),
        "flight_end": str(date.today() + timedelta(days=49)),
        "channels": [
            {"channel": "CTV", "budget_pct": 60.0},
            {"channel": "DISPLAY", "budget_pct": 40.0},
        ],
        "target_audience": ["auto_intenders", "iab-607"],
    }


def _full_brief_data() -> dict:
    """Return a campaign brief with all optional fields populated."""
    data = _minimal_brief_data()
    data.update(
        {
            "agency_id": "agency-apex-001",
            "description": "Launch campaign for the Rivian R2 electric SUV.",
            "target_geo": [
                {"geo_type": "COUNTRY", "geo_value": "US"},
                {"geo_type": "DMA", "geo_value": "501"},  # NYC
            ],
            "kpis": [
                {"metric": "CPCV", "target_value": 0.05},
                {"metric": "CTR", "target_value": 0.012},
            ],
            "brand_safety": {
                "excluded_categories": ["IAB25-3", "IAB26"],
                "excluded_keywords": ["violence", "gambling"],
            },
            "frequency_cap": {
                "max_impressions": 3,
                "period_hours": 24,
            },
            "pacing_model": "EVEN",
            "preferred_sellers": ["seller-espn-001", "seller-hulu-002"],
            "excluded_sellers": ["seller-sketchy-999"],
            "creative_ids": ["cr-vid-30s-001", "cr-banner-300x250-002"],
            "approval_config": {
                "plan_review": True,
                "booking": True,
                "creative": False,
                "pacing_adjustment": False,
            },
            "deal_preferences": {
                "preferred_deal_types": ["PG", "PD"],
                "max_cpm": 25.00,
                "min_impressions": 10000,
            },
            "exclusion_list": ["competitor-brand-x.com", "competitor-brand-y.com"],
            "notes": "High-priority launch. CEO wants daily pacing updates.",
        }
    )
    # Add format preferences to channels
    data["channels"][0]["format_prefs"] = ["VAST_4_2", "30s"]
    data["channels"][1]["format_prefs"] = ["300x250", "728x90"]
    return data


# ===========================================================================
# Test class: Required field validation
# ===========================================================================


class TestRequiredFields:
    """Verify that all required fields are enforced."""

    def test_minimal_valid_brief_parses(self):
        """A brief with only required fields should parse successfully."""
        data = _minimal_brief_data()
        brief = CampaignBrief(**data)

        assert brief.advertiser_id == "adv-rivian-001"
        assert brief.campaign_name == "Rivian R2 Launch"
        assert brief.objective == CampaignObjective.AWARENESS
        assert brief.total_budget == 500000.00
        assert brief.currency == "USD"
        assert len(brief.channels) == 2
        assert len(brief.target_audience) == 2

    def test_missing_advertiser_id_rejected(self):
        """Brief without advertiser_id must fail validation."""
        data = _minimal_brief_data()
        del data["advertiser_id"]
        with pytest.raises(ValidationError) as exc_info:
            CampaignBrief(**data)
        assert "advertiser_id" in str(exc_info.value)

    def test_missing_campaign_name_rejected(self):
        """Brief without campaign_name must fail validation."""
        data = _minimal_brief_data()
        del data["campaign_name"]
        with pytest.raises(ValidationError) as exc_info:
            CampaignBrief(**data)
        assert "campaign_name" in str(exc_info.value)

    def test_missing_objective_rejected(self):
        """Brief without objective must fail validation."""
        data = _minimal_brief_data()
        del data["objective"]
        with pytest.raises(ValidationError) as exc_info:
            CampaignBrief(**data)
        assert "objective" in str(exc_info.value)

    def test_missing_total_budget_rejected(self):
        """Brief without total_budget must fail validation."""
        data = _minimal_brief_data()
        del data["total_budget"]
        with pytest.raises(ValidationError) as exc_info:
            CampaignBrief(**data)
        assert "total_budget" in str(exc_info.value)

    def test_missing_currency_rejected(self):
        """Brief without currency must fail validation."""
        data = _minimal_brief_data()
        del data["currency"]
        with pytest.raises(ValidationError) as exc_info:
            CampaignBrief(**data)
        assert "currency" in str(exc_info.value)

    def test_missing_flight_start_rejected(self):
        """Brief without flight_start must fail validation."""
        data = _minimal_brief_data()
        del data["flight_start"]
        with pytest.raises(ValidationError) as exc_info:
            CampaignBrief(**data)
        assert "flight_start" in str(exc_info.value)

    def test_missing_flight_end_rejected(self):
        """Brief without flight_end must fail validation."""
        data = _minimal_brief_data()
        del data["flight_end"]
        with pytest.raises(ValidationError) as exc_info:
            CampaignBrief(**data)
        assert "flight_end" in str(exc_info.value)

    def test_missing_channels_rejected(self):
        """Brief without channels must fail validation."""
        data = _minimal_brief_data()
        del data["channels"]
        with pytest.raises(ValidationError) as exc_info:
            CampaignBrief(**data)
        assert "channels" in str(exc_info.value)

    def test_empty_channels_rejected(self):
        """Brief with empty channels list must fail validation."""
        data = _minimal_brief_data()
        data["channels"] = []
        with pytest.raises(ValidationError) as exc_info:
            CampaignBrief(**data)
        # min_length=1 should trigger
        assert "channels" in str(exc_info.value)

    def test_missing_target_audience_rejected(self):
        """Brief without target_audience must fail validation."""
        data = _minimal_brief_data()
        del data["target_audience"]
        with pytest.raises(ValidationError) as exc_info:
            CampaignBrief(**data)
        assert "target_audience" in str(exc_info.value)

    def test_empty_target_audience_rejected(self):
        """Brief with empty target_audience must fail validation."""
        data = _minimal_brief_data()
        data["target_audience"] = []
        with pytest.raises(ValidationError) as exc_info:
            CampaignBrief(**data)
        assert "target_audience" in str(exc_info.value)


# ===========================================================================
# Test class: Budget and financial validation
# ===========================================================================


class TestBudgetValidation:
    """Validate budget-related constraints."""

    def test_negative_budget_rejected(self):
        """Budget must be positive."""
        data = _minimal_brief_data()
        data["total_budget"] = -1000.00
        with pytest.raises(ValidationError) as exc_info:
            CampaignBrief(**data)
        assert "total_budget" in str(exc_info.value)

    def test_zero_budget_rejected(self):
        """Budget must be > 0."""
        data = _minimal_brief_data()
        data["total_budget"] = 0.0
        with pytest.raises(ValidationError) as exc_info:
            CampaignBrief(**data)
        assert "total_budget" in str(exc_info.value)

    def test_currency_must_be_three_chars(self):
        """Currency must be a 3-letter ISO 4217 code."""
        data = _minimal_brief_data()
        data["currency"] = "US"  # too short
        with pytest.raises(ValidationError) as exc_info:
            CampaignBrief(**data)
        assert "currency" in str(exc_info.value)

    def test_currency_four_chars_rejected(self):
        """Currency must be exactly 3 chars."""
        data = _minimal_brief_data()
        data["currency"] = "USDD"
        with pytest.raises(ValidationError) as exc_info:
            CampaignBrief(**data)
        assert "currency" in str(exc_info.value)

    def test_valid_currency_accepted(self):
        """Standard ISO 4217 codes are accepted."""
        for code in ["USD", "EUR", "GBP", "JPY"]:
            data = _minimal_brief_data()
            data["currency"] = code
            brief = CampaignBrief(**data)
            assert brief.currency == code


# ===========================================================================
# Test class: Channel allocation validation
# ===========================================================================


class TestChannelValidation:
    """Validate channel allocation constraints."""

    def test_valid_channel_types(self):
        """All supported channel types should be accepted."""
        for ch in ["CTV", "DISPLAY", "AUDIO", "NATIVE", "DOOH", "LINEAR_TV"]:
            data = _minimal_brief_data()
            data["channels"] = [{"channel": ch, "budget_pct": 100.0}]
            brief = CampaignBrief(**data)
            assert brief.channels[0].channel == ChannelType(ch)

    def test_invalid_channel_type_rejected(self):
        """Unknown channel type should be rejected."""
        data = _minimal_brief_data()
        data["channels"] = [{"channel": "SMOKE_SIGNAL", "budget_pct": 100.0}]
        with pytest.raises(ValidationError):
            CampaignBrief(**data)

    def test_channel_budget_pct_must_sum_to_100(self):
        """Channel budget percentages must sum to 100."""
        data = _minimal_brief_data()
        data["channels"] = [
            {"channel": "CTV", "budget_pct": 60.0},
            {"channel": "DISPLAY", "budget_pct": 30.0},
            # total = 90, not 100
        ]
        with pytest.raises(ValidationError) as exc_info:
            CampaignBrief(**data)
        assert "100" in str(exc_info.value)

    def test_channel_budget_pct_over_100_rejected(self):
        """Channel budget percentages summing to >100 should be rejected."""
        data = _minimal_brief_data()
        data["channels"] = [
            {"channel": "CTV", "budget_pct": 70.0},
            {"channel": "DISPLAY", "budget_pct": 40.0},
        ]
        with pytest.raises(ValidationError) as exc_info:
            CampaignBrief(**data)
        assert "100" in str(exc_info.value)

    def test_channel_budget_pct_negative_rejected(self):
        """Negative budget percentage should be rejected."""
        data = _minimal_brief_data()
        data["channels"] = [
            {"channel": "CTV", "budget_pct": -10.0},
            {"channel": "DISPLAY", "budget_pct": 110.0},
        ]
        with pytest.raises(ValidationError) as exc_info:
            CampaignBrief(**data)
        assert "budget_pct" in str(exc_info.value)

    def test_channel_budget_pct_zero_rejected(self):
        """Zero budget percentage should be rejected (use > 0)."""
        data = _minimal_brief_data()
        data["channels"] = [
            {"channel": "CTV", "budget_pct": 0.0},
            {"channel": "DISPLAY", "budget_pct": 100.0},
        ]
        with pytest.raises(ValidationError) as exc_info:
            CampaignBrief(**data)
        assert "budget_pct" in str(exc_info.value)

    def test_duplicate_channel_types_rejected(self):
        """Same channel type appearing twice should be rejected."""
        data = _minimal_brief_data()
        data["channels"] = [
            {"channel": "CTV", "budget_pct": 60.0},
            {"channel": "CTV", "budget_pct": 40.0},
        ]
        with pytest.raises(ValidationError) as exc_info:
            CampaignBrief(**data)
        assert "duplicate" in str(exc_info.value).lower()

    def test_channel_budget_amount_computed(self):
        """budget_amount should be computed from total_budget * budget_pct / 100."""
        data = _minimal_brief_data()
        data["total_budget"] = 1000000.00
        data["channels"] = [
            {"channel": "CTV", "budget_pct": 60.0},
            {"channel": "DISPLAY", "budget_pct": 40.0},
        ]
        brief = CampaignBrief(**data)
        assert brief.channels[0].budget_amount == 600000.00
        assert brief.channels[1].budget_amount == 400000.00

    def test_channel_format_prefs_optional(self):
        """format_prefs is optional on a channel."""
        data = _minimal_brief_data()
        brief = CampaignBrief(**data)
        assert brief.channels[0].format_prefs == []


# ===========================================================================
# Test class: Date validation
# ===========================================================================


class TestDateValidation:
    """Validate flight date constraints."""

    def test_flight_end_before_start_rejected(self):
        """flight_end must not be before flight_start."""
        data = _minimal_brief_data()
        data["flight_start"] = str(date.today() + timedelta(days=30))
        data["flight_end"] = str(date.today() + timedelta(days=7))
        with pytest.raises(ValidationError) as exc_info:
            CampaignBrief(**data)
        assert "flight_end" in str(exc_info.value) or "flight" in str(
            exc_info.value
        ).lower()

    def test_flight_end_same_as_start_rejected(self):
        """flight_end must be after flight_start (not the same day)."""
        data = _minimal_brief_data()
        same_day = str(date.today() + timedelta(days=7))
        data["flight_start"] = same_day
        data["flight_end"] = same_day
        with pytest.raises(ValidationError) as exc_info:
            CampaignBrief(**data)
        assert "flight" in str(exc_info.value).lower()

    def test_valid_date_range_accepted(self):
        """Valid future date range should be accepted."""
        data = _minimal_brief_data()
        brief = CampaignBrief(**data)
        assert brief.flight_start < brief.flight_end


# ===========================================================================
# Test class: Objective enum
# ===========================================================================


class TestObjectiveEnum:
    """Validate campaign objective values."""

    def test_all_valid_objectives(self):
        """All defined objectives should be accepted."""
        for obj in ["AWARENESS", "CONSIDERATION", "CONVERSION", "REACH"]:
            data = _minimal_brief_data()
            data["objective"] = obj
            brief = CampaignBrief(**data)
            assert brief.objective == CampaignObjective(obj)

    def test_invalid_objective_rejected(self):
        """Unknown objective should be rejected."""
        data = _minimal_brief_data()
        data["objective"] = "PROFIT"
        with pytest.raises(ValidationError):
            CampaignBrief(**data)

    def test_case_insensitive_objective(self):
        """Objective should accept case variations."""
        data = _minimal_brief_data()
        data["objective"] = "awareness"
        brief = CampaignBrief(**data)
        assert brief.objective == CampaignObjective.AWARENESS


# ===========================================================================
# Test class: Optional fields
# ===========================================================================


class TestOptionalFields:
    """Verify optional fields work correctly with defaults."""

    def test_optional_fields_have_defaults(self):
        """Optional fields should default to None or empty list."""
        data = _minimal_brief_data()
        brief = CampaignBrief(**data)

        assert brief.agency_id is None
        assert brief.description is None
        assert brief.target_geo == []
        assert brief.kpis == []
        assert brief.brand_safety is None
        assert brief.frequency_cap is None
        assert brief.pacing_model == PacingModel.EVEN
        assert brief.preferred_sellers == []
        assert brief.excluded_sellers == []
        assert brief.creative_ids == []
        assert brief.approval_config is not None  # has defaults
        assert brief.deal_preferences is None
        assert brief.exclusion_list == []
        assert brief.notes is None

    def test_full_brief_with_all_optional_fields(self):
        """A brief with all fields populated should parse correctly."""
        data = _full_brief_data()
        brief = CampaignBrief(**data)

        assert brief.agency_id == "agency-apex-001"
        assert brief.description is not None
        assert len(brief.target_geo) == 2
        assert len(brief.kpis) == 2
        assert brief.brand_safety is not None
        assert brief.frequency_cap is not None
        assert brief.pacing_model == PacingModel.EVEN
        assert len(brief.preferred_sellers) == 2
        assert len(brief.excluded_sellers) == 1
        assert len(brief.creative_ids) == 2
        assert brief.notes is not None


# ===========================================================================
# Test class: KPI validation
# ===========================================================================


class TestKPIValidation:
    """Validate KPI definitions."""

    def test_valid_kpi_metrics(self):
        """All supported KPI metrics should be accepted."""
        for metric in ["CPM", "CPC", "CPCV", "CTR", "VCR", "ROAS", "GRP"]:
            kpi = KPI(metric=metric, target_value=1.0)
            assert kpi.metric == KPIMetric(metric)

    def test_invalid_kpi_metric_rejected(self):
        """Unknown KPI metric should be rejected."""
        with pytest.raises(ValidationError):
            KPI(metric="MAGIC", target_value=1.0)

    def test_kpi_target_value_must_be_positive(self):
        """KPI target_value must be > 0."""
        with pytest.raises(ValidationError):
            KPI(metric="CPM", target_value=-5.0)

    def test_kpi_zero_target_rejected(self):
        """KPI target_value of 0 should be rejected."""
        with pytest.raises(ValidationError):
            KPI(metric="CPM", target_value=0.0)


# ===========================================================================
# Test class: Geographic targeting
# ===========================================================================


class TestGeoTargeting:
    """Validate geographic targeting sub-model."""

    def test_valid_geo_types(self):
        """All supported geo types should be accepted."""
        for geo_type in ["COUNTRY", "STATE", "DMA", "METRO", "ZIP"]:
            geo = GeoTarget(geo_type=geo_type, geo_value="US")
            assert geo.geo_type == GeoType(geo_type)

    def test_invalid_geo_type_rejected(self):
        """Unknown geo type should be rejected."""
        with pytest.raises(ValidationError):
            GeoTarget(geo_type="PLANET", geo_value="Earth")


# ===========================================================================
# Test class: Brand safety
# ===========================================================================


class TestBrandSafety:
    """Validate brand safety sub-model."""

    def test_brand_safety_with_categories(self):
        """Brand safety can specify excluded IAB content categories."""
        bs = BrandSafety(excluded_categories=["IAB25-3", "IAB26"])
        assert len(bs.excluded_categories) == 2

    def test_brand_safety_with_keywords(self):
        """Brand safety can specify excluded keywords."""
        bs = BrandSafety(excluded_keywords=["violence", "gambling"])
        assert len(bs.excluded_keywords) == 2

    def test_brand_safety_empty_is_valid(self):
        """Brand safety with empty lists is valid (no restrictions)."""
        bs = BrandSafety()
        assert bs.excluded_categories == []
        assert bs.excluded_keywords == []


# ===========================================================================
# Test class: Frequency cap
# ===========================================================================


class TestFrequencyCap:
    """Validate frequency cap sub-model."""

    def test_valid_frequency_cap(self):
        """Standard frequency cap should be accepted."""
        fc = FrequencyCap(max_impressions=3, period_hours=24)
        assert fc.max_impressions == 3
        assert fc.period_hours == 24

    def test_frequency_cap_zero_impressions_rejected(self):
        """max_impressions must be > 0."""
        with pytest.raises(ValidationError):
            FrequencyCap(max_impressions=0, period_hours=24)

    def test_frequency_cap_zero_period_rejected(self):
        """period_hours must be > 0."""
        with pytest.raises(ValidationError):
            FrequencyCap(max_impressions=3, period_hours=0)


# ===========================================================================
# Test class: Approval config (D-3)
# ===========================================================================


class TestApprovalConfig:
    """Validate approval_config per D-3: configurable per campaign."""

    def test_default_approval_config(self):
        """Default: plan_review=True, booking=True, creative=False, pacing=False."""
        config = ApprovalConfig()
        assert config.plan_review is True
        assert config.booking is True
        assert config.creative is False
        assert config.pacing_adjustment is False

    def test_fully_automated_config(self):
        """All gates disabled for fully automated execution."""
        config = ApprovalConfig(
            plan_review=False,
            booking=False,
            creative=False,
            pacing_adjustment=False,
        )
        assert config.plan_review is False
        assert config.booking is False

    def test_all_gates_enabled(self):
        """All gates can be enabled for maximum oversight."""
        config = ApprovalConfig(
            plan_review=True,
            booking=True,
            creative=True,
            pacing_adjustment=True,
        )
        assert config.pacing_adjustment is True

    def test_approval_stages_list(self):
        """approval_stages() should return list of stages requiring approval."""
        config = ApprovalConfig(
            plan_review=True,
            booking=True,
            creative=False,
            pacing_adjustment=False,
        )
        stages = config.approval_stages()
        assert ApprovalStage.PLAN_REVIEW in stages
        assert ApprovalStage.BOOKING in stages
        assert ApprovalStage.CREATIVE not in stages
        assert ApprovalStage.PACING_ADJUSTMENT not in stages


# ===========================================================================
# Test class: Pacing model
# ===========================================================================


class TestPacingModel:
    """Validate pacing model enum and defaults."""

    def test_valid_pacing_models(self):
        """All supported pacing models should be accepted."""
        for model in ["EVEN", "FRONT_LOADED", "BACK_LOADED", "CUSTOM"]:
            data = _minimal_brief_data()
            data["pacing_model"] = model
            brief = CampaignBrief(**data)
            assert brief.pacing_model == PacingModel(model)

    def test_default_pacing_is_even(self):
        """Default pacing model should be EVEN."""
        data = _minimal_brief_data()
        brief = CampaignBrief(**data)
        assert brief.pacing_model == PacingModel.EVEN

    def test_invalid_pacing_model_rejected(self):
        """Unknown pacing model should be rejected."""
        data = _minimal_brief_data()
        data["pacing_model"] = "RANDOM"
        with pytest.raises(ValidationError):
            CampaignBrief(**data)


# ===========================================================================
# Test class: Brief parser function
# ===========================================================================


class TestBriefParser:
    """Test the parse_campaign_brief function."""

    def test_parse_valid_dict(self):
        """Parser should accept a valid dict and return CampaignBrief."""
        data = _minimal_brief_data()
        brief = parse_campaign_brief(data)
        assert isinstance(brief, CampaignBrief)
        assert brief.advertiser_id == "adv-rivian-001"

    def test_parse_valid_json_string(self):
        """Parser should accept a valid JSON string and return CampaignBrief."""
        import json

        data = _minimal_brief_data()
        json_str = json.dumps(data)
        brief = parse_campaign_brief(json_str)
        assert isinstance(brief, CampaignBrief)
        assert brief.advertiser_id == "adv-rivian-001"

    def test_parse_invalid_json_string_raises(self):
        """Parser should raise ValueError for malformed JSON."""
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_campaign_brief("{not valid json")

    def test_parse_invalid_data_raises_validation_error(self):
        """Parser should raise ValidationError for invalid brief data."""
        with pytest.raises(ValidationError):
            parse_campaign_brief({"advertiser_id": "x"})

    def test_parse_full_brief(self):
        """Parser should handle a full brief with all optional fields."""
        data = _full_brief_data()
        brief = parse_campaign_brief(data)
        assert isinstance(brief, CampaignBrief)
        assert brief.agency_id == "agency-apex-001"
        assert len(brief.kpis) == 2
        assert brief.brand_safety is not None


# ===========================================================================
# Test class: JSON Schema export
# ===========================================================================


class TestJSONSchemaExport:
    """Verify the model can produce a valid JSON Schema document."""

    def test_json_schema_is_dict(self):
        """model_json_schema() should return a dict."""
        schema = CampaignBrief.model_json_schema()
        assert isinstance(schema, dict)

    def test_json_schema_has_required_fields(self):
        """JSON Schema should list all required fields."""
        schema = CampaignBrief.model_json_schema()
        assert "required" in schema
        required = schema["required"]
        for field in [
            "advertiser_id",
            "campaign_name",
            "objective",
            "total_budget",
            "currency",
            "flight_start",
            "flight_end",
            "channels",
            "target_audience",
        ]:
            assert field in required, f"{field} should be in required fields"

    def test_json_schema_has_properties(self):
        """JSON Schema should include properties for all fields."""
        schema = CampaignBrief.model_json_schema()
        assert "properties" in schema
        props = schema["properties"]
        # Check a few key fields exist
        assert "advertiser_id" in props
        assert "total_budget" in props
        assert "channels" in props
        assert "approval_config" in props

    def test_json_schema_title(self):
        """JSON Schema should have a meaningful title."""
        schema = CampaignBrief.model_json_schema()
        assert "title" in schema
        assert "CampaignBrief" in schema["title"]


# ===========================================================================
# Test class: Serialization round-trip
# ===========================================================================


class TestSerialization:
    """Verify model serializes and deserializes cleanly."""

    def test_dict_round_trip(self):
        """Brief should survive dict -> model -> dict -> model."""
        data = _full_brief_data()
        brief1 = CampaignBrief(**data)
        exported = brief1.model_dump(mode="json")
        brief2 = CampaignBrief(**exported)
        assert brief1.advertiser_id == brief2.advertiser_id
        assert brief1.total_budget == brief2.total_budget
        assert len(brief1.channels) == len(brief2.channels)

    def test_json_round_trip(self):
        """Brief should survive model -> JSON -> model."""
        data = _full_brief_data()
        brief1 = CampaignBrief(**data)
        json_str = brief1.model_dump_json()
        brief2 = CampaignBrief.model_validate_json(json_str)
        assert brief1.advertiser_id == brief2.advertiser_id
        assert brief1.total_budget == brief2.total_budget
