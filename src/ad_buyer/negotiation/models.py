# Author: Agent Range
# Donated to IAB Tech Lab

"""Models for the buyer-side negotiation client.

These are the buyer's view of a negotiation -- distinct from the seller's
NegotiationHistory/NegotiationRound models. The buyer tracks its own
session state and outcome.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from ..time_utils import utc_now


class NegotiationOutcome(str, Enum):
    """Outcome of a completed negotiation."""

    ACCEPTED = "accepted"
    WALKED_AWAY = "walked_away"
    DECLINED = "declined"
    ERROR = "error"


class NegotiationRound(BaseModel):
    """A single round in a negotiation, from the buyer's perspective."""

    round_number: int
    buyer_price: float
    seller_price: float
    action: str  # "counter", "accept", "reject", "final_offer"
    rationale: str = ""
    timestamp: datetime = Field(default_factory=utc_now)


class NegotiationSession(BaseModel):
    """Buyer's view of an active negotiation session.

    Tracks the state needed to continue negotiating with a seller.
    """

    proposal_id: str
    seller_url: str
    negotiation_id: str
    current_seller_price: float
    our_last_offer: float | None = None
    rounds: list[NegotiationRound] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utc_now)


class NegotiationResult(BaseModel):
    """Final result of a completed negotiation."""

    proposal_id: str
    outcome: NegotiationOutcome
    final_price: float | None = None
    rounds_count: int = 0
    rounds: list[NegotiationRound] = Field(default_factory=list)
    completed_at: datetime = Field(default_factory=utc_now)
