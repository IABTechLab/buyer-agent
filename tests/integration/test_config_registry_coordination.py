# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Integration tests: config -> registry -> client interactions.

Tests that configuration settings flow through to registry clients,
UnifiedClient construction, and identity management. Verifies the
coordination between the config, registry, and client modules.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ad_buyer.clients.unified_client import Protocol, UnifiedClient, UnifiedResult
from ad_buyer.config.settings import Settings
from ad_buyer.identity.strategy import (
    CampaignGoal,
    DealContext,
    IdentityStrategy,
    SellerRelationship,
)
from ad_buyer.models.buyer_identity import (
    AccessTier,
    BuyerContext,
    BuyerIdentity,
    DealType,
)
from ad_buyer.registry.cache import SellerCache
from ad_buyer.registry.client import RegistryClient
from ad_buyer.registry.models import AgentCapability, AgentCard, TrustLevel


class TestSettingsToClientConfiguration:
    """Tests that Settings values propagate to client construction."""

    def test_settings_seller_endpoints_parsing(self):
        """Settings should correctly parse comma-separated seller endpoints."""
        settings = Settings.model_construct(
            seller_endpoints="http://seller1.test,http://seller2.test, http://seller3.test ",
            api_key="",
            anthropic_api_key="",
            iab_server_url="http://localhost:8001",
            opendirect_base_url="http://localhost:3000/api/v2.1",
            opendirect_token=None,
            opendirect_api_key=None,
            default_llm_model="anthropic/claude-sonnet-4-5-20250929",
            manager_llm_model="anthropic/claude-opus-4-20250514",
            llm_temperature=0.3,
            llm_max_tokens=4096,
            database_url="sqlite:///./test.db",
            redis_url=None,
            crew_memory_enabled=True,
            crew_verbose=True,
            crew_max_iterations=15,
            cors_allowed_origins="",
            environment="test",
            log_level="DEBUG",
        )

        endpoints = settings.get_seller_endpoints()
        assert len(endpoints) == 3
        assert "http://seller1.test" in endpoints
        assert "http://seller3.test" in endpoints

    def test_settings_empty_endpoints(self):
        """Empty seller_endpoints should return empty list."""
        settings = Settings.model_construct(
            seller_endpoints="",
            api_key="",
            anthropic_api_key="",
            iab_server_url="http://localhost:8001",
            opendirect_base_url="http://localhost:3000/api/v2.1",
            opendirect_token=None,
            opendirect_api_key=None,
            default_llm_model="anthropic/claude-sonnet-4-5-20250929",
            manager_llm_model="anthropic/claude-opus-4-20250514",
            llm_temperature=0.3,
            llm_max_tokens=4096,
            database_url="sqlite:///./test.db",
            redis_url=None,
            crew_memory_enabled=True,
            crew_verbose=True,
            crew_max_iterations=15,
            cors_allowed_origins="",
            environment="test",
            log_level="DEBUG",
        )

        assert settings.get_seller_endpoints() == []

    def test_cors_origins_parsing(self):
        """CORS origins should be correctly parsed from settings."""
        settings = Settings.model_construct(
            cors_allowed_origins="http://localhost:3000,http://localhost:8080",
            api_key="",
            anthropic_api_key="",
            iab_server_url="http://localhost:8001",
            seller_endpoints="",
            opendirect_base_url="http://localhost:3000/api/v2.1",
            opendirect_token=None,
            opendirect_api_key=None,
            default_llm_model="anthropic/claude-sonnet-4-5-20250929",
            manager_llm_model="anthropic/claude-opus-4-20250514",
            llm_temperature=0.3,
            llm_max_tokens=4096,
            database_url="sqlite:///./test.db",
            redis_url=None,
            crew_memory_enabled=True,
            crew_verbose=True,
            crew_max_iterations=15,
            environment="test",
            log_level="DEBUG",
        )

        origins = settings.get_cors_origins()
        assert len(origins) == 2
        assert "http://localhost:3000" in origins


class TestRegistryToUnifiedClientCoordination:
    """Tests creating UnifiedClients from registry-discovered sellers."""

    @pytest.mark.asyncio
    async def test_discovered_seller_protocols_determine_client_mode(
        self,
        seller_agent_cards: list[AgentCard],
    ):
        """UnifiedClient protocol should match the seller's advertised capabilities."""
        for card in seller_agent_cards:
            if "mcp" in card.protocols:
                client = UnifiedClient(
                    base_url=card.url,
                    protocol=Protocol.MCP,
                )
                assert client.default_protocol == Protocol.MCP
            elif "a2a" in card.protocols:
                client = UnifiedClient(
                    base_url=card.url,
                    protocol=Protocol.A2A,
                )
                assert client.default_protocol == Protocol.A2A
            await client.close()

    @pytest.mark.asyncio
    async def test_identity_context_flows_through_unified_client(
        self,
        advertiser_identity: BuyerIdentity,
    ):
        """Identity context set on UnifiedClient should appear in identity methods."""
        client = UnifiedClient(
            base_url="http://fake-seller.test",
            buyer_identity=advertiser_identity,
        )

        assert client.get_access_tier() == "advertiser"

        context = client._get_identity_context()
        assert context["seat_id"] == "ttd-seat-123"
        assert context["agency_id"] == "omnicom-456"
        assert context["advertiser_id"] == "coca-cola-789"
        assert context["access_tier"] == "advertiser"

        await client.close()

    @pytest.mark.asyncio
    async def test_identity_change_updates_client(
        self,
        seat_identity: BuyerIdentity,
        advertiser_identity: BuyerIdentity,
    ):
        """Changing buyer identity on client should update access tier."""
        client = UnifiedClient(
            base_url="http://fake-seller.test",
            buyer_identity=seat_identity,
        )

        assert client.get_access_tier() == "seat"

        # Change identity
        client.set_buyer_identity(advertiser_identity)
        assert client.get_access_tier() == "advertiser"

        await client.close()


class TestIdentityStrategyToClientCoordination:
    """Tests identity strategy recommendations feeding into client configuration."""

    @pytest.mark.asyncio
    async def test_strategy_recommend_then_set_on_client(
        self,
        advertiser_identity: BuyerIdentity,
    ):
        """Strategy recommendation should correctly configure the client."""
        strategy = IdentityStrategy()

        # Low-value deal with unknown seller -> conservative tier
        ctx = DealContext(
            deal_value_usd=5_000,
            deal_type=DealType.PRIVATE_AUCTION,
            seller_relationship=SellerRelationship.UNKNOWN,
            campaign_goal=CampaignGoal.AWARENESS,
        )
        tier = strategy.recommend_tier(ctx)
        masked = strategy.build_identity(advertiser_identity, tier)

        # Create client with masked identity
        client = UnifiedClient(
            base_url="http://seller.test",
            buyer_identity=masked,
        )

        # Should be a conservative tier (not advertiser for low-value unknown seller)
        assert client.get_access_tier() in ("seat", "agency", "public")

        await client.close()

    @pytest.mark.asyncio
    async def test_high_value_trusted_gets_full_identity(
        self,
        advertiser_identity: BuyerIdentity,
    ):
        """High-value deal with trusted seller should get advertiser tier."""
        strategy = IdentityStrategy()

        ctx = DealContext(
            deal_value_usd=500_000,
            deal_type=DealType.PREFERRED_DEAL,
            seller_relationship=SellerRelationship.TRUSTED,
            campaign_goal=CampaignGoal.PERFORMANCE,
        )
        tier = strategy.recommend_tier(ctx)
        masked = strategy.build_identity(advertiser_identity, tier)

        client = UnifiedClient(
            base_url="http://seller.test",
            buyer_identity=masked,
        )

        assert client.get_access_tier() == "advertiser"
        assert masked.advertiser_id == advertiser_identity.advertiser_id

        await client.close()


class TestUnifiedClientProtocolSwitching:
    """Tests protocol switching behavior on UnifiedClient."""

    @pytest.mark.asyncio
    async def test_tool_to_natural_language_mapping(self):
        """_tool_to_natural_language should produce reasonable messages."""
        client = UnifiedClient(base_url="http://test.test")

        # Simple tool mapping
        msg = client._tool_to_natural_language("list_products", {})
        assert "List all available advertising products" in msg

        # Tool with args
        msg = client._tool_to_natural_language(
            "create_account", {"name": "TestCo", "type": "advertiser"}
        )
        assert "TestCo" in msg
        assert "advertiser" in msg

        # Create order
        msg = client._tool_to_natural_language(
            "create_order",
            {"name": "Q1 Campaign", "accountId": "acct-1", "budget": 50000},
        )
        assert "Q1 Campaign" in msg
        assert "50,000" in msg

        await client.close()

    @pytest.mark.asyncio
    async def test_unified_result_from_mcp(self):
        """UnifiedResult.from_mcp should correctly wrap MCP results."""
        mcp_result = MagicMock()
        mcp_result.success = True
        mcp_result.data = {"id": "prod_1", "name": "Test Product"}
        mcp_result.error = ""
        mcp_result.raw = None

        unified = UnifiedResult.from_mcp(mcp_result)
        assert unified.success is True
        assert unified.data["id"] == "prod_1"
        assert unified.protocol == Protocol.MCP

    @pytest.mark.asyncio
    async def test_unified_result_from_a2a(self):
        """UnifiedResult.from_a2a should correctly wrap A2A responses."""
        a2a_response = MagicMock()
        a2a_response.success = True
        a2a_response.data = [{"id": "prod_1"}]
        a2a_response.text = ""
        a2a_response.error = ""
        a2a_response.raw = None

        unified = UnifiedResult.from_a2a(a2a_response)
        assert unified.success is True
        assert unified.data == {"id": "prod_1"}
        assert unified.protocol == Protocol.A2A
