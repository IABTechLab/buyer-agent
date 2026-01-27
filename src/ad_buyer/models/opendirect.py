# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Pydantic models for IAB OpenDirect 2.1 resources."""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class RateType(str, Enum):
    """Rate type for pricing."""

    CPM = "CPM"
    CPMV = "CPMV"
    CPC = "CPC"
    CPD = "CPD"
    FLAT_RATE = "FlatRate"


class DeliveryType(str, Enum):
    """Delivery type for products."""

    EXCLUSIVE = "Exclusive"
    GUARANTEED = "Guaranteed"
    PMP = "PMP"


class OrderStatus(str, Enum):
    """Order status states."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class LineBookingStatus(str, Enum):
    """Line booking status states."""

    DRAFT = "Draft"
    PENDING_RESERVATION = "PendingReservation"
    RESERVED = "Reserved"
    PENDING_BOOKING = "PendingBooking"
    BOOKED = "Booked"
    IN_FLIGHT = "InFlight"
    FINISHED = "Finished"
    STOPPED = "Stopped"
    CANCELLED = "Cancelled"
    EXPIRED = "Expired"


class Organization(BaseModel):
    """Organization resource (advertisers, agencies, publishers)."""

    id: Optional[str] = None
    name: str = Field(..., max_length=128)
    type: str = Field(..., description="Type: advertiser, agency, publisher")
    address: Optional[str] = None
    contacts: Optional[list[dict[str, Any]]] = None
    ext: Optional[dict[str, Any]] = None


class Account(BaseModel):
    """Account resource - buyer-advertiser relationship."""

    id: Optional[str] = None
    advertiser_id: str = Field(..., alias="advertiserId")
    buyer_id: str = Field(..., alias="buyerId")
    name: str = Field(..., max_length=36)
    ext: Optional[dict[str, Any]] = None

    model_config = {"populate_by_name": True}


class Product(BaseModel):
    """Product resource - publisher inventory item."""

    id: Optional[str] = None
    publisher_id: str = Field(..., alias="publisherId")
    name: str = Field(..., max_length=38)
    description: Optional[str] = None
    currency: str = Field(default="USD", description="ISO-4217 currency code")
    base_price: float = Field(..., alias="basePrice", ge=0)
    rate_type: RateType = Field(..., alias="rateType")
    delivery_type: DeliveryType = Field(default=DeliveryType.GUARANTEED, alias="deliveryType")
    domain: Optional[str] = None
    ad_unit: Optional[dict[str, Any]] = Field(default=None, alias="adUnit")
    targeting: Optional[dict[str, Any]] = None
    available_impressions: Optional[int] = Field(default=None, alias="availableImpressions")
    ext: Optional[dict[str, Any]] = None

    model_config = {"populate_by_name": True}


class Order(BaseModel):
    """Order resource - campaign container (IO)."""

    id: Optional[str] = None
    name: str = Field(..., max_length=100)
    account_id: str = Field(..., alias="accountId")
    publisher_id: Optional[str] = Field(default=None, alias="publisherId")
    brand_id: Optional[str] = Field(default=None, alias="brandId")
    currency: str = Field(default="USD", description="ISO-4217 currency code")
    budget: float = Field(..., ge=0, description="Estimated budget")
    start_date: datetime = Field(..., alias="startDate")
    end_date: datetime = Field(..., alias="endDate")
    order_status: OrderStatus = Field(default=OrderStatus.PENDING, alias="orderStatus")
    ext: Optional[dict[str, Any]] = None

    model_config = {"populate_by_name": True}


class Line(BaseModel):
    """Line resource - individual product booking."""

    id: Optional[str] = None
    order_id: str = Field(..., alias="orderId")
    product_id: str = Field(..., alias="productId")
    name: str = Field(..., max_length=200)
    start_date: datetime = Field(..., alias="startDate")
    end_date: datetime = Field(..., alias="endDate")
    rate_type: RateType = Field(..., alias="rateType")
    rate: float = Field(..., ge=0)
    quantity: int = Field(..., ge=0, description="Target impressions or units")
    cost: Optional[float] = Field(default=None, ge=0, description="Calculated cost (read-only)")
    booking_status: LineBookingStatus = Field(
        default=LineBookingStatus.DRAFT, alias="bookingStatus"
    )
    targeting: Optional[dict[str, Any]] = None
    ext: Optional[dict[str, Any]] = None

    model_config = {"populate_by_name": True}


class Creative(BaseModel):
    """Creative resource - ad asset."""

    id: Optional[str] = None
    account_id: str = Field(..., alias="accountId")
    name: str = Field(..., max_length=255)
    language: Optional[str] = Field(default=None, description="ISO-639-1 language code")
    click_url: Optional[str] = Field(default=None, alias="clickUrl")
    creative_asset: Optional[dict[str, Any]] = Field(default=None, alias="creativeAsset")
    creative_approvals: Optional[list[dict[str, Any]]] = Field(
        default=None, alias="creativeApprovals"
    )
    ext: Optional[dict[str, Any]] = None

    model_config = {"populate_by_name": True}


class Assignment(BaseModel):
    """Assignment resource - creative-to-line binding."""

    id: Optional[str] = None
    creative_id: str = Field(..., alias="creativeId")
    line_id: str = Field(..., alias="lineId")
    status: Optional[str] = None
    ext: Optional[dict[str, Any]] = None

    model_config = {"populate_by_name": True}


class AvailsRequest(BaseModel):
    """Request for availability check."""

    product_id: str = Field(..., alias="productId")
    start_date: datetime = Field(..., alias="startDate")
    end_date: datetime = Field(..., alias="endDate")
    requested_impressions: Optional[int] = Field(default=None, alias="requestedImpressions")
    budget: Optional[float] = None
    targeting: Optional[dict[str, Any]] = None

    model_config = {"populate_by_name": True}


class AvailsResponse(BaseModel):
    """Response from availability check."""

    product_id: str = Field(..., alias="productId")
    available_impressions: int = Field(..., alias="availableImpressions")
    guaranteed_impressions: Optional[int] = Field(default=None, alias="guaranteedImpressions")
    estimated_cpm: float = Field(..., alias="estimatedCpm")
    total_cost: float = Field(..., alias="totalCost")
    delivery_confidence: Optional[float] = Field(
        default=None, alias="deliveryConfidence", ge=0, le=100
    )
    available_targeting: Optional[list[str]] = Field(default=None, alias="availableTargeting")

    model_config = {"populate_by_name": True}


class LineStats(BaseModel):
    """Performance statistics for a line item."""

    line_id: str = Field(..., alias="lineId")
    impressions_delivered: int = Field(default=0, alias="impressionsDelivered")
    target_impressions: int = Field(default=0, alias="targetImpressions")
    delivery_rate: float = Field(default=0.0, alias="deliveryRate", ge=0, le=100)
    pacing_status: Optional[str] = Field(default=None, alias="pacingStatus")
    amount_spent: float = Field(default=0.0, alias="amountSpent")
    budget: float = Field(default=0.0)
    budget_utilization: float = Field(default=0.0, alias="budgetUtilization", ge=0, le=100)
    effective_cpm: float = Field(default=0.0, alias="effectiveCpm")
    vcr: Optional[float] = Field(default=None, description="Video completion rate", ge=0, le=100)
    viewability: Optional[float] = Field(default=None, ge=0, le=100)
    ctr: Optional[float] = Field(default=None, description="Click-through rate", ge=0, le=100)
    last_updated: Optional[datetime] = Field(default=None, alias="lastUpdated")

    model_config = {"populate_by_name": True}
