# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Pydantic models for the IAB Deals API v1.0 (quote-then-book flow).

These models match the seller's API contract defined in
docs/api/deal-creation-api-contract.md. They represent the buyer-side
view of quotes and deals returned by the seller's /api/v1/quotes and
/api/v1/deals endpoints.

Extended with linear TV support (Option C hybrid approach, bead buyer-6io):
- media_type discriminator on QuoteRequest/QuoteResponse
- LinearTVParams nested object on QuoteRequest
- LinearTVQuoteDetails nested object on QuoteResponse
- CPP pricing fields on PricingInfo
- GRP/demo fields on TermsInfo
"""

from typing import Any, Optional

from pydantic import BaseModel, Field

from .linear_tv import LinearTVParams, LinearTVQuoteDetails


# ---------------------------------------------------------------------------
# Shared sub-models (nested objects in API responses)
# ---------------------------------------------------------------------------


class BuyerIdentityPayload(BaseModel):
    """Buyer identity included in quote/deal requests.

    Maps to the ``buyer_identity`` object in the API contract.
    """

    seat_id: Optional[str] = None
    agency_id: Optional[str] = None
    advertiser_id: Optional[str] = None
    dsp_platform: Optional[str] = None


class ProductInfo(BaseModel):
    """Product summary embedded in quote/deal responses."""

    product_id: str
    name: str
    inventory_type: Optional[str] = None


class PricingInfo(BaseModel):
    """Pricing breakdown returned by the seller.

    Extended with CPP fields for linear TV (pricing_model "cpp" or "hybrid").
    """

    base_cpm: float
    tier_discount_pct: float = 0.0
    volume_discount_pct: float = 0.0
    final_cpm: float
    currency: str = "USD"
    pricing_model: str = "cpm"  # "cpm", "cpp", "unit_rate", "hybrid"
    rationale: str = ""

    # Linear TV CPP pricing (None for digital/CTV)
    base_cpp: Optional[float] = None
    final_cpp: Optional[float] = None


class TermsInfo(BaseModel):
    """Deal/quote terms (volume, flight dates, guarantee).

    Extended with GRP-based fields for linear TV.
    """

    impressions: Optional[int] = None
    flight_start: Optional[str] = None
    flight_end: Optional[str] = None
    guaranteed: bool = False

    # Linear TV GRP-based terms (None for digital/CTV)
    grps: Optional[int] = None
    guaranteed_grps: Optional[int] = None
    target_demo: Optional[str] = None


class AvailabilityInfo(BaseModel):
    """Inventory availability information in a quote."""

    inventory_available: bool = True
    estimated_fill_rate: Optional[float] = None
    competing_demand: Optional[str] = None


class OpenRTBParams(BaseModel):
    """OpenRTB deal parameters for DSP activation."""

    id: str
    bidfloor: float
    bidfloorcur: str = "USD"
    at: int = 3
    wseat: list[str] = Field(default_factory=list)
    wadomain: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Request models (buyer -> seller)
# ---------------------------------------------------------------------------


class QuoteRequest(BaseModel):
    """Request body for POST /api/v1/quotes.

    Buyer requests non-binding pricing from a seller.
    Works for digital, CTV, and linear TV (via media_type discriminator).
    """

    product_id: str
    deal_type: str = "PD"
    impressions: Optional[int] = None
    flight_start: Optional[str] = None
    flight_end: Optional[str] = None
    target_cpm: Optional[float] = None
    buyer_identity: Optional[BuyerIdentityPayload] = None
    agent_url: Optional[str] = None

    # Media type discriminator (Option C hybrid approach)
    media_type: str = "digital"  # "digital", "ctv", "linear_tv"

    # Linear TV nested params (None for digital/CTV)
    linear_tv: Optional[LinearTVParams] = None


class DealBookingRequest(BaseModel):
    """Request body for POST /api/v1/deals.

    Buyer books a deal from an existing quote.
    """

    quote_id: str
    buyer_identity: Optional[BuyerIdentityPayload] = None
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Response models (seller -> buyer)
# ---------------------------------------------------------------------------


class QuoteResponse(BaseModel):
    """Response from GET/POST /api/v1/quotes.

    Represents a non-binding price quote from the seller.
    Extended with linear TV details when media_type is "linear_tv".
    """

    quote_id: str
    status: str  # available, expired, declined, booked
    product: ProductInfo
    pricing: PricingInfo
    terms: TermsInfo
    availability: Optional[AvailabilityInfo] = None
    buyer_tier: str = "public"
    expires_at: Optional[str] = None
    seller_id: Optional[str] = None
    created_at: Optional[str] = None

    # Media type (echoes the request)
    media_type: str = "digital"

    # Linear TV quote details (None for digital/CTV)
    linear_tv: Optional[LinearTVQuoteDetails] = None


class DealResponse(BaseModel):
    """Response from GET/POST /api/v1/deals.

    Represents a confirmed deal with a seller-issued Deal ID.
    """

    deal_id: str
    deal_type: str
    status: str  # proposed, active, rejected, expired, completed
    quote_id: Optional[str] = None
    product: ProductInfo
    pricing: PricingInfo
    terms: TermsInfo
    buyer_tier: str = "public"
    expires_at: Optional[str] = None
    activation_instructions: dict[str, str] = Field(default_factory=dict)
    openrtb_params: Optional[OpenRTBParams] = None
    created_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Error model
# ---------------------------------------------------------------------------


class SellerErrorResponse(BaseModel):
    """Structured error returned by the seller API."""

    error: str
    detail: str = ""
    status_code: int = 0
