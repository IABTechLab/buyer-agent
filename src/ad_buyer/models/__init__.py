# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Data models for the Ad Buyer System."""

from .buyer_identity import (
    AccessTier,
    BuyerContext,
    BuyerIdentity,
    DealRequest,
    DealResponse,
    DealType,
)
from .campaign_brief import (
    KPI,
    ApprovalConfig,
    ApprovalStage,
    BrandSafety,
    CampaignBrief,
    CampaignObjective,
    ChannelAllocation,
    ChannelType,
    DealPreferences,
    FrequencyCap,
    GeoTarget,
    GeoType,
    KPIMetric,
    PacingModel,
    parse_campaign_brief,
)
from .creative_asset import (
    AssetType,
    CreativeAsset,
    ValidationStatus,
)
from .deals import (
    AvailabilityInfo,
    BuyerIdentityPayload,
    DealBookingRequest,
    OpenRTBParams,
    PricingInfo,
    ProductInfo,
    QuoteRequest,
    QuoteResponse,
    SellerErrorResponse,
    TermsInfo,
)

# Avoid shadowing buyer_identity.DealResponse with deals.DealResponse
# by importing the deals version under a distinct name
from .deals import DealResponse as SellerDealResponse
from .flow_state import BookingState
from .linear_tv import (
    CancellationRequest,
    CancellationTerms,
    LinearTVParams,
    LinearTVQuoteDetails,
    MakegoodRequest,
    cpm_to_cpp,
    cpp_to_cpm,
)
from .opendirect import (
    Account,
    Creative,
    Line,
    LineBookingStatus,
    Order,
    OrderStatus,
    Organization,
    Product,
    RateType,
)
from .ucp import (
    AudienceCapability,
    AudiencePlan,
    AudienceValidationResult,
    CoverageEstimate,
    EmbeddingType,
    SignalType,
    SimilarityMetric,
    UCPConsent,
    UCPContextDescriptor,
    UCPEmbedding,
    UCPModelDescriptor,
)

__all__ = [
    # OpenDirect models
    "Account",
    "Creative",
    "Line",
    "LineBookingStatus",
    "Order",
    "OrderStatus",
    "Organization",
    "Product",
    "RateType",
    # Flow state models
    "BookingState",
    # Buyer identity models
    "AccessTier",
    "BuyerContext",
    "BuyerIdentity",
    "DealRequest",
    "DealResponse",
    "DealType",
    # UCP models
    "AudienceCapability",
    "AudiencePlan",
    "AudienceValidationResult",
    "CoverageEstimate",
    "EmbeddingType",
    "SignalType",
    "SimilarityMetric",
    "UCPConsent",
    "UCPContextDescriptor",
    "UCPEmbedding",
    "UCPModelDescriptor",
    # Linear TV models (Option C hybrid)
    "CancellationRequest",
    "CancellationTerms",
    "LinearTVParams",
    "LinearTVQuoteDetails",
    "MakegoodRequest",
    "cpp_to_cpm",
    "cpm_to_cpp",
    # Deals API v1.0 models (quote-then-book)
    "AvailabilityInfo",
    "BuyerIdentityPayload",
    "DealBookingRequest",
    "OpenRTBParams",
    "PricingInfo",
    "ProductInfo",
    "QuoteRequest",
    "QuoteResponse",
    "SellerDealResponse",
    "SellerErrorResponse",
    "TermsInfo",
    # Creative asset models (Campaign Automation)
    "AssetType",
    "CreativeAsset",
    "ValidationStatus",
    # Campaign brief models (buyer-80k)
    "ApprovalConfig",
    "ApprovalStage",
    "BrandSafety",
    "CampaignBrief",
    "CampaignObjective",
    "ChannelAllocation",
    "ChannelType",
    "DealPreferences",
    "FrequencyCap",
    "GeoTarget",
    "GeoType",
    "KPI",
    "KPIMetric",
    "PacingModel",
    "parse_campaign_brief",
]
