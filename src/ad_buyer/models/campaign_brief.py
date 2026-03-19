# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Pydantic models for the campaign brief JSON schema.

The campaign brief is the structured input that drives the Campaign
Automation pipeline.  It specifies what an advertiser wants to achieve
(audience, budget, channels, flight dates, KPIs) and how much human
oversight to apply at each stage (approval_config, per D-3).

Required fields (Section 7.1 of Campaign Automation Strategic Plan):
  advertiser_id, campaign_name, objective, total_budget, currency,
  flight_start, flight_end, channels[], target_audience[]

Optional fields:
  agency_id, description, target_geo[], kpis[], brand_safety,
  frequency_cap, pacing_model, preferred_sellers[], excluded_sellers[],
  creative_ids[], approval_config, deal_preferences, exclusion_list,
  notes

References:
  - Campaign Automation Strategic Plan, Sections 6.1 and 7.1
  - IAB Audience Taxonomy 1.1 (target_audience segment IDs)
  - ISO 4217 (currency codes)
  - bead: buyer-80k
"""

from __future__ import annotations

import json
from datetime import date
from enum import Enum
from typing import Any, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CampaignObjective(str, Enum):
    """Campaign objective types (Section 6.1)."""

    AWARENESS = "AWARENESS"
    CONSIDERATION = "CONSIDERATION"
    CONVERSION = "CONVERSION"
    REACH = "REACH"

    @classmethod
    def _missing_(cls, value: object) -> CampaignObjective | None:
        """Allow case-insensitive lookup."""
        if isinstance(value, str):
            upper = value.upper()
            for member in cls:
                if member.value == upper:
                    return member
        return None


class ChannelType(str, Enum):
    """Supported advertising channel types (Section 6.1)."""

    CTV = "CTV"
    DISPLAY = "DISPLAY"
    AUDIO = "AUDIO"
    NATIVE = "NATIVE"
    DOOH = "DOOH"
    LINEAR_TV = "LINEAR_TV"


class KPIMetric(str, Enum):
    """Supported KPI metric types (Section 6.1)."""

    CPM = "CPM"
    CPC = "CPC"
    CPCV = "CPCV"
    CTR = "CTR"
    VCR = "VCR"
    ROAS = "ROAS"
    GRP = "GRP"


class PacingModel(str, Enum):
    """Budget pacing models (Section 7.1 / 7.3)."""

    EVEN = "EVEN"
    FRONT_LOADED = "FRONT_LOADED"
    BACK_LOADED = "BACK_LOADED"
    CUSTOM = "CUSTOM"


class GeoType(str, Enum):
    """Geographic targeting granularity levels."""

    COUNTRY = "COUNTRY"
    STATE = "STATE"
    DMA = "DMA"
    METRO = "METRO"
    ZIP = "ZIP"


class ApprovalStage(str, Enum):
    """Stages at which human approval can be required (D-3)."""

    PLAN_REVIEW = "PLAN_REVIEW"
    BOOKING = "BOOKING"
    CREATIVE = "CREATIVE"
    PACING_ADJUSTMENT = "PACING_ADJUSTMENT"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class ChannelAllocation(BaseModel):
    """A single channel's budget allocation within the campaign.

    budget_pct is the percentage of total_budget allocated to this channel.
    budget_amount is computed by the parent CampaignBrief model after
    validation.
    """

    channel: ChannelType
    budget_pct: float = Field(..., gt=0, le=100, description="Percentage of total budget (0 < pct <= 100)")
    format_prefs: list[str] = Field(default_factory=list, description="Preferred ad formats for this channel")

    # Computed after model validation on the parent
    budget_amount: Optional[float] = Field(default=None, description="Computed: total_budget * budget_pct / 100")


class KPI(BaseModel):
    """A performance target for the campaign (Section 6.1)."""

    metric: KPIMetric
    target_value: float = Field(..., gt=0, description="Target metric value (must be > 0)")


class GeoTarget(BaseModel):
    """A geographic targeting criterion."""

    geo_type: GeoType
    geo_value: str = Field(..., min_length=1, description="Geographic value (country code, DMA ID, etc.)")


class BrandSafety(BaseModel):
    """Brand safety constraints — content exclusions.

    excluded_categories: IAB Content Taxonomy 3.0 category IDs to avoid.
    excluded_keywords: Keywords that should not appear alongside ads.
    """

    excluded_categories: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)


class FrequencyCap(BaseModel):
    """Cross-channel frequency cap specification."""

    max_impressions: int = Field(..., gt=0, description="Max impressions per user within the period")
    period_hours: int = Field(..., gt=0, description="Period length in hours")


class ApprovalConfig(BaseModel):
    """Configurable human approval gates per pipeline stage (D-3).

    Default: plan_review and booking require approval; creative and
    pacing_adjustment do not.  Set all to False for fully automated
    execution.
    """

    plan_review: bool = Field(default=True, description="Require approval for the campaign plan")
    booking: bool = Field(default=True, description="Require approval before booking deals")
    creative: bool = Field(default=False, description="Require approval for creative assignments")
    pacing_adjustment: bool = Field(default=False, description="Require approval for budget reallocations")

    def approval_stages(self) -> list[ApprovalStage]:
        """Return a list of stages that require human approval."""
        stages: list[ApprovalStage] = []
        if self.plan_review:
            stages.append(ApprovalStage.PLAN_REVIEW)
        if self.booking:
            stages.append(ApprovalStage.BOOKING)
        if self.creative:
            stages.append(ApprovalStage.CREATIVE)
        if self.pacing_adjustment:
            stages.append(ApprovalStage.PACING_ADJUSTMENT)
        return stages


class DealPreferences(BaseModel):
    """Deal-level preferences for the campaign."""

    preferred_deal_types: list[str] = Field(default_factory=list, description="Preferred deal types: PG, PD, PA")
    max_cpm: Optional[float] = Field(default=None, gt=0, description="Maximum acceptable CPM")
    min_impressions: Optional[int] = Field(default=None, gt=0, description="Minimum impressions per deal")


# ---------------------------------------------------------------------------
# Main model
# ---------------------------------------------------------------------------


class CampaignBrief(BaseModel):
    """The campaign brief — entry point for the Campaign Automation pipeline.

    A structured JSON document describing what an advertiser wants to
    achieve.  The brief parser validates this against the schema and
    produces a campaign record in DRAFT status.

    See: Campaign Automation Strategic Plan, Sections 6.1 and 7.1.
    """

    # --- Required fields ---
    advertiser_id: str = Field(..., min_length=1, description="Advertiser identifier")
    campaign_name: str = Field(..., min_length=1, description="Human-readable campaign name")
    objective: CampaignObjective = Field(..., description="Campaign objective")
    total_budget: float = Field(..., gt=0, description="Total campaign budget (must be > 0)")
    currency: str = Field(..., min_length=3, max_length=3, pattern=r"^[A-Z]{3}$", description="ISO 4217 currency code")
    flight_start: date = Field(..., description="Campaign start date (YYYY-MM-DD)")
    flight_end: date = Field(..., description="Campaign end date (YYYY-MM-DD)")
    channels: list[ChannelAllocation] = Field(..., min_length=1, description="Channel allocations (at least one)")
    target_audience: list[str] = Field(..., min_length=1, description="IAB Audience Taxonomy segment IDs")

    # --- Optional fields ---
    agency_id: Optional[str] = Field(default=None, description="Agency identifier")
    description: Optional[str] = Field(default=None, description="Campaign objective/notes")
    target_geo: list[GeoTarget] = Field(default_factory=list, description="Geographic targeting (defaults to national)")
    kpis: list[KPI] = Field(default_factory=list, description="Performance targets")
    brand_safety: Optional[BrandSafety] = Field(default=None, description="Brand safety / content exclusions")
    frequency_cap: Optional[FrequencyCap] = Field(default=None, description="Cross-channel frequency cap")
    pacing_model: PacingModel = Field(default=PacingModel.EVEN, description="Budget pacing model")
    preferred_sellers: list[str] = Field(default_factory=list, description="Seller IDs to prioritize")
    excluded_sellers: list[str] = Field(default_factory=list, description="Seller IDs to avoid")
    creative_ids: list[str] = Field(default_factory=list, description="Pre-uploaded creative asset IDs")
    approval_config: ApprovalConfig = Field(default_factory=ApprovalConfig, description="Human approval gates (D-3)")
    deal_preferences: Optional[DealPreferences] = Field(default=None, description="Deal-level preferences")
    exclusion_list: list[str] = Field(default_factory=list, description="Domains/brands to exclude")
    notes: Optional[str] = Field(default=None, description="Free-text notes")

    # --- Validators ---

    @model_validator(mode="after")
    def _validate_brief(self) -> CampaignBrief:
        """Cross-field validations run after individual fields pass."""
        # flight_end must be strictly after flight_start
        if self.flight_end <= self.flight_start:
            raise ValueError("flight_end must be after flight_start")

        # Channel budget_pct values must sum to 100
        total_pct = sum(ch.budget_pct for ch in self.channels)
        if abs(total_pct - 100.0) > 0.01:
            raise ValueError(
                f"Channel budget_pct values must sum to 100 (got {total_pct:.2f})"
            )

        # No duplicate channel types
        channel_types = [ch.channel for ch in self.channels]
        if len(channel_types) != len(set(channel_types)):
            seen = set()
            dupes = []
            for ct in channel_types:
                if ct in seen:
                    dupes.append(ct.value)
                seen.add(ct)
            raise ValueError(
                f"Duplicate channel types are not allowed: {', '.join(dupes)}"
            )

        # Compute budget_amount for each channel
        for ch in self.channels:
            ch.budget_amount = round(self.total_budget * ch.budget_pct / 100.0, 2)

        return self


# ---------------------------------------------------------------------------
# Brief parser function
# ---------------------------------------------------------------------------


def parse_campaign_brief(input_data: Union[str, dict[str, Any]]) -> CampaignBrief:
    """Parse and validate a campaign brief from a JSON string or dict.

    Args:
        input_data: Either a JSON string or a dict containing brief fields.

    Returns:
        A validated CampaignBrief instance.

    Raises:
        ValueError: If input_data is a string that is not valid JSON.
        pydantic.ValidationError: If the data fails schema validation.
    """
    if isinstance(input_data, str):
        try:
            data = json.loads(input_data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc
    else:
        data = input_data

    return CampaignBrief(**data)
