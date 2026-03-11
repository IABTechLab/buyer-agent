# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Linear TV models for the Option C hybrid approach.

These models extend the existing quote-then-book deal flow with
linear-TV-specific parameters. They are nested under the `linear_tv`
field in QuoteRequest and QuoteResponse, activated when
`media_type == "linear_tv"`.

Design decisions (from LINEAR_TV_DEAL_FLOW_RESEARCH.md):
- Option C (Hybrid): same endpoints, media_type branching
- DMA-level granularity (all 210 DMAs)
- Context-dependent deal_type: linear_tv uses ["upfront", "scatter", "opportunistic"]
- Scatter-only for v1 (upfronts TBD, separate bead ar-gh6)
- TIP-compatible, not TIP-native
- Nielsen measurement currency only for v1
"""

from typing import Any, Optional

from pydantic import BaseModel, Field


class LinearTVParams(BaseModel):
    """Linear-TV-specific parameters, nested under QuoteRequest.linear_tv.

    Used when ``media_type == "linear_tv"`` in the QuoteRequest.
    Contains all the fields needed for a linear TV quote that have
    no equivalent in digital/CTV buying.
    """

    # Required: target demographic for audience measurement
    target_demo: str = Field(
        ...,
        description=(
            'Target demographic. Standard values: "A18-49", "A25-54", '
            '"HH" (households), "P2+" (persons 2+), etc.'
        ),
    )

    # Volume specification (in GRPs, alternative to impressions)
    grps_requested: Optional[int] = Field(
        default=None,
        description="Requested volume in Gross Rating Points.",
    )

    # Inventory targeting
    dayparts: Optional[list[str]] = Field(
        default=None,
        description=(
            "Target dayparts. Standard values: "
            '"primetime", "daytime", "early_morning", "late_night", '
            '"early_fringe", "prime_access", "overnight", "weekend".'
        ),
    )
    networks: Optional[list[str]] = Field(
        default=None,
        description='Target networks (e.g., ["NBC", "CBS", "ESPN"]).',
    )
    dmas: Optional[list[str]] = Field(
        default=None,
        description=(
            "Nielsen DMA codes for local buying. None means national. "
            'E.g., ["501"] for New York, ["803"] for Los Angeles.'
        ),
    )

    # Spot specification
    spot_length: int = Field(
        default=30,
        description="Spot length in seconds: 15, 30, or 60.",
    )

    # Pricing target
    target_cpp: Optional[float] = Field(
        default=None,
        description="Buyer's desired Cost Per Point (CPP).",
    )

    # Measurement
    measurement_currency: str = Field(
        default="nielsen",
        description=(
            "Audience measurement provider used for billing. "
            'Values: "nielsen", "comscore", "videoamp".'
        ),
    )

    # Rotation type
    rotation: str = Field(
        default="ros",
        description=(
            "Spot rotation type. "
            '"ros" (run of schedule), "fixed", "program_specific".'
        ),
    )


class CancellationTerms(BaseModel):
    """Cancellation window specification for linear TV deals.

    Linear TV deals have structured cancellation rules that differ
    from digital (where cancellation is a simple status change).
    """

    notice_days: int = Field(
        ...,
        description="Days of notice required before cancellation.",
    )
    cancellable_pct: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Portion of the deal that can be cancelled (0.0 to 1.0).",
    )
    deadline: Optional[str] = Field(
        default=None,
        description="Absolute deadline for cancellation (ISO date string).",
    )
    force_majeure: bool = Field(
        default=True,
        description="Whether force majeure exceptions apply.",
    )


class LinearTVQuoteDetails(BaseModel):
    """Linear-TV-specific quote details, nested under QuoteResponse.linear_tv.

    Populated by the seller when a linear TV quote is generated.
    Contains audience estimates, inventory details, and deal terms
    specific to linear TV.
    """

    target_demo: str
    estimated_grps: float
    estimated_rating: float
    cpp: float = Field(description="Cost Per Point offered by seller.")
    dayparts: list[str]
    networks: list[str]
    spots_per_week: int
    total_spots: int
    spot_length: int
    measurement_currency: str
    audience_estimate: dict[str, Any] = Field(
        description=(
            "Audience size estimates. Expected keys: "
            '"demo", "universe", "impressions_equiv".'
        ),
    )
    cancellation_terms: Optional[CancellationTerms] = None
    makegood_policy: Optional[str] = Field(
        default=None,
        description=(
            'Makegood policy: "standard" (ADU), "negotiated", or "none".'
        ),
    )


class MakegoodRequest(BaseModel):
    """Request body for POST /api/v1/deals/{deal_id}/makegoods.

    Sent by the buyer when audience delivery falls short of the
    guaranteed GRP level. The seller responds with offered replacement
    inventory.
    """

    shortfall_grps: float = Field(
        ...,
        description="GRP shortfall that needs to be made up.",
    )
    original_daypart: str = Field(
        ...,
        description="Daypart where the underdelivery occurred.",
    )
    target_demo: str = Field(
        ...,
        description="Target demographic for makegood inventory.",
    )
    preferred_dayparts: Optional[list[str]] = Field(
        default=None,
        description="Buyer's preferred dayparts for replacement inventory.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Additional context about the makegood request.",
    )


class CancellationRequest(BaseModel):
    """Request body for POST /api/v1/deals/{deal_id}/cancel.

    Supports both full and partial cancellation, with notice period
    awareness. The seller validates against the deal's cancellation
    terms before processing.
    """

    cancel_pct: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Portion to cancel (1.0 = full, 0.3 = 30%).",
    )
    reason: str = Field(
        ...,
        description="Reason for cancellation.",
    )
    effective_date: Optional[str] = Field(
        default=None,
        description="Requested effective date for cancellation (ISO date).",
    )


# ---------------------------------------------------------------------------
# CPM <-> CPP conversion utilities
# ---------------------------------------------------------------------------


def cpp_to_cpm(cpp: float, universe_size: int) -> float:
    """Convert Cost Per Point to CPM equivalent.

    1 GRP = 1% of the target universe. So 1 GRP reaches
    ``universe_size * 0.01`` people.

    CPM = cost / (reached people / 1000)
        = CPP / (universe_size * 0.01 / 1000)
        = CPP * 100000 / universe_size

    Args:
        cpp: Cost Per Point.
        universe_size: Size of the target demographic universe.

    Returns:
        Equivalent CPM value.
    """
    if universe_size <= 0:
        raise ValueError("universe_size must be positive")
    return cpp * 100_000 / universe_size


def cpm_to_cpp(cpm: float, universe_size: int) -> float:
    """Convert CPM to Cost Per Point equivalent.

    Inverse of cpp_to_cpm:
    CPP = CPM * universe_size / 100000

    Args:
        cpm: Cost Per Thousand impressions.
        universe_size: Size of the target demographic universe.

    Returns:
        Equivalent CPP value.
    """
    if universe_size <= 0:
        raise ValueError("universe_size must be positive")
    return cpm * universe_size / 100_000
