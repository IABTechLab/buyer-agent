# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for the Mixpeek contextual enrichment client."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ad_buyer.clients.mixpeek_client import MixpeekClient, MixpeekError


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
            "results": [{"label": "Sports", "score": 0.95}]
        }

        with patch.object(client._client, "request", new_callable=AsyncMock, return_value=mock_resp):
            result = await client.classify_content(
                taxonomy_id="tax-123", text="NFL football scores"
            )

        assert result["results"][0]["label"] == "Sports"

    @pytest.mark.asyncio
    async def test_classify_requires_text_or_url(self, client):
        with pytest.raises(ValueError, match="Either text or url"):
            await client.classify_content(taxonomy_id="tax-123")

    @pytest.mark.asyncio
    async def test_classify_api_error_raises(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"

        with patch.object(client._client, "request", new_callable=AsyncMock, return_value=mock_resp):
            with pytest.raises(MixpeekError, match="401"):
                await client.classify_content(
                    taxonomy_id="tax-123", text="test"
                )


class TestSearchContent:
    @pytest.mark.asyncio
    async def test_search_builds_correct_body(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": []}

        with patch.object(client._client, "request", new_callable=AsyncMock, return_value=mock_resp) as mock_req:
            await client.search_content(
                retriever_id="ret-456", query="sports news", limit=5
            )

        call_args = mock_req.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert body["inputs"]["query"] == "sports news"
        assert body["page_size"] == 5


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
