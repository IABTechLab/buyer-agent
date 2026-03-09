# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for agent registry discovery client.

Tests cover:
- AgentCard and related model creation/validation
- RegistryClient seller discovery (mocked registry responses)
- RegistryClient agent card fetching
- RegistryClient buyer registration
- RegistryClient trust verification
- SellerCache hit/miss/expiry behavior
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ad_buyer.registry.cache import SellerCache
from ad_buyer.registry.client import RegistryClient
from ad_buyer.registry.models import (
    AgentCapability,
    AgentCard,
    AgentTrustInfo,
    TrustLevel,
)


# =============================================================================
# Model Tests
# =============================================================================


class TestAgentCard:
    """Tests for AgentCard model."""

    def test_create_minimal(self):
        card = AgentCard(
            agent_id="seller-001",
            name="Test Seller",
            url="https://seller.example.com",
        )
        assert card.agent_id == "seller-001"
        assert card.name == "Test Seller"
        assert card.url == "https://seller.example.com"
        assert card.protocols == []
        assert card.capabilities == []
        assert card.trust_level == TrustLevel.UNKNOWN

    def test_create_full(self):
        card = AgentCard(
            agent_id="seller-002",
            name="Premium Seller",
            url="https://premium.example.com",
            protocols=["mcp", "a2a", "rest"],
            capabilities=[
                AgentCapability(name="ctv", description="CTV inventory"),
                AgentCapability(name="display", description="Display ads"),
            ],
            trust_level=TrustLevel.VERIFIED,
        )
        assert card.agent_id == "seller-002"
        assert len(card.protocols) == 3
        assert len(card.capabilities) == 2
        assert card.trust_level == TrustLevel.VERIFIED

    def test_card_to_dict(self):
        card = AgentCard(
            agent_id="seller-001",
            name="Test",
            url="https://test.com",
        )
        d = card.model_dump()
        assert d["agent_id"] == "seller-001"
        assert d["name"] == "Test"

    def test_card_from_dict(self):
        data = {
            "agent_id": "seller-003",
            "name": "From Dict",
            "url": "https://dict.example.com",
            "protocols": ["a2a"],
            "capabilities": [{"name": "video", "description": "Video ads"}],
            "trust_level": "registered",
        }
        card = AgentCard(**data)
        assert card.agent_id == "seller-003"
        assert card.protocols == ["a2a"]
        assert card.trust_level == TrustLevel.REGISTERED


class TestAgentCapability:
    """Tests for AgentCapability model."""

    def test_create(self):
        cap = AgentCapability(name="ctv", description="CTV inventory")
        assert cap.name == "ctv"
        assert cap.description == "CTV inventory"

    def test_with_tags(self):
        cap = AgentCapability(
            name="display",
            description="Display advertising",
            tags=["programmatic", "rtb"],
        )
        assert cap.tags == ["programmatic", "rtb"]


class TestAgentTrustInfo:
    """Tests for AgentTrustInfo model."""

    def test_create(self):
        info = AgentTrustInfo(
            agent_url="https://seller.example.com",
            is_registered=True,
            trust_level=TrustLevel.VERIFIED,
            registry_id="aamp-abc123",
        )
        assert info.is_registered is True
        assert info.trust_level == TrustLevel.VERIFIED
        assert info.registry_id == "aamp-abc123"

    def test_unregistered(self):
        info = AgentTrustInfo(
            agent_url="https://unknown.example.com",
            is_registered=False,
            trust_level=TrustLevel.UNKNOWN,
        )
        assert info.is_registered is False
        assert info.registry_id is None


class TestTrustLevel:
    """Tests for TrustLevel enum."""

    def test_values(self):
        assert TrustLevel.UNKNOWN == "unknown"
        assert TrustLevel.REGISTERED == "registered"
        assert TrustLevel.VERIFIED == "verified"
        assert TrustLevel.PREFERRED == "preferred"
        assert TrustLevel.BLOCKED == "blocked"


# =============================================================================
# SellerCache Tests
# =============================================================================


class TestSellerCache:
    """Tests for SellerCache with TTL."""

    def test_cache_put_and_get(self):
        cache = SellerCache(ttl_seconds=60)
        card = AgentCard(
            agent_id="s1", name="Seller 1", url="https://s1.example.com"
        )
        cache.put("s1", card)
        result = cache.get("s1")
        assert result is not None
        assert result.agent_id == "s1"

    def test_cache_miss(self):
        cache = SellerCache(ttl_seconds=60)
        result = cache.get("nonexistent")
        assert result is None

    def test_cache_expiry(self):
        cache = SellerCache(ttl_seconds=0.1)
        card = AgentCard(
            agent_id="s1", name="Seller 1", url="https://s1.example.com"
        )
        cache.put("s1", card)
        # Should be available immediately
        assert cache.get("s1") is not None
        # Wait for expiry
        time.sleep(0.15)
        assert cache.get("s1") is None

    def test_cache_put_list(self):
        cache = SellerCache(ttl_seconds=60)
        cards = [
            AgentCard(agent_id="s1", name="S1", url="https://s1.com"),
            AgentCard(agent_id="s2", name="S2", url="https://s2.com"),
        ]
        cache.put_list("sellers", cards)
        result = cache.get_list("sellers")
        assert result is not None
        assert len(result) == 2

    def test_cache_list_expiry(self):
        cache = SellerCache(ttl_seconds=0.1)
        cards = [
            AgentCard(agent_id="s1", name="S1", url="https://s1.com"),
        ]
        cache.put_list("key", cards)
        assert cache.get_list("key") is not None
        time.sleep(0.15)
        assert cache.get_list("key") is None

    def test_cache_clear(self):
        cache = SellerCache(ttl_seconds=60)
        card = AgentCard(agent_id="s1", name="S1", url="https://s1.com")
        cache.put("s1", card)
        cache.put_list("all", [card])
        cache.clear()
        assert cache.get("s1") is None
        assert cache.get_list("all") is None

    def test_cache_invalidate(self):
        cache = SellerCache(ttl_seconds=60)
        card = AgentCard(agent_id="s1", name="S1", url="https://s1.com")
        cache.put("s1", card)
        cache.invalidate("s1")
        assert cache.get("s1") is None


# =============================================================================
# RegistryClient Tests
# =============================================================================


class TestRegistryClient:
    """Tests for RegistryClient."""

    def _make_client(self, registry_url: str = "https://registry.example.com"):
        return RegistryClient(registry_url=registry_url, cache_ttl_seconds=60)

    @pytest.mark.asyncio
    async def test_discover_sellers_success(self):
        """Test discovering sellers from registry."""
        client = self._make_client()
        mock_response_data = [
            {
                "agent_id": "seller-001",
                "name": "CTV Seller",
                "url": "https://ctv-seller.com",
                "protocols": ["mcp", "a2a"],
                "capabilities": [{"name": "ctv", "description": "CTV inventory"}],
                "trust_level": "verified",
            },
            {
                "agent_id": "seller-002",
                "name": "Display Seller",
                "url": "https://display-seller.com",
                "protocols": ["a2a"],
                "capabilities": [{"name": "display", "description": "Display ads"}],
                "trust_level": "registered",
            },
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"agents": mock_response_data}

        with patch("ad_buyer.registry.client.httpx.AsyncClient") as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_http

            sellers = await client.discover_sellers()

        assert len(sellers) == 2
        assert sellers[0].agent_id == "seller-001"
        assert sellers[0].trust_level == TrustLevel.VERIFIED
        assert sellers[1].agent_id == "seller-002"

    @pytest.mark.asyncio
    async def test_discover_sellers_with_filter(self):
        """Test discovering sellers filtered by capability."""
        client = self._make_client()
        mock_response_data = [
            {
                "agent_id": "seller-001",
                "name": "CTV Seller",
                "url": "https://ctv-seller.com",
                "protocols": ["mcp"],
                "capabilities": [{"name": "ctv", "description": "CTV inventory"}],
                "trust_level": "verified",
            },
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"agents": mock_response_data}

        with patch("ad_buyer.registry.client.httpx.AsyncClient") as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_http

            sellers = await client.discover_sellers(capabilities_filter=["ctv"])

        assert len(sellers) == 1
        assert sellers[0].capabilities[0].name == "ctv"

    @pytest.mark.asyncio
    async def test_discover_sellers_registry_error(self):
        """Test graceful handling of registry errors."""
        client = self._make_client()

        with patch("ad_buyer.registry.client.httpx.AsyncClient") as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(side_effect=httpx.HTTPError("Connection failed"))
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_http

            sellers = await client.discover_sellers()

        assert sellers == []

    @pytest.mark.asyncio
    async def test_discover_sellers_uses_cache(self):
        """Test that repeated discover calls use the cache."""
        client = self._make_client()
        mock_response_data = [
            {
                "agent_id": "seller-001",
                "name": "Cached Seller",
                "url": "https://cached.com",
                "protocols": [],
                "capabilities": [],
                "trust_level": "unknown",
            },
        ]

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"agents": mock_response_data}

        with patch("ad_buyer.registry.client.httpx.AsyncClient") as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_http

            # First call - hits registry
            sellers1 = await client.discover_sellers()
            # Second call - should use cache
            sellers2 = await client.discover_sellers()

        assert len(sellers1) == 1
        assert len(sellers2) == 1
        # HTTP should only be called once (second call uses cache)
        assert mock_http.get.call_count == 1

    @pytest.mark.asyncio
    async def test_fetch_agent_card_success(self):
        """Test fetching a single agent card."""
        client = self._make_client()
        card_data = {
            "agent_id": "seller-abc",
            "name": "Test Agent",
            "url": "https://agent.example.com",
            "protocols": ["a2a", "mcp"],
            "capabilities": [],
            "trust_level": "verified",
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = card_data

        with patch("ad_buyer.registry.client.httpx.AsyncClient") as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_http

            card = await client.fetch_agent_card("https://agent.example.com")

        assert card is not None
        assert card.agent_id == "seller-abc"
        assert card.name == "Test Agent"

    @pytest.mark.asyncio
    async def test_fetch_agent_card_not_found(self):
        """Test fetching agent card when not available."""
        client = self._make_client()

        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("ad_buyer.registry.client.httpx.AsyncClient") as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_http

            card = await client.fetch_agent_card("https://nonexistent.com")

        assert card is None

    @pytest.mark.asyncio
    async def test_fetch_agent_card_http_error(self):
        """Test fetching agent card with network error."""
        client = self._make_client()

        with patch("ad_buyer.registry.client.httpx.AsyncClient") as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(side_effect=httpx.HTTPError("timeout"))
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_http

            card = await client.fetch_agent_card("https://timeout.com")

        assert card is None

    @pytest.mark.asyncio
    async def test_register_buyer_success(self):
        """Test registering the buyer agent."""
        client = self._make_client()
        buyer_card = AgentCard(
            agent_id="buyer-001",
            name="Test Buyer DSP",
            url="https://buyer.example.com",
            protocols=["mcp", "a2a"],
        )

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"registered": True, "agent_id": "buyer-001"}

        with patch("ad_buyer.registry.client.httpx.AsyncClient") as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_http

            result = await client.register_buyer(buyer_card)

        assert result is True

    @pytest.mark.asyncio
    async def test_register_buyer_failure(self):
        """Test buyer registration failure."""
        client = self._make_client()
        buyer_card = AgentCard(
            agent_id="buyer-001",
            name="Test Buyer",
            url="https://buyer.example.com",
        )

        mock_response = MagicMock()
        mock_response.status_code = 400

        with patch("ad_buyer.registry.client.httpx.AsyncClient") as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_http

            result = await client.register_buyer(buyer_card)

        assert result is False

    @pytest.mark.asyncio
    async def test_verify_agent_registered(self):
        """Test verifying a registered agent."""
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "is_registered": True,
            "trust_level": "verified",
            "registry_id": "aamp-xyz789",
        }

        with patch("ad_buyer.registry.client.httpx.AsyncClient") as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_http

            info = await client.verify_agent("https://trusted-seller.com")

        assert info.is_registered is True
        assert info.trust_level == TrustLevel.VERIFIED
        assert info.registry_id == "aamp-xyz789"

    @pytest.mark.asyncio
    async def test_verify_agent_not_registered(self):
        """Test verifying an unregistered agent."""
        client = self._make_client()
        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("ad_buyer.registry.client.httpx.AsyncClient") as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_http

            info = await client.verify_agent("https://unknown-agent.com")

        assert info.is_registered is False
        assert info.trust_level == TrustLevel.UNKNOWN

    @pytest.mark.asyncio
    async def test_verify_agent_network_error(self):
        """Test verify_agent with network error returns unknown."""
        client = self._make_client()

        with patch("ad_buyer.registry.client.httpx.AsyncClient") as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(side_effect=httpx.HTTPError("network"))
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_http

            info = await client.verify_agent("https://unreachable.com")

        assert info.is_registered is False
        assert info.trust_level == TrustLevel.UNKNOWN

    @pytest.mark.asyncio
    async def test_fetch_agent_card_caches_result(self):
        """Test that fetched agent cards are cached."""
        client = self._make_client()
        card_data = {
            "agent_id": "seller-cached",
            "name": "Cached Agent",
            "url": "https://cached-agent.com",
            "protocols": ["a2a"],
            "capabilities": [],
            "trust_level": "registered",
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = card_data

        with patch("ad_buyer.registry.client.httpx.AsyncClient") as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_http

            # First fetch - hits network
            card1 = await client.fetch_agent_card("https://cached-agent.com")
            # Second fetch - should use cache
            card2 = await client.fetch_agent_card("https://cached-agent.com")

        assert card1 is not None
        assert card2 is not None
        assert card1.agent_id == card2.agent_id
        # Only one HTTP call should have been made
        assert mock_http.get.call_count == 1
