# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for CreativeMatcher — matches validated creative assets to deals.

TDD RED phase: these tests are written before the implementation.

bead: buyer-3aa
"""

from dataclasses import dataclass, field
from typing import Any

import pytest

from ad_buyer.models.creative_asset import AssetType, CreativeAsset, ValidationStatus
from ad_buyer.tools.creative.matcher import CreativeMatcher, MatchResult


# -----------------------------------------------------------------------
# Fixtures & Helpers
# -----------------------------------------------------------------------


def _make_asset(
    asset_type: AssetType,
    format_spec: dict,
    validation_status: ValidationStatus = ValidationStatus.VALID,
    **kwargs,
) -> CreativeAsset:
    """Helper to construct a validated CreativeAsset."""
    defaults = {
        "campaign_id": "camp-test",
        "asset_name": "Test Asset",
        "source_url": "https://example.com/creative.bin",
    }
    defaults.update(kwargs)
    return CreativeAsset(
        asset_type=asset_type,
        format_spec=format_spec,
        validation_status=validation_status,
        **defaults,
    )


def _make_deal(
    deal_id: str,
    media_type: str,
    **requirements,
) -> dict[str, Any]:
    """Create a mock deal dict with creative requirements.

    The deal format matches what DealStore returns — a dict with keys
    like seller_deal_id, media_type, and creative_requirements.
    """
    return {
        "seller_deal_id": deal_id,
        "deal_name": f"Deal {deal_id}",
        "media_type": media_type,
        "creative_requirements": requirements,
    }


@pytest.fixture
def matcher():
    """Create a CreativeMatcher instance."""
    return CreativeMatcher()


# -----------------------------------------------------------------------
# Basic Matching Tests
# -----------------------------------------------------------------------


class TestBasicMatching:
    """Tests for basic creative-to-deal matching."""

    def test_display_matches_display_deal(self, matcher):
        """A display creative matches a display deal with matching dimensions."""
        asset = _make_asset(
            AssetType.DISPLAY,
            {"width": 300, "height": 250},
            asset_id="cr-001",
        )
        deal = _make_deal("DJ-1111", "display", width=300, height=250)

        result = matcher.match_creatives_to_deals([asset], [deal])

        assert len(result.matches) == 1
        assert result.matches[0]["deal_id"] == "DJ-1111"
        assert result.matches[0]["asset_id"] == "cr-001"

    def test_video_matches_video_deal_by_duration(self, matcher):
        """A video creative matches a deal requiring its duration."""
        asset = _make_asset(
            AssetType.VIDEO,
            {"vast_version": "4.2", "duration_sec": 30, "format": "mp4"},
            asset_id="cr-002",
        )
        deal = _make_deal("DJ-2222", "video", duration_sec=30)

        result = matcher.match_creatives_to_deals([asset], [deal])

        assert len(result.matches) == 1
        assert result.matches[0]["deal_id"] == "DJ-2222"

    def test_audio_matches_audio_deal(self, matcher):
        """An audio creative matches an audio deal."""
        asset = _make_asset(
            AssetType.AUDIO,
            {"daast_version": "1.0", "duration_sec": 30, "format": "mp3"},
            asset_id="cr-003",
        )
        deal = _make_deal("DJ-3333", "audio", duration_sec=30)

        result = matcher.match_creatives_to_deals([asset], [deal])

        assert len(result.matches) == 1

    def test_one_creative_matches_multiple_deals(self, matcher):
        """A single creative can match multiple deals."""
        asset = _make_asset(
            AssetType.DISPLAY,
            {"width": 300, "height": 250},
            asset_id="cr-004",
        )
        deal_a = _make_deal("DJ-4001", "display", width=300, height=250)
        deal_b = _make_deal("DJ-4002", "display", width=300, height=250)

        result = matcher.match_creatives_to_deals([asset], [deal_a, deal_b])

        assert len(result.matches) == 2

    def test_multiple_creatives_match_same_deal(self, matcher):
        """Multiple creatives can match the same deal."""
        asset_a = _make_asset(
            AssetType.DISPLAY,
            {"width": 300, "height": 250},
            asset_id="cr-005a",
            asset_name="Banner A",
        )
        asset_b = _make_asset(
            AssetType.DISPLAY,
            {"width": 300, "height": 250},
            asset_id="cr-005b",
            asset_name="Banner B",
        )
        deal = _make_deal("DJ-5000", "display", width=300, height=250)

        result = matcher.match_creatives_to_deals([asset_a, asset_b], [deal])

        assert len(result.matches) == 2


# -----------------------------------------------------------------------
# Mismatch Tests
# -----------------------------------------------------------------------


class TestMismatches:
    """Tests for mismatch detection and reporting."""

    def test_media_type_mismatch(self, matcher):
        """Display creative does not match a video deal."""
        asset = _make_asset(
            AssetType.DISPLAY,
            {"width": 300, "height": 250},
            asset_id="cr-010",
        )
        deal = _make_deal("DJ-6666", "video", duration_sec=30)

        result = matcher.match_creatives_to_deals([asset], [deal])

        assert len(result.matches) == 0
        assert len(result.mismatches) > 0

    def test_display_size_mismatch(self, matcher):
        """Display creative with wrong dimensions is a mismatch."""
        asset = _make_asset(
            AssetType.DISPLAY,
            {"width": 728, "height": 90},
            asset_id="cr-011",
        )
        deal = _make_deal("DJ-7777", "display", width=300, height=250)

        result = matcher.match_creatives_to_deals([asset], [deal])

        assert len(result.matches) == 0
        assert len(result.mismatches) > 0

    def test_video_duration_mismatch(self, matcher):
        """Video creative with wrong duration flags a mismatch."""
        asset = _make_asset(
            AssetType.VIDEO,
            {"vast_version": "4.2", "duration_sec": 15, "format": "mp4"},
            asset_id="cr-012",
        )
        deal = _make_deal("DJ-8888", "video", duration_sec=30)

        result = matcher.match_creatives_to_deals([asset], [deal])

        assert len(result.matches) == 0
        assert len(result.mismatches) > 0
        # Mismatch should explain the issue
        mismatch = result.mismatches[0]
        assert "DJ-8888" in mismatch["message"]
        assert "30" in mismatch["message"] or "duration" in mismatch["message"].lower()

    def test_deal_with_no_matching_creatives(self, matcher):
        """A deal with no matching creatives appears in mismatches."""
        deal = _make_deal("DJ-9999", "video", duration_sec=30)

        result = matcher.match_creatives_to_deals([], [deal])

        assert len(result.matches) == 0
        assert len(result.mismatches) > 0
        assert any("DJ-9999" in m["message"] for m in result.mismatches)

    def test_mismatch_message_descriptive(self, matcher):
        """Mismatch messages are descriptive per the spec."""
        asset = _make_asset(
            AssetType.VIDEO,
            {"vast_version": "4.2", "duration_sec": 15, "format": "mp4"},
            asset_id="cr-013",
            asset_name="Short Spot",
        )
        deal = _make_deal("DJ-4567", "video", duration_sec=30)

        result = matcher.match_creatives_to_deals([asset], [deal])

        # Per spec: "Deal DJ-4567 requires 30s CTV but only 15s available"
        assert len(result.mismatches) > 0
        msg = result.mismatches[0]["message"]
        assert "DJ-4567" in msg


# -----------------------------------------------------------------------
# VAST Version Matching Tests
# -----------------------------------------------------------------------


class TestVastVersionMatching:
    """Tests for VAST version compatibility checking."""

    def test_vast_version_match(self, matcher):
        """Deal requiring specific VAST version matches creative with that version."""
        asset = _make_asset(
            AssetType.VIDEO,
            {"vast_version": "4.2", "duration_sec": 30, "format": "mp4"},
            asset_id="cr-020",
        )
        deal = _make_deal("DJ-V001", "video", duration_sec=30, vast_version="4.2")

        result = matcher.match_creatives_to_deals([asset], [deal])

        assert len(result.matches) == 1

    def test_vast_version_mismatch(self, matcher):
        """Deal requiring VAST 4.2 does not match creative with VAST 3.0."""
        asset = _make_asset(
            AssetType.VIDEO,
            {"vast_version": "3.0", "duration_sec": 30, "format": "mp4"},
            asset_id="cr-021",
        )
        deal = _make_deal("DJ-V002", "video", duration_sec=30, vast_version="4.2")

        result = matcher.match_creatives_to_deals([asset], [deal])

        assert len(result.matches) == 0
        assert len(result.mismatches) > 0


# -----------------------------------------------------------------------
# Filtering Tests
# -----------------------------------------------------------------------


class TestFiltering:
    """Tests for filtering behavior."""

    def test_only_valid_creatives_are_matched(self, matcher):
        """Creatives with PENDING or INVALID status are not matched."""
        valid_asset = _make_asset(
            AssetType.DISPLAY,
            {"width": 300, "height": 250},
            asset_id="cr-valid",
            validation_status=ValidationStatus.VALID,
        )
        pending_asset = _make_asset(
            AssetType.DISPLAY,
            {"width": 300, "height": 250},
            asset_id="cr-pending",
            validation_status=ValidationStatus.PENDING,
        )
        invalid_asset = _make_asset(
            AssetType.DISPLAY,
            {"width": 300, "height": 250},
            asset_id="cr-invalid",
            validation_status=ValidationStatus.INVALID,
        )

        deal = _make_deal("DJ-F001", "display", width=300, height=250)

        result = matcher.match_creatives_to_deals(
            [valid_asset, pending_asset, invalid_asset], [deal]
        )

        assert len(result.matches) == 1
        assert result.matches[0]["asset_id"] == "cr-valid"

    def test_empty_assets_produces_mismatches_for_all_deals(self, matcher):
        """Empty asset list produces mismatches for every deal."""
        deals = [
            _make_deal("DJ-E001", "display", width=300, height=250),
            _make_deal("DJ-E002", "video", duration_sec=30),
        ]

        result = matcher.match_creatives_to_deals([], deals)

        assert len(result.matches) == 0
        assert len(result.mismatches) == 2

    def test_empty_deals_produces_no_matches(self, matcher):
        """Empty deal list produces no matches and no mismatches."""
        asset = _make_asset(AssetType.DISPLAY, {"width": 300, "height": 250})

        result = matcher.match_creatives_to_deals([asset], [])

        assert len(result.matches) == 0
        assert len(result.mismatches) == 0


# -----------------------------------------------------------------------
# MatchResult Structure Tests
# -----------------------------------------------------------------------


class TestMatchResult:
    """Tests for the MatchResult data structure."""

    def test_match_result_has_matches_and_mismatches(self, matcher):
        """MatchResult has both matches and mismatches lists."""
        asset = _make_asset(
            AssetType.DISPLAY,
            {"width": 300, "height": 250},
            asset_id="cr-030",
        )
        deal_match = _make_deal("DJ-M001", "display", width=300, height=250)
        deal_no_match = _make_deal("DJ-M002", "video", duration_sec=30)

        result = matcher.match_creatives_to_deals(
            [asset], [deal_match, deal_no_match]
        )

        assert hasattr(result, "matches")
        assert hasattr(result, "mismatches")
        assert len(result.matches) == 1
        assert len(result.mismatches) == 1

    def test_match_entry_structure(self, matcher):
        """Each match entry has deal_id and asset_id."""
        asset = _make_asset(
            AssetType.DISPLAY,
            {"width": 300, "height": 250},
            asset_id="cr-031",
        )
        deal = _make_deal("DJ-S001", "display", width=300, height=250)

        result = matcher.match_creatives_to_deals([asset], [deal])

        match = result.matches[0]
        assert "deal_id" in match
        assert "asset_id" in match
        assert match["deal_id"] == "DJ-S001"
        assert match["asset_id"] == "cr-031"

    def test_mismatch_entry_structure(self, matcher):
        """Each mismatch entry has deal_id and message."""
        deal = _make_deal("DJ-S002", "video", duration_sec=30)

        result = matcher.match_creatives_to_deals([], [deal])

        mismatch = result.mismatches[0]
        assert "deal_id" in mismatch
        assert "message" in mismatch
        assert mismatch["deal_id"] == "DJ-S002"


# -----------------------------------------------------------------------
# Deal With No Requirements (Flexible Matching)
# -----------------------------------------------------------------------


class TestFlexibleMatching:
    """Tests for deals without strict creative requirements."""

    def test_deal_without_size_matches_any_display(self, matcher):
        """A display deal without size requirements matches any display creative."""
        asset = _make_asset(
            AssetType.DISPLAY,
            {"width": 300, "height": 250},
            asset_id="cr-040",
        )
        # Deal has no width/height in requirements
        deal = _make_deal("DJ-FLEX1", "display")

        result = matcher.match_creatives_to_deals([asset], [deal])

        assert len(result.matches) == 1

    def test_deal_without_duration_matches_any_video(self, matcher):
        """A video deal without duration matches any video creative."""
        asset = _make_asset(
            AssetType.VIDEO,
            {"vast_version": "4.2", "duration_sec": 15, "format": "mp4"},
            asset_id="cr-041",
        )
        deal = _make_deal("DJ-FLEX2", "video")

        result = matcher.match_creatives_to_deals([asset], [deal])

        assert len(result.matches) == 1
