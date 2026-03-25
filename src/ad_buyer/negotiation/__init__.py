# Author: Agent Range
# Donated to IAB Tech Lab

"""Multi-turn negotiation client with swappable strategy pattern."""

from .client import NegotiationClient
from .models import NegotiationOutcome, NegotiationResult, NegotiationRound, NegotiationSession
from .strategy import NegotiationContext, NegotiationStrategy

__all__ = [
    "NegotiationStrategy",
    "NegotiationContext",
    "NegotiationClient",
    "NegotiationSession",
    "NegotiationRound",
    "NegotiationResult",
    "NegotiationOutcome",
]
