# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Flow state models for workflow persistence."""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ExecutionStatus(str, Enum):
    """Execution status for the booking flow."""

    INITIALIZED = "initialized"
    BRIEF_RECEIVED = "brief_received"
    VALIDATION_FAILED = "validation_failed"
    BUDGET_ALLOCATED = "budget_allocated"
    RESEARCHING = "researching"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING_BOOKINGS = "executing_bookings"
    COMPLETED = "completed"
    FAILED = "failed"


class ChannelAllocation(BaseModel):
    """Budget allocation for a single channel."""

    channel: str
    budget: float = Field(..., ge=0)
    percentage: float = Field(..., ge=0, le=100)
    rationale: str


class ProductRecommendation(BaseModel):
    """A recommended product/deal for booking."""

    product_id: str = Field(..., alias="productId")
    product_name: str = Field(..., alias="productName")
    publisher: str
    channel: str
    format: Optional[str] = None
    impressions: int = Field(..., ge=0)
    cpm: float = Field(..., ge=0)
    cost: float = Field(..., ge=0)
    targeting: Optional[dict[str, Any]] = None
    priority: int = Field(default=0, ge=0)
    status: str = Field(default="pending")
    rationale: Optional[str] = None

    model_config = {"populate_by_name": True}


class BookedLine(BaseModel):
    """A successfully booked line item."""

    line_id: str = Field(..., alias="lineId")
    order_id: str = Field(..., alias="orderId")
    product_id: str = Field(..., alias="productId")
    product_name: str = Field(..., alias="productName")
    channel: str
    impressions: int = Field(..., ge=0)
    cost: float = Field(..., ge=0)
    booking_status: str = Field(..., alias="bookingStatus")
    booked_at: datetime = Field(..., alias="bookedAt")

    model_config = {"populate_by_name": True}


class CampaignBrief(BaseModel):
    """Campaign brief input from user."""

    name: str
    objectives: list[str]
    budget: float = Field(..., ge=0)
    start_date: str = Field(..., alias="startDate")
    end_date: str = Field(..., alias="endDate")
    target_audience: dict[str, Any] = Field(..., alias="targetAudience")
    kpis: dict[str, Any] = Field(default_factory=dict)
    channels: Optional[list[str]] = None
    constraints: Optional[dict[str, Any]] = None

    model_config = {"populate_by_name": True}


class BookingState(BaseModel):
    """State model for the deal booking flow."""

    # Input
    campaign_brief: dict[str, Any] = Field(default_factory=dict, alias="campaignBrief")

    # Audience planning (added for UCP integration)
    audience_plan: Optional[dict[str, Any]] = Field(
        default=None,
        alias="audiencePlan",
        description="Audience plan from Audience Planner Agent",
    )
    audience_coverage_estimates: dict[str, float] = Field(
        default_factory=dict,
        alias="audienceCoverageEstimates",
        description="Coverage estimates per channel (0-100%)",
    )
    audience_gaps: list[str] = Field(
        default_factory=list,
        alias="audienceGaps",
        description="Audience requirements that cannot be fulfilled",
    )

    # Budget allocation
    budget_allocations: dict[str, ChannelAllocation] = Field(
        default_factory=dict, alias="budgetAllocations"
    )

    # Channel recommendations (channel -> list of recommendations)
    channel_recommendations: dict[str, list[ProductRecommendation]] = Field(
        default_factory=dict, alias="channelRecommendations"
    )

    # Pending approvals (flattened from channel recommendations)
    pending_approvals: list[ProductRecommendation] = Field(
        default_factory=list, alias="pendingApprovals"
    )

    # Booked lines
    booked_lines: list[BookedLine] = Field(default_factory=list, alias="bookedLines")

    # Execution tracking
    execution_status: ExecutionStatus = Field(
        default=ExecutionStatus.INITIALIZED, alias="executionStatus"
    )
    errors: list[str] = Field(default_factory=list)

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow, alias="createdAt")
    updated_at: datetime = Field(default_factory=datetime.utcnow, alias="updatedAt")

    model_config = {"populate_by_name": True}


class ChannelBrief(BaseModel):
    """Brief for a single channel derived from campaign brief and allocation."""

    channel: str
    budget: float = Field(..., ge=0)
    start_date: str = Field(..., alias="startDate")
    end_date: str = Field(..., alias="endDate")
    target_audience: dict[str, Any] = Field(..., alias="targetAudience")
    objectives: list[str] = Field(default_factory=list)
    kpis: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}
