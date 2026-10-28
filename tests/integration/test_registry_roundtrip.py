# Donated to IAB Tech Lab

"""Task 7.3 — register → discover → connect ROUND-TRIP (no network, no hosted registry).

Proves the cross-org discovery wiring end-to-end IN PROCESS:
1. Seed the library's registry double (``create_registry_double``) with a seller
   runtime record whose ``endpoint_url`` is its AgentCore invocations URL and
   whose ``authentication`` advertises an OAuth2 token endpoint + scope.
2. Discover it through the buyer's :class:`AampRegistryClient` over an httpx
   ``ASGITransport`` (the double's FastAPI app, no sockets).
3. Assert the buyer's transport selector (4.3) routes the discovered
   OAuth-advertising endpoint to the JWT/HTTPS MCP client, and that an ``arn:``
   endpoint without OAuth routes to the SigV4 proxy (parity case).

Registry is config-swappable, so prod is only an ``AAMP_REGISTRY_URL`` change —
this test asserts the seam, not a hosted registry.
"""

import sys
from pathlib import Path

import httpx
import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ad_buyer.registry.aamp_client import AampRegistryClient  # noqa: E402
from ad_buyer.registry.transport_selector import (  # noqa: E402
    agentcore_invocations_url,
    select_seller_client,
)

pytestmark = pytest.mark.asyncio

# A CUSTOM_JWT seller MCP runtime advertised in the registry. Placeholder IDs
# only (never the real account/pool/client).
_SELLER_ARN = (
    "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/aamp_seller_mcp-EXAMPLE01"
)
_TOKEN_ENDPOINT = "https://example-seller.auth.us-west-2.amazoncognito.com/oauth2/token"
_SCOPE = "seller-agent/invoke"


def _seed_seller(app, *, endpoint_url: str) -> None:
    """Preseed the double's store with one OAuth-advertising seller runtime."""
    app.state.store.agents[1] = {
        "id": 1,
        "agent_name": "aamp-seller",
        "primary_domain": "example.com",
        "endpoint_url": endpoint_url,
        "protocol_type": "MCP",
        "verification_status": "verified",
        "domain_verified": True,
        "capabilities": ["advertising_inventory"],
        "authentication": {
            "type": "oauth2",
            "token_endpoint": _TOKEN_ENDPOINT,
            "scope": _SCOPE,
        },
    }


async def _discover(app):
    """Discover sellers through AampRegistryClient over an in-process ASGI transport."""
    transport = httpx.ASGITransport(app=app)
    client = AampRegistryClient(
        base_url="http://registry.local",
        auth_token="user-token",  # a valid token in the double's default table
        transport=transport,
    )
    return await client.discover_sellers()


async def test_roundtrip_discovers_seeded_seller():
    """The seeded seller is discoverable in-process and carries its endpoint."""
    from iab_agentic_primitives.sandbox_registry import create_registry_double

    https_endpoint = agentcore_invocations_url(_SELLER_ARN)
    app = create_registry_double()
    _seed_seller(app, endpoint_url=https_endpoint)

    cards = await _discover(app)

    assert cards, "discovery returned no sellers from the in-process registry"
    assert any(c.url == https_endpoint for c in cards), (
        f"seeded seller endpoint not surfaced; got {[c.url for c in cards]}"
    )


async def test_roundtrip_oauth_endpoint_routes_to_jwt_client():
    """An OAuth-advertising https endpoint routes to the JWT/HTTPS client (4.3).

    The selector is fail-closed: when the seller advertises OAuth it REQUIRES
    buyer credentials (a token_provider or BUYER_OAUTH_CLIENT_ID/SECRET) and
    raises otherwise. We supply a stub provider — the same seam the crew uses —
    so the JWT client is built.
    """
    https_endpoint = agentcore_invocations_url(_SELLER_ARN)
    authentication = {
        "type": "oauth2",
        "token_endpoint": _TOKEN_ENDPOINT,
        "scope": _SCOPE,
    }

    class _StubTokenProvider:
        configured = True

        def get_token(self):
            return "stub-jwt"

    client = select_seller_client(
        https_endpoint,
        authentication=authentication,
        region="us-west-2",
        token_provider=_StubTokenProvider(),
    )
    # JWT-mode AgentCoreSellerProxy (keeps the OpenDirect surface, MCP over
    # Bearer HTTPS). It exposes the JWT-mode marker set by the selector.
    assert getattr(client, "_use_jwt", False), (
        f"OAuth endpoint should route to the JWT client, got {type(client).__name__}"
    )


async def test_roundtrip_arn_without_oauth_routes_to_sigv4_proxy():
    """Parity case: an arn endpoint WITHOUT advertised OAuth uses the SigV4 proxy."""
    client = select_seller_client(_SELLER_ARN, authentication=None, region="us-west-2")
    # SigV4 proxy: NOT in JWT mode.
    assert not getattr(client, "_use_jwt", False), (
        "arn without OAuth should use the SigV4 proxy, not the JWT client"
    )
