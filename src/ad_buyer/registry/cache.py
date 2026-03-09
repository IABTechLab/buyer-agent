# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Seller cache with TTL for agent registry discovery.

Caches discovered seller agent cards to avoid repeated registry queries.
Supports both individual agent cards and lists of agents.
"""

import time
from typing import Optional

from .models import AgentCard


class SellerCache:
    """In-memory cache for discovered seller agent cards with TTL expiry.

    Args:
        ttl_seconds: Time-to-live in seconds for cached entries.
            Defaults to 300 (5 minutes).
    """

    def __init__(self, ttl_seconds: float = 300.0):
        self._ttl = ttl_seconds
        # key -> (timestamp, AgentCard)
        self._cards: dict[str, tuple[float, AgentCard]] = {}
        # key -> (timestamp, list[AgentCard])
        self._lists: dict[str, tuple[float, list[AgentCard]]] = {}

    def get(self, key: str) -> Optional[AgentCard]:
        """Get a cached agent card by key.

        Returns None if the key is not found or the entry has expired.
        """
        entry = self._cards.get(key)
        if entry is None:
            return None
        timestamp, card = entry
        if time.monotonic() - timestamp > self._ttl:
            del self._cards[key]
            return None
        return card

    def put(self, key: str, card: AgentCard) -> None:
        """Cache an agent card with the current timestamp."""
        self._cards[key] = (time.monotonic(), card)

    def get_list(self, key: str) -> Optional[list[AgentCard]]:
        """Get a cached list of agent cards by key.

        Returns None if the key is not found or the entry has expired.
        """
        entry = self._lists.get(key)
        if entry is None:
            return None
        timestamp, cards = entry
        if time.monotonic() - timestamp > self._ttl:
            del self._lists[key]
            return None
        return cards

    def put_list(self, key: str, cards: list[AgentCard]) -> None:
        """Cache a list of agent cards with the current timestamp."""
        self._lists[key] = (time.monotonic(), cards)

    def invalidate(self, key: str) -> None:
        """Remove a specific entry from the cache."""
        self._cards.pop(key, None)
        self._lists.pop(key, None)

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cards.clear()
        self._lists.clear()
