# Author: SafeGuard Privacy
# Donated to IAB Tech Lab

"""SafeGuard Privacy (SGP) platform client.

Async HTTP client for the SafeGuard Privacy integration API. Currently
exposes a single capability: checking whether a vendor has the IAB
buyer-agent approval flag set on the buyer's SGP tenant.

Endpoint:
    GET /api/v1/integrations/iab/buyer-agent-approval?domain=a.com,b.com

Auth: api-key header, scope `iab:buyerAgent`.
Limit: up to 10 domains/internalIds per call (SGP-enforced).
"""

from __future__ import annotations

import logging
import time
from typing import Optional
from urllib.parse import urlparse

import httpx

from ..models.sgp import ApprovalRecord

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 15.0
_MAX_BATCH = 10
_RETRYABLE_STATUS_CODES = {502, 503, 504}
_ENDPOINT = "/api/v1/integrations/iab/buyer-agent-approval"

# Ordered list of product-dict keys to probe when deriving a seller domain
# for an SGP approval lookup. Explicit domain fields come first; publisher
# identifiers are used only when they look like a hostname.
_DOMAIN_KEYS = ("seller_url", "sellerUrl", "publisher_domain", "publisherDomain")
_PUBLISHER_KEYS = ("publisherId", "publisher")


def extract_product_domain(product: dict) -> Optional[str]:
    """Best-guess seller domain from a product dict for an SGP lookup.

    Checks explicit domain/URL fields first, then falls back to
    ``publisherId`` / ``publisher`` when those values contain a ``.``
    (i.e. look like a hostname rather than an opaque ID). Returns the
    raw value; ``SGPClient.normalize_domain`` handles cleanup.
    """
    for key in _DOMAIN_KEYS:
        value = product.get(key)
        if isinstance(value, str) and value:
            return value
    for key in _PUBLISHER_KEYS:
        value = product.get(key)
        if isinstance(value, str) and "." in value:
            return value
    return None


class SGPClientError(Exception):
    """Error raised by SGPClient for API or transport failures."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


class SGPAuthError(SGPClientError):
    """Raised on 401 — api-key missing, invalid, or lacks required scope."""


class SGPClient:
    """Async client for SafeGuard Privacy buyer-agent approval checks.

    Normalizes domains (strips scheme, www, port, lowercases), dedupes,
    chunks into groups of 10, and caches per-domain results for
    ``cache_ttl_seconds``. Returns a dict keyed by normalized domain; a
    value of ``None`` means the vendor is unknown to SGP (HTTP 404 or
    absent from the batch response).

    Args:
        api_key: SGP API key with ``iab:buyerAgent`` scope.
        base_url: SGP base URL. Defaults to production
            (``https://api.safeguardprivacy.com``). The demo environment
            is at ``https://api.safeguardprivacy-demo.com``.
        timeout: Request timeout in seconds.
        cache_ttl_seconds: How long to cache per-domain results.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.safeguardprivacy.com",
        timeout: float = _DEFAULT_TIMEOUT,
        cache_ttl_seconds: int = 900,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, Optional[ApprovalRecord]]] = {}
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"api-key": api_key},
            timeout=timeout,
        )

    async def aclose(self) -> None:
        """Close the underlying httpx client."""
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_domain(value: str) -> str:
        """Reduce a seller URL or raw domain to the form SGP accepts.

        Strips scheme, ``www.``, path, query, and port; lowercases.
        Returns an empty string for inputs that yield no host.
        """
        if not value:
            return ""
        raw = value.strip()
        # urlparse needs a scheme to extract netloc reliably
        if "://" not in raw:
            raw = "http://" + raw
        host = urlparse(raw).hostname or ""
        host = host.lower()
        if host.startswith("www."):
            host = host[4:]
        return host

    async def check_approvals(
        self, domains: list[str]
    ) -> dict[str, Optional[ApprovalRecord]]:
        """Look up IAB buyer-agent approval for a list of domains.

        Args:
            domains: Raw seller URLs or domains. Duplicates and invalid
                entries are silently dropped.

        Returns:
            Dict keyed by normalized domain. ``None`` value means the
            vendor is unknown to SGP (not onboarded on the buyer's tenant).
        """
        normalized = [self.normalize_domain(d) for d in domains]
        normalized = [d for d in normalized if d]
        if not normalized:
            return {}

        now = time.monotonic()
        result: dict[str, Optional[ApprovalRecord]] = {}
        to_fetch: list[str] = []

        seen: set[str] = set()
        for d in normalized:
            if d in seen:
                continue
            seen.add(d)
            cached = self._cache.get(d)
            if cached and (now - cached[0]) < self._cache_ttl:
                result[d] = cached[1]
            else:
                to_fetch.append(d)

        for i in range(0, len(to_fetch), _MAX_BATCH):
            chunk = to_fetch[i : i + _MAX_BATCH]
            chunk_result = await self._fetch_chunk(chunk)
            stamp = time.monotonic()
            for d in chunk:
                record = chunk_result.get(d)
                self._cache[d] = (stamp, record)
                result[d] = record

        return result

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    async def _fetch_chunk(
        self, domains: list[str]
    ) -> dict[str, Optional[ApprovalRecord]]:
        """Fetch approvals for up to 10 domains in a single HTTP call."""
        params = {"domain": ",".join(domains)}
        try:
            resp = await self._http.get(_ENDPOINT, params=params)
        except httpx.RequestError as exc:
            # Connection refused, timeout, DNS, read errors, etc. — surface
            # as SGPClientError so callers catch it on a single type and
            # the deal-request gate can fail closed.
            raise SGPClientError(
                f"SafeGuard Privacy request failed: {exc.__class__.__name__}: {exc}"
            ) from exc

        if resp.status_code == 404:
            # Entire batch unknown to SGP.
            return {d: None for d in domains}

        if resp.status_code == 401:
            raise SGPAuthError(
                "SafeGuard Privacy rejected the api-key "
                "(missing or lacks iab:buyerAgent scope)",
                status_code=401,
            )

        if resp.status_code == 400:
            raise SGPClientError(
                f"SafeGuard Privacy rejected the request as malformed: {resp.text}",
                status_code=400,
            )

        if resp.status_code in _RETRYABLE_STATUS_CODES or resp.status_code >= 500:
            raise SGPClientError(
                f"SafeGuard Privacy returned {resp.status_code}: {resp.text}",
                status_code=resp.status_code,
            )

        if resp.status_code != 200:
            raise SGPClientError(
                f"Unexpected SafeGuard Privacy response {resp.status_code}: {resp.text}",
                status_code=resp.status_code,
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise SGPClientError(f"SGP response was not JSON: {exc}") from None

        raw_records = payload.get("data") or []
        by_domain: dict[str, Optional[ApprovalRecord]] = {d: None for d in domains}
        for raw in raw_records:
            try:
                record = ApprovalRecord.model_validate(raw)
            except (ValueError, TypeError):
                logger.warning("Skipping malformed SGP record: %r", raw)
                continue
            domain_key = self.normalize_domain(record.domain) or record.domain.lower()
            by_domain[domain_key] = record

        return by_domain