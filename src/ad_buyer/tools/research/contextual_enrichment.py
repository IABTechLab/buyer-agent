# Contextual enrichment tools powered by Mixpeek.
#
# Provides IAB taxonomy classification, brand-safety scoring,
# and contextual inventory search that buyer agents can use during
# inventory research and deal evaluation.

from __future__ import annotations

import json
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...async_utils import run_async
from ...clients.mixpeek_client import MixpeekClient, MixpeekError
from ...config.settings import Settings


def _get_mixpeek_client() -> MixpeekClient:
    """Create a MixpeekClient from current settings."""
    settings = Settings()
    return MixpeekClient(
        api_key=settings.mixpeek_api_key,
        base_url=settings.mixpeek_base_url,
        namespace=settings.mixpeek_namespace,
    )


# -----------------------------------------------------------------------
# 1. Content Classification (IAB Taxonomy)
# -----------------------------------------------------------------------

class ClassifyContentInput(BaseModel):
    """Input for contextual content classification."""

    text: str = Field(
        description="Page or ad-creative text to classify into IAB categories",
    )
    retriever_id: str | None = Field(
        default=None,
        description=(
            "Mixpeek retriever pipeline ID for IAB classification. "
            "If omitted, auto-discovers an IAB retriever in the namespace."
        ),
    )
    limit: int = Field(
        default=10,
        description="Max IAB category matches to return",
        ge=1,
        le=50,
    )


class ClassifyContentTool(BaseTool):
    """Classify page or creative content into IAB v3.0 categories.

    Uses a Mixpeek retriever pipeline to perform semantic search
    against an IAB category reference corpus.  Returns ranked
    category matches with hierarchical paths (e.g.
    Sports > American Football) and confidence scores.

    Buyer agents use these categories for contextual targeting
    decisions and brand-safety evaluation.
    """

    name: str = "classify_content"
    description: str = (
        "Classify page or ad-creative content into IAB v3.0 taxonomy "
        "categories using Mixpeek. Supply text content to classify. "
        "Returns ranked IAB category matches with hierarchical paths "
        "(e.g. Sports > American Football) and confidence scores for "
        "contextual targeting and brand-safety evaluation."
    )
    args_schema: type[BaseModel] = ClassifyContentInput

    def _run(
        self,
        text: str = "",
        retriever_id: str | None = None,
        limit: int = 10,
    ) -> str:
        return run_async(self._arun(text=text, retriever_id=retriever_id, limit=limit))

    async def _arun(
        self,
        text: str = "",
        retriever_id: str | None = None,
        limit: int = 10,
    ) -> str:
        if not text:
            return json.dumps({"error": "text must be provided"})

        client = _get_mixpeek_client()
        try:
            rid = retriever_id
            if not rid:
                rid = await _discover_iab_retriever(client)
                if not rid:
                    return json.dumps({
                        "error": "No IAB retriever found in this namespace. "
                        "Set MIXPEEK_NAMESPACE to a namespace with IAB data, "
                        "or pass retriever_id explicitly."
                    })

            result = await client.classify_content(
                retriever_id=rid, text=text, limit=limit,
            )

            # Simplify output for the agent
            docs = result.get("documents", [])
            categories = [
                {
                    "category": d.get("iab_category_name"),
                    "path": d.get("iab_path", []),
                    "tier": d.get("iab_tier"),
                    "score": round(d.get("score", 0), 4),
                }
                for d in docs
            ]
            return json.dumps({"categories": categories}, indent=2)

        except MixpeekError as exc:
            return json.dumps({"error": str(exc)})
        finally:
            await client.close()


# -----------------------------------------------------------------------
# 2. Brand Safety Check
# -----------------------------------------------------------------------

class BrandSafetyInput(BaseModel):
    """Input for brand-safety evaluation."""

    text: str = Field(
        description="Page or ad-creative text to evaluate for brand safety",
    )
    retriever_id: str | None = Field(
        default=None,
        description=(
            "Mixpeek retriever pipeline ID for IAB classification. "
            "If omitted, auto-discovers an IAB retriever in the namespace."
        ),
    )
    threshold: float = Field(
        default=0.80,
        description="Minimum confidence score to consider a category match",
        ge=0.0,
        le=1.0,
    )


class BrandSafetyTool(BaseTool):
    """Evaluate content for brand-safety risk.

    Classifies content via IAB taxonomy and flags matches against
    known sensitive categories (gambling, adult content, etc.).
    Returns a safety verdict with risk level and flagged categories.
    """

    name: str = "check_brand_safety"
    description: str = (
        "Evaluate page or ad-creative content for brand-safety risk. "
        "Classifies content into IAB categories and flags sensitive "
        "categories (gambling, adult, etc.). Returns safe/unsafe verdict, "
        "risk level (low/medium/high), and flagged categories."
    )
    args_schema: type[BaseModel] = BrandSafetyInput

    def _run(
        self,
        text: str = "",
        retriever_id: str | None = None,
        threshold: float = 0.80,
    ) -> str:
        return run_async(
            self._arun(text=text, retriever_id=retriever_id, threshold=threshold)
        )

    async def _arun(
        self,
        text: str = "",
        retriever_id: str | None = None,
        threshold: float = 0.80,
    ) -> str:
        if not text:
            return json.dumps({"error": "text must be provided"})

        client = _get_mixpeek_client()
        try:
            rid = retriever_id
            if not rid:
                rid = await _discover_iab_retriever(client)
                if not rid:
                    return json.dumps({
                        "error": "No IAB retriever found in this namespace."
                    })

            result = await client.check_brand_safety(
                retriever_id=rid, text=text, threshold=threshold,
            )
            return json.dumps(result, indent=2)

        except MixpeekError as exc:
            return json.dumps({"error": str(exc)})
        finally:
            await client.close()


# -----------------------------------------------------------------------
# 3. Contextual Search (inventory enrichment)
# -----------------------------------------------------------------------

class ContextualSearchInput(BaseModel):
    """Input for contextual inventory search."""

    query: str = Field(
        description="Natural-language search query (e.g. 'sports news articles')",
    )
    retriever_id: str = Field(
        description="Mixpeek retriever pipeline ID to execute",
    )
    limit: int = Field(
        default=10,
        description="Max results to return",
        ge=1,
        le=100,
    )


class ContextualSearchTool(BaseTool):
    """Search indexed inventory via a Mixpeek retriever pipeline.

    Retriever pipelines can chain feature search, brand-safety
    filtering, taxonomy enrichment, and reranking in a single call.
    Use this to find contextually relevant inventory during the
    research phase.
    """

    name: str = "contextual_search"
    description: str = (
        "Search indexed ad inventory using a Mixpeek retriever pipeline. "
        "Pipelines can combine multimodal search, brand-safety filtering, "
        "IAB taxonomy enrichment, and reranking. Returns matching inventory "
        "with relevance scores and enriched metadata."
    )
    args_schema: type[BaseModel] = ContextualSearchInput

    def _run(
        self,
        query: str = "",
        retriever_id: str = "",
        limit: int = 10,
    ) -> str:
        return run_async(
            self._arun(query=query, retriever_id=retriever_id, limit=limit)
        )

    async def _arun(
        self,
        query: str = "",
        retriever_id: str = "",
        limit: int = 10,
    ) -> str:
        client = _get_mixpeek_client()
        try:
            result = await client.search_content(
                retriever_id=retriever_id,
                query=query,
                limit=limit,
            )
            return json.dumps(result, indent=2)
        except MixpeekError as exc:
            return json.dumps({"error": str(exc)})
        finally:
            await client.close()


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

async def _discover_iab_retriever(client: MixpeekClient) -> str | None:
    """Find an IAB text search retriever in the current namespace."""
    retrievers = await client.list_retrievers()
    # Prefer a retriever with "iab" and "text" in the name
    for r in retrievers:
        name = r.get("retriever_name", "").lower()
        if "iab" in name and "text" in name:
            return r["retriever_id"]
    # Fall back to any retriever with "iab" in the name
    for r in retrievers:
        if "iab" in r.get("retriever_name", "").lower():
            return r["retriever_id"]
    return None
