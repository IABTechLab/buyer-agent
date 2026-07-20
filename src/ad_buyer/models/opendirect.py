# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Pydantic models for IAB OpenDirect 2.1 resources."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RateType(str, Enum):
    """Rate type for pricing."""

    CPM = "CPM"
    CPMV = "CPMV"
    CPC = "CPC"
    CPD = "CPD"
    FLAT_RATE = "FlatRate"


class DeliveryType(str, Enum):
    """Delivery type for products.

    ``exclusive``/``guaranteed`` follow the OpenDirect 2.1 spec spellings
    (Object: Product, ``deliverytype``). ``PMP`` is a project extension
    value with no spec equivalent.
    """

    EXCLUSIVE = "exclusive"
    GUARANTEED = "guaranteed"
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
    # Spec spelling is "Canceled" (Object: Line, 'bookingstatus').
    CANCELLED = "Canceled"
    EXPIRED = "Expired"


class Organization(BaseModel):
    """Organization resource (advertisers, agencies, publishers)."""

    id: str | None = None
    name: str = Field(..., max_length=128)
    type: str = Field(..., description="Type: advertiser, agency, publisher")
    address: str | None = None
    contacts: list[dict[str, Any]] | None = None
    ext: dict[str, Any] | None = None


class Account(BaseModel):
    """Account resource - buyer-advertiser relationship."""

    id: str | None = None
    advertiser_id: str = Field(..., alias="advertiserid")
    buyer_id: str = Field(..., alias="buyerid")
    name: str = Field(..., max_length=36)
    ext: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


class Product(BaseModel):
    """Product resource - publisher inventory item."""

    id: str | None = None
    publisher_id: str = Field(..., alias="publisherid")
    name: str = Field(..., max_length=38)
    description: str | None = None
    currency: str = Field(default="USD", description="ISO-4217 currency code")
    base_price: float = Field(..., alias="baseprice", ge=0)
    rate_type: RateType = Field(..., alias="ratetype")
    delivery_type: DeliveryType = Field(default=DeliveryType.GUARANTEED, alias="deliverytype")
    domain: str | None = None
    ad_unit: dict[str, Any] | None = Field(default=None, alias="adunit")
    targeting: dict[str, Any] | None = None
    available_impressions: int | None = Field(default=None, alias="availableImpressions")
    ext: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


class Order(BaseModel):
    """Order resource - campaign container (IO)."""

    id: str | None = None
    name: str = Field(..., max_length=100)
    account_id: str = Field(..., alias="accountid")
    publisher_id: str | None = Field(default=None, alias="publisherid")
    brand_id: str | None = Field(default=None, alias="brandId")
    currency: str = Field(default="USD", description="ISO-4217 currency code")
    budget: float = Field(..., ge=0, description="Estimated budget")
    start_date: datetime = Field(..., alias="startdate")
    end_date: datetime = Field(..., alias="enddate")
    order_status: OrderStatus = Field(default=OrderStatus.PENDING, alias="orderstatus")
    ext: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


class Line(BaseModel):
    """Line resource - individual product booking."""

    id: str | None = None
    order_id: str = Field(..., alias="orderid")
    product_id: str = Field(..., alias="productid")
    name: str = Field(..., max_length=200)
    start_date: datetime = Field(..., alias="startdate")
    end_date: datetime = Field(..., alias="enddate")
    rate_type: RateType = Field(..., alias="ratetype")
    rate: float = Field(..., ge=0)
    quantity: int = Field(..., alias="qty", ge=0, description="Target impressions or units")
    cost: float | None = Field(default=None, ge=0, description="Calculated cost (read-only)")
    booking_status: LineBookingStatus = Field(
        default=LineBookingStatus.DRAFT, alias="bookingstatus"
    )
    targeting: dict[str, Any] | None = None
    ext: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


class Creative(BaseModel):
    """Creative resource - ad asset."""

    id: str | None = None
    account_id: str = Field(..., alias="accountid")
    name: str = Field(..., max_length=255)
    language: str | None = Field(default=None, description="ISO-639-1 language code")
    click_url: str | None = Field(default=None, alias="clickUrl")
    creative_asset: dict[str, Any] | None = Field(default=None, alias="creativeAsset")
    creative_approvals: list[dict[str, Any]] | None = Field(default=None, alias="creativeapprovals")
    ext: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


class Assignment(BaseModel):
    """Assignment resource - creative-to-line binding."""

    id: str | None = None
    creative_id: str = Field(..., alias="creativeid")
    line_id: str = Field(..., alias="lineId")
    status: str | None = None
    ext: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


class AvailsRequest(BaseModel):
    """Request for availability check.

    ``productid`` is still scalar (the spec's ``productids`` array is the
    Tier-2 structural change); the alias follows the spec-lowercase
    dialect so the field set stays case-consistent on the wire.
    """

    product_id: str = Field(..., alias="productid")
    start_date: datetime = Field(..., alias="startdate")
    end_date: datetime = Field(..., alias="enddate")
    requested_impressions: int | None = Field(default=None, alias="requestedImpressions")
    budget: float | None = None
    targeting: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


class AvailsResponse(BaseModel):
    """Response from availability check."""

    product_id: str = Field(..., alias="productid")
    available_impressions: int = Field(..., alias="availableImpressions")
    guaranteed_impressions: int | None = Field(default=None, alias="guaranteedImpressions")
    estimated_cpm: float = Field(..., alias="estimatedCpm")
    total_cost: float = Field(..., alias="totalCost")
    delivery_confidence: float | None = Field(
        default=None, alias="deliveryConfidence", ge=0, le=100
    )
    available_targeting: list[str] | None = Field(default=None, alias="availableTargeting")

    model_config = {"populate_by_name": True}


class LineStats(BaseModel):
    """Performance statistics for a line item."""

    line_id: str = Field(..., alias="lineId")
    impressions_delivered: int = Field(default=0, alias="impressionsDelivered")
    target_impressions: int = Field(default=0, alias="targetImpressions")
    delivery_rate: float = Field(default=0.0, alias="deliveryRate", ge=0, le=100)
    pacing_status: str | None = Field(default=None, alias="pacingStatus")
    amount_spent: float = Field(default=0.0, alias="amountSpent")
    budget: float = Field(default=0.0)
    budget_utilization: float = Field(default=0.0, alias="budgetUtilization", ge=0, le=100)
    effective_cpm: float = Field(default=0.0, alias="effectiveCpm")
    vcr: float | None = Field(default=None, description="Video completion rate", ge=0, le=100)
    viewability: float | None = Field(default=None, ge=0, le=100)
    ctr: float | None = Field(default=None, description="Click-through rate", ge=0, le=100)
    last_updated: datetime | None = Field(default=None, alias="lastUpdated")

    model_config = {"populate_by_name": True}
