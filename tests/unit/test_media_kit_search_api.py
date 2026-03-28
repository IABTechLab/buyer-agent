# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the /media-kit/search API endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ad_buyer.interfaces.api import main as api_module
from ad_buyer.media_kit.models import PackageSummary


@pytest.fixture
def client():
    return TestClient(api_module.app)


def test_media_kit_search_returns_503_when_no_sellers(client):
    """When no seller URLs are configured, return 503."""
    mock_settings = MagicMock()
    mock_settings.get_media_kit_seller_urls.return_value = []
    with patch.object(api_module, "_current_settings", return_value=mock_settings):
        response = client.post(
            "/media-kit/search",
            json={"query": "sports"},
        )
    assert response.status_code == 503
    assert "No seller" in response.json()["detail"]


def test_media_kit_search_returns_packages(client):
    """When sellers return packages, endpoint returns query + packages + total."""
    pkg = PackageSummary(
        package_id="pkg-sports-1",
        name="Sports Premium Video",
        description="High-impact sports inventory",
        price_range="$28-$42 CPM",
        tags=["sports", "video"],
        seller_url="http://localhost:3000",
    )
    mock_search = AsyncMock(return_value=[pkg])
    mock_close = AsyncMock()

    mock_settings = MagicMock()
    mock_settings.get_media_kit_seller_urls.return_value = ["http://localhost:3000"]
    mock_settings.opendirect_api_key = None

    with patch.object(api_module, "_current_settings", return_value=mock_settings):
        with patch.object(api_module, "MediaKitClient") as MockClient:
            instance = MagicMock()
            instance.search_packages = mock_search
            instance.close = mock_close
            MockClient.return_value = instance

            response = client.post(
                "/media-kit/search",
                json={"query": "sports"},
            )

    assert response.status_code == 200
    data = response.json()
    assert data["query"] == "sports"
    assert data["total"] == 1
    assert len(data["packages"]) == 1
    assert data["packages"][0]["package_id"] == "pkg-sports-1"
    assert data["packages"][0]["name"] == "Sports Premium Video"
    assert data["packages"][0]["seller_url"] == "http://localhost:3000"
    mock_search.assert_called_once_with("http://localhost:3000", query="sports")
    mock_close.assert_called_once()
