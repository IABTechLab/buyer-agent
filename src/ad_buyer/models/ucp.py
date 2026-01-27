# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""UCP (User Context Protocol) models for audience planning.

IAB Tech Lab User Context Protocol enables exchange of embeddings encoding
identity, contextual, and reinforcement signals for real-time audience
matching between buyer and seller agents.

Transport: HTTPS JSON with Content-Type: application/vnd.ucp.embedding+json; v=1
"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class EmbeddingType(str, Enum):
    """Types of embeddings that can be exchanged via UCP."""

    CONTEXT = "context"  # Contextual signals from page/content
    CREATIVE = "creative"  # Creative/ad content signals
    USER_INTENT = "user_intent"  # User intent signals
    INVENTORY = "inventory"  # Inventory characteristics
    QUERY = "query"  # Search/query context


class SignalType(str, Enum):
    """UCP signal types as defined by IAB Tech Lab."""

    IDENTITY = "identity"  # User identity signals (hashed IDs, etc.)
    CONTEXTUAL = "contextual"  # Page/content context signals
    REINFORCEMENT = "reinforcement"  # Feedback/learning signals


class SimilarityMetric(str, Enum):
    """Supported similarity metrics for embedding comparison."""

    COSINE = "cosine"  # Cosine similarity (most common)
    DOT = "dot"  # Dot product
    L2 = "l2"  # Euclidean distance (L2 norm)


class UCPModelDescriptor(BaseModel):
    """Describes the embedding model used to generate vectors.

    Both parties must use compatible models for meaningful similarity.
    """

    id: str = Field(..., description="Model identifier (e.g., 'ucp-embedding-v1')")
    version: str = Field(..., description="Model version (e.g., '1.0.0')")
    dimension: int = Field(
        ..., ge=256, le=1024, description="Embedding dimension (256-1024)"
    )
    metric: SimilarityMetric = Field(
        default=SimilarityMetric.COSINE,
        description="Recommended similarity metric",
    )
    embedding_space_id: str = Field(
        default="iab-ucp-v1",
        description="Embedding space identifier for compatibility",
    )

    model_config = {"populate_by_name": True}


class UCPContextDescriptor(BaseModel):
    """Contextual information about the embedding source."""

    url: Optional[str] = Field(default=None, description="Page URL if applicable")
    page_title: Optional[str] = Field(
        default=None, alias="pageTitle", description="Page title"
    )
    keywords: list[str] = Field(
        default_factory=list, description="Content keywords"
    )
    language: str = Field(default="en", description="Content language (ISO 639-1)")
    device: Optional[str] = Field(
        default=None, description="Device type: desktop, mobile, ctv, tablet"
    )
    geography: Optional[str] = Field(
        default=None, description="Geography (ISO 3166-1 alpha-2)"
    )
    content_categories: list[str] = Field(
        default_factory=list,
        alias="contentCategories",
        description="IAB content categories",
    )

    model_config = {"populate_by_name": True}


class UCPConsent(BaseModel):
    """Consent information for UCP exchange.

    Required for all UCP exchanges to ensure compliance with privacy regulations.
    """

    framework: str = Field(
        default="IAB-TCFv2",
        description="Consent framework (IAB-TCFv2, IAB-USPrivacy, etc.)",
    )
    consent_string: Optional[str] = Field(
        default=None,
        alias="consentString",
        description="Encoded consent string",
    )
    permissible_uses: list[str] = Field(
        default_factory=list,
        alias="permissibleUses",
        description="Allowed purposes (e.g., 'personalization', 'measurement')",
    )
    ttl_seconds: int = Field(
        default=86400,
        alias="ttlSeconds",
        ge=0,
        description="Consent validity duration in seconds",
    )
    vendor_id: Optional[str] = Field(
        default=None,
        alias="vendorId",
        description="Vendor ID for consent lookup",
    )

    model_config = {"populate_by_name": True}


class UCPEmbedding(BaseModel):
    """A UCP embedding with metadata for exchange.

    This is the core payload exchanged between buyer and seller agents.
    """

    embedding_type: EmbeddingType = Field(
        ..., alias="embeddingType", description="Type of embedding"
    )
    signal_type: SignalType = Field(
        ..., alias="signalType", description="UCP signal type"
    )
    vector: list[float] = Field(
        ..., min_length=256, max_length=1024, description="Embedding vector"
    )
    dimension: int = Field(
        ..., ge=256, le=1024, description="Vector dimension"
    )
    model_descriptor: UCPModelDescriptor = Field(
        ..., alias="modelDescriptor", description="Model that generated this embedding"
    )
    context: Optional[UCPContextDescriptor] = Field(
        default=None, description="Contextual metadata"
    )
    consent: UCPConsent = Field(
        ..., description="Consent information (required)"
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the embedding was generated",
    )
    ttl_seconds: int = Field(
        default=3600,
        alias="ttlSeconds",
        ge=0,
        description="Embedding validity duration",
    )

    model_config = {"populate_by_name": True}

    def is_expired(self) -> bool:
        """Check if the embedding has expired."""
        from datetime import timezone

        age = (datetime.now(timezone.utc) - self.timestamp.replace(tzinfo=timezone.utc)).total_seconds()
        return age > self.ttl_seconds


class AudienceCapability(BaseModel):
    """Describes an audience capability offered by a seller.

    Used for discovery of available audience signals.
    """

    capability_id: str = Field(
        ..., alias="capabilityId", description="Unique capability identifier"
    )
    name: str = Field(..., description="Human-readable capability name")
    description: Optional[str] = Field(default=None, description="Detailed description")
    signal_type: SignalType = Field(
        ..., alias="signalType", description="Type of signal provided"
    )
    coverage_percentage: float = Field(
        default=0.0,
        alias="coveragePercentage",
        ge=0,
        le=100,
        description="Estimated coverage percentage of inventory",
    )
    available_segments: list[str] = Field(
        default_factory=list,
        alias="availableSegments",
        description="Available audience segment IDs",
    )
    taxonomy: Optional[str] = Field(
        default=None, description="Audience taxonomy (e.g., 'IAB-1.0', 'custom')"
    )
    minimum_match_rate: float = Field(
        default=0.0,
        alias="minimumMatchRate",
        ge=0,
        le=1,
        description="Minimum required match rate for activation",
    )
    ucp_compatible: bool = Field(
        default=True,
        alias="ucpCompatible",
        description="Whether UCP embedding exchange is supported",
    )
    embedding_dimension: Optional[int] = Field(
        default=None,
        alias="embeddingDimension",
        ge=256,
        le=1024,
        description="Embedding dimension if UCP compatible",
    )

    model_config = {"populate_by_name": True}


class AudienceValidationResult(BaseModel):
    """Result of validating a buyer's audience request against seller capabilities."""

    validation_status: str = Field(
        ...,
        alias="validationStatus",
        description="Status: valid, partial_match, no_match, invalid",
    )
    overall_coverage_percentage: float = Field(
        default=0.0,
        alias="overallCoveragePercentage",
        ge=0,
        le=100,
        description="Estimated overall audience coverage",
    )
    matched_capabilities: list[str] = Field(
        default_factory=list,
        alias="matchedCapabilities",
        description="Capability IDs that match requirements",
    )
    gaps: list[str] = Field(
        default_factory=list,
        description="Audience requirements that cannot be fulfilled",
    )
    alternatives: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Alternative capabilities that could partially fulfill gaps",
    )
    ucp_similarity_score: Optional[float] = Field(
        default=None,
        alias="ucpSimilarityScore",
        ge=0,
        le=1,
        description="UCP embedding similarity score if computed",
    )
    targeting_compatible: bool = Field(
        default=False,
        alias="targetingCompatible",
        description="Whether targeting requirements can be fulfilled",
    )
    estimated_reach: Optional[int] = Field(
        default=None,
        alias="estimatedReach",
        ge=0,
        description="Estimated audience reach (impressions)",
    )
    validation_notes: list[str] = Field(
        default_factory=list,
        alias="validationNotes",
        description="Additional notes from validation",
    )
    validated_at: datetime = Field(
        default_factory=datetime.utcnow,
        alias="validatedAt",
        description="Validation timestamp",
    )

    model_config = {"populate_by_name": True}


class AudiencePlan(BaseModel):
    """Audience plan generated by the buyer's Audience Planner Agent.

    Contains audience targeting strategy derived from campaign requirements.
    """

    plan_id: str = Field(..., alias="planId", description="Unique plan identifier")
    campaign_id: Optional[str] = Field(
        default=None, alias="campaignId", description="Associated campaign ID"
    )

    # Requirements from campaign brief
    target_demographics: dict[str, Any] = Field(
        default_factory=dict,
        alias="targetDemographics",
        description="Demographic targeting requirements",
    )
    target_interests: list[str] = Field(
        default_factory=list,
        alias="targetInterests",
        description="Interest-based targeting",
    )
    target_behaviors: list[str] = Field(
        default_factory=list,
        alias="targetBehaviors",
        description="Behavioral targeting",
    )
    exclusions: list[str] = Field(
        default_factory=list,
        description="Audience exclusions",
    )

    # UCP-derived signals
    requested_signal_types: list[SignalType] = Field(
        default_factory=list,
        alias="requestedSignalTypes",
        description="Signal types needed",
    )
    query_embedding: Optional[UCPEmbedding] = Field(
        default=None,
        alias="queryEmbedding",
        description="UCP embedding representing audience intent",
    )

    # Coverage estimates per channel
    channel_coverage_estimates: dict[str, float] = Field(
        default_factory=dict,
        alias="channelCoverageEstimates",
        description="Estimated coverage by channel (0-100%)",
    )

    # Strategy
    audience_expansion_enabled: bool = Field(
        default=True,
        alias="audienceExpansionEnabled",
        description="Whether to expand audiences for reach",
    )
    expansion_factor: float = Field(
        default=1.0,
        alias="expansionFactor",
        ge=1.0,
        le=3.0,
        description="Audience expansion factor (1.0 = no expansion)",
    )

    # Metadata
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        alias="createdAt",
    )

    model_config = {"populate_by_name": True}


class CoverageEstimate(BaseModel):
    """Coverage estimate for a targeting combination."""

    targeting_key: str = Field(
        ..., alias="targetingKey", description="Targeting combination identifier"
    )
    estimated_impressions: int = Field(
        default=0,
        alias="estimatedImpressions",
        ge=0,
        description="Estimated available impressions",
    )
    coverage_percentage: float = Field(
        default=0.0,
        alias="coveragePercentage",
        ge=0,
        le=100,
        description="Coverage as percentage of total inventory",
    )
    confidence_level: str = Field(
        default="medium",
        alias="confidenceLevel",
        description="Confidence: high, medium, low",
    )
    limiting_factors: list[str] = Field(
        default_factory=list,
        alias="limitingFactors",
        description="Factors limiting coverage",
    )
    channel: Optional[str] = Field(
        default=None, description="Channel if channel-specific"
    )

    model_config = {"populate_by_name": True}
