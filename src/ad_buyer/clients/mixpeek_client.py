# Mixpeek contextual enrichment client for the Ad Buyer Agent.
#
# Provides IAB taxonomy classification via retriever pipelines,
# brand-safety scoring, and contextual inventory search via the
# Mixpeek REST API.

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Default timeout for Mixpeek API calls (seconds).
_DEFAULT_TIMEOUT = 30.0

# Brand-safety sensitive IAB categories that advertisers typically
# want to avoid or require explicit opt-in for.
BRAND_UNSAFE_CATEGORIES = frozenset({
    "Poker and Professional Gambling",
    "Casinos & Gambling",
    "Casino Games",
    "Lotteries and Scratchcards",
    "Sensitive Topics",
    "Adult Content",
    "Illegal Content",
    "Debated Sensitive Social Topics",
    "Terrorism",
    "Crime",
    "Drugs",
    "Tobacco",
    "Arms & Ammunition",
    "Death & Grieving",
})


class MixpeekError(Exception):
    """Raised when a Mixpeek API call fails."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class MixpeekClient:
    """Async HTTP client for the Mixpeek content-intelligence API.

    The client wraps capabilities useful during buyer-agent research
    and execution phases:

    1. **IAB classification** – classify text/URL content against IAB
       v3.0 taxonomy categories using a retriever pipeline that performs
       semantic search against an IAB category reference corpus.
    2. **Brand-safety check** – score content for brand-safety risk
       by identifying sensitive IAB categories in the classification.
    3. **Contextual search** – search indexed ad inventory via retriever
       pipelines combining multimodal search, taxonomy enrichment,
       and reranking.

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

    async def list_retrievers(self, namespace: str | None = None) -> list[dict]:
        """List available retriever pipelines in the namespace."""
        data = await self._request(
            "POST", "/v1/retrievers/list", namespace=namespace, json={}
        )
        return data.get("results", [])

    async def classify_content(
        self,
        retriever_id: str,
        text: str,
        *,
        namespace: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Classify content into IAB taxonomy categories.

        Uses a retriever pipeline that performs semantic search against
        an IAB category reference corpus.  Each result represents an
        IAB category match with a confidence score.

        Args:
            retriever_id: Retriever pipeline configured for IAB search.
            text: Content text to classify.
            namespace: Override namespace for this call.
            limit: Max category matches to return.

        Returns:
            Dict with ``documents`` list, each containing
            ``iab_category_name``, ``iab_path``, ``iab_tier``,
            and ``score``.
        """
        body: dict[str, Any] = {
            "inputs": {"query": text},
            "page_size": limit,
        }
        return await self._request(
            "POST",
            f"/v1/retrievers/{retriever_id}/execute",
            namespace=namespace,
            json=body,
        )

    async def check_brand_safety(
        self,
        retriever_id: str,
        text: str,
        *,
        namespace: str | None = None,
        threshold: float = 0.80,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Check content for brand-safety risk.

        Classifies content via the IAB retriever and flags any matches
        against known sensitive categories (gambling, adult, etc.).

        Args:
            retriever_id: Retriever pipeline configured for IAB search.
            text: Content text to evaluate.
            namespace: Override namespace for this call.
            threshold: Minimum score to consider a category match.
            limit: Max category matches to evaluate.

        Returns:
            Dict with ``safe`` bool, ``risk_level`` (low/medium/high),
            ``flagged_categories`` list, and full ``categories`` list.
        """
        result = await self.classify_content(
            retriever_id=retriever_id,
            text=text,
            namespace=namespace,
            limit=limit,
        )

        docs = result.get("documents", [])
        categories = []
        flagged = []

        for doc in docs:
            score = doc.get("score", 0)
            if score < threshold:
                continue
            cat_name = doc.get("iab_category_name", "")
            entry = {
                "category": cat_name,
                "path": doc.get("iab_path", []),
                "tier": doc.get("iab_tier"),
                "score": score,
            }
            categories.append(entry)
            if cat_name in BRAND_UNSAFE_CATEGORIES:
                flagged.append(entry)

        if flagged:
            max_score = max(f["score"] for f in flagged)
            risk_level = "high" if max_score >= 0.85 else "medium"
        else:
            risk_level = "low"

        return {
            "safe": len(flagged) == 0,
            "risk_level": risk_level,
            "flagged_categories": flagged,
            "categories": categories,
        }

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
