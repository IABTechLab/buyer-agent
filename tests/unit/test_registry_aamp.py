# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the AAMP-backed registry client (EP-5.1).

The AampRegistryClient wires the buyer's discovery seam to the REAL IAB
agent registry API (``/api/agents`` with the ``{"success": true, "data":
...}`` envelope) via the shared contract library's RegistryClient.

These tests drive the library's faithful in-process test double of the
real registry (``create_registry_double``) over an httpx ASGITransport —
no network, no sockets.
"""

import httpx
import pytest
from iab_agentic_primitives.sandbox_registry.real_double import create_registry_double

from ad_buyer.registry import create_registry_client
from ad_buyer.registry.aamp_client import AampRegistryClient
from ad_buyer.registry.client import RegistryClient
from ad_buyer.registry.models import TrustLevel

BASE_URL = "http://registry.test"


def _seed_agents(app) -> None:
    """Seed the double's store with two representative agents."""
    store = app.state.store
    store.agents[1] = {
        "id": 1,
        "agent_name": "Acme CTV Seller",
        "primary_domain": "acme-seller.example.com",
        "type": "remote",
        "endpoint_url": "https://seller.acme.example.com",
        "protocol_type": "a2a",
        "capabilities": ["ctv", "display"],
        "iab_capabilities": ["video"],
        "verification_status": "active",
        "domain_verified": True,
        "iab_member": False,
    }
    store.agents[2] = {
        "id": 2,
        "agent_name": "Beta Display Seller",
        "primary_domain": "beta-seller.example.com",
        "type": "remote",
        "endpoint_url": "https://seller.beta.example.com",
        "protocol_type": "mcp",
        "capabilities": ["display"],
        "verification_status": "pending",
        "domain_verified": False,
        "iab_member": False,
    }
    store.next_id = 3


@pytest.fixture
def double_app():
    app = create_registry_double()
    _seed_agents(app)
    return app


@pytest.fixture
def client(double_app):
    return AampRegistryClient(
        base_url=BASE_URL,
        auth_token="user-token",
        cache_ttl_seconds=60,
        transport=httpx.ASGITransport(app=double_app),
    )


class TestDiscoverSellers:
    async def test_discovers_all_agents(self, client):
        sellers = await client.discover_sellers()
        assert len(sellers) == 2
        by_id = {s.agent_id: s for s in sellers}
        assert by_id["1"].name == "Acme CTV Seller"
        assert by_id["1"].url == "https://seller.acme.example.com"
        assert by_id["1"].protocols == ["a2a"]
        assert by_id["2"].name == "Beta Display Seller"

    async def test_maps_trust_level_from_verification_status(self, client):
        sellers = await client.discover_sellers()
        by_id = {s.agent_id: s for s in sellers}
        # verification_status == "active" -> VERIFIED
        assert by_id["1"].trust_level == TrustLevel.VERIFIED
        # present in the registry but not active -> REGISTERED
        assert by_id["2"].trust_level == TrustLevel.REGISTERED

    async def test_maps_capabilities(self, client):
        sellers = await client.discover_sellers()
        acme = next(s for s in sellers if s.agent_id == "1")
        names = {c.name for c in acme.capabilities}
        assert names == {"ctv", "display", "video"}

    async def test_capability_filter_is_applied_client_side(self, client):
        sellers = await client.discover_sellers(capabilities_filter=["ctv"])
        assert [s.agent_id for s in sellers] == ["1"]

    async def test_capability_filter_no_match(self, client):
        sellers = await client.discover_sellers(capabilities_filter=["audio"])
        assert sellers == []

    async def test_results_are_cached(self, client, double_app):
        first = await client.discover_sellers()
        assert len(first) == 2
        # Mutate the backing store; the cached listing must still be served.
        double_app.state.store.agents.clear()
        second = await client.discover_sellers()
        assert len(second) == 2

    async def test_unauthorized_degrades_to_empty_list(self, double_app):
        client = AampRegistryClient(
            base_url=BASE_URL,
            auth_token=None,
            transport=httpx.ASGITransport(app=double_app),
        )
        assert await client.discover_sellers() == []


class TestNullListTolerance:
    """The hosted registry serializes unset list columns as JSON null
    (verified against UAT); the wiring must not choke on them."""

    async def test_null_list_fields_are_tolerated(self, client, double_app):
        double_app.state.store.agents[3] = {
            "id": 3,
            "agent_name": "Null Fields Seller",
            "primary_domain": "nulls.example.com",
            "type": "remote",
            "endpoint_url": "https://seller.nulls.example.com",
            # Exactly what registry-uat.iabtechlab.com returns for unset fields.
            "endorsements": None,
            "iab_capabilities": None,
            "iab_subcategories": None,
            "capabilities": None,
            "industry_roles": None,
            "verification_status": "pending",
        }
        sellers = await client.discover_sellers()
        assert "3" in {s.agent_id for s in sellers}
        card = await client.fetch_card("3")
        assert card is not None
        assert card.capabilities == []


class TestFetchCard:
    async def test_fetches_card_by_registry_id(self, client):
        card = await client.fetch_card("1")
        assert card is not None
        assert card.agent_id == "1"
        assert card.name == "Acme CTV Seller"
        assert card.trust_level == TrustLevel.VERIFIED

    async def test_missing_agent_returns_none(self, client):
        assert await client.fetch_card("999") is None


class TestVerifyAgent:
    async def test_known_endpoint_url_is_registered(self, client):
        info = await client.verify_agent("https://seller.acme.example.com/")
        assert info.is_registered is True
        assert info.trust_level == TrustLevel.VERIFIED
        assert info.registry_id is not None

    async def test_pending_agent_is_registered_not_verified(self, client):
        info = await client.verify_agent("https://seller.beta.example.com")
        assert info.is_registered is True
        assert info.trust_level == TrustLevel.REGISTERED

    async def test_unknown_agent_is_unknown(self, client):
        info = await client.verify_agent("https://nobody.example.com")
        assert info.is_registered is False
        assert info.trust_level == TrustLevel.UNKNOWN


class TestRegisterBuyer:
    async def test_registers_buyer_card(self, client):
        from ad_buyer.registry.models import AgentCard

        card = AgentCard(
            agent_id="buyer-001",
            name="Test Buyer",
            # The double scopes registration to the token's company domain.
            url="https://example.com/buyer",
            protocols=["a2a"],
        )
        assert await client.register_buyer(card) is True

    async def test_domain_mismatch_fails_registration(self, client):
        from ad_buyer.registry.models import AgentCard

        card = AgentCard(
            agent_id="buyer-002",
            name="Rogue Buyer",
            url="https://not-our-domain.example.org/buyer",
        )
        assert await client.register_buyer(card) is False


class TestFactory:
    def test_returns_aamp_client_when_registry_url_configured(self, monkeypatch):
        monkeypatch.setenv("AAMP_REGISTRY_URL", BASE_URL)
        monkeypatch.setenv("AAMP_REGISTRY_AUTH_TOKEN", "user-token")
        from ad_buyer.config.settings import Settings

        client = create_registry_client(Settings())
        assert isinstance(client, AampRegistryClient)
        assert client.base_url == BASE_URL

    def test_defaults_to_legacy_client_when_unconfigured(self, monkeypatch):
        monkeypatch.delenv("AAMP_REGISTRY_URL", raising=False)
        monkeypatch.delenv("AAMP_REGISTRY_AUTH_TOKEN", raising=False)
        from ad_buyer.config.settings import Settings

        client = create_registry_client(Settings(aamp_registry_url=""))
        assert isinstance(client, RegistryClient)
