# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Authentication middleware for outbound HTTP requests to sellers.

Attaches API keys from :class:`ApiKeyStore` to outgoing ``httpx``
requests and inspects responses for 401 status codes that indicate
expired or revoked credentials.
"""

from dataclasses import dataclass
from typing import Literal, Optional
from urllib.parse import urlparse

import httpx

from .key_store import ApiKeyStore


@dataclass
class AuthResponse:
    """Result of inspecting an HTTP response for auth issues.

    Attributes:
        needs_reauth: True if the response indicates the key is invalid.
        seller_url: The base URL of the seller that returned the error.
        status_code: The HTTP status code from the response.
    """

    needs_reauth: bool = False
    seller_url: str = ""
    status_code: int = 0


class AuthMiddleware:
    """Attaches stored API keys to outgoing requests and handles 401s.

    Args:
        key_store: The :class:`ApiKeyStore` holding per-seller keys.
        header_type: How to send the key.  ``"api_key"`` uses the
            ``X-Api-Key`` header; ``"bearer"`` uses
            ``Authorization: Bearer <key>``.
    """

    def __init__(
        self,
        key_store: ApiKeyStore,
        header_type: Literal["api_key", "bearer"] = "api_key",
    ) -> None:
        self._store = key_store
        self._header_type = header_type

    # ------------------------------------------------------------------
    # Request decoration
    # ------------------------------------------------------------------

    def add_auth(self, request: httpx.Request) -> httpx.Request:
        """Return a copy of *request* with the appropriate auth header.

        If no key is stored for the request's seller URL the request is
        returned unchanged.
        """
        seller_url = self._extract_base_url(request.url)
        api_key = self._store.get_key(seller_url)
        if api_key is None:
            return request

        # Build new headers (httpx.Request headers are immutable once set,
        # so we construct a fresh request with the extra header).
        new_headers = dict(request.headers)
        if self._header_type == "bearer":
            new_headers["Authorization"] = f"Bearer {api_key}"
        else:
            new_headers["X-Api-Key"] = api_key

        return httpx.Request(
            method=request.method,
            url=request.url,
            headers=new_headers,
            content=request.content,
        )

    # ------------------------------------------------------------------
    # Response inspection
    # ------------------------------------------------------------------

    def handle_response(self, response: httpx.Response) -> AuthResponse:
        """Inspect *response* and return an :class:`AuthResponse`.

        Only HTTP 401 triggers ``needs_reauth``; 403 (authorization, not
        authentication) is intentionally excluded.
        """
        seller_url = self._extract_base_url(response.request.url)
        if response.status_code == 401:
            return AuthResponse(
                needs_reauth=True,
                seller_url=seller_url,
                status_code=401,
            )
        return AuthResponse(
            needs_reauth=False,
            seller_url=seller_url,
            status_code=response.status_code,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_base_url(url: httpx.URL | str) -> str:
        """Extract ``scheme://host[:port]`` from a full URL."""
        parsed = urlparse(str(url))
        base = f"{parsed.scheme}://{parsed.netloc}"
        return base.rstrip("/")
