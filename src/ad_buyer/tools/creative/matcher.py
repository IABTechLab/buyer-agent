# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Creative-to-deal matching for campaign automation.

Matches validated creative assets to booked deals based on media type,
dimensions (display), duration (video/audio), and VAST version
compatibility. Reports both successful matches and mismatches with
descriptive messages.

References:
  - Campaign Automation Strategic Plan, Section 7.4 (Creative-Deal Matching)

bead: buyer-3aa
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ...models.creative_asset import AssetType, CreativeAsset, ValidationStatus

logger = logging.getLogger(__name__)

# Map deal media_type strings to AssetType enum values
_MEDIA_TYPE_TO_ASSET_TYPE: dict[str, AssetType] = {
    "display": AssetType.DISPLAY,
    "video": AssetType.VIDEO,
    "audio": AssetType.AUDIO,
    "interactive": AssetType.INTERACTIVE,
    "native": AssetType.NATIVE,
}


@dataclass
class MatchResult:
    """Result of creative-to-deal matching.

    Attributes:
        matches: List of successful match dicts, each with:
            - deal_id: The deal's seller_deal_id
            - asset_id: The creative asset's ID
            - deal_name: Human-readable deal name
            - asset_name: Human-readable asset name
        mismatches: List of mismatch dicts, each with:
            - deal_id: The deal's seller_deal_id
            - message: Descriptive explanation of the mismatch
    """

    matches: list[dict[str, Any]] = field(default_factory=list)
    mismatches: list[dict[str, Any]] = field(default_factory=list)


class CreativeMatcher:
    """Matches validated creative assets to deals.

    Only assets with ``validation_status == VALID`` are considered for
    matching.  Deals are matched by media type first, then by
    format-specific requirements (size, duration, VAST version).
    """

    def match_creatives_to_deals(
        self,
        assets: list[CreativeAsset],
        deals: list[dict[str, Any]],
    ) -> MatchResult:
        """Match creative assets to deals and report mismatches.

        Args:
            assets: List of CreativeAsset instances (any validation status).
            deals: List of deal dicts as returned by DealStore. Each deal
                should have: seller_deal_id, deal_name, media_type,
                creative_requirements (dict).

        Returns:
            MatchResult with matches and mismatches.
        """
        result = MatchResult()

        # Filter to only validated assets
        valid_assets = [
            a for a in assets if a.validation_status == ValidationStatus.VALID
        ]

        for deal in deals:
            deal_id = deal.get("seller_deal_id", "unknown")
            deal_name = deal.get("deal_name", deal_id)
            deal_media = deal.get("media_type", "")
            requirements = deal.get("creative_requirements", {})

            # Find matching assets for this deal
            matching = []
            for asset in valid_assets:
                if self._asset_matches_deal(asset, deal_media, requirements):
                    matching.append(asset)

            if matching:
                for asset in matching:
                    result.matches.append({
                        "deal_id": deal_id,
                        "asset_id": asset.asset_id,
                        "deal_name": deal_name,
                        "asset_name": asset.asset_name,
                    })
            else:
                msg = self._build_mismatch_message(
                    deal_id, deal_name, deal_media, requirements, valid_assets
                )
                result.mismatches.append({
                    "deal_id": deal_id,
                    "message": msg,
                })

        return result

    def _asset_matches_deal(
        self,
        asset: CreativeAsset,
        deal_media: str,
        requirements: dict[str, Any],
    ) -> bool:
        """Check whether a single asset matches a deal's requirements.

        Args:
            asset: A validated creative asset.
            deal_media: The deal's media type string (e.g. "display", "video").
            requirements: The deal's creative_requirements dict.

        Returns:
            True if the asset is compatible with the deal.
        """
        # Media type must match
        expected_type = _MEDIA_TYPE_TO_ASSET_TYPE.get(deal_media)
        if expected_type is None or asset.asset_type != expected_type:
            return False

        # Check format-specific requirements
        if deal_media == "display":
            return self._display_matches(asset, requirements)
        elif deal_media == "video":
            return self._video_matches(asset, requirements)
        elif deal_media == "audio":
            return self._audio_matches(asset, requirements)
        else:
            # Interactive, native — media type match is sufficient
            return True

    def _display_matches(
        self, asset: CreativeAsset, requirements: dict[str, Any]
    ) -> bool:
        """Check display creative matches deal size requirements."""
        req_width = requirements.get("width")
        req_height = requirements.get("height")

        # If deal has no size requirements, any display creative matches
        if req_width is None and req_height is None:
            return True

        spec = asset.format_spec
        asset_width = spec.get("width")
        asset_height = spec.get("height")

        if req_width is not None and asset_width != req_width:
            return False
        if req_height is not None and asset_height != req_height:
            return False

        return True

    def _video_matches(
        self, asset: CreativeAsset, requirements: dict[str, Any]
    ) -> bool:
        """Check video creative matches deal duration and VAST requirements."""
        spec = asset.format_spec

        # Check duration
        req_duration = requirements.get("duration_sec")
        if req_duration is not None:
            asset_duration = spec.get("duration_sec")
            if asset_duration != req_duration:
                return False

        # Check VAST version
        req_vast = requirements.get("vast_version")
        if req_vast is not None:
            asset_vast = spec.get("vast_version")
            if asset_vast != req_vast:
                return False

        return True

    def _audio_matches(
        self, asset: CreativeAsset, requirements: dict[str, Any]
    ) -> bool:
        """Check audio creative matches deal duration requirements."""
        spec = asset.format_spec

        req_duration = requirements.get("duration_sec")
        if req_duration is not None:
            asset_duration = spec.get("duration_sec")
            if asset_duration != req_duration:
                return False

        return True

    def _build_mismatch_message(
        self,
        deal_id: str,
        deal_name: str,
        deal_media: str,
        requirements: dict[str, Any],
        valid_assets: list[CreativeAsset],
    ) -> str:
        """Build a descriptive mismatch message for a deal.

        Args:
            deal_id: The deal's seller_deal_id.
            deal_name: Human-readable deal name.
            deal_media: The deal's media type.
            requirements: The deal's creative_requirements.
            valid_assets: All valid assets available.

        Returns:
            A descriptive message explaining the mismatch.
        """
        expected_type = _MEDIA_TYPE_TO_ASSET_TYPE.get(deal_media)

        # Count how many assets match the media type
        same_type_assets = [
            a for a in valid_assets
            if a.asset_type == expected_type
        ] if expected_type else []

        if not valid_assets:
            return f"Deal {deal_id} ({deal_name}) has no matching creative — no validated creatives available."

        if not same_type_assets:
            available_types = sorted({a.asset_type.value for a in valid_assets})
            return (
                f"Deal {deal_id} ({deal_name}) requires {deal_media} creative "
                f"but only {', '.join(available_types)} creatives are available."
            )

        # Same media type exists but doesn't match specific requirements
        if deal_media == "video":
            req_duration = requirements.get("duration_sec")
            available_durations = sorted({
                a.format_spec.get("duration_sec")
                for a in same_type_assets
                if a.format_spec.get("duration_sec") is not None
            })
            if req_duration and available_durations:
                dur_str = ", ".join(f"{d}s" for d in available_durations)
                return (
                    f"Deal {deal_id} ({deal_name}) requires {req_duration}s video "
                    f"but available durations are: {dur_str}."
                )

        if deal_media == "display":
            req_w = requirements.get("width")
            req_h = requirements.get("height")
            available_sizes = sorted({
                (a.format_spec.get("width"), a.format_spec.get("height"))
                for a in same_type_assets
            })
            if req_w and req_h:
                sizes_str = ", ".join(f"{w}x{h}" for w, h in available_sizes)
                return (
                    f"Deal {deal_id} ({deal_name}) requires {req_w}x{req_h} display "
                    f"but available sizes are: {sizes_str}."
                )

        if deal_media == "audio":
            req_duration = requirements.get("duration_sec")
            available_durations = sorted({
                a.format_spec.get("duration_sec")
                for a in same_type_assets
                if a.format_spec.get("duration_sec") is not None
            })
            if req_duration and available_durations:
                dur_str = ", ".join(f"{d}s" for d in available_durations)
                return (
                    f"Deal {deal_id} ({deal_name}) requires {req_duration}s audio "
                    f"but available durations are: {dur_str}."
                )

        return (
            f"Deal {deal_id} ({deal_name}) has no matching creative "
            f"among {len(valid_assets)} validated assets."
        )
