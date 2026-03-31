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

from pydantic import BaseModel, Field

from .linear_tv import LinearTVParams, LinearTVQuoteDetails

# ---------------------------------------------------------------------------
# Shared sub-models (nested objects in API responses)
# ---------------------------------------------------------------------------


class BuyerIdentityPayload(BaseModel):
    """Buyer identity included in quote/deal requests.

    Maps to the ``buyer_identity`` object in the API contract.
    """

    seat_id: str | None = None
    agency_id: str | None = None
    advertiser_id: str | None = None
    dsp_platform: str | None = None


class ProductInfo(BaseModel):
    """Product summary embedded in quote/deal responses."""

    product_id: str
    name: str
    inventory_type: str | None = None


class PricingInfo(BaseModel):
    """Pricing breakdown returned by the seller.

    Extended with CPP fields for linear TV (pricing_model "cpp" or "hybrid").

    base_cpm and final_cpm are Optional to support ``pricing_type=on_request``
    (Layer 2b — pricing provenance tracking, bead ar-r76d).  When the seller
    has not provided pricing, these fields are None.
    """

    base_cpm: float | None = None
    tier_discount_pct: float = 0.0
    volume_discount_pct: float = 0.0
    final_cpm: float | None = None
    currency: str = "USD"
    pricing_model: str = "cpm"  # "cpm", "cpp", "unit_rate", "hybrid"
    rationale: str = ""

    # Linear TV CPP pricing (None for digital/CTV)
    base_cpp: float | None = None
    final_cpp: float | None = None


class TermsInfo(BaseModel):
    """Deal/quote terms (volume, flight dates, guarantee).

    Extended with GRP-based fields for linear TV.
    """

    impressions: int | None = None
    flight_start: str | None = None
    flight_end: str | None = None
    guaranteed: bool = False

    # Linear TV GRP-based terms (None for digital/CTV)
    grps: int | None = None
    guaranteed_grps: int | None = None
    target_demo: str | None = None


class AvailabilityInfo(BaseModel):
    """Inventory availability information in a quote."""

    inventory_available: bool = True
    estimated_fill_rate: float | None = None
    competing_demand: str | None = None


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
    impressions: int | None = None
    flight_start: str | None = None
    flight_end: str | None = None
    target_cpm: float | None = None
    buyer_identity: BuyerIdentityPayload | None = None
    agent_url: str | None = None

    # Media type discriminator (Option C hybrid approach)
    media_type: str = "digital"  # "digital", "ctv", "linear_tv"

    # Linear TV nested params (None for digital/CTV)
    linear_tv: LinearTVParams | None = None


class DealBookingRequest(BaseModel):
    """Request body for POST /api/v1/deals.

    Buyer books a deal from an existing quote.
    """

    quote_id: str
    buyer_identity: BuyerIdentityPayload | None = None
    notes: str | None = None


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
    availability: AvailabilityInfo | None = None
    buyer_tier: str = "public"
    expires_at: str | None = None
    seller_id: str | None = None
    created_at: str | None = None

    # Media type (echoes the request)
    media_type: str = "digital"

    # Linear TV quote details (None for digital/CTV)
    linear_tv: LinearTVQuoteDetails | None = None


class DealResponse(BaseModel):
    """Response from GET/POST /api/v1/deals.

    Represents a confirmed deal with a seller-issued Deal ID.
    """

    deal_id: str
    deal_type: str
    status: str  # proposed, active, rejected, expired, completed
    quote_id: str | None = None
    product: ProductInfo
    pricing: PricingInfo
    terms: TermsInfo
    buyer_tier: str = "public"
    expires_at: str | None = None
    activation_instructions: dict[str, str] = Field(default_factory=dict)
    openrtb_params: OpenRTBParams | None = None
    created_at: str | None = None


# ---------------------------------------------------------------------------
# Error model
# ---------------------------------------------------------------------------


class SellerErrorResponse(BaseModel):
    """Structured error returned by the seller API."""

    error: str
    detail: str = ""
    status_code: int = 0
