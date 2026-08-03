# Author: SafeGuard Privacy
# Donated to IAB Tech Lab

"""IAB Diligence Platform (SGP) platform client.

Async HTTP client for the IAB Diligence Platform integration API. Currently
exposes a single capability: checking whether a vendor has the IAB
buyer-agent approval flag set on the buyer's SGP tenant.

Endpoint:
    GET /api/v1/integrations/iab/buyer-agent-approval?domain=a.com,b.com

Auth: api-key header, scope `iab:buyerAgent`.
Limit: up to 10 domains per call (SGP-enforced).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from ..models.sgp import ApprovalRecord

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 15.0
_MAX_BATCH = 10
# Statuses worth another attempt: gateway/availability blips and rate limiting.
# Everything else (400/401/404) is a definite answer and is never retried.
_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}
_DEFAULT_MAX_RETRIES = 2
_DEFAULT_RETRY_BACKOFF_SECONDS = 0.5
_ENDPOINT = "/api/v1/integrations/iab/buyer-agent-approval"


# Ordered list of dict keys to probe when deriving a seller domain for an
# SGP approval lookup.
#
# The first group is the product vocabulary: ``domain`` is the field the
# OpenDirect Product resource defines (see models.opendirect.Product), and
# ``seller_domain`` is this codebase's normalized name for it.
#
# The second group is the deal / SSP-connector vocabulary, kept so that a
# connector-derived deal dict also resolves: ``publisherDomain`` comes from
# Magnite and Index Exchange raw deals, ``publisher_domain`` from PubMatic
# and CSV import, and ``seller_url`` from deal records (where it is the
# seller *system endpoint*, hence lowest priority). These are not product
# fields — do not treat them as authoritative for a product.
_DOMAIN_KEYS = (
    "domain",
    "seller_domain",
    "sellerDomain",
    "publisherDomain",
    "publisher_domain",
    "seller_url",
)
_PUBLISHER_KEYS = ("publisherId", "publisher")


def extract_product_domain(product: dict) -> str | None:
    """Best-guess seller domain from a product dict for an SGP lookup.

    Checks explicit domain fields first (product vocabulary before the
    deal/SSP vocabulary — see ``_DOMAIN_KEYS``), then falls back to
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


def _same_site(requested: str, returned: str) -> bool:
    """True when two hostnames are the same site, or one is a parent of the other.

    Compares on a label boundary, so ``notexample.com`` never matches
    ``example.com``. Used to pair an SGP response back to the domain that
    was actually queried when the platform echoes a different spelling.
    """
    if not requested or not returned:
        return False
    if requested == returned:
        return True
    return requested.endswith("." + returned) or returned.endswith("." + requested)


class SGPClientError(Exception):
    """Error raised by SGPClient for API or transport failures."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


class SGPAuthError(SGPClientError):
    """Raised on 401 — api-key missing, invalid, or lacks required scope."""


class SGPClient:
    """Async client for IAB Diligence Platform buyer-agent approval checks.

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
        max_retries: Extra attempts for transport errors and the statuses in
            ``_RETRYABLE_STATUS_CODES``. Callers enforcing approval fail
            closed on exhaustion, so a transient blip should not be the same
            event as a definite denial. ``0`` disables retrying.
        retry_backoff_seconds: Base delay, doubled per attempt. ``0`` retries
            immediately (used by tests).

    Can be used as an async context manager, which closes the underlying
    httpx client on exit.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.safeguardprivacy.com",
        timeout: float = _DEFAULT_TIMEOUT,
        cache_ttl_seconds: int = 900,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        retry_backoff_seconds: float = _DEFAULT_RETRY_BACKOFF_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._cache_ttl = cache_ttl_seconds
        self._max_retries = max(0, max_retries)
        self._retry_backoff = max(0.0, retry_backoff_seconds)
        self._cache: dict[str, tuple[float, ApprovalRecord | None]] = {}
        self._closed = False
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"api-key": api_key},
            timeout=timeout,
        )

    async def __aenter__(self) -> SGPClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying httpx client. Idempotent."""
        if self._closed:
            return
        self._closed = True
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_domain(value: str) -> str:
        """Reduce a seller URL or raw domain to the form SGP accepts.

        Strips scheme, ``www.``, path, query, port, and any trailing FQDN
        dot; lowercases. Returns an empty string for inputs that yield no
        host.
        """
        if not value:
            return ""
        raw = value.strip()
        # urlparse needs a scheme to extract netloc reliably
        if "://" not in raw:
            raw = "http://" + raw
        host = urlparse(raw).hostname or ""
        host = host.lower().rstrip(".")
        if host.startswith("www."):
            host = host[4:]
        return host

    async def check_approvals(self, domains: list[str]) -> dict[str, ApprovalRecord | None]:
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
        result: dict[str, ApprovalRecord | None] = {}
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

    async def _fetch_chunk(self, domains: list[str]) -> dict[str, ApprovalRecord | None]:
        """Fetch approvals for up to 10 domains, retrying transient failures.

        Transport errors and the statuses in ``_RETRYABLE_STATUS_CODES`` get
        up to ``max_retries`` extra attempts with exponential backoff. A
        definite answer (200/400/401/404) is returned or raised on the first
        attempt -- retrying those would only delay the verdict.

        On exhaustion the original failure is raised as ``SGPClientError``,
        so enforcing callers still fail closed.
        """
        params = {"domain": ",".join(domains)}
        attempts = self._max_retries + 1

        for attempt in range(attempts):
            final = attempt == attempts - 1
            try:
                resp = await self._http.get(_ENDPOINT, params=params)
            except httpx.RequestError as exc:
                # Connection refused, timeout, DNS, read errors, etc. — surface
                # as SGPClientError so callers catch it on a single type and
                # the deal-request gate can fail closed.
                if final:
                    raise SGPClientError(
                        f"IAB Diligence Platform request failed after "
                        f"{attempts} attempt(s): {exc.__class__.__name__}: {exc}"
                    ) from exc
                await self._backoff(attempt, f"{exc.__class__.__name__}: {exc}")
                continue

            if resp.status_code in _RETRYABLE_STATUS_CODES and not final:
                await self._backoff(attempt, f"HTTP {resp.status_code}")
                continue

            return self._parse_chunk_response(resp, domains, attempts=attempts)

        # Unreachable: the final attempt always returns or raises above.
        raise SGPClientError("IAB Diligence Platform retry loop exited unexpectedly")

    async def _backoff(self, attempt: int, why: str) -> None:
        """Wait before the next attempt, doubling the delay each time."""
        delay = self._retry_backoff * (2**attempt)
        logger.warning(
            "IAB Diligence Platform lookup failed (%s); retrying in %.2fs (attempt %d of %d)",
            why,
            delay,
            attempt + 1,
            self._max_retries + 1,
        )
        if delay > 0:
            await asyncio.sleep(delay)

    def _parse_chunk_response(
        self,
        resp: httpx.Response,
        domains: list[str],
        *,
        attempts: int = 1,
    ) -> dict[str, ApprovalRecord | None]:
        """Turn one SGP response into per-domain records, or raise."""

        if resp.status_code == 404:
            # Entire batch unknown to SGP.
            return {d: None for d in domains}

        if resp.status_code == 401:
            raise SGPAuthError(
                "IAB Diligence Platform rejected the api-key "
                "(missing or lacks iab:buyerAgent scope)",
                status_code=401,
            )

        if resp.status_code == 400:
            raise SGPClientError(
                f"IAB Diligence Platform rejected the request as malformed: {resp.text}",
                status_code=400,
            )

        if resp.status_code in _RETRYABLE_STATUS_CODES or resp.status_code >= 500:
            # Reached only once retries are exhausted (or disabled), so say so
            # -- "returned 503" and "returned 503 three times" are different
            # operational events for whoever reads this.
            raise SGPClientError(
                f"IAB Diligence Platform returned {resp.status_code} after "
                f"{attempts} attempt(s): {resp.text}",
                status_code=resp.status_code,
            )

        if resp.status_code != 200:
            raise SGPClientError(
                f"Unexpected IAB Diligence Platform response {resp.status_code}: {resp.text}",
                status_code=resp.status_code,
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise SGPClientError(f"SGP response was not JSON: {exc}") from None

        raw_records = payload.get("data") or []
        by_domain: dict[str, ApprovalRecord | None] = {d: None for d in domains}
        requested = set(domains)

        # Pass 1: records whose echoed domain matches a requested one exactly.
        leftover: list[tuple[str, ApprovalRecord]] = []
        for raw in raw_records:
            try:
                record = ApprovalRecord.model_validate(raw)
            except (ValueError, TypeError):
                logger.warning("Skipping malformed SGP record: %r", raw)
                continue
            key = self.normalize_domain(record.domain) or record.domain.strip().lower()
            if key in requested:
                by_domain[key] = record
            else:
                leftover.append((key, record))

        # Pass 2: SGP may answer with the vendor's canonical/apex domain for a
        # queried subdomain (or the reverse). Resolve those against domains
        # still unanswered so the record is not discarded -- dropping it would
        # leave the requested domain looking UNKNOWN and, because
        # ``check_approvals`` caches whatever lands here, would block an
        # approved vendor for the full cache TTL.
        #
        # Matching is deliberately conservative: only a parent/child match on a
        # label boundary counts. A record for an unrelated domain is never
        # accepted as the verdict for a queried one, not even when it is the
        # only record in the response -- attributing another vendor's approval
        # would make this gate fail open.
        used: set[int] = set()
        for d in domains:
            if by_domain[d] is not None:
                continue
            candidates = [
                (key, record, i) for i, (key, record) in enumerate(leftover) if _same_site(d, key)
            ]
            if not candidates:
                continue
            # Most specific (longest) matching domain wins. Among equally
            # specific records a non-approval wins, so a response carrying
            # conflicting records can never upgrade a vendor to approved.
            key, record, index = min(
                candidates,
                key=lambda c: (-len(c[0]), c[1].iab_buyer_agent_approval),
            )
            by_domain[d] = record
            used.add(index)

        for i, (key, record) in enumerate(leftover):
            if i in used or any(_same_site(d, key) for d in domains):
                # Either applied, or pairable but a more specific record won.
                continue
            logger.warning(
                "SGP returned an approval record for %r that could not be paired "
                "with any requested domain %s; ignoring it",
                record.domain,
                domains,
            )

        return by_domain
