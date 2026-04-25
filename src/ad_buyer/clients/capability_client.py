# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Buyer-side seller capability discovery client.

Implements proposal §5.7 layer 1 + §6 row 13: pre-flight capability
discovery the orchestrator runs against each candidate seller before
booking. Fetches `/.well-known/agent.json` and pulls out the
`audience_capabilities` block.

Design decisions:

- **TTL <=1h cache** keyed by seller endpoint (proposal §5.7: "the buyer
  caches capability responses for at most 1 hour"). The cache is in-process
  only; Epic 1 does not need a shared cache.
- **`Cache-Control: max-age=N` honored** when the seller emits it. A
  shorter max-age shortens the buyer's TTL for that response; a longer
  max-age is clamped to the 1h ceiling so a misconfigured seller cannot
  push the buyer into a stale-cap state for hours.
- **Legacy seller fallback.** A seller that does not ship
  `audience_capabilities` (legacy or older deployment) is treated as
  legacy via `_legacy_default_capabilities()` per §5.7.
  Same fallback applies on HTTP errors / parse errors -- failing closed
  to "I know less about this seller than I think" is the safe move.
- **Cache hit / miss is observable.** Each `discover_capabilities` call
  returns a `(capabilities, cache_status)` tuple where `cache_status` is
  one of "hit", "miss", "stale", "error", "legacy". The orchestrator
  logs the status and lands it in the audit trail (proposal §13a).

This module is async (matches the rest of the buyer's client surface,
e.g., `UCPClient`, `DealsClient`). The cache implementation is lock-free
because the fast path is read-only -- a brief race where two concurrent
discoveries both fetch is harmless (the later one wins; both responses
go through the same parser).

Bead: ar-gkbr (proposal §5.7 layer 1 + §6 row 13).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal, TYPE_CHECKING

import httpx

# Runtime import deferred to avoid a circular import:
#   ad_buyer.orchestration.__init__ -> multi_seller -> clients.capability_client
# `audience_degradation` itself has no client-side dependencies so we import
# the module (not the package) lazily on first use. The TYPE_CHECKING import
# keeps the type annotations precise without triggering the cycle.
if TYPE_CHECKING:
    from ..orchestration.audience_degradation import SellerAudienceCapabilities

logger = logging.getLogger(__name__)


def _legacy_default_capabilities() -> "SellerAudienceCapabilities":
    """Return `SellerAudienceCapabilities.legacy_default()` (deferred import).

    Wraps the deferred import so call sites stay readable. The first call
    pays the import cost; subsequent calls hit the resolved module.
    """

    from ..orchestration.audience_degradation import (
        SellerAudienceCapabilities as _SAC,
    )

    return _SAC.legacy_default()


def _validate_capabilities(block: dict[str, Any]) -> "SellerAudienceCapabilities":
    """Parse a JSON block into `SellerAudienceCapabilities` (deferred import)."""

    from ..orchestration.audience_degradation import (
        SellerAudienceCapabilities as _SAC,
    )

    return _SAC.model_validate(block)


# Per proposal §5.7: "the buyer caches capability responses for at most
# 1 hour". This is the ceiling -- shorter Cache-Control max-age values
# from the seller take precedence; longer ones are clamped.
DEFAULT_CACHE_TTL_SECONDS: float = 3600.0

# Conservative default fetch timeout. The discovery call is a small JSON
# GET; long timeouts here would block the booking path.
DEFAULT_DISCOVERY_TIMEOUT: float = 10.0


CacheStatus = Literal["hit", "miss", "stale", "error", "legacy"]


@dataclass(frozen=True)
class CapabilityDiscoveryResult:
    """Tuple returned by `CapabilityClient.discover_capabilities`.

    Attributes:
        capabilities: Buyer-side mirror of the seller's
            `CapabilityAudienceBlock`. Always populated -- on legacy /
            error / parse-failure paths, this is `legacy_default()`.
        cache_status: "hit" (served from cache), "miss" (fetched fresh),
            "stale" (TTL expired, re-fetched), "error" (fetch failed,
            served legacy default), "legacy" (seller doesn't ship the
            block, served legacy default). Lands in the audit trail per
            proposal §13a.
        fetched_at: Monotonic clock seconds when the underlying response
            was fetched. Useful for observability and debugging stale
            caches.
    """

    capabilities: SellerAudienceCapabilities
    cache_status: CacheStatus
    fetched_at: float


# Match `max-age=NNN` in a Cache-Control header. Tolerant of surrounding
# directives (no-cache, public, etc.) which we ignore for this purpose.
_MAX_AGE_RE = re.compile(r"\bmax-age\s*=\s*(\d+)", re.IGNORECASE)


def _parse_max_age(cache_control: str | None) -> float | None:
    """Pull `max-age=N` out of a Cache-Control header.

    Returns the integer seconds as a float, or None if no max-age
    directive is present. Negative or non-numeric values are ignored
    (treated as "no max-age").
    """

    if not cache_control:
        return None
    match = _MAX_AGE_RE.search(cache_control)
    if match is None:
        return None
    try:
        value = int(match.group(1))
    except ValueError:
        return None
    if value < 0:
        return None
    return float(value)


@dataclass
class _CacheEntry:
    """Internal cache entry. Mutable so we can update timestamps in-place."""

    capabilities: SellerAudienceCapabilities
    fetched_at: float
    expires_at: float


class CapabilityClient:
    """Fetches and caches seller `audience_capabilities` blocks.

    Used by the orchestrator's pre-flight integration: before booking a
    deal with a seller, the orchestrator calls `discover_capabilities`,
    runs `degrade_plan_for_seller`, and decides whether to proceed based
    on the campaign's `audience_strictness` policy.

    Args:
        timeout: HTTP timeout for the discovery GET (seconds).
        cache_ttl_seconds: Maximum cache TTL ceiling (seconds). Per
            proposal §5.7, defaults to 3600 (1h). Shorter values from
            seller `Cache-Control: max-age` take precedence.
        clock: Optional monotonic clock function for testing time-based
            cache expiry. Defaults to `time.monotonic`.
        client_factory: Optional factory for `httpx.AsyncClient`. Lets
            tests substitute `MockTransport`-backed clients without
            patching at the module level.
    """

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_DISCOVERY_TIMEOUT,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        clock: Callable[[], float] | None = None,
        client_factory: Callable[..., httpx.AsyncClient] | None = None,
    ) -> None:
        self._timeout = timeout
        self._cache_ttl_ceiling = cache_ttl_seconds
        self._clock = clock or time.monotonic
        self._client_factory = client_factory or self._default_client_factory
        self._cache: dict[str, _CacheEntry] = {}

    def _default_client_factory(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout)

    @staticmethod
    def _agent_card_url(seller_endpoint: str) -> str:
        """Build the well-known agent-card URL for a seller endpoint.

        Trims trailing slashes so a seller registered as either
        `https://x.example.com` or `https://x.example.com/` lands on the
        same cache key and the same URL.
        """

        return f"{seller_endpoint.rstrip('/')}/.well-known/agent.json"

    def _cache_key(self, seller_endpoint: str) -> str:
        """Cache key. Mirrors `_agent_card_url`'s normalization."""

        return seller_endpoint.rstrip("/")

    def invalidate(self, seller_endpoint: str | None = None) -> None:
        """Drop a single seller's cached caps, or the whole cache.

        Tests use this to force a re-fetch after manipulating the clock.
        Production callers typically don't need it; TTL handles staleness.
        """

        if seller_endpoint is None:
            self._cache.clear()
            return
        self._cache.pop(self._cache_key(seller_endpoint), None)

    async def discover_capabilities(
        self, seller_endpoint: str
    ) -> CapabilityDiscoveryResult:
        """Discover a seller's audience capabilities.

        Hits the cache first, returns immediately on a fresh hit. On a
        miss / stale entry, fetches `/.well-known/agent.json` from the
        seller, parses the `audience_capabilities` block, applies any
        `Cache-Control: max-age` from the response (clamped to the
        1h ceiling), and stores in the cache.

        On any failure path (HTTP error, parse error, missing block) the
        function returns `_legacy_default_capabilities()`
        with `cache_status="error"` or `"legacy"` -- the orchestrator
        always gets a usable capabilities object so booking can proceed
        under the most conservative assumption.

        Args:
            seller_endpoint: The seller's base URL (e.g.
                `https://seller-a.example.com`).

        Returns:
            `CapabilityDiscoveryResult` carrying the parsed
            capabilities, a cache-status indicator, and the fetch
            timestamp.
        """

        key = self._cache_key(seller_endpoint)
        now = self._clock()

        # ---- cache lookup ----
        cached = self._cache.get(key)
        if cached is not None and cached.expires_at > now:
            logger.info(
                "capability_client cache hit endpoint=%s expires_in=%.1fs",
                seller_endpoint,
                cached.expires_at - now,
            )
            return CapabilityDiscoveryResult(
                capabilities=cached.capabilities,
                cache_status="hit",
                fetched_at=cached.fetched_at,
            )

        cache_status: CacheStatus = "stale" if cached is not None else "miss"

        # ---- fetch ----
        url = self._agent_card_url(seller_endpoint)
        try:
            client = self._client_factory()
            try:
                response = await client.get(url)
            finally:
                await client.aclose()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "capability_client fetch failed endpoint=%s err=%s -- "
                "treating as legacy",
                seller_endpoint,
                exc,
            )
            caps = _legacy_default_capabilities()
            self._store(key, caps, fetched_at=now, max_age=None)
            return CapabilityDiscoveryResult(
                capabilities=caps, cache_status="error", fetched_at=now
            )

        if response.status_code != 200:
            logger.warning(
                "capability_client non-200 endpoint=%s status=%d -- "
                "treating as legacy",
                seller_endpoint,
                response.status_code,
            )
            caps = _legacy_default_capabilities()
            self._store(key, caps, fetched_at=now, max_age=None)
            return CapabilityDiscoveryResult(
                capabilities=caps, cache_status="error", fetched_at=now
            )

        # ---- parse ----
        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning(
                "capability_client invalid JSON endpoint=%s err=%s -- "
                "treating as legacy",
                seller_endpoint,
                exc,
            )
            caps = _legacy_default_capabilities()
            self._store(key, caps, fetched_at=now, max_age=None)
            return CapabilityDiscoveryResult(
                capabilities=caps, cache_status="error", fetched_at=now
            )

        block = (payload or {}).get("audience_capabilities")
        if block is None:
            # Legacy seller: agent-card landed but ships no audience
            # capabilities block. Per §5.7 this is the "treat as legacy"
            # fallback -- standard segments only, no constraints, no
            # extensions, no exclusions, no agentic.
            logger.info(
                "capability_client legacy seller (no audience_capabilities) "
                "endpoint=%s",
                seller_endpoint,
            )
            caps = _legacy_default_capabilities()
            self._store(
                key,
                caps,
                fetched_at=now,
                max_age=_parse_max_age(response.headers.get("cache-control")),
            )
            return CapabilityDiscoveryResult(
                capabilities=caps, cache_status="legacy", fetched_at=now
            )

        try:
            caps = _validate_capabilities(block)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "capability_client malformed audience_capabilities "
                "endpoint=%s err=%s -- treating as legacy",
                seller_endpoint,
                exc,
            )
            caps = _legacy_default_capabilities()
            self._store(key, caps, fetched_at=now, max_age=None)
            return CapabilityDiscoveryResult(
                capabilities=caps, cache_status="error", fetched_at=now
            )

        max_age = _parse_max_age(response.headers.get("cache-control"))
        self._store(key, caps, fetched_at=now, max_age=max_age)

        logger.info(
            "capability_client %s endpoint=%s schema=%s agentic=%s "
            "supports=(c=%s,e=%s,x=%s)",
            cache_status,
            seller_endpoint,
            caps.schema_version,
            caps.agentic.supported,
            caps.supports_constraints,
            caps.supports_extensions,
            caps.supports_exclusions,
        )

        return CapabilityDiscoveryResult(
            capabilities=caps, cache_status=cache_status, fetched_at=now
        )

    def _store(
        self,
        key: str,
        caps: SellerAudienceCapabilities,
        *,
        fetched_at: float,
        max_age: float | None,
    ) -> None:
        """Insert or refresh a cache entry.

        TTL = min(max_age from response, configured ceiling). When the
        seller doesn't emit max-age, we use the ceiling. Negative /
        zero max-age values force an immediate expiry (the next call
        will re-fetch); we still cache the response so observers can
        see the most recent discovery.
        """

        if max_age is None:
            ttl = self._cache_ttl_ceiling
        else:
            ttl = min(max_age, self._cache_ttl_ceiling)
        ttl = max(ttl, 0.0)

        self._cache[key] = _CacheEntry(
            capabilities=caps,
            fetched_at=fetched_at,
            expires_at=fetched_at + ttl,
        )


__all__ = [
    "CapabilityClient",
    "CapabilityDiscoveryResult",
    "CacheStatus",
    "DEFAULT_CACHE_TTL_SECONDS",
    "DEFAULT_DISCOVERY_TIMEOUT",
]
