# Donated to IAB Tech Lab

"""Tests for the buyer transport selector (task 4.3 / Req 6.3)."""

import httpx
import pytest

from ad_buyer.auth.oauth_token_provider import OAuthTokenProvider
from ad_buyer.clients.mcp_client import IABMCPClient
from ad_buyer.registry.transport_selector import (
    _oauth_from_authentication,
    agentcore_invocations_url,
    select_seller_client,
)

_ARN = "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/seller_mcp-abc"
_HTTPS = "https://seller.example/agent"
_AUTH = {
    "type": "oauth2",
    "token_endpoint": "https://issuer.example/oauth2/token",
    "scope": "seller-agent/invoke",
}


def _configured_provider():
    return OAuthTokenProvider(client_id="buyer", client_secret="secret")


class TestSelectSellerClient:
    def test_arn_routes_to_agentcore_proxy(self):
        from ad_buyer.interfaces.agentcore.agentcore_seller_proxy import AgentCoreSellerProxy

        client = select_seller_client(_ARN, region="us-west-2")
        assert isinstance(client, AgentCoreSellerProxy)

    def test_https_with_oauth_routes_to_jwt_proxy(self):
        from ad_buyer.interfaces.agentcore.agentcore_seller_proxy import AgentCoreSellerProxy

        client = select_seller_client(
            _HTTPS, authentication=_AUTH, token_provider=_configured_provider()
        )
        assert isinstance(client, AgentCoreSellerProxy)
        assert client._use_jwt is True
        assert client._token_endpoint == _AUTH["token_endpoint"]
        assert client._scope == "seller-agent/invoke"

    def test_https_without_auth_routes_to_plain_client(self):
        client = select_seller_client(_HTTPS)
        assert isinstance(client, IABMCPClient)
        assert client._auth_provider is None

    def test_https_requires_auth_but_unconfigured_raises(self):
        unconfigured = OAuthTokenProvider(client_id="", client_secret="")
        with pytest.raises(RuntimeError):
            select_seller_client(_HTTPS, authentication=_AUTH, token_provider=unconfigured)

    def test_arn_without_auth_routes_to_sigv4_proxy(self):
        from ad_buyer.interfaces.agentcore.agentcore_seller_proxy import AgentCoreSellerProxy

        client = select_seller_client(_ARN, region="us-west-2")
        assert isinstance(client, AgentCoreSellerProxy)
        assert client._use_jwt is False

    def test_arn_with_auth_routes_to_jwt_proxy(self):
        """A CUSTOM_JWT runtime given by ARN → JWT-mode proxy (SigV4 disallowed)."""
        from ad_buyer.interfaces.agentcore.agentcore_seller_proxy import AgentCoreSellerProxy

        client = select_seller_client(
            _ARN, authentication=_AUTH, token_provider=_configured_provider()
        )
        assert isinstance(client, AgentCoreSellerProxy)
        assert client._use_jwt is True


class TestAgentCoreInvocationsUrl:
    def test_encodes_arn_into_verified_shape(self):
        url = agentcore_invocations_url(_ARN)
        assert url == (
            "https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/"
            "arn%3Aaws%3Abedrock-agentcore%3Aus-west-2%3A123456789012%3Aruntime"
            "%2Fseller_mcp-abc/invocations?qualifier=DEFAULT"
        )

    def test_full_https_endpoint_used_as_is(self):
        """A discovered full invocations URL routes to a JWT-mode proxy that
        posts to that URL directly (no ARN encoding, no /mcp/sse)."""
        from ad_buyer.interfaces.agentcore.agentcore_seller_proxy import AgentCoreSellerProxy

        full = (
            "https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/"
            "arn%3Aaws%3A.../invocations?qualifier=DEFAULT"
        )
        client = select_seller_client(
            full, authentication=_AUTH, token_provider=_configured_provider()
        )
        assert isinstance(client, AgentCoreSellerProxy)
        # The proxy stores the full URL as its "arn"; JWT invoke uses it directly
        # (not ARN-encoded) because it does not start with "arn:".
        assert client._arn == full
        assert not client._arn.startswith("arn:")


class TestOAuthExtraction:
    def test_none_returns_empty(self):
        assert _oauth_from_authentication(None) == ("", "")

    def test_snake_case(self):
        assert _oauth_from_authentication(
            {"token_endpoint": "u", "scope": "s"}
        ) == ("u", "s")

    def test_camel_case_and_token_url(self):
        assert _oauth_from_authentication({"tokenEndpoint": "u"})[0] == "u"
        assert _oauth_from_authentication({"token_url": "u"})[0] == "u"

    def test_scopes_list_is_space_joined(self):
        assert _oauth_from_authentication(
            {"token_endpoint": "u", "scopes": ["a", "b"]}
        ) == ("u", "a b")


class TestJwtMcpClientAuthHeaders:
    def test_bearer_header_minted_from_provider(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"access_token": "jwt-xyz", "expires_in": 3600})

        provider = OAuthTokenProvider(
            client_id="buyer",
            client_secret="secret",
            transport=httpx.MockTransport(handler),
        )
        client = IABMCPClient(
            base_url=_HTTPS,
            auth_token_provider=provider,
            token_endpoint=_AUTH["token_endpoint"],
            scope="seller-agent/invoke",
        )
        assert client._auth_headers() == {"Authorization": "Bearer jwt-xyz"}

    def test_no_provider_no_headers(self):
        client = IABMCPClient(base_url=_HTTPS)
        assert client._auth_headers() == {}
