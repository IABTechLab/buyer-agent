# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""AAMP registry client backed by the REAL IAB agent registry (EP-5.1).

Speaks the real registry API (``/api/agents`` with the ``{"success": true,
"data": ...}`` envelope, JWT bearer auth) through the shared contract
library's :class:`iab_agentic_primitives.registry_client.RegistryClient`,
and adapts the results onto the buyer's existing discovery surface
(:class:`~ad_buyer.registry.models.AgentCard` /
:class:`~ad_buyer.registry.models.AgentTrustInfo`) so callers —
``discovery_service``, ``MultiSellerOrchestrator``, the MCP tools — are
untouched.

Selection is config, not code: :func:`ad_buyer.registry.create_registry_client`
returns this client when ``AAMP_REGISTRY_URL`` is configured and the legacy
:class:`~ad_buyer.registry.client.RegistryClient` otherwise.

A fresh library client is created per operation (mirroring the repo's
per-request httpx pattern) so long-lived instances survive event-loop
churn across ``run_async`` calls.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlparse

import httpx
from iab_agentic_primitives.registry_client import (
    ENV_BACKEND,
    ENV_URL,
    RegistryAgent,
    RegistryError,
)
from iab_agentic_primitives.registry_client import (
    RegistryClient as LibRegistryClient,
)

from .cache import SellerCache
from .models import AgentCapability, AgentCard, AgentTrustInfo, TrustLevel

logger = logging.getLogger(__name__)


class _TolerantLibClient(LibRegistryClient):
    """Library client tolerant of the hosted registry's null list fields.

    KNOWN LIB GAP (EP-5.1, verified against registry-uat.iabtechlab.com):
    the real registry serializes unset list-typed columns as JSON ``null``
    (``endorsements``, ``iab_capabilities``, ``iab_subcategories``, ...),
    but ``RegistryAgent`` declares them as ``list[...]`` with a
    default_factory, so ``model_validate`` rejects ``null``. Until the lib
    coerces ``None`` to the field default, this shim drops null-valued keys
    from envelope payloads before validation (every RegistryAgent field
    except ``agent_name``/``primary_domain`` is optional, so dropping is
    lossless).
    """

    @staticmethod
    def _scrub(record: Any) -> Any:
        if isinstance(record, dict):
            return {k: v for k, v in record.items() if v is not None}
        return record

    @staticmethod
    def _data(response: httpx.Response) -> Any:
        data = LibRegistryClient._data(response)
        if isinstance(data, dict):
            if isinstance(data.get("agents"), list):
                return {
                    **data,
                    "agents": [_TolerantLibClient._scrub(a) for a in data["agents"]],
                }
            return _TolerantLibClient._scrub(data)
        return data


def _normalize_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def _to_agent_card(agent: RegistryAgent) -> AgentCard:
    """Map a real-registry agent record onto the buyer's AgentCard."""
    capability_names: list[str] = []
    for name in [*agent.capabilities, *agent.iab_capabilities]:
        if name and name not in capability_names:
            capability_names.append(name)
    return AgentCard(
        agent_id=str(agent.id) if agent.id is not None else agent.agent_name,
        name=agent.agent_name,
        url=agent.endpoint_url or agent.repository_url or "",
        protocols=[agent.protocol_type] if agent.protocol_type else [],
        capabilities=[AgentCapability(name=name, description="") for name in capability_names],
        trust_level=_trust_level(agent),
    )


def _trust_level(agent: RegistryAgent) -> TrustLevel:
    """Registry-verified trust: 'active' verification is VERIFIED, else the
    agent is merely present in the registry (REGISTERED)."""
    if agent.verification_status == "active":
        return TrustLevel.VERIFIED
    return TrustLevel.REGISTERED


class AampRegistryClient:
    """Buyer discovery client backed by the real AAMP agent registry.

    Duck-type compatible with :class:`~ad_buyer.registry.client.RegistryClient`
    (``discover_sellers`` / ``fetch_agent_card`` / ``register_buyer`` /
    ``verify_agent``), plus registry-card fetch by id (:meth:`fetch_card`).

    Args:
        base_url: Registry base URL. Defaults to ``AAMP_REGISTRY_URL``.
        auth_token: Bearer JWT. Defaults to ``AAMP_REGISTRY_AUTH_TOKEN`` /
            ``AAMP_REGISTRY_TOKEN``. Never logged.
        cache_ttl_seconds: TTL for the discovery cache. Defaults to 300.
        timeout: HTTP request timeout in seconds. Defaults to 15.
        transport: Optional httpx transport (tests pass an ASGITransport
            wrapping the library's in-process registry double).
    """

    def __init__(
        self,
        base_url: str | None = None,
        auth_token: str | None = None,
        *,
        cache_ttl_seconds: float = 300.0,
        timeout: float = 15.0,
        transport: Any = None,
    ):
        self._base_url = base_url
        self._auth_token = auth_token
        self._timeout = timeout
        self._transport = transport
        self._cache = SellerCache(ttl_seconds=cache_ttl_seconds)
        resolved = base_url or os.environ.get(ENV_URL, "")
        #: Where this client points (for introspection/logging; may be "").
        self.base_url = resolved.rstrip("/")

    def _make_client(self) -> LibRegistryClient:
        """Fresh library client per operation (event-loop-churn safe)."""
        # An explicitly-supplied URL means "the real IAB registry" unless the
        # environment pins a backend; without one, the library's env/default
        # resolution applies.
        backend = os.environ.get(ENV_BACKEND) or ("IAB_SANDBOX" if self._base_url else None)
        return _TolerantLibClient(
            backend=backend,
            base_url=self._base_url,
            auth_token=self._auth_token,
            transport=self._transport,
            timeout=self._timeout,
        )

    # -- discovery ----------------------------------------------------------

    async def discover_sellers(
        self,
        capabilities_filter: list[str] | None = None,
    ) -> list[AgentCard]:
        """Discover seller agents from the real registry.

        The real API has no capability or agent-type query filter, so the
        capability filter is applied client-side against each agent's
        declared ``capabilities`` + ``iab_capabilities``.
        """
        cache_key = f"discover:{','.join(sorted(capabilities_filter or []))}"
        cached = self._cache.get_list(cache_key)
        if cached is not None:
            return cached

        try:
            async with self._make_client() as client:
                agents = await client.list_agents()
        except (RegistryError, httpx.HTTPError, ValueError) as exc:
            logger.warning("Failed to discover sellers from AAMP registry: %s", exc)
            return []

        cards = [_to_agent_card(agent) for agent in agents]
        if capabilities_filter:
            wanted = {c.lower() for c in capabilities_filter}
            cards = [
                card
                for card in cards
                if wanted & {c.name.lower() for c in card.capabilities}
            ]

        self._cache.put_list(cache_key, cards)
        for card in cards:
            self._cache.put(card.agent_id, card)
        return cards

    async def fetch_card(self, agent_id: str) -> AgentCard | None:
        """Fetch a single agent card by registry id (``GET /api/agents/:id``)."""
        cache_key = f"card-id:{agent_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            async with self._make_client() as client:
                agent = await client.get_agent(agent_id)
        except (RegistryError, httpx.HTTPError, ValueError) as exc:
            logger.debug("Failed to fetch agent card %s from AAMP registry: %s", agent_id, exc)
            return None
        card = _to_agent_card(agent)
        self._cache.put(cache_key, card)
        return card

    async def fetch_agent_card(self, agent_url: str) -> AgentCard | None:
        """Fetch an agent's card from its ``.well-known/agent.json`` endpoint.

        Registry-independent (same contract as the legacy client): any
        A2A-compliant agent serves its own card.
        """
        cache_key = f"card:{agent_url}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        url = f"{agent_url.rstrip('/')}/.well-known/agent.json"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url)
            if response.status_code != 200:
                logger.debug("Agent card not found at %s (status %d)", url, response.status_code)
                return None
            card = AgentCard(**response.json())
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("Failed to fetch agent card from %s: %s", url, exc)
            return None
        except Exception as exc:  # pydantic ValidationError
            logger.debug("Invalid agent card from %s: %s", url, exc)
            return None
        self._cache.put(cache_key, card)
        return card

    # -- registration / trust ------------------------------------------------

    async def register_buyer(self, buyer_card: AgentCard) -> bool:
        """Register the buyer agent in the real registry.

        Maps the buyer's card onto the registry's agent shape; the registry
        requires ``agent_name`` + ``primary_domain`` (which must match the
        JWT's company domain) and an ``endpoint_url`` for remote agents.
        """
        host = urlparse(buyer_card.url).hostname or ""
        protocol_type = next(
            (p for p in buyer_card.protocols if p in ("a2a", "mcp")),
            None,
        )
        record: dict[str, Any] = {
            "agent_name": buyer_card.name,
            "primary_domain": host,
            "type": "remote",
            "endpoint_url": buyer_card.url,
            "capabilities": [c.name for c in buyer_card.capabilities],
            "industry_roles": ["buyer"],
        }
        if protocol_type:
            record["protocol_type"] = protocol_type
        try:
            async with self._make_client() as client:
                stored = await client.register_agent(record)
            logger.info(
                "Registered buyer %s in AAMP registry (id=%s)",
                buyer_card.agent_id,
                stored.id,
            )
            return True
        except (RegistryError, httpx.HTTPError, ValueError) as exc:
            logger.warning("Failed to register buyer in AAMP registry: %s", exc)
            return False

    async def verify_agent(self, agent_url: str) -> AgentTrustInfo:
        """Verify an agent's registration in the real registry by endpoint URL.

        The real API has no URL-lookup endpoint, so this lists agents and
        matches ``endpoint_url``.
        """
        wanted = _normalize_url(agent_url)
        try:
            async with self._make_client() as client:
                agents = await client.list_agents()
                registry_id = client.backend.value
        except (RegistryError, httpx.HTTPError, ValueError) as exc:
            logger.debug("Failed to verify agent %s in AAMP registry: %s", agent_url, exc)
            return AgentTrustInfo(
                agent_url=agent_url,
                is_registered=False,
                trust_level=TrustLevel.UNKNOWN,
            )

        for agent in agents:
            if agent.endpoint_url and _normalize_url(agent.endpoint_url) == wanted:
                return AgentTrustInfo(
                    agent_url=agent_url,
                    is_registered=True,
                    trust_level=_trust_level(agent),
                    registry_id=registry_id,
                )
        return AgentTrustInfo(
            agent_url=agent_url,
            is_registered=False,
            trust_level=TrustLevel.UNKNOWN,
        )
