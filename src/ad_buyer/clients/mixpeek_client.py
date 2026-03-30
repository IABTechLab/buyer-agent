# Mixpeek contextual enrichment client for the Ad Buyer Agent.
#
# Provides content classification (IAB taxonomy), brand-safety scoring,
# and visual creative analysis via the Mixpeek REST API.

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Default timeout for Mixpeek API calls (seconds).
_DEFAULT_TIMEOUT = 30.0


class MixpeekError(Exception):
    """Raised when a Mixpeek API call fails."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class MixpeekClient:
    """Async HTTP client for the Mixpeek content-intelligence API.

    The client wraps three capabilities that are useful during the
    buyer-agent research & execution phases:

    1. **Content classification** – map a page URL or text to IAB v3.0
       taxonomy categories via Mixpeek's ``execute_taxonomy`` endpoint.
    2. **Brand-safety check** – lightweight sentiment / safety score
       for a given URL or text snippet.
    3. **Creative analysis** – extract objects, text (OCR), and brand
       logos from an ad-creative image or video URL.

    All methods are async and use ``httpx.AsyncClient`` under the hood.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.mixpeek.com",
        namespace: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.namespace = namespace
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            follow_redirects=True,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self, namespace: str | None = None) -> dict[str, str]:
        """Build request headers with auth and optional namespace."""
        h: dict[str, str] = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        ns = namespace or self.namespace
        if ns:
            h["X-Namespace"] = ns
        return h

    async def _request(
        self,
        method: str,
        path: str,
        *,
        namespace: str | None = None,
        json: dict | None = None,
        params: dict | None = None,
    ) -> dict[str, Any]:
        """Send a request and return the parsed JSON response."""
        try:
            resp = await self._client.request(
                method,
                path,
                headers=self._headers(namespace),
                json=json,
                params=params,
            )
        except httpx.HTTPError as exc:
            raise MixpeekError(f"HTTP error: {exc}") from exc

        if resp.status_code >= 400:
            raise MixpeekError(
                f"Mixpeek API error {resp.status_code}: {resp.text}",
                status_code=resp.status_code,
            )
        return resp.json()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def list_taxonomies(self, namespace: str | None = None) -> list[dict]:
        """List available taxonomies in the namespace."""
        data = await self._request(
            "POST", "/v1/taxonomies/list", namespace=namespace, json={}
        )
        return data.get("results", [])

    async def classify_content(
        self,
        taxonomy_id: str,
        text: str | None = None,
        url: str | None = None,
        *,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """Classify content against an IAB taxonomy.

        Either *text* (raw page text) or *url* (page URL for Mixpeek to
        scrape) should be supplied.  Returns taxonomy node assignments
        with confidence scores.
        """
        body: dict[str, Any] = {}
        if text:
            body["input"] = {"text": text}
        elif url:
            body["input"] = {"url": url}
        else:
            raise ValueError("Either text or url must be provided")

        return await self._request(
            "POST",
            f"/v1/taxonomies/execute/{taxonomy_id}",
            namespace=namespace,
            json=body,
        )

    async def search_content(
        self,
        retriever_id: str,
        query: str,
        *,
        namespace: str | None = None,
        limit: int = 10,
        filters: dict | None = None,
    ) -> dict[str, Any]:
        """Execute a retriever pipeline (contextual search).

        The retriever can combine feature search, brand-safety filtering,
        taxonomy enrichment, and reranking in a single call.
        """
        inputs: dict[str, Any] = {"query": query}
        if filters:
            inputs.update(filters)

        body: dict[str, Any] = {
            "inputs": inputs,
            "page_size": limit,
        }

        return await self._request(
            "POST",
            f"/v1/retrievers/{retriever_id}/execute",
            namespace=namespace,
            json=body,
        )

    async def get_tools(self) -> list[dict]:
        """Fetch the public MCP tools list (no auth required).

        Hits the ``/tools`` REST endpoint on the MCP server, which
        returns all 48 tool definitions without authentication.
        """
        resp = await self._client.get(
            "https://mcp.mixpeek.com/tools",
            timeout=10,
        )
        return resp.json().get("tools", [])

    async def health(self) -> dict[str, Any]:
        """Check MCP server health (no auth required)."""
        resp = await self._client.get(
            "https://mcp.mixpeek.com/health",
            timeout=5,
        )
        return resp.json()

    async def close(self) -> None:
        """Shut down the underlying HTTP client."""
        await self._client.aclose()
