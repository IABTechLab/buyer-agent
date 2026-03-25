# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for Seller Discovery MCP tools.

Tests three seller discovery MCP tools: discover_sellers,
get_seller_media_kit, compare_sellers.

bead: buyer-nob
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from ad_buyer.interfaces.mcp_server import mcp
from ad_buyer.media_kit.models import MediaKit, MediaKitError, PackageSummary
from ad_buyer.registry.models import AgentCapability, AgentCard, TrustLevel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_text(call_result) -> str:
    """Extract text content from a FastMCP call_tool result."""
    content_list = call_result[0]
    return content_list[0].text


def _make_agent_card(
    agent_id: str = "seller-001",
    name: str = "Premium Publisher",
    url: str = "http://seller1.example.com",
    capabilities: list[AgentCapability] | None = None,
    trust_level: TrustLevel = TrustLevel.VERIFIED,
    protocols: list[str] | None = None,
) -> AgentCard:
    """Create a test AgentCard."""
    return AgentCard(
        agent_id=agent_id,
        name=name,
        url=url,
        capabilities=capabilities or [],
        trust_level=trust_level,
        protocols=protocols or ["openrtb", "a2a"],
    )


def _make_package_summary(
    package_id: str = "pkg-001",
    name: str = "Premium Display",
    price_range: str = "$15-$25 CPM",
    ad_formats: list[str] | None = None,
    device_types: list[int] | None = None,
    seller_url: str | None = None,
) -> PackageSummary:
    """Create a test PackageSummary."""
    return PackageSummary(
        package_id=package_id,
        name=name,
        description=f"Test package: {name}",
        ad_formats=ad_formats or ["display"],
        device_types=device_types or [1, 2],
        price_range=price_range,
        rate_type="cpm",
        is_featured=False,
        seller_url=seller_url,
    )


def _make_media_kit(
    seller_url: str = "http://seller1.example.com",
    seller_name: str = "Premium Publisher",
    packages: list[PackageSummary] | None = None,
) -> MediaKit:
    """Create a test MediaKit."""
    pkgs = packages or [_make_package_summary(seller_url=seller_url)]
    return MediaKit(
        seller_url=seller_url,
        seller_name=seller_name,
        total_packages=len(pkgs),
        featured=[],
        all_packages=pkgs,
    )


# ---------------------------------------------------------------------------
# Test tool registration
# ---------------------------------------------------------------------------


class TestSellerDiscoveryToolRegistration:
    """Verify all 3 seller discovery MCP tools are registered."""

    @pytest.mark.asyncio
    async def test_discover_sellers_registered(self):
        """discover_sellers should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "discover_sellers" in names

    @pytest.mark.asyncio
    async def test_get_seller_media_kit_registered(self):
        """get_seller_media_kit should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "get_seller_media_kit" in names

    @pytest.mark.asyncio
    async def test_compare_sellers_registered(self):
        """compare_sellers should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "compare_sellers" in names


# ---------------------------------------------------------------------------
# Test discover_sellers
# ---------------------------------------------------------------------------


class TestDiscoverSellers:
    """Tests for the discover_sellers MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_sellers(self, monkeypatch):
        """discover_sellers should return a list of discovered sellers."""
        sellers = [
            _make_agent_card(agent_id="s1", name="Publisher A", url="http://a.com"),
            _make_agent_card(agent_id="s2", name="Publisher B", url="http://b.com"),
        ]
        mock_client = AsyncMock()
        mock_client.discover_sellers.return_value = sellers
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_registry_client",
            lambda: mock_client,
        )

        result = await mcp.call_tool("discover_sellers", {})
        data = json.loads(_extract_text(result))

        assert data["total"] == 2
        assert len(data["sellers"]) == 2
        assert data["sellers"][0]["name"] == "Publisher A"
        assert data["sellers"][1]["name"] == "Publisher B"

    @pytest.mark.asyncio
    async def test_filter_by_capability(self, monkeypatch):
        """discover_sellers should pass capability filter to the client."""
        ctv_cap = AgentCapability(name="ctv", description="CTV inventory")
        sellers = [
            _make_agent_card(
                agent_id="s1", name="CTV Publisher",
                url="http://ctv.com", capabilities=[ctv_cap],
            ),
        ]
        mock_client = AsyncMock()
        mock_client.discover_sellers.return_value = sellers
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_registry_client",
            lambda: mock_client,
        )

        result = await mcp.call_tool(
            "discover_sellers", {"capability": "ctv"}
        )
        data = json.loads(_extract_text(result))

        assert data["total"] == 1
        assert data["sellers"][0]["name"] == "CTV Publisher"
        # Verify capability filter was passed through
        mock_client.discover_sellers.assert_called_once_with(
            capabilities_filter=["ctv"],
        )

    @pytest.mark.asyncio
    async def test_no_sellers_found(self, monkeypatch):
        """discover_sellers should return empty list when no sellers match."""
        mock_client = AsyncMock()
        mock_client.discover_sellers.return_value = []
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_registry_client",
            lambda: mock_client,
        )

        result = await mcp.call_tool("discover_sellers", {})
        data = json.loads(_extract_text(result))

        assert data["total"] == 0
        assert data["sellers"] == []

    @pytest.mark.asyncio
    async def test_seller_fields_included(self, monkeypatch):
        """Each seller in results should include key identification fields."""
        cap = AgentCapability(name="display", description="Display ads")
        sellers = [
            _make_agent_card(
                agent_id="s1",
                name="Test Publisher",
                url="http://test.com",
                capabilities=[cap],
                trust_level=TrustLevel.VERIFIED,
                protocols=["openrtb", "a2a"],
            ),
        ]
        mock_client = AsyncMock()
        mock_client.discover_sellers.return_value = sellers
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_registry_client",
            lambda: mock_client,
        )

        result = await mcp.call_tool("discover_sellers", {})
        data = json.loads(_extract_text(result))
        seller = data["sellers"][0]

        assert "agent_id" in seller
        assert "name" in seller
        assert "url" in seller
        assert "capabilities" in seller
        assert "trust_level" in seller
        assert "protocols" in seller

    @pytest.mark.asyncio
    async def test_returns_valid_json(self, monkeypatch):
        """discover_sellers should return valid JSON."""
        mock_client = AsyncMock()
        mock_client.discover_sellers.return_value = []
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_registry_client",
            lambda: mock_client,
        )

        result = await mcp.call_tool("discover_sellers", {})
        data = json.loads(_extract_text(result))
        assert isinstance(data, dict)
        assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_handles_registry_error(self, monkeypatch):
        """discover_sellers should handle registry errors gracefully."""
        mock_client = AsyncMock()
        mock_client.discover_sellers.side_effect = Exception("Registry down")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_registry_client",
            lambda: mock_client,
        )

        result = await mcp.call_tool("discover_sellers", {})
        data = json.loads(_extract_text(result))
        assert "error" in data


# ---------------------------------------------------------------------------
# Test get_seller_media_kit
# ---------------------------------------------------------------------------


class TestGetSellerMediaKit:
    """Tests for the get_seller_media_kit MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_media_kit(self, monkeypatch):
        """get_seller_media_kit should return a seller's media kit."""
        kit = _make_media_kit(
            seller_url="http://seller.example.com",
            seller_name="Premium Publisher",
            packages=[
                _make_package_summary(
                    package_id="pkg-1",
                    name="Display Package",
                    seller_url="http://seller.example.com",
                ),
                _make_package_summary(
                    package_id="pkg-2",
                    name="CTV Package",
                    price_range="$30-$45 CPM",
                    seller_url="http://seller.example.com",
                ),
            ],
        )
        mock_client = AsyncMock()
        mock_client.get_media_kit.return_value = kit
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_media_kit_client",
            lambda: mock_client,
        )

        result = await mcp.call_tool(
            "get_seller_media_kit",
            {"seller_url": "http://seller.example.com"},
        )
        data = json.loads(_extract_text(result))

        assert data["seller_name"] == "Premium Publisher"
        assert data["total_packages"] == 2
        assert len(data["packages"]) == 2
        assert data["packages"][0]["name"] == "Display Package"
        assert data["packages"][1]["name"] == "CTV Package"

    @pytest.mark.asyncio
    async def test_package_fields_included(self, monkeypatch):
        """Each package should include key fields."""
        kit = _make_media_kit()
        mock_client = AsyncMock()
        mock_client.get_media_kit.return_value = kit
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_media_kit_client",
            lambda: mock_client,
        )

        result = await mcp.call_tool(
            "get_seller_media_kit",
            {"seller_url": "http://seller1.example.com"},
        )
        data = json.loads(_extract_text(result))
        pkg = data["packages"][0]

        required_fields = [
            "package_id", "name", "ad_formats",
            "price_range", "rate_type",
        ]
        for field in required_fields:
            assert field in pkg, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_media_kit_not_reachable(self, monkeypatch):
        """get_seller_media_kit should handle unreachable sellers."""
        mock_client = AsyncMock()
        mock_client.get_media_kit.side_effect = MediaKitError(
            message="Failed to connect to http://down.example.com",
            seller_url="http://down.example.com",
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_media_kit_client",
            lambda: mock_client,
        )

        result = await mcp.call_tool(
            "get_seller_media_kit",
            {"seller_url": "http://down.example.com"},
        )
        data = json.loads(_extract_text(result))
        assert "error" in data

    @pytest.mark.asyncio
    async def test_returns_valid_json(self, monkeypatch):
        """get_seller_media_kit should return valid JSON."""
        kit = _make_media_kit()
        mock_client = AsyncMock()
        mock_client.get_media_kit.return_value = kit
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_media_kit_client",
            lambda: mock_client,
        )

        result = await mcp.call_tool(
            "get_seller_media_kit",
            {"seller_url": "http://seller1.example.com"},
        )
        data = json.loads(_extract_text(result))
        assert isinstance(data, dict)
        assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_empty_media_kit(self, monkeypatch):
        """get_seller_media_kit should handle empty media kits."""
        kit = MediaKit(
            seller_url="http://empty.example.com",
            seller_name="Empty Publisher",
            total_packages=0,
            featured=[],
            all_packages=[],
        )
        mock_client = AsyncMock()
        mock_client.get_media_kit.return_value = kit
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_media_kit_client",
            lambda: mock_client,
        )

        result = await mcp.call_tool(
            "get_seller_media_kit",
            {"seller_url": "http://empty.example.com"},
        )
        data = json.loads(_extract_text(result))

        assert data["total_packages"] == 0
        assert data["packages"] == []


# ---------------------------------------------------------------------------
# Test compare_sellers
# ---------------------------------------------------------------------------


class TestCompareSellers:
    """Tests for the compare_sellers MCP tool."""

    @pytest.mark.asyncio
    async def test_compare_two_sellers(self, monkeypatch):
        """compare_sellers should compare pricing across multiple sellers."""
        kit_a = _make_media_kit(
            seller_url="http://a.example.com",
            seller_name="Publisher A",
            packages=[
                _make_package_summary(
                    package_id="a-1",
                    name="Display",
                    price_range="$10-$20 CPM",
                    ad_formats=["display"],
                    seller_url="http://a.example.com",
                ),
            ],
        )
        kit_b = _make_media_kit(
            seller_url="http://b.example.com",
            seller_name="Publisher B",
            packages=[
                _make_package_summary(
                    package_id="b-1",
                    name="Display Premium",
                    price_range="$20-$35 CPM",
                    ad_formats=["display"],
                    seller_url="http://b.example.com",
                ),
                _make_package_summary(
                    package_id="b-2",
                    name="CTV",
                    price_range="$30-$45 CPM",
                    ad_formats=["video"],
                    seller_url="http://b.example.com",
                ),
            ],
        )

        mock_client = AsyncMock()
        mock_client.get_media_kit.side_effect = [kit_a, kit_b]
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_media_kit_client",
            lambda: mock_client,
        )

        result = await mcp.call_tool(
            "compare_sellers",
            {"seller_urls": ["http://a.example.com", "http://b.example.com"]},
        )
        data = json.loads(_extract_text(result))

        assert data["sellers_compared"] == 2
        assert len(data["sellers"]) == 2
        # Publisher A
        assert data["sellers"][0]["seller_name"] == "Publisher A"
        assert data["sellers"][0]["total_packages"] == 1
        # Publisher B
        assert data["sellers"][1]["seller_name"] == "Publisher B"
        assert data["sellers"][1]["total_packages"] == 2

    @pytest.mark.asyncio
    async def test_compare_with_one_failing_seller(self, monkeypatch):
        """compare_sellers should handle unreachable sellers gracefully."""
        kit_a = _make_media_kit(
            seller_url="http://a.example.com",
            seller_name="Publisher A",
        )

        mock_client = AsyncMock()
        mock_client.get_media_kit.side_effect = [
            kit_a,
            MediaKitError(
                message="Failed to connect",
                seller_url="http://down.example.com",
            ),
        ]
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_media_kit_client",
            lambda: mock_client,
        )

        result = await mcp.call_tool(
            "compare_sellers",
            {"seller_urls": ["http://a.example.com", "http://down.example.com"]},
        )
        data = json.loads(_extract_text(result))

        # Should still return results for reachable seller
        assert data["sellers_compared"] == 2
        reachable = [s for s in data["sellers"] if "error" not in s]
        unreachable = [s for s in data["sellers"] if "error" in s]
        assert len(reachable) == 1
        assert len(unreachable) == 1
        assert reachable[0]["seller_name"] == "Publisher A"

    @pytest.mark.asyncio
    async def test_compare_empty_list(self, monkeypatch):
        """compare_sellers with empty seller list should return empty result."""
        mock_client = AsyncMock()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_media_kit_client",
            lambda: mock_client,
        )

        result = await mcp.call_tool(
            "compare_sellers", {"seller_urls": []}
        )
        data = json.loads(_extract_text(result))

        assert data["sellers_compared"] == 0
        assert data["sellers"] == []

    @pytest.mark.asyncio
    async def test_compare_includes_summary(self, monkeypatch):
        """compare_sellers should include a comparison summary."""
        kit = _make_media_kit(
            seller_url="http://a.example.com",
            seller_name="Publisher A",
            packages=[
                _make_package_summary(
                    name="Display",
                    ad_formats=["display"],
                    seller_url="http://a.example.com",
                ),
            ],
        )
        mock_client = AsyncMock()
        mock_client.get_media_kit.return_value = kit
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_media_kit_client",
            lambda: mock_client,
        )

        result = await mcp.call_tool(
            "compare_sellers",
            {"seller_urls": ["http://a.example.com"]},
        )
        data = json.loads(_extract_text(result))

        assert "summary" in data
        assert "total_packages_across_sellers" in data["summary"]

    @pytest.mark.asyncio
    async def test_returns_valid_json(self, monkeypatch):
        """compare_sellers should return valid JSON."""
        mock_client = AsyncMock()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_media_kit_client",
            lambda: mock_client,
        )

        result = await mcp.call_tool(
            "compare_sellers", {"seller_urls": []}
        )
        data = json.loads(_extract_text(result))
        assert isinstance(data, dict)
        assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_compare_sellers_ad_format_breakdown(self, monkeypatch):
        """compare_sellers should aggregate ad format capabilities."""
        kit_a = _make_media_kit(
            seller_url="http://a.example.com",
            seller_name="Publisher A",
            packages=[
                _make_package_summary(
                    name="Display", ad_formats=["display", "native"],
                    seller_url="http://a.example.com",
                ),
            ],
        )
        kit_b = _make_media_kit(
            seller_url="http://b.example.com",
            seller_name="Publisher B",
            packages=[
                _make_package_summary(
                    name="Video", ad_formats=["video"],
                    seller_url="http://b.example.com",
                ),
            ],
        )
        mock_client = AsyncMock()
        mock_client.get_media_kit.side_effect = [kit_a, kit_b]
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_media_kit_client",
            lambda: mock_client,
        )

        result = await mcp.call_tool(
            "compare_sellers",
            {"seller_urls": ["http://a.example.com", "http://b.example.com"]},
        )
        data = json.loads(_extract_text(result))

        # Each seller entry should show its ad formats
        seller_a = data["sellers"][0]
        assert "ad_formats" in seller_a
        assert "display" in seller_a["ad_formats"]
        assert "native" in seller_a["ad_formats"]
