# Donated to IAB Tech Lab

"""Transport selection for a discovered seller endpoint.

Maps a seller's discovered endpoint (and its advertised auth, if any) to the
right client, keeping the three transports the buyer supports behind one
decision point:

- ``arn:aws:bedrock-agentcore:...`` → :class:`AgentCoreSellerProxy`
  (InvokeAgentRuntime, authenticated with **SigV4/IAM** — same-partition path).
- ``https://`` / ``http://`` WITH an advertised OAuth issuer → an authenticated
  :class:`IABMCPClient` that mints a client_credentials **JWT** via
  :class:`OAuthTokenProvider` and sends ``Authorization: Bearer`` (the AWS
  Bedrock AgentCore CUSTOM_JWT deployment path, where a cross-org seller
  advertises a token endpoint in its registry ``authentication`` object).
- ``https://`` / ``http://`` WITHOUT an issuer → a plain (unauthenticated)
  :class:`IABMCPClient` (local dev / open servers).

Only the AgentCore CUSTOM_JWT case uses OAuth — see the note in
:mod:`ad_buyer.auth.oauth_token_provider` on why the other paths use SigV4 or a
pre-shared API key instead.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def agentcore_invocations_url(runtime_arn: str, *, qualifier: str = "DEFAULT") -> str:
    """Build the HTTPS MCP invocation URL for an AgentCore runtime ARN.

    Verified shape (AWS AgentCore MCP docs): a CUSTOM_JWT (OAuth) runtime is
    invoked over HTTPS at
    ``https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<ENCODED_ARN>/invocations?qualifier=<Q>``
    where ``ENCODED_ARN`` replaces ``:``→``%3A`` and ``/``→``%2F``. boto3/SigV4
    is DISALLOWED for OAuth runtimes, so this URL is called with a Bearer token
    via the Streamable-HTTP MCP transport.
    """
    # arn:aws:bedrock-agentcore:<region>:<acct>:runtime/<name-id>
    parts = runtime_arn.split(":")
    region = parts[3] if len(parts) > 4 else ""
    encoded = runtime_arn.replace(":", "%3A").replace("/", "%2F")
    return (
        f"https://bedrock-agentcore.{region}.amazonaws.com"
        f"/runtimes/{encoded}/invocations?qualifier={qualifier}"
    )


def select_seller_client(
    endpoint: str,
    *,
    authentication: dict[str, Any] | None = None,
    region: str | None = None,
    token_provider: Any = None,
):
    """Return an appropriate seller client for ``endpoint``.

    Routing:
    - ``arn:`` WITHOUT advertised OAuth → SigV4 ``AgentCoreSellerProxy``
      (same-account/dev; the seller does not enforce CUSTOM_JWT).
    - ``arn:`` WITH advertised OAuth → the runtime enforces CUSTOM_JWT, so
      SigV4 is rejected; build the HTTPS invocations URL from the ARN and use
      the JWT client (boto3 is disallowed for OAuth runtimes).
    - ``https://``/``http://`` WITH advertised OAuth → JWT client on that URL.
    - ``https://``/``http://`` WITHOUT OAuth → plain (unauthenticated) client.

    Args:
        endpoint: The discovered ``endpoint_url`` (an ``arn:`` or an http(s) URL).
        authentication: The discovered record's ``authentication`` object, e.g.
            ``{"type": "oauth2", "token_endpoint": "...", "scope": "..."}``.
        region: AWS region (ARN path).
        token_provider: Optional pre-built
            :class:`~ad_buyer.auth.oauth_token_provider.OAuthTokenProvider`.
    """
    token_endpoint, scope = _oauth_from_authentication(authentication)

    if endpoint.startswith("arn:") and not token_endpoint:
        from ad_buyer.interfaces.agentcore.agentcore_seller_proxy import (
            AgentCoreSellerProxy,
        )

        logger.info("Transport: AgentCore SigV4 proxy (arn, no auth) for %s", endpoint)
        return AgentCoreSellerProxy(
            runtime_arn=endpoint,
            region=region or os.environ.get("AWS_REGION"),
        )

    # From here, an OAuth issuer is advertised → JWT/HTTPS path. The seller
    # runtime enforces CUSTOM_JWT (SigV4/boto3 disallowed). Use the
    # AgentCoreSellerProxy in JWT mode: it keeps the OpenDirect-shaped surface
    # DealBookingFlow needs, but frames MCP over a raw HTTPS POST with a Bearer
    # token instead of SigV4. An ARN endpoint lets the proxy derive the HTTPS
    # invocations URL; a full https:// endpoint is used directly.
    if token_endpoint:
        from ad_buyer.auth.oauth_token_provider import OAuthTokenProvider
        from ad_buyer.interfaces.agentcore.agentcore_seller_proxy import (
            AgentCoreSellerProxy,
        )

        provider = token_provider or OAuthTokenProvider()
        if not provider.configured:
            # The seller REQUIRES auth but the buyer has no client credentials —
            # fail loudly rather than silently connecting unauthenticated.
            raise RuntimeError(
                "seller advertises OAuth auth but the buyer has no client "
                "credentials configured (set BUYER_OAUTH_CLIENT_ID / "
                "BUYER_OAUTH_CLIENT_SECRET)"
            )
        logger.info("Transport: JWT/HTTPS AgentCore proxy (OAuth) for %s", endpoint)
        return AgentCoreSellerProxy(
            runtime_arn=endpoint,
            region=region or os.environ.get("AWS_REGION"),
            token_provider=provider,
            token_endpoint=token_endpoint,
            scope=scope,
        )

    from ad_buyer.clients.mcp_client import IABMCPClient

    logger.info("Transport: plain MCP client (no auth) for %s", endpoint)
    return IABMCPClient(base_url=endpoint)


def _oauth_from_authentication(
    authentication: dict[str, Any] | None,
) -> tuple[str, str]:
    """Extract ``(token_endpoint, scope)`` from a discovered auth object.

    Tolerant of the field-name variants a registry record may carry
    (``token_endpoint``/``tokenEndpoint``/``token_url``; ``scope``/``scopes``).
    Returns ``("", "")`` when no OAuth issuer is advertised.
    """
    if not authentication:
        return "", ""
    token_endpoint = (
        authentication.get("token_endpoint")
        or authentication.get("tokenEndpoint")
        or authentication.get("token_url")
        or ""
    )
    scope = authentication.get("scope") or authentication.get("scopes") or ""
    if isinstance(scope, (list, tuple)):
        scope = " ".join(str(s) for s in scope)
    return token_endpoint, scope
