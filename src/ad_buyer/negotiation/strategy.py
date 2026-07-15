# Author: Agent Range
# Donated to IAB Tech Lab

"""Abstract base class for swappable negotiation strategies.

The strategy pattern allows different negotiation behaviors to be plugged
into the same NegotiationClient. Implement this ABC to create custom
negotiation strategies (e.g., threshold-based, adaptive, competitive).
"""

from abc import ABC, abstractmethod
from datetime import datetime

from pydantic import BaseModel, Field

from ..time_utils import utc_now


class NegotiationContext(BaseModel):
    """Tracks negotiation state passed to strategy methods.

    Provides the strategy with enough context to make decisions
    without coupling to HTTP or client internals.
    """

    rounds_completed: int
    seller_last_price: float
    our_last_offer: float | None = None
    seller_previous_price: float | None = None
    started_at: datetime = Field(default_factory=utc_now)


class NegotiationStrategy(ABC):
    """Base class for swappable negotiation strategies.

    Subclasses implement the three decision methods. The NegotiationClient
    calls these during auto_negotiate to drive the negotiation loop.
    """

    @abstractmethod
    def should_accept(self, seller_price: float, context: NegotiationContext) -> bool:
        """Should we accept the seller's current offer?

        Args:
            seller_price: The seller's current asking price.
            context: Current negotiation state.

        Returns:
            True if the offer is acceptable.
        """

    @abstractmethod
    def next_offer(self, seller_price: float, context: NegotiationContext) -> float:
        """Calculate our next counter-offer price.

        Args:
            seller_price: The seller's current asking price.
            context: Current negotiation state.

        Returns:
            The price we should offer next.
        """

    @abstractmethod
    def should_walk_away(self, seller_price: float, context: NegotiationContext) -> bool:
        """Should we abandon this negotiation?

        Args:
            seller_price: The seller's current asking price.
            context: Current negotiation state.

        Returns:
            True if we should stop negotiating.
        """
