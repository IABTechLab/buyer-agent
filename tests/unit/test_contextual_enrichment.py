# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for contextual enrichment CrewAI tools."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from ad_buyer.tools.research.contextual_enrichment import (
    ClassifyContentTool,
    ContextualSearchTool,
)


@pytest.fixture
def classify_tool():
    return ClassifyContentTool()


@pytest.fixture
def search_tool():
    return ContextualSearchTool()


class TestClassifyContentTool:
    def test_tool_name(self, classify_tool):
        assert classify_tool.name == "classify_content"

    @pytest.mark.asyncio
    async def test_returns_error_without_input(self, classify_tool):
        result = await classify_tool._arun()
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_classify_with_taxonomy_id(self, classify_tool):
        mock_client = AsyncMock()
        mock_client.classify_content.return_value = {
            "results": [{"label": "Sports", "score": 0.9}]
        }
        mock_client.close = AsyncMock()

        with patch(
            "ad_buyer.tools.research.contextual_enrichment._get_mixpeek_client",
            return_value=mock_client,
        ):
            result = await classify_tool._arun(
                text="NFL scores", taxonomy_id="tax-123"
            )

        data = json.loads(result)
        assert data["results"][0]["label"] == "Sports"
        mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_classify_auto_discovers_taxonomy(self, classify_tool):
        mock_client = AsyncMock()
        mock_client.list_taxonomies.return_value = [
            {"taxonomy_id": "t1", "taxonomy_name": "IAB Content Taxonomy v3.0"}
        ]
        mock_client.classify_content.return_value = {"results": []}
        mock_client.close = AsyncMock()

        with patch(
            "ad_buyer.tools.research.contextual_enrichment._get_mixpeek_client",
            return_value=mock_client,
        ):
            result = await classify_tool._arun(text="test content")

        data = json.loads(result)
        mock_client.classify_content.assert_called_once_with(
            taxonomy_id="t1", text="test content", url=None,
        )

    @pytest.mark.asyncio
    async def test_classify_no_taxonomies(self, classify_tool):
        mock_client = AsyncMock()
        mock_client.list_taxonomies.return_value = []
        mock_client.close = AsyncMock()

        with patch(
            "ad_buyer.tools.research.contextual_enrichment._get_mixpeek_client",
            return_value=mock_client,
        ):
            result = await classify_tool._arun(text="test")

        data = json.loads(result)
        assert "No taxonomies found" in data["error"]


class TestContextualSearchTool:
    def test_tool_name(self, search_tool):
        assert search_tool.name == "contextual_search"

    @pytest.mark.asyncio
    async def test_search(self, search_tool):
        mock_client = AsyncMock()
        mock_client.search_content.return_value = {
            "results": [{"doc_id": "d1", "score": 0.85}]
        }
        mock_client.close = AsyncMock()

        with patch(
            "ad_buyer.tools.research.contextual_enrichment._get_mixpeek_client",
            return_value=mock_client,
        ):
            result = await search_tool._arun(
                query="sports news", retriever_id="ret-1", limit=5
            )

        data = json.loads(result)
        assert data["results"][0]["score"] == 0.85
        mock_client.search_content.assert_called_once_with(
            retriever_id="ret-1", query="sports news", limit=5,
        )
        mock_client.close.assert_called_once()
