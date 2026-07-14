# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the seller discovery application service (EP-2.2).

Exercises ``ad_buyer.services.discovery_service`` directly with faked
registry / media-kit clients -- happy paths plus error degradation.

bead: ar-22w1
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from ad_buyer.media_kit.models import MediaKit, MediaKitError, PackageSummary
from ad_buyer.registry.models import AgentCapability, AgentCard, TrustLevel
from ad_buyer.services import discovery_service


def _agent_card() -> AgentCard:
    return AgentCard(
        agent_id="seller-001",
        name="Premium Publisher",
        url="http://seller1.example.com",
        capabilities=[AgentCapability(name="ctv", description="CTV inventory", tags=["video"])],
        trust_level=TrustLevel.VERIFIED,
        protocols=["openrtb", "a2a"],
    )


def _media_kit(seller_url: str = "http://seller1.example.com") -> MediaKit:
    pkg = PackageSummary(
        package_id="pkg-001",
        name="Premium Display",
        description="Test package",
        ad_formats=["display"],
        device_types=[1, 2],
        price_range="$15-$25 CPM",
        rate_type="cpm",
        is_featured=False,
        seller_url=seller_url,
    )
    return MediaKit(
        seller_url=seller_url,
        seller_name="Premium Publisher",
        total_packages=1,
        featured=[],
        all_packages=[pkg],
    )


# ---------------------------------------------------------------------------
# discover_sellers
# ---------------------------------------------------------------------------


class TestDiscoverSellers:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        registry = AsyncMock()
        registry.discover_sellers.return_value = [_agent_card()]
        result = await discovery_service.discover_sellers(registry, capability="ctv")
        assert result["total"] == 1
        assert result["sellers"][0]["agent_id"] == "seller-001"
        assert result["sellers"][0]["trust_level"] == TrustLevel.VERIFIED.value
        registry.discover_sellers.assert_awaited_once_with(capabilities_filter=["ctv"])

    @pytest.mark.asyncio
    async def test_error_degrades_to_payload(self):
        registry = AsyncMock()
        registry.discover_sellers.side_effect = RuntimeError("registry down")
        result = await discovery_service.discover_sellers(registry)
        assert result["total"] == 0
        assert result["sellers"] == []
        assert "error" in result


# ---------------------------------------------------------------------------
# get_seller_media_kit
# ---------------------------------------------------------------------------


class TestGetSellerMediaKit:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        client = AsyncMock()
        client.get_media_kit.return_value = _media_kit()
        result = await discovery_service.get_seller_media_kit(client, "http://seller1.example.com")
        assert result["seller_name"] == "Premium Publisher"
        assert result["total_packages"] == 1
        assert result["packages"][0]["package_id"] == "pkg-001"

    @pytest.mark.asyncio
    async def test_media_kit_error(self):
        client = AsyncMock()
        client.get_media_kit.side_effect = MediaKitError("not found")
        result = await discovery_service.get_seller_media_kit(client, "http://x.example.com")
        assert "error" in result
        assert result["seller_url"] == "http://x.example.com"


# ---------------------------------------------------------------------------
# compare_sellers
# ---------------------------------------------------------------------------


class TestCompareSellers:
    @pytest.mark.asyncio
    async def test_mixed_reachability(self):
        good_url = "http://good.example.com"
        bad_url = "http://bad.example.com"

        async def _fetch(url):
            if url == bad_url:
                raise MediaKitError("unreachable")
            return _media_kit(seller_url=good_url)

        client = AsyncMock()
        client.get_media_kit.side_effect = _fetch

        result = await discovery_service.compare_sellers(client, [good_url, bad_url])
        assert result["sellers_compared"] == 2
        assert result["summary"]["sellers_reachable"] == 1
        assert result["summary"]["sellers_unreachable"] == 1
