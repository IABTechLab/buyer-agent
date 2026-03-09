# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Registry client for discovering seller agents via IAB AAMP.

Queries the IAB AAMP agent registry to find seller agents by capability,
fetch agent cards from .well-known/agent.json endpoints, register the
buyer agent, and verify agent trust status.

Registry URL is configurable; defaults to localhost for testing.
"""

import logging
from typing import Optional

import httpx
from pydantic import ValidationError

from .cache import SellerCache
from .models import AgentCapability, AgentCard, AgentTrustInfo, TrustLevel

logger = logging.getLogger(__name__)


class RegistryClient:
    """Client for discovering seller agents via the IAB AAMP agent registry.

    Args:
        registry_url: Base URL of the agent registry.
            Defaults to http://localhost:8080/agent-registry for local dev.
        cache_ttl_seconds: TTL for the seller cache in seconds. Defaults to 300.
        timeout: HTTP request timeout in seconds. Defaults to 15.
    """

    def __init__(
        self,
        registry_url: str = "http://localhost:8080/agent-registry",
        cache_ttl_seconds: float = 300.0,
        timeout: float = 15.0,
    ):
        self._registry_url = registry_url.rstrip("/")
        self._cache = SellerCache(ttl_seconds=cache_ttl_seconds)
        self._timeout = timeout

    async def discover_sellers(
        self,
        capabilities_filter: Optional[list[str]] = None,
    ) -> list[AgentCard]:
        """Discover seller agents from the registry.

        Queries the registry for seller agents, optionally filtering by
        capability names. Results are cached to avoid repeated calls.

        Args:
            capabilities_filter: Optional list of capability names to filter by
                (e.g., ["ctv", "display"]). If None, returns all sellers.

        Returns:
            List of AgentCard for discovered sellers.
        """
        # Build cache key from filter
        cache_key = f"discover:{','.join(sorted(capabilities_filter or []))}"

        # Check cache first
        cached = self._cache.get_list(cache_key)
        if cached is not None:
            return cached

        # Query registry
        params: dict[str, str] = {"agent_type": "seller"}
        if capabilities_filter:
            params["capabilities"] = ",".join(capabilities_filter)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"{self._registry_url}/agents",
                    params=params,
                )

            if response.status_code != 200:
                logger.warning(
                    "Registry returned %d when discovering sellers",
                    response.status_code,
                )
                return []

            data = response.json()
            agents_data = data.get("agents", [])
            sellers = [AgentCard(**agent) for agent in agents_data]

            # Cache the result
            self._cache.put_list(cache_key, sellers)

            # Also cache individual cards
            for seller in sellers:
                self._cache.put(seller.agent_id, seller)

            return sellers

        except (httpx.HTTPError, ValidationError, ValueError) as e:
            logger.warning("Failed to discover sellers: %s", e)
            return []

    async def fetch_agent_card(self, agent_url: str) -> Optional[AgentCard]:
        """Fetch an agent's card from its .well-known/agent.json endpoint.

        Results are cached by agent URL to avoid repeated fetches.

        Args:
            agent_url: Base URL of the agent (e.g., https://seller.example.com)

        Returns:
            AgentCard if successfully fetched, None otherwise.
        """
        # Check cache first
        cache_key = f"card:{agent_url}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        url = f"{agent_url.rstrip('/')}/.well-known/agent.json"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url)

            if response.status_code != 200:
                logger.debug(
                    "Agent card not found at %s (status %d)", url, response.status_code
                )
                return None

            card = AgentCard(**response.json())

            # Cache the result
            self._cache.put(cache_key, card)

            return card

        except (httpx.HTTPError, ValidationError, ValueError) as e:
            logger.debug("Failed to fetch agent card from %s: %s", url, e)
            return None

    async def register_buyer(self, buyer_card: AgentCard) -> bool:
        """Register the buyer agent in the registry.

        Submits the buyer's agent card to the registry so that seller
        agents can discover and verify this buyer.

        Args:
            buyer_card: The buyer's AgentCard with identity and capabilities.

        Returns:
            True if registration succeeded, False otherwise.
        """
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._registry_url}/agents",
                    json=buyer_card.model_dump(),
                )

            if response.status_code in (200, 201):
                logger.info(
                    "Successfully registered buyer %s in registry",
                    buyer_card.agent_id,
                )
                return True

            logger.warning(
                "Failed to register buyer (status %d)", response.status_code
            )
            return False

        except httpx.HTTPError as e:
            logger.warning("Failed to register buyer: %s", e)
            return False

    async def verify_agent(self, agent_url: str) -> AgentTrustInfo:
        """Verify an agent's trust status in the registry.

        Checks whether the agent at the given URL is registered in the
        AAMP registry and what its trust level is.

        Args:
            agent_url: URL of the agent to verify.

        Returns:
            AgentTrustInfo with trust status details.
        """
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"{self._registry_url}/verify",
                    params={"agent_url": agent_url},
                )

            if response.status_code == 200:
                data = response.json()
                return AgentTrustInfo(
                    agent_url=agent_url,
                    is_registered=data.get("is_registered", False),
                    trust_level=TrustLevel(data.get("trust_level", "unknown")),
                    registry_id=data.get("registry_id"),
                )

            # Not found or error - return unknown trust
            return AgentTrustInfo(
                agent_url=agent_url,
                is_registered=False,
                trust_level=TrustLevel.UNKNOWN,
            )

        except (httpx.HTTPError, ValueError) as e:
            logger.debug("Failed to verify agent %s: %s", agent_url, e)
            return AgentTrustInfo(
                agent_url=agent_url,
                is_registered=False,
                trust_level=TrustLevel.UNKNOWN,
            )
