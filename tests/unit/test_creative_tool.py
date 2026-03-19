# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for CreativeManagementTool — CrewAI BaseTool wrapper.

TDD RED phase: these tests are written before the implementation.

bead: buyer-3aa
"""

import json
from typing import Any

import pytest

from ad_buyer.models.creative_asset import AssetType, CreativeAsset, ValidationStatus
from ad_buyer.tools.creative.tool import CreativeManagementTool


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------


@pytest.fixture
def tool():
    """Create a CreativeManagementTool instance."""
    return CreativeManagementTool()


# -----------------------------------------------------------------------
# Tool Metadata Tests
# -----------------------------------------------------------------------


class TestToolMetadata:
    """Tests for tool name, description, and schema."""

    def test_tool_has_name(self, tool):
        """Tool has a non-empty name."""
        assert tool.name
        assert isinstance(tool.name, str)

    def test_tool_has_description(self, tool):
        """Tool has a non-empty description."""
        assert tool.description
        assert isinstance(tool.description, str)

    def test_tool_has_args_schema(self, tool):
        """Tool has an args_schema class."""
        assert tool.args_schema is not None


# -----------------------------------------------------------------------
# Validate Action Tests
# -----------------------------------------------------------------------


class TestValidateAction:
    """Tests for the 'validate' action."""

    def test_validate_valid_display_asset(self, tool):
        """Validate action on a valid display asset returns success."""
        asset_dict = {
            "asset_type": "display",
            "format_spec": {"width": 300, "height": 250},
            "campaign_id": "camp-1",
            "asset_name": "Banner",
            "source_url": "https://example.com/banner.jpg",
        }
        result = tool._run(action="validate", assets=[asset_dict])
        # Result should be parseable and indicate valid
        assert "valid" in result.lower() or "VALID" in result

    def test_validate_invalid_display_asset(self, tool):
        """Validate action on an invalid display asset returns errors."""
        asset_dict = {
            "asset_type": "display",
            "format_spec": {"width": 123, "height": 456},
            "campaign_id": "camp-1",
            "asset_name": "Bad Banner",
            "source_url": "https://example.com/banner.jpg",
        }
        result = tool._run(action="validate", assets=[asset_dict])
        assert "invalid" in result.lower() or "INVALID" in result

    def test_validate_multiple_assets(self, tool):
        """Validate action handles multiple assets."""
        assets = [
            {
                "asset_type": "display",
                "format_spec": {"width": 300, "height": 250},
                "campaign_id": "camp-1",
                "asset_name": "Good Banner",
                "source_url": "https://example.com/good.jpg",
            },
            {
                "asset_type": "display",
                "format_spec": {"width": 999, "height": 999},
                "campaign_id": "camp-1",
                "asset_name": "Bad Banner",
                "source_url": "https://example.com/bad.jpg",
            },
        ]
        result = tool._run(action="validate", assets=assets)
        # Should report on both assets
        assert "Good Banner" in result or "good" in result.lower()
        assert "Bad Banner" in result or "bad" in result.lower()


# -----------------------------------------------------------------------
# Match Action Tests
# -----------------------------------------------------------------------


class TestMatchAction:
    """Tests for the 'match' action."""

    def test_match_returns_assignments(self, tool):
        """Match action returns assignments when creatives match deals."""
        assets = [
            {
                "asset_type": "display",
                "format_spec": {"width": 300, "height": 250},
                "campaign_id": "camp-1",
                "asset_name": "Banner",
                "asset_id": "cr-001",
                "source_url": "https://example.com/banner.jpg",
                "validation_status": "valid",
            },
        ]
        deals = [
            {
                "seller_deal_id": "DJ-1111",
                "deal_name": "Display Deal",
                "media_type": "display",
                "creative_requirements": {"width": 300, "height": 250},
            },
        ]
        result = tool._run(action="match", assets=assets, deals=deals)
        assert "DJ-1111" in result
        assert "cr-001" in result or "Banner" in result

    def test_match_reports_mismatches(self, tool):
        """Match action reports mismatches for unmatched deals."""
        assets = [
            {
                "asset_type": "display",
                "format_spec": {"width": 300, "height": 250},
                "campaign_id": "camp-1",
                "asset_name": "Banner",
                "asset_id": "cr-001",
                "source_url": "https://example.com/banner.jpg",
                "validation_status": "valid",
            },
        ]
        deals = [
            {
                "seller_deal_id": "DJ-2222",
                "deal_name": "Video Deal",
                "media_type": "video",
                "creative_requirements": {"duration_sec": 30},
            },
        ]
        result = tool._run(action="match", assets=assets, deals=deals)
        assert "DJ-2222" in result
        assert "mismatch" in result.lower() or "no matching" in result.lower()


# -----------------------------------------------------------------------
# List Mismatches Action Tests
# -----------------------------------------------------------------------


class TestListMismatchesAction:
    """Tests for the 'list_mismatches' action."""

    def test_list_mismatches_shows_unmatched(self, tool):
        """list_mismatches action returns deals without matching creatives."""
        assets = [
            {
                "asset_type": "video",
                "format_spec": {"vast_version": "4.2", "duration_sec": 15, "format": "mp4"},
                "campaign_id": "camp-1",
                "asset_name": "Short Spot",
                "asset_id": "cr-010",
                "source_url": "https://example.com/short.mp4",
                "validation_status": "valid",
            },
        ]
        deals = [
            {
                "seller_deal_id": "DJ-4567",
                "deal_name": "CTV 30s Deal",
                "media_type": "video",
                "creative_requirements": {"duration_sec": 30},
            },
        ]
        result = tool._run(action="list_mismatches", assets=assets, deals=deals)
        assert "DJ-4567" in result

    def test_list_mismatches_empty_when_all_match(self, tool):
        """list_mismatches returns no mismatches when everything matches."""
        assets = [
            {
                "asset_type": "display",
                "format_spec": {"width": 300, "height": 250},
                "campaign_id": "camp-1",
                "asset_name": "Banner",
                "asset_id": "cr-011",
                "source_url": "https://example.com/banner.jpg",
                "validation_status": "valid",
            },
        ]
        deals = [
            {
                "seller_deal_id": "DJ-9000",
                "deal_name": "Display Deal",
                "media_type": "display",
                "creative_requirements": {"width": 300, "height": 250},
            },
        ]
        result = tool._run(action="list_mismatches", assets=assets, deals=deals)
        assert "no mismatches" in result.lower() or "all" in result.lower()


# -----------------------------------------------------------------------
# Error Handling Tests
# -----------------------------------------------------------------------


class TestErrorHandling:
    """Tests for error handling in the tool."""

    def test_unknown_action_returns_error(self, tool):
        """An unknown action returns an error message."""
        result = tool._run(action="unknown_action")
        assert "error" in result.lower() or "unknown" in result.lower()

    def test_validate_with_empty_assets(self, tool):
        """Validate with empty asset list returns informative message."""
        result = tool._run(action="validate", assets=[])
        assert "no assets" in result.lower() or "empty" in result.lower() or "0" in result

    def test_match_with_no_deals(self, tool):
        """Match with no deals returns informative message."""
        result = tool._run(action="match", assets=[], deals=[])
        # Should handle gracefully
        assert isinstance(result, str)
