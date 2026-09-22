# Donated to IAB Tech Lab

"""OAuth2 client_credentials token provider for authenticated seller runtimes.

**Scope: the AWS Bedrock AgentCore deployment path.** This provider exists for
the case where a seller is deployed as a Bedrock AgentCore *runtime* fronted by
a ``CUSTOM_JWT`` authorizer, and advertises an OAuth2 token endpoint + scope in
its AAMP registry record's ``authentication`` object. That is a genuinely
CROSS-ORG, machine-to-machine setting — the buyer and seller are different
organizations with no shared API-key exchange — which is exactly what the OAuth2
``client_credentials`` grant is the industry-standard answer for.

It deliberately does NOT replace the existing per-seller API-key path
(:class:`ad_buyer.auth.middleware.AuthMiddleware` + ``ApiKeyStore``). That path
predates AgentCore and targets same-/trusted-account HTTP sellers where a
pre-shared API key is provisioned out of band; there is no issuer to obtain a
token from, so OAuth would have nothing to point at. The ``arn:`` AgentCore
proxy path authenticates with SigV4 (IAM), another standard, so it needs no
bearer token either. OAuth becomes the right tool specifically when a discovered
seller ADVERTISES an issuer — i.e. this AgentCore CUSTOM_JWT path — and the
transport selector routes only those ``https://`` endpoints through here.

When the buyer discovers such a seller runtime (``auth_required=true`` with an
``authentication`` object: token endpoint + scope), it must present
``Authorization: Bearer <JWT>`` to invoke it. This provider mints that JWT via
the OAuth2 ``client_credentials`` grant.

Design (Req 6.6):
- The token ENDPOINT and SCOPE come from the DISCOVERED registry record's
  ``authentication`` object — the seller advertises where to authenticate.
- The buyer's OWN ``client_id``/``client_secret`` come from settings/env
  (``BUYER_OAUTH_CLIENT_ID`` / ``BUYER_OAUTH_CLIENT_SECRET``) — its identity,
  NEVER read from the registry and NEVER logged.
- Tokens are cached keyed by ``(token_endpoint, scope)`` and refreshed
  PROACTIVELY before ``expires_in`` (minus a safety skew) and REACTIVELY when a
  caller reports a 401 (:meth:`invalidate`).
- The same provider serves a BYO-IdP seller: the discovered token endpoint is
  simply the customer's IdP rather than the shared Cognito domain.
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# Refresh this many seconds BEFORE the token's stated expiry, to avoid handing
# out a token that expires mid-flight.
_EXPIRY_SKEW_SECONDS = 60.0


class OAuthTokenError(RuntimeError):
    """Raised when a client_credentials token cannot be minted."""


@dataclass(frozen=True)
class _CacheKey:
    token_endpoint: str
    scope: str


@dataclass
class _CachedToken:
    access_token: str
    expires_at: float  # monotonic deadline

    def valid(self, *, now: float) -> bool:
        return now < (self.expires_at - _EXPIRY_SKEW_SECONDS)


class OAuthTokenProvider:
    """Mints + caches OAuth2 client_credentials bearer tokens.

    Args:
        client_id: The buyer's own OAuth app-client id. Defaults to
            ``settings.buyer_oauth_client_id``.
        client_secret: The buyer's own app-client secret. Defaults to
            ``settings.buyer_oauth_client_secret``. Never logged.
        timeout: HTTP timeout for the token endpoint (seconds).
        transport: Optional httpx transport (tests inject a stub endpoint).
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        *,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if client_id is None or client_secret is None:
            # Lazy import so importing this module never triggers settings load.
            from ad_buyer.config.settings import get_settings

            settings = get_settings()
            client_id = client_id or settings.buyer_oauth_client_id
            client_secret = client_secret or settings.buyer_oauth_client_secret

        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout
        self._transport = transport
        self._cache: dict[_CacheKey, _CachedToken] = {}
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        """True when the buyer has its own client credentials to mint with."""
        return bool(self._client_id and self._client_secret)

    def get_token(self, token_endpoint: str, scope: str = "", *, force: bool = False) -> str:
        """Return a valid bearer token for (token_endpoint, scope).

        Reuses a cached token within its lifetime; mints a fresh one otherwise
        (or when ``force`` is set). Raises :class:`OAuthTokenError` on failure.
        """
        if not self.configured:
            raise OAuthTokenError(
                "buyer OAuth client credentials are not configured "
                "(set BUYER_OAUTH_CLIENT_ID / BUYER_OAUTH_CLIENT_SECRET)"
            )
        if not token_endpoint:
            raise OAuthTokenError("no token_endpoint provided by the discovered record")

        key = _CacheKey(token_endpoint=token_endpoint, scope=scope or "")
        now = time.monotonic()

        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and not force and cached.valid(now=now):
                return cached.access_token

        # Mint outside the lock (network I/O); re-check under the lock on store.
        token, expires_in = self._mint(token_endpoint, scope)
        with self._lock:
            self._cache[key] = _CachedToken(
                access_token=token,
                expires_at=time.monotonic() + max(float(expires_in), 0.0),
            )
        return token

    def invalidate(self, token_endpoint: str, scope: str = "") -> None:
        """Drop the cached token so the next :meth:`get_token` re-mints.

        Call this reactively when a downstream call returns 401 with an
        otherwise-valid cached token.
        """
        key = _CacheKey(token_endpoint=token_endpoint, scope=scope or "")
        with self._lock:
            self._cache.pop(key, None)

    def _mint(self, token_endpoint: str, scope: str) -> tuple[str, float]:
        """POST grant_type=client_credentials (HTTP Basic) and parse the JWT."""
        basic = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode()
        ).decode()
        data = {"grant_type": "client_credentials"}
        if scope:
            data["scope"] = scope
        headers = {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                resp = client.post(token_endpoint, data=data, headers=headers)
        except httpx.HTTPError as exc:
            raise OAuthTokenError(f"token endpoint request failed: {exc}") from exc

        if resp.status_code != 200:
            # Never echo the response body verbatim (may reflect credentials).
            raise OAuthTokenError(
                f"token endpoint returned HTTP {resp.status_code} for scope "
                f"'{scope or '(none)'}'"
            )
        try:
            payload = resp.json()
        except ValueError as exc:
            raise OAuthTokenError("token endpoint returned a non-JSON body") from exc

        token = payload.get("access_token")
        if not token:
            raise OAuthTokenError("token endpoint response had no access_token")
        expires_in = payload.get("expires_in", 3600)
        logger.info(
            "Minted a client_credentials token (scope=%s, expires_in=%ss).",
            scope or "(none)",
            expires_in,
        )
        return token, float(expires_in)
