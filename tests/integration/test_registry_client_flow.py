# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Integration tests: registry -> client -> flow coordination.

Tests the pipeline from discovering sellers in the registry through
creating clients and initiating flows. Validates caching behavior,
trust verification, and error propagation across module boundaries.
"""

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ad_buyer.clients.unified_client import Protocol, UnifiedClient, UnifiedResult
from ad_buyer.models.buyer_identity import (
    AccessTier,
    BuyerContext,
    BuyerIdentity,
    DealType,
)
from ad_buyer.registry.cache import SellerCache
from ad_buyer.registry.client import RegistryClient
from ad_buyer.registry.models import (
    AgentCapability,
    AgentCard,
    AgentTrustInfo,
    TrustLevel,
)


class TestRegistryDiscoveryToClientCreation:
    """Tests registry discovery feeding into UnifiedClient creation."""

    @pytest.mark.asyncio
    async def test_discover_sellers_then_create_clients(
        self,
        seller_agent_cards: list[AgentCard],
    ):
        """Discover sellers from registry, then create UnifiedClients for each."""
        registry = RegistryClient(registry_url="http://fake-registry.test")

        # Mock the registry HTTP call
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "agents": [card.model_dump() for card in seller_agent_cards],
        }

        with patch("httpx.AsyncClient") as MockAsyncClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(return_value=mock_response)
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

            sellers = await registry.discover_sellers(capabilities_filter=["ctv"])

        assert len(sellers) == 2

        # Create UnifiedClients from discovered sellers
        clients = []
        for seller in sellers:
            client = UnifiedClient(
                base_url=seller.url,
                protocol=Protocol.MCP if "mcp" in seller.protocols else Protocol.A2A,
            )
            clients.append(client)

        assert len(clients) == 2
        assert clients[0].base_url == "http://seller-streaming.example.com"
        assert clients[0].default_protocol == Protocol.MCP

        # Clean up
        for c in clients:
            await c.close()

    @pytest.mark.asyncio
    async def test_registry_cache_avoids_repeated_calls(
        self,
        seller_agent_cards: list[AgentCard],
    ):
        """Second discover call should hit cache, not make HTTP request."""
        registry = RegistryClient(
            registry_url="http://fake-registry.test",
            cache_ttl_seconds=60.0,
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "agents": [card.model_dump() for card in seller_agent_cards],
        }

        call_count = 0

        async def mock_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return mock_response

        with patch("httpx.AsyncClient") as MockAsyncClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.get = mock_get
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

            # First call - should hit HTTP
            sellers1 = await registry.discover_sellers(capabilities_filter=["ctv"])
            assert len(sellers1) == 2
            assert call_count == 1

            # Second call - should hit cache
            sellers2 = await registry.discover_sellers(capabilities_filter=["ctv"])
            assert len(sellers2) == 2
            assert call_count == 1  # No additional HTTP call

    @pytest.mark.asyncio
    async def test_registry_failure_returns_empty_list(self):
        """Network errors from registry should return empty, not raise."""
        registry = RegistryClient(registry_url="http://fake-registry.test")

        with patch("httpx.AsyncClient") as MockAsyncClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

            sellers = await registry.discover_sellers()

        assert sellers == []


class TestRegistryTrustVerification:
    """Tests trust verification influencing client behavior."""

    @pytest.mark.asyncio
    async def test_verify_trusted_agent(self):
        """Verified agent should return correct trust info."""
        registry = RegistryClient(registry_url="http://fake-registry.test")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "is_registered": True,
            "trust_level": "verified",
            "registry_id": "reg-001",
        }

        with patch("httpx.AsyncClient") as MockAsyncClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(return_value=mock_response)
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

            trust = await registry.verify_agent("http://seller.example.com")

        assert trust.is_registered is True
        assert trust.trust_level == TrustLevel.VERIFIED
        assert trust.registry_id == "reg-001"

    @pytest.mark.asyncio
    async def test_verify_unknown_agent(self):
        """Unknown agent should return unregistered trust info."""
        registry = RegistryClient(registry_url="http://fake-registry.test")

        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("httpx.AsyncClient") as MockAsyncClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(return_value=mock_response)
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

            trust = await registry.verify_agent("http://unknown-seller.example.com")

        assert trust.is_registered is False
        assert trust.trust_level == TrustLevel.UNKNOWN


class TestSellerCacheIntegration:
    """Tests cache behavior with real agent card operations."""

    def test_cache_stores_and_retrieves_individual_cards(
        self,
        seller_agent_cards: list[AgentCard],
    ):
        """Cache should correctly store and retrieve individual cards."""
        cache = SellerCache(ttl_seconds=60.0)

        for card in seller_agent_cards:
            cache.put(card.agent_id, card)

        retrieved = cache.get("seller-streaming-001")
        assert retrieved is not None
        assert retrieved.name == "StreamCo Ad Server"
        assert TrustLevel.VERIFIED == retrieved.trust_level

    def test_cache_stores_and_retrieves_lists(
        self,
        seller_agent_cards: list[AgentCard],
    ):
        """Cache should handle list storage correctly."""
        cache = SellerCache(ttl_seconds=60.0)
        cache.put_list("all_sellers", seller_agent_cards)

        retrieved = cache.get_list("all_sellers")
        assert retrieved is not None
        assert len(retrieved) == 2

    def test_cache_expiry_works(self):
        """Expired entries should return None."""
        cache = SellerCache(ttl_seconds=0.01)  # Very short TTL
        card = AgentCard(
            agent_id="test",
            name="Test",
            url="http://test.example.com",
        )
        cache.put("test", card)

        # Wait for expiry
        time.sleep(0.02)
        assert cache.get("test") is None

    def test_cache_invalidation(
        self,
        seller_agent_cards: list[AgentCard],
    ):
        """Invalidating a key should remove both card and list entries."""
        cache = SellerCache(ttl_seconds=60.0)
        card = seller_agent_cards[0]

        cache.put(card.agent_id, card)
        cache.put_list(card.agent_id, seller_agent_cards)

        cache.invalidate(card.agent_id)
        assert cache.get(card.agent_id) is None
        assert cache.get_list(card.agent_id) is None


class TestRegistryToClientToIdentityFlow:
    """End-to-end: registry discover -> create client -> set identity -> check access."""

    @pytest.mark.asyncio
    async def test_full_discovery_to_access_tier_flow(
        self,
        seller_agent_cards: list[AgentCard],
        advertiser_identity: BuyerIdentity,
    ):
        """Discover sellers, create client with identity, verify access tier propagates."""
        registry = RegistryClient(registry_url="http://fake-registry.test")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "agents": [card.model_dump() for card in seller_agent_cards],
        }

        with patch("httpx.AsyncClient") as MockAsyncClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(return_value=mock_response)
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

            sellers = await registry.discover_sellers()

        # Pick a seller and create client with buyer identity
        seller = sellers[0]
        client = UnifiedClient(
            base_url=seller.url,
            buyer_identity=advertiser_identity,
        )

        # Verify identity context propagates through the client
        assert client.get_access_tier() == "advertiser"
        context = client._get_identity_context()
        assert context["advertiser_id"] == "coca-cola-789"
        assert context["access_tier"] == "advertiser"

        await client.close()

    @pytest.mark.asyncio
    async def test_buyer_registration_flow(self):
        """Test that buyer can register itself in the registry."""
        registry = RegistryClient(registry_url="http://fake-registry.test")

        buyer_card = AgentCard(
            agent_id="buyer-agent-001",
            name="Ad Buyer Agent",
            url="http://buyer.example.com",
            protocols=["mcp"],
            capabilities=[
                AgentCapability(
                    name="buying",
                    description="Programmatic media buying",
                    tags=["dsp", "buyer"],
                ),
            ],
        )

        mock_response = MagicMock()
        mock_response.status_code = 201

        with patch("httpx.AsyncClient") as MockAsyncClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(return_value=mock_response)
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

            success = await registry.register_buyer(buyer_card)

        assert success is True
