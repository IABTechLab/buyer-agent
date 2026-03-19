# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""CreativeAsset data model for campaign creative management.

Defines the CreativeAsset dataclass and supporting enums (AssetType,
ValidationStatus) used by the creative asset CRUD layer. The model
maps to the ``creative_assets`` table in the SQLite schema.

Design notes:
  - Uses dataclasses (not Pydantic) to match DealStore's dict-based
    pattern.  The store serializes/deserializes JSON fields (format_spec,
    validation_errors) transparently.
  - format_spec is a free-form dict whose structure varies by asset_type
    (e.g., width/height for display, duration_sec/vast_version for video).
  - validation_status tracks IAB spec compliance checks.

References:
  - Campaign Automation Strategic Plan, Section 6.3
  - bead: ar-pw8u
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class AssetType(str, Enum):
    """Creative asset type (Section 6.3 creative_type)."""

    DISPLAY = "display"
    VIDEO = "video"
    AUDIO = "audio"
    INTERACTIVE = "interactive"
    NATIVE = "native"


class ValidationStatus(str, Enum):
    """Validation status for creative assets."""

    PENDING = "pending"
    VALID = "valid"
    INVALID = "invalid"


def _default_uuid() -> str:
    """Generate a new UUID4 string."""
    return str(uuid.uuid4())


def _now_utc() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


@dataclass
class CreativeAsset:
    """A creative asset associated with a campaign.

    Represents a display banner, video, audio clip, interactive unit,
    or native ad creative. Tracks format specifications, validation
    status, and source URL.

    Attributes:
        asset_id: Unique identifier (UUID). Auto-generated if not provided.
        campaign_id: ID of the campaign this asset belongs to.
        asset_name: Human-readable name for the creative.
        asset_type: Type of creative (display, video, audio, etc.).
        format_spec: Format-specific metadata dict (varies by asset_type).
        source_url: URL where the creative file is hosted.
        validation_status: IAB spec validation status.
        validation_errors: List of validation error/warning messages.
        created_at: Timestamp when the asset was created.
    """

    campaign_id: str
    asset_name: str
    asset_type: AssetType
    format_spec: dict[str, Any]
    source_url: str
    asset_id: str = field(default_factory=_default_uuid)
    validation_status: ValidationStatus = ValidationStatus.PENDING
    validation_errors: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=_now_utc)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict suitable for JSON encoding.

        Returns:
            Dict with all fields. Enums are converted to their string
            values; datetime is formatted as ISO 8601.
        """
        return {
            "asset_id": self.asset_id,
            "campaign_id": self.campaign_id,
            "asset_name": self.asset_name,
            "asset_type": self.asset_type.value if isinstance(self.asset_type, Enum) else self.asset_type,
            "format_spec": self.format_spec,
            "source_url": self.source_url,
            "validation_status": self.validation_status.value if isinstance(self.validation_status, Enum) else self.validation_status,
            "validation_errors": list(self.validation_errors),
            "created_at": self.created_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if isinstance(self.created_at, datetime) else self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CreativeAsset:
        """Reconstruct a CreativeAsset from a dict (e.g., from to_dict()).

        Args:
            data: Dict with CreativeAsset fields.

        Returns:
            A new CreativeAsset instance.
        """
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            # Parse ISO 8601 format
            created_at = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)

        return cls(
            asset_id=data["asset_id"],
            campaign_id=data["campaign_id"],
            asset_name=data["asset_name"],
            asset_type=AssetType(data["asset_type"]),
            format_spec=data["format_spec"],
            source_url=data["source_url"],
            validation_status=ValidationStatus(data.get("validation_status", "pending")),
            validation_errors=data.get("validation_errors", []),
            created_at=created_at or _now_utc(),
        )
