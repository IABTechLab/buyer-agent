# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""CrewAI BaseTool wrapper for creative management operations.

Exposes creative validation and matching through a single tool interface
that agents can use. Actions: validate, match, list_mismatches.

References:
  - Campaign Automation Strategic Plan, Section 7.4

bead: buyer-3aa
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...models.creative_asset import AssetType, CreativeAsset, ValidationStatus
from .matcher import CreativeMatcher
from .validator import CreativeValidator

logger = logging.getLogger(__name__)


class CreativeManagementInput(BaseModel):
    """Input schema for the creative management tool."""

    action: str = Field(
        description=(
            "Action to perform: 'validate' (validate creative assets against "
            "IAB specs), 'match' (match creatives to deals), or "
            "'list_mismatches' (show deals without matching creatives)."
        ),
    )
    assets: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description=(
            "List of creative asset dicts. Each should have: asset_type, "
            "format_spec, campaign_id, asset_name, source_url. For match/list_mismatches, "
            "also include asset_id and validation_status."
        ),
    )
    deals: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description=(
            "List of deal dicts. Each should have: seller_deal_id, deal_name, "
            "media_type, creative_requirements (dict with format-specific fields)."
        ),
    )


class CreativeManagementTool(BaseTool):
    """Manage creative assets — validate against IAB specs and match to deals.

    This tool wraps CreativeValidator and CreativeMatcher for use by
    CrewAI agents in the campaign automation hierarchy.

    Actions:
        validate: Validate creative assets against IAB specifications.
        match: Match validated creatives to booked deals.
        list_mismatches: Show deals that have no matching creative.
    """

    name: str = "manage_creatives"
    description: str = """Manage creative assets for campaigns. Supports three actions:

- 'validate': Validate creative assets against IAB specs (display sizes, VAST for video, DAAST for audio, SIMID for interactive).
- 'match': Match validated creatives to booked deals and show assignments.
- 'list_mismatches': Show deals that have no matching creative asset.

Args:
    action: 'validate', 'match', or 'list_mismatches'
    assets: List of creative asset dicts
    deals: List of deal dicts (for match/list_mismatches)

Returns:
    Formatted report of validation results, match assignments, or mismatches."""

    args_schema: type[BaseModel] = CreativeManagementInput

    _validator: CreativeValidator = CreativeValidator()
    _matcher: CreativeMatcher = CreativeMatcher()

    def _run(
        self,
        action: str = "",
        assets: Optional[list[dict[str, Any]]] = None,
        deals: Optional[list[dict[str, Any]]] = None,
    ) -> str:
        """Execute the requested creative management action.

        Args:
            action: One of 'validate', 'match', 'list_mismatches'.
            assets: List of creative asset dicts.
            deals: List of deal dicts.

        Returns:
            Formatted string report.
        """
        if action == "validate":
            return self._handle_validate(assets or [])
        elif action == "match":
            return self._handle_match(assets or [], deals or [])
        elif action == "list_mismatches":
            return self._handle_list_mismatches(assets or [], deals or [])
        else:
            return f"Error: Unknown action '{action}'. Use 'validate', 'match', or 'list_mismatches'."

    def _handle_validate(self, asset_dicts: list[dict[str, Any]]) -> str:
        """Validate a list of creative assets.

        Args:
            asset_dicts: List of asset dicts to validate.

        Returns:
            Formatted validation report.
        """
        if not asset_dicts:
            return "No assets to validate (0 assets provided)."

        results: list[str] = []
        valid_count = 0
        invalid_count = 0

        for ad in asset_dicts:
            asset = self._dict_to_asset(ad)
            self._validator.validate(asset)

            if asset.validation_status == ValidationStatus.VALID:
                valid_count += 1
                results.append(
                    f"  VALID: {asset.asset_name} ({asset.asset_type.value})"
                )
            else:
                invalid_count += 1
                errors_str = "; ".join(asset.validation_errors)
                results.append(
                    f"  INVALID: {asset.asset_name} ({asset.asset_type.value}) — {errors_str}"
                )

        header = (
            f"Validation Results: {valid_count} valid, {invalid_count} invalid "
            f"out of {len(asset_dicts)} assets.\n"
        )
        return header + "\n".join(results)

    def _handle_match(
        self,
        asset_dicts: list[dict[str, Any]],
        deals: list[dict[str, Any]],
    ) -> str:
        """Match creatives to deals and report assignments.

        Args:
            asset_dicts: List of asset dicts.
            deals: List of deal dicts.

        Returns:
            Formatted match report.
        """
        assets = [self._dict_to_asset(ad) for ad in asset_dicts]
        result = self._matcher.match_creatives_to_deals(assets, deals)

        lines: list[str] = []

        if result.matches:
            lines.append(f"Matches ({len(result.matches)}):")
            for m in result.matches:
                lines.append(
                    f"  {m['asset_name']} ({m['asset_id']}) -> "
                    f"{m['deal_name']} ({m['deal_id']})"
                )

        if result.mismatches:
            lines.append(f"\nMismatches ({len(result.mismatches)}):")
            for mm in result.mismatches:
                lines.append(f"  {mm['message']}")

        if not result.matches and not result.mismatches:
            lines.append("No matches or mismatches (no deals provided).")

        return "\n".join(lines)

    def _handle_list_mismatches(
        self,
        asset_dicts: list[dict[str, Any]],
        deals: list[dict[str, Any]],
    ) -> str:
        """List only the mismatches (deals without matching creatives).

        Args:
            asset_dicts: List of asset dicts.
            deals: List of deal dicts.

        Returns:
            Formatted mismatches report.
        """
        assets = [self._dict_to_asset(ad) for ad in asset_dicts]
        result = self._matcher.match_creatives_to_deals(assets, deals)

        if not result.mismatches:
            return "No mismatches — all deals have matching creatives."

        lines = [f"Mismatches ({len(result.mismatches)}):"]
        for mm in result.mismatches:
            lines.append(f"  {mm['message']}")
        return "\n".join(lines)

    @staticmethod
    def _dict_to_asset(d: dict[str, Any]) -> CreativeAsset:
        """Convert a dict to a CreativeAsset, handling missing fields gracefully.

        Args:
            d: Asset dict with at least asset_type and format_spec.

        Returns:
            A CreativeAsset instance.
        """
        asset_type_raw = d.get("asset_type", "display")
        try:
            asset_type = AssetType(asset_type_raw)
        except ValueError:
            asset_type = AssetType.DISPLAY

        validation_status_raw = d.get("validation_status", "pending")
        try:
            validation_status = ValidationStatus(validation_status_raw)
        except ValueError:
            validation_status = ValidationStatus.PENDING

        return CreativeAsset(
            asset_id=d.get("asset_id", ""),
            campaign_id=d.get("campaign_id", ""),
            asset_name=d.get("asset_name", "Unnamed"),
            asset_type=asset_type,
            format_spec=d.get("format_spec", {}),
            source_url=d.get("source_url", ""),
            validation_status=validation_status,
            validation_errors=d.get("validation_errors", []),
        )
