# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for contextual enrichment CrewAI tools."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from ad_buyer.tools.research.contextual_enrichment import (
    BrandSafetyTool,
    ClassifyContentTool,
    ContextualSearchTool,
)


@pytest.fixture
def classify_tool():
    return ClassifyContentTool()


@pytest.fixture
def brand_safety_tool():
    return BrandSafetyTool()


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
    async def test_classify_with_retriever_id(self, classify_tool):
        mock_client = AsyncMock()
        mock_client.classify_content.return_value = {
            "documents": [
                {
                    "iab_category_name": "Sports",
                    "iab_path": ["Sports"],
                    "iab_tier": 1,
                    "score": 0.9,
                }
            ]
        }
        mock_client.close = AsyncMock()

        with patch(
            "ad_buyer.tools.research.contextual_enrichment._get_mixpeek_client",
            return_value=mock_client,
        ):
            result = await classify_tool._arun(
                text="NFL scores", retriever_id="ret-123"
            )

        data = json.loads(result)
        assert data["categories"][0]["category"] == "Sports"
        assert data["categories"][0]["score"] == 0.9
        mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_classify_auto_discovers_retriever(self, classify_tool):
        mock_client = AsyncMock()
        mock_client.list_retrievers.return_value = [
            {"retriever_id": "ret-1", "retriever_name": "iab_text_search"}
        ]
        mock_client.classify_content.return_value = {"documents": []}
        mock_client.close = AsyncMock()

        with patch(
            "ad_buyer.tools.research.contextual_enrichment._get_mixpeek_client",
            return_value=mock_client,
        ):
            result = await classify_tool._arun(text="test content")

        mock_client.classify_content.assert_called_once_with(
            retriever_id="ret-1", text="test content", limit=10,
        )

    @pytest.mark.asyncio
    async def test_classify_no_retriever(self, classify_tool):
        mock_client = AsyncMock()
        mock_client.list_retrievers.return_value = []
        mock_client.close = AsyncMock()

        with patch(
            "ad_buyer.tools.research.contextual_enrichment._get_mixpeek_client",
            return_value=mock_client,
        ):
            result = await classify_tool._arun(text="test")

        data = json.loads(result)
        assert "No IAB retriever found" in data["error"]


class TestBrandSafetyTool:
    def test_tool_name(self, brand_safety_tool):
        assert brand_safety_tool.name == "check_brand_safety"

    @pytest.mark.asyncio
    async def test_safe_content(self, brand_safety_tool):
        mock_client = AsyncMock()
        mock_client.list_retrievers.return_value = [
            {"retriever_id": "ret-1", "retriever_name": "iab_text_search"}
        ]
        mock_client.check_brand_safety.return_value = {
            "safe": True,
            "risk_level": "low",
            "flagged_categories": [],
            "categories": [{"category": "Sports", "score": 0.9}],
        }
        mock_client.close = AsyncMock()

        with patch(
            "ad_buyer.tools.research.contextual_enrichment._get_mixpeek_client",
            return_value=mock_client,
        ):
            result = await brand_safety_tool._arun(text="basketball game")

        data = json.loads(result)
        assert data["safe"] is True

    @pytest.mark.asyncio
    async def test_unsafe_content(self, brand_safety_tool):
        mock_client = AsyncMock()
        mock_client.list_retrievers.return_value = [
            {"retriever_id": "ret-1", "retriever_name": "iab_text_search"}
        ]
        mock_client.check_brand_safety.return_value = {
            "safe": False,
            "risk_level": "high",
            "flagged_categories": [
                {"category": "Casinos & Gambling", "score": 0.88}
            ],
            "categories": [],
        }
        mock_client.close = AsyncMock()

        with patch(
            "ad_buyer.tools.research.contextual_enrichment._get_mixpeek_client",
            return_value=mock_client,
        ):
            result = await brand_safety_tool._arun(text="casino gambling")

        data = json.loads(result)
        assert data["safe"] is False
        assert data["risk_level"] == "high"


class TestContextualSearchTool:
    def test_tool_name(self, search_tool):
        assert search_tool.name == "contextual_search"

    @pytest.mark.asyncio
    async def test_search(self, search_tool):
        mock_client = AsyncMock()
        mock_client.search_content.return_value = {
            "documents": [{"document_id": "d1", "score": 0.85}]
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
        assert data["documents"][0]["score"] == 0.85
        mock_client.search_content.assert_called_once_with(
            retriever_id="ret-1", query="sports news", limit=5,
        )
        mock_client.close.assert_called_once()
