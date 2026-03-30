# Contextual enrichment tools powered by Mixpeek.
#
# Provides IAB taxonomy classification and brand-safety scoring
# that buyer agents can use during inventory research and deal
# evaluation.

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
# 1. Content Classification
# -----------------------------------------------------------------------

class ClassifyContentInput(BaseModel):
    """Input for contextual content classification."""

    text: str | None = Field(
        default=None,
        description="Raw page/creative text to classify",
    )
    url: str | None = Field(
        default=None,
        description="Page URL — Mixpeek will scrape and classify it",
    )
    taxonomy_id: str | None = Field(
        default=None,
        description="Mixpeek taxonomy ID. Uses default IAB taxonomy if omitted.",
    )


class ClassifyContentTool(BaseTool):
    """Classify page or creative content into IAB v3.0 categories.

    Uses Mixpeek's taxonomy engine to map text or a URL to
    standardised IAB content categories with confidence scores.
    Buyer agents use these categories for contextual targeting
    decisions and brand-safety evaluation.
    """

    name: str = "classify_content"
    description: str = (
        "Classify page or ad-creative content into IAB v3.0 taxonomy "
        "categories using Mixpeek. Supply either raw text or a URL. "
        "Returns category codes with confidence scores for contextual "
        "targeting and brand-safety evaluation."
    )
    args_schema: type[BaseModel] = ClassifyContentInput

    def _run(
        self,
        text: str | None = None,
        url: str | None = None,
        taxonomy_id: str | None = None,
    ) -> str:
        return run_async(self._arun(text=text, url=url, taxonomy_id=taxonomy_id))

    async def _arun(
        self,
        text: str | None = None,
        url: str | None = None,
        taxonomy_id: str | None = None,
    ) -> str:
        if not text and not url:
            return json.dumps({"error": "Either text or url must be provided"})

        client = _get_mixpeek_client()
        try:
            # If no taxonomy_id supplied, try to find an IAB taxonomy
            tid = taxonomy_id
            if not tid:
                taxonomies = await client.list_taxonomies()
                iab = [
                    t for t in taxonomies
                    if "iab" in t.get("taxonomy_name", "").lower()
                ]
                if iab:
                    tid = iab[0]["taxonomy_id"]
                elif taxonomies:
                    tid = taxonomies[0]["taxonomy_id"]
                else:
                    return json.dumps({
                        "error": "No taxonomies found in this namespace. "
                        "Create one first via the Mixpeek dashboard."
                    })

            result = await client.classify_content(
                taxonomy_id=tid,
                text=text,
                url=url,
            )
            return json.dumps(result, indent=2)

        except MixpeekError as exc:
            return json.dumps({"error": str(exc)})
        finally:
            await client.close()


# -----------------------------------------------------------------------
# 2. Contextual Search (inventory enrichment)
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
