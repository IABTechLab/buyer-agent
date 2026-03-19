# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Creative asset validation against IAB specifications.

Validates creative assets for IAB compliance based on media type:
- Display: IAB Ad Unit Portfolio standard sizes
- Video: VAST version compliance and duration
- Audio: DAAST compliance and duration
- Interactive: SIMID compliance
- Native: pass-through (no strict spec checks yet)

References:
  - IAB Ad Unit Portfolio (display standard sizes)
  - VAST 4.2 / 4.1 / 4.0 / 3.0 (video)
  - DAAST 1.0 (audio)
  - SIMID 1.1 / 1.0 (interactive)
  - Campaign Automation Strategic Plan, Section 7.4

bead: buyer-3aa
"""

from __future__ import annotations

import logging
from typing import Any

from ...models.creative_asset import AssetType, CreativeAsset, ValidationStatus

logger = logging.getLogger(__name__)

# IAB standard display ad sizes (width, height)
# Reference: IAB Ad Unit Portfolio / IAB New Ad Portfolio
IAB_STANDARD_DISPLAY_SIZES: set[tuple[int, int]] = {
    # Desktop
    (300, 250),   # Medium Rectangle
    (728, 90),    # Leaderboard
    (160, 600),   # Wide Skyscraper
    (300, 600),   # Half Page
    (970, 250),   # Billboard
    (970, 90),    # Super Leaderboard
    (468, 60),    # Full Banner
    (120, 600),   # Skyscraper
    (250, 250),   # Square
    (336, 280),   # Large Rectangle
    # Mobile
    (320, 50),    # Mobile Leaderboard
    (320, 100),   # Large Mobile Banner
    (300, 50),    # Mobile Banner
    (320, 480),   # Mobile Interstitial
    (480, 320),   # Mobile Interstitial Landscape
    # Tablet
    (768, 1024),  # Tablet Portrait Interstitial
    (1024, 768),  # Tablet Landscape Interstitial
}

# Accepted VAST versions
ACCEPTED_VAST_VERSIONS: set[str] = {"2.0", "3.0", "4.0", "4.1", "4.2"}

# Accepted DAAST versions
ACCEPTED_DAAST_VERSIONS: set[str] = {"1.0"}

# Accepted SIMID versions
ACCEPTED_SIMID_VERSIONS: set[str] = {"1.0", "1.1"}


class CreativeValidator:
    """Validates creative assets against IAB specifications.

    Each media type has a dedicated validation method. The ``validate()``
    dispatcher routes to the correct one based on ``asset.asset_type``.
    Validation updates the asset's ``validation_status`` and
    ``validation_errors`` fields in place, and also returns the asset
    for convenience.
    """

    def validate(self, asset: CreativeAsset) -> CreativeAsset:
        """Validate a creative asset by dispatching to the right sub-validator.

        Args:
            asset: The creative asset to validate.

        Returns:
            The same asset with updated validation_status and validation_errors.
        """
        dispatch = {
            AssetType.DISPLAY: self.validate_display,
            AssetType.VIDEO: self.validate_video,
            AssetType.AUDIO: self.validate_audio,
            AssetType.INTERACTIVE: self.validate_interactive,
            AssetType.NATIVE: self.validate_native,
        }

        handler = dispatch.get(asset.asset_type)
        if handler is None:
            asset.validation_status = ValidationStatus.INVALID
            asset.validation_errors = [
                f"Unknown asset type: {asset.asset_type}"
            ]
            return asset

        return handler(asset)

    def validate_display(self, asset: CreativeAsset) -> CreativeAsset:
        """Validate a display creative against IAB standard sizes.

        Checks that width and height are present, positive, and match
        an IAB standard size from the Ad Unit Portfolio.

        Args:
            asset: A display creative asset.

        Returns:
            The asset with updated validation status.
        """
        errors: list[str] = []
        spec = asset.format_spec

        width = spec.get("width")
        height = spec.get("height")

        if width is None:
            errors.append("Missing required field: width")
        elif not isinstance(width, (int, float)) or width <= 0:
            errors.append(f"Invalid width: {width} (must be a positive number)")

        if height is None:
            errors.append("Missing required field: height")
        elif not isinstance(height, (int, float)) or height <= 0:
            errors.append(f"Invalid height: {height} (must be a positive number)")

        # If basic checks pass, check IAB standard sizes
        if not errors:
            size = (int(width), int(height))
            if size not in IAB_STANDARD_DISPLAY_SIZES:
                errors.append(
                    f"Non-standard IAB display size: {size[0]}x{size[1]}. "
                    f"Standard sizes include: 300x250, 728x90, 160x600, 320x50, etc."
                )

        return self._finalize(asset, errors)

    def validate_video(self, asset: CreativeAsset) -> CreativeAsset:
        """Validate a video creative for VAST compliance and duration.

        Checks that vast_version is present and supported, and that
        duration_sec is present and positive.

        Args:
            asset: A video creative asset.

        Returns:
            The asset with updated validation status.
        """
        errors: list[str] = []
        spec = asset.format_spec

        vast_version = spec.get("vast_version")
        duration_sec = spec.get("duration_sec")

        if vast_version is None:
            errors.append("Missing required field: vast_version")
        elif str(vast_version) not in ACCEPTED_VAST_VERSIONS:
            errors.append(
                f"Unsupported VAST version: {vast_version}. "
                f"Accepted versions: {', '.join(sorted(ACCEPTED_VAST_VERSIONS))}"
            )

        if duration_sec is None:
            errors.append("Missing required field: duration_sec")
        elif not isinstance(duration_sec, (int, float)) or duration_sec <= 0:
            errors.append(
                f"Invalid duration_sec: {duration_sec} (must be a positive number)"
            )

        return self._finalize(asset, errors)

    def validate_audio(self, asset: CreativeAsset) -> CreativeAsset:
        """Validate an audio creative for DAAST compliance and duration.

        Checks that daast_version is present and supported, and that
        duration_sec is present and positive.

        Args:
            asset: An audio creative asset.

        Returns:
            The asset with updated validation status.
        """
        errors: list[str] = []
        spec = asset.format_spec

        daast_version = spec.get("daast_version")
        duration_sec = spec.get("duration_sec")

        if daast_version is None:
            errors.append("Missing required field: daast_version")
        elif str(daast_version) not in ACCEPTED_DAAST_VERSIONS:
            errors.append(
                f"Unsupported DAAST version: {daast_version}. "
                f"Accepted versions: {', '.join(sorted(ACCEPTED_DAAST_VERSIONS))}"
            )

        if duration_sec is None:
            errors.append("Missing required field: duration_sec")
        elif not isinstance(duration_sec, (int, float)) or duration_sec <= 0:
            errors.append(
                f"Invalid duration_sec: {duration_sec} (must be a positive number)"
            )

        return self._finalize(asset, errors)

    def validate_interactive(self, asset: CreativeAsset) -> CreativeAsset:
        """Validate an interactive creative for SIMID compliance.

        Checks that simid_version is present and supported.

        Args:
            asset: An interactive creative asset.

        Returns:
            The asset with updated validation status.
        """
        errors: list[str] = []
        spec = asset.format_spec

        simid_version = spec.get("simid_version")

        if simid_version is None:
            errors.append("Missing required field: simid_version")
        elif str(simid_version) not in ACCEPTED_SIMID_VERSIONS:
            errors.append(
                f"Unsupported SIMID version: {simid_version}. "
                f"Accepted versions: {', '.join(sorted(ACCEPTED_SIMID_VERSIONS))}"
            )

        return self._finalize(asset, errors)

    def validate_native(self, asset: CreativeAsset) -> CreativeAsset:
        """Validate a native creative asset.

        Native ads currently pass through without strict spec checks.
        Future versions will validate against OpenRTB Native 1.2.

        Args:
            asset: A native creative asset.

        Returns:
            The asset marked as VALID.
        """
        return self._finalize(asset, [])

    @staticmethod
    def _finalize(
        asset: CreativeAsset, errors: list[str]
    ) -> CreativeAsset:
        """Set validation status and errors on the asset.

        Args:
            asset: The creative asset to update.
            errors: List of validation error messages (empty = valid).

        Returns:
            The updated asset.
        """
        if errors:
            asset.validation_status = ValidationStatus.INVALID
            asset.validation_errors = errors
        else:
            asset.validation_status = ValidationStatus.VALID
            asset.validation_errors = []
        return asset
