# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for CreativeValidator — IAB spec validation of creative assets.

TDD RED phase: these tests are written before the implementation.

bead: buyer-3aa
"""

import pytest

from ad_buyer.models.creative_asset import AssetType, CreativeAsset, ValidationStatus
from ad_buyer.tools.creative.validator import CreativeValidator


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------


@pytest.fixture
def validator():
    """Create a CreativeValidator instance."""
    return CreativeValidator()


def _make_asset(
    asset_type: AssetType,
    format_spec: dict,
    **kwargs,
) -> CreativeAsset:
    """Helper to construct a CreativeAsset with sensible defaults."""
    defaults = {
        "campaign_id": "camp-test",
        "asset_name": "Test Asset",
        "source_url": "https://example.com/creative.bin",
    }
    defaults.update(kwargs)
    return CreativeAsset(
        asset_type=asset_type,
        format_spec=format_spec,
        **defaults,
    )


# -----------------------------------------------------------------------
# Display Validation Tests
# -----------------------------------------------------------------------


class TestValidateDisplay:
    """Tests for validate_display — IAB standard display sizes."""

    def test_valid_300x250(self, validator):
        """300x250 medium rectangle is a standard IAB size."""
        asset = _make_asset(AssetType.DISPLAY, {"width": 300, "height": 250})
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.VALID
        assert result.validation_errors == []

    def test_valid_728x90(self, validator):
        """728x90 leaderboard is a standard IAB size."""
        asset = _make_asset(AssetType.DISPLAY, {"width": 728, "height": 90})
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.VALID

    def test_valid_160x600(self, validator):
        """160x600 wide skyscraper is a standard IAB size."""
        asset = _make_asset(AssetType.DISPLAY, {"width": 160, "height": 600})
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.VALID

    def test_valid_320x50(self, validator):
        """320x50 mobile leaderboard is a standard IAB size."""
        asset = _make_asset(AssetType.DISPLAY, {"width": 320, "height": 50})
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.VALID

    def test_valid_970x250(self, validator):
        """970x250 billboard is a standard IAB size."""
        asset = _make_asset(AssetType.DISPLAY, {"width": 970, "height": 250})
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.VALID

    def test_valid_300x600(self, validator):
        """300x600 half-page is a standard IAB size."""
        asset = _make_asset(AssetType.DISPLAY, {"width": 300, "height": 600})
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.VALID

    def test_invalid_nonstandard_size(self, validator):
        """Non-standard dimensions produce INVALID with error message."""
        asset = _make_asset(AssetType.DISPLAY, {"width": 123, "height": 456})
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.INVALID
        assert len(result.validation_errors) > 0
        assert any("123x456" in e for e in result.validation_errors)

    def test_missing_width(self, validator):
        """Missing width field produces INVALID."""
        asset = _make_asset(AssetType.DISPLAY, {"height": 250})
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.INVALID
        assert any("width" in e.lower() for e in result.validation_errors)

    def test_missing_height(self, validator):
        """Missing height field produces INVALID."""
        asset = _make_asset(AssetType.DISPLAY, {"width": 300})
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.INVALID
        assert any("height" in e.lower() for e in result.validation_errors)

    def test_missing_both_dimensions(self, validator):
        """Missing both width and height produces INVALID."""
        asset = _make_asset(AssetType.DISPLAY, {})
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.INVALID

    def test_negative_width(self, validator):
        """Negative width produces INVALID."""
        asset = _make_asset(AssetType.DISPLAY, {"width": -300, "height": 250})
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.INVALID

    def test_zero_width(self, validator):
        """Zero width produces INVALID."""
        asset = _make_asset(AssetType.DISPLAY, {"width": 0, "height": 250})
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.INVALID


# -----------------------------------------------------------------------
# Video Validation Tests
# -----------------------------------------------------------------------


class TestValidateVideo:
    """Tests for validate_video — VAST version and duration checks."""

    def test_valid_vast_42_30s(self, validator):
        """VAST 4.2 with 30s duration is valid."""
        asset = _make_asset(
            AssetType.VIDEO,
            {"vast_version": "4.2", "duration_sec": 30, "format": "mp4"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.VALID
        assert result.validation_errors == []

    def test_valid_vast_41(self, validator):
        """VAST 4.1 is an accepted version."""
        asset = _make_asset(
            AssetType.VIDEO,
            {"vast_version": "4.1", "duration_sec": 15, "format": "mp4"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.VALID

    def test_valid_vast_30(self, validator):
        """VAST 3.0 is an accepted version."""
        asset = _make_asset(
            AssetType.VIDEO,
            {"vast_version": "3.0", "duration_sec": 60, "format": "mp4"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.VALID

    def test_valid_15s_duration(self, validator):
        """15-second video is a standard duration."""
        asset = _make_asset(
            AssetType.VIDEO,
            {"vast_version": "4.2", "duration_sec": 15, "format": "mp4"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.VALID

    def test_valid_6s_bumper(self, validator):
        """6-second bumper is a standard duration."""
        asset = _make_asset(
            AssetType.VIDEO,
            {"vast_version": "4.2", "duration_sec": 6, "format": "mp4"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.VALID

    def test_invalid_vast_version(self, validator):
        """Unsupported VAST version produces INVALID."""
        asset = _make_asset(
            AssetType.VIDEO,
            {"vast_version": "1.0", "duration_sec": 30, "format": "mp4"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.INVALID
        assert any("vast" in e.lower() for e in result.validation_errors)

    def test_missing_vast_version(self, validator):
        """Missing vast_version field produces INVALID."""
        asset = _make_asset(
            AssetType.VIDEO,
            {"duration_sec": 30, "format": "mp4"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.INVALID
        assert any("vast_version" in e.lower() for e in result.validation_errors)

    def test_missing_duration(self, validator):
        """Missing duration_sec field produces INVALID."""
        asset = _make_asset(
            AssetType.VIDEO,
            {"vast_version": "4.2", "format": "mp4"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.INVALID
        assert any("duration" in e.lower() for e in result.validation_errors)

    def test_zero_duration(self, validator):
        """Zero duration produces INVALID."""
        asset = _make_asset(
            AssetType.VIDEO,
            {"vast_version": "4.2", "duration_sec": 0, "format": "mp4"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.INVALID

    def test_negative_duration(self, validator):
        """Negative duration produces INVALID."""
        asset = _make_asset(
            AssetType.VIDEO,
            {"vast_version": "4.2", "duration_sec": -15, "format": "mp4"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.INVALID

    def test_nonstandard_duration_valid(self, validator):
        """Non-standard durations (e.g. 20s) are valid if positive."""
        asset = _make_asset(
            AssetType.VIDEO,
            {"vast_version": "4.2", "duration_sec": 20, "format": "mp4"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.VALID


# -----------------------------------------------------------------------
# Audio Validation Tests
# -----------------------------------------------------------------------


class TestValidateAudio:
    """Tests for validate_audio — DAAST compliance and duration."""

    def test_valid_daast_10_15s(self, validator):
        """DAAST 1.0 with 15s duration is valid."""
        asset = _make_asset(
            AssetType.AUDIO,
            {"daast_version": "1.0", "duration_sec": 15, "format": "mp3"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.VALID
        assert result.validation_errors == []

    def test_valid_30s_audio(self, validator):
        """30-second audio spot is valid."""
        asset = _make_asset(
            AssetType.AUDIO,
            {"daast_version": "1.0", "duration_sec": 30, "format": "mp3"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.VALID

    def test_valid_60s_audio(self, validator):
        """60-second audio spot is valid."""
        asset = _make_asset(
            AssetType.AUDIO,
            {"daast_version": "1.0", "duration_sec": 60, "format": "mp3"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.VALID

    def test_invalid_daast_version(self, validator):
        """Unsupported DAAST version produces INVALID."""
        asset = _make_asset(
            AssetType.AUDIO,
            {"daast_version": "0.5", "duration_sec": 30, "format": "mp3"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.INVALID
        assert any("daast" in e.lower() for e in result.validation_errors)

    def test_missing_daast_version(self, validator):
        """Missing daast_version field produces INVALID."""
        asset = _make_asset(
            AssetType.AUDIO,
            {"duration_sec": 30, "format": "mp3"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.INVALID
        assert any("daast_version" in e.lower() for e in result.validation_errors)

    def test_missing_duration(self, validator):
        """Missing duration_sec field produces INVALID."""
        asset = _make_asset(
            AssetType.AUDIO,
            {"daast_version": "1.0", "format": "mp3"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.INVALID
        assert any("duration" in e.lower() for e in result.validation_errors)

    def test_zero_duration(self, validator):
        """Zero duration produces INVALID."""
        asset = _make_asset(
            AssetType.AUDIO,
            {"daast_version": "1.0", "duration_sec": 0, "format": "mp3"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.INVALID


# -----------------------------------------------------------------------
# Interactive Validation Tests
# -----------------------------------------------------------------------


class TestValidateInteractive:
    """Tests for validate_interactive — SIMID compliance."""

    def test_valid_simid_11(self, validator):
        """SIMID 1.1 with required fields is valid."""
        asset = _make_asset(
            AssetType.INTERACTIVE,
            {"simid_version": "1.1", "container_type": "full"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.VALID
        assert result.validation_errors == []

    def test_valid_simid_10(self, validator):
        """SIMID 1.0 is accepted."""
        asset = _make_asset(
            AssetType.INTERACTIVE,
            {"simid_version": "1.0", "container_type": "non-linear"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.VALID

    def test_invalid_simid_version(self, validator):
        """Unsupported SIMID version produces INVALID."""
        asset = _make_asset(
            AssetType.INTERACTIVE,
            {"simid_version": "0.1", "container_type": "full"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.INVALID
        assert any("simid" in e.lower() for e in result.validation_errors)

    def test_missing_simid_version(self, validator):
        """Missing simid_version field produces INVALID."""
        asset = _make_asset(
            AssetType.INTERACTIVE,
            {"container_type": "full"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.INVALID
        assert any("simid_version" in e.lower() for e in result.validation_errors)


# -----------------------------------------------------------------------
# Dispatch Tests
# -----------------------------------------------------------------------


class TestValidateDispatch:
    """Tests for validate() method — dispatches to the right sub-validator."""

    def test_dispatches_display(self, validator):
        """validate() calls validate_display for DISPLAY assets."""
        asset = _make_asset(AssetType.DISPLAY, {"width": 300, "height": 250})
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.VALID

    def test_dispatches_video(self, validator):
        """validate() calls validate_video for VIDEO assets."""
        asset = _make_asset(
            AssetType.VIDEO,
            {"vast_version": "4.2", "duration_sec": 30, "format": "mp4"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.VALID

    def test_dispatches_audio(self, validator):
        """validate() calls validate_audio for AUDIO assets."""
        asset = _make_asset(
            AssetType.AUDIO,
            {"daast_version": "1.0", "duration_sec": 15, "format": "mp3"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.VALID

    def test_dispatches_interactive(self, validator):
        """validate() calls validate_interactive for INTERACTIVE assets."""
        asset = _make_asset(
            AssetType.INTERACTIVE,
            {"simid_version": "1.1", "container_type": "full"},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.VALID

    def test_native_passes_through(self, validator):
        """NATIVE assets pass validation (no strict spec checks yet)."""
        asset = _make_asset(
            AssetType.NATIVE,
            {"title_max_len": 50, "image_sizes": ["1200x627"]},
        )
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.VALID

    def test_validate_mutates_asset(self, validator):
        """validate() updates the asset's validation_status and validation_errors."""
        asset = _make_asset(AssetType.DISPLAY, {"width": 300, "height": 250})
        result = validator.validate(asset)
        # The returned asset should have updated status
        assert result.validation_status == ValidationStatus.VALID
        assert result.validation_errors == []

    def test_validate_invalid_mutates_errors(self, validator):
        """validate() on invalid asset fills in validation_errors."""
        asset = _make_asset(AssetType.DISPLAY, {"width": 999, "height": 999})
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.INVALID
        assert len(result.validation_errors) > 0


# -----------------------------------------------------------------------
# Multiple Errors Tests
# -----------------------------------------------------------------------


class TestMultipleErrors:
    """Tests that multiple errors are collected, not just the first."""

    def test_video_missing_both_fields(self, validator):
        """Missing both vast_version and duration_sec produces two errors."""
        asset = _make_asset(AssetType.VIDEO, {"format": "mp4"})
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.INVALID
        assert len(result.validation_errors) >= 2

    def test_audio_missing_both_fields(self, validator):
        """Missing both daast_version and duration_sec produces two errors."""
        asset = _make_asset(AssetType.AUDIO, {"format": "mp3"})
        result = validator.validate(asset)
        assert result.validation_status == ValidationStatus.INVALID
        assert len(result.validation_errors) >= 2
