# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for the Mixpeek contextual enrichment client."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ad_buyer.clients.mixpeek_client import (
    BRAND_UNSAFE_CATEGORIES,
    MixpeekClient,
    MixpeekError,
)


@pytest.fixture
def client():
    return MixpeekClient(
        api_key="test-key",
        base_url="https://api.mixpeek.com",
        namespace="test-ns",
    )


class TestMixpeekClientInit:
    def test_defaults(self):
        c = MixpeekClient(api_key="k")
        assert c.api_key == "k"
        assert c.base_url == "https://api.mixpeek.com"
        assert c.namespace is None

    def test_custom_base_url_strips_trailing_slash(self):
        c = MixpeekClient(api_key="k", base_url="https://example.com/")
        assert c.base_url == "https://example.com"

    def test_headers_include_namespace(self, client):
        h = client._headers()
        assert h["Authorization"] == "Bearer test-key"
        assert h["X-Namespace"] == "test-ns"

    def test_headers_without_namespace(self):
        c = MixpeekClient(api_key="k")
        h = c._headers()
        assert "X-Namespace" not in h


class TestClassifyContent:
    @pytest.mark.asyncio
    async def test_classify_with_text(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "documents": [
                {
                    "iab_category_name": "American Football",
                    "iab_path": ["Sports", "American Football"],
                    "iab_tier": 2,
                    "score": 0.87,
                }
            ]
        }

        with patch.object(client._client, "request", new_callable=AsyncMock, return_value=mock_resp):
            result = await client.classify_content(
                retriever_id="ret-123", text="NFL football scores"
            )

        assert result["documents"][0]["iab_category_name"] == "American Football"

    @pytest.mark.asyncio
    async def test_classify_api_error_raises(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"

        with patch.object(client._client, "request", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(MixpeekError, match="401"):
                await client.classify_content(
                    retriever_id="ret-123", text="test"
                )


class TestBrandSafety:
    @pytest.mark.asyncio
    async def test_safe_content(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "documents": [
                {
                    "iab_category_name": "Sports",
                    "iab_path": ["Sports"],
                    "iab_tier": 1,
                    "score": 0.90,
                }
            ]
        }

        with patch.object(client._client, "request", new_callable=AsyncMock, return_value=mock_resp):
            result = await client.check_brand_safety(
                retriever_id="ret-123", text="local basketball game"
            )

        assert result["safe"] is True
        assert result["risk_level"] == "low"
        assert len(result["flagged_categories"]) == 0

    @pytest.mark.asyncio
    async def test_unsafe_gambling_content(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "documents": [
                {
                    "iab_category_name": "Poker and Professional Gambling",
                    "iab_path": ["Sports", "Poker and Professional Gambling"],
                    "iab_tier": 2,
                    "score": 0.88,
                },
                {
                    "iab_category_name": "Casinos & Gambling",
                    "iab_path": ["Attractions", "Casinos & Gambling"],
                    "iab_tier": 2,
                    "score": 0.85,
                },
            ]
        }

        with patch.object(client._client, "request", new_callable=AsyncMock, return_value=mock_resp):
            result = await client.check_brand_safety(
                retriever_id="ret-123", text="poker casino betting"
            )

        assert result["safe"] is False
        assert result["risk_level"] == "high"
        assert len(result["flagged_categories"]) == 2

    @pytest.mark.asyncio
    async def test_threshold_filters_low_scores(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "documents": [
                {
                    "iab_category_name": "Casinos & Gambling",
                    "iab_path": ["Attractions", "Casinos & Gambling"],
                    "iab_tier": 2,
                    "score": 0.75,  # Below threshold
                },
            ]
        }

        with patch.object(client._client, "request", new_callable=AsyncMock, return_value=mock_resp):
            result = await client.check_brand_safety(
                retriever_id="ret-123",
                text="card games",
                threshold=0.80,
            )

        assert result["safe"] is True
        assert len(result["categories"]) == 0  # Filtered out

    def test_brand_unsafe_categories_is_frozenset(self):
        assert isinstance(BRAND_UNSAFE_CATEGORIES, frozenset)
        assert "Casinos & Gambling" in BRAND_UNSAFE_CATEGORIES


class TestSearchContent:
    @pytest.mark.asyncio
    async def test_search_builds_correct_body(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"documents": []}

        with patch.object(client._client, "request", new_callable=AsyncMock, return_value=mock_resp) as mock_req:
            await client.search_content(
                retriever_id="ret-456", query="sports news", limit=5
            )

        call_args = mock_req.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert body["inputs"]["query"] == "sports news"
        assert body["page_size"] == 5


class TestListRetrievers:
    @pytest.mark.asyncio
    async def test_list_returns_results(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [
                {"retriever_id": "r1", "retriever_name": "iab_text_search"}
            ]
        }

        with patch.object(client._client, "request", new_callable=AsyncMock, return_value=mock_resp):
            result = await client.list_retrievers()

        assert len(result) == 1
        assert result[0]["retriever_name"] == "iab_text_search"


class TestListTaxonomies:
    @pytest.mark.asyncio
    async def test_list_returns_results(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [{"taxonomy_id": "t1", "taxonomy_name": "IAB v3.0"}]
        }

        with patch.object(client._client, "request", new_callable=AsyncMock, return_value=mock_resp):
            result = await client.list_taxonomies()

        assert len(result) == 1
        assert result[0]["taxonomy_name"] == "IAB v3.0"


class TestGetTools:
    @pytest.mark.asyncio
    async def test_get_tools_no_auth(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tools": [{"name": "tool1"}]}

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
            tools = await client.get_tools()

        assert len(tools) == 1


class TestClose:
    @pytest.mark.asyncio
    async def test_close(self, client):
        with patch.object(client._client, "aclose", new_callable=AsyncMock) as mock_close:
            await client.close()
            mock_close.assert_called_once()
