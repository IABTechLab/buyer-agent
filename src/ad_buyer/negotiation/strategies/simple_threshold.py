# Author: Agent Range
# Donated to IAB Tech Lab

"""Simple threshold-based negotiation strategy.

The v1 strategy for buyer-side negotiation. Uses fixed thresholds
and linear concession steps. Suitable for straightforward negotiations
where the buyer has clear budget limits.
"""

from ..strategy import NegotiationContext, NegotiationStrategy


class SimpleThresholdStrategy(NegotiationStrategy):
    """Threshold-based negotiation with linear concession.

    Decision logic:
    - Accept if seller_price <= max_cpm
    - Counter by moving up from last offer by concession_step
    - Walk away after max_rounds or if seller isn't moving

    Args:
        target_cpm: Ideal price we want to pay.
        max_cpm: Absolute ceiling we will accept.
        concession_step: How much to concede per round (e.g., $1).
        max_rounds: Maximum negotiation rounds before walking away.
    """

    def __init__(
        self,
        target_cpm: float,
        max_cpm: float,
        concession_step: float,
        max_rounds: int,
    ) -> None:
        self.target_cpm = target_cpm
        self.max_cpm = max_cpm
        self.concession_step = concession_step
        self.max_rounds = max_rounds

    def should_accept(self, seller_price: float, context: NegotiationContext) -> bool:
        """Accept if seller's price is at or below our max_cpm."""
        return seller_price <= self.max_cpm

    def next_offer(self, seller_price: float, context: NegotiationContext) -> float:
        """Calculate next offer: start at target, concede by step each round.

        The offer is capped at max_cpm to avoid overpaying.
        """
        if context.our_last_offer is None:
            # First offer: start at target
            return self.target_cpm

        # Concede by one step from our last offer
        next_price = context.our_last_offer + self.concession_step
        # Never exceed our ceiling
        return min(next_price, self.max_cpm)

    def should_walk_away(self, seller_price: float, context: NegotiationContext) -> bool:
        """Walk away once max rounds are reached or if the seller isn't moving."""
        # Walk away after max_rounds
        if context.rounds_completed >= self.max_rounds:
            return True

        # Walk away if seller hasn't moved (requires at least one prior round)
        if (
            context.seller_previous_price is not None
            and context.rounds_completed > 0
            and seller_price >= context.seller_previous_price
        ):
            return True

        return False
