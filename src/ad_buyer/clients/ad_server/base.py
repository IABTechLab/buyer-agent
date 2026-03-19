# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Abstract base class for ad server integration clients.

Defines the interface that all ad server clients (Innovid, Flashtalking,
and any future integrations) must implement. Each method corresponds to
a core ad server operation: campaign management, creative upload,
creative-to-line-item assignment, delivery reporting, and status sync.

Design notes:
  - Methods return dicts rather than typed models to keep the interface
    flexible across different ad servers with varying response shapes.
  - Each return dict includes an ``ad_server`` field identifying the
    platform, enabling callers to handle platform-specific fields.
  - The ``ad_server_type`` property lets callers identify which platform
    a client instance represents without inspecting the class name.

References:
  - Campaign Automation Strategic Plan, Section 7.5

bead: buyer-7m8
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ...models.campaign import AdServerType


class AdServerClient(ABC):
    """Abstract base for ad server platform clients.

    Subclasses implement the five core operations for a specific ad server
    (Innovid, Flashtalking, etc.). The interface is intentionally dict-based
    to accommodate differences between ad server APIs while maintaining a
    consistent calling convention.

    Attributes:
        ad_server_type: The AdServerType enum value for this client.
    """

    @property
    @abstractmethod
    def ad_server_type(self) -> AdServerType:
        """Return the AdServerType enum for this client."""
        ...

    @abstractmethod
    def create_campaign(self, campaign_data: dict[str, Any]) -> dict[str, Any]:
        """Create a campaign on the ad server.

        Args:
            campaign_data: Dict containing campaign details. Expected keys:
                - campaign_id: Internal campaign identifier
                - name: Campaign display name
                - advertiser: Advertiser name
                - budget: Campaign budget (optional)
                - start_date: Flight start date (optional)
                - end_date: Flight end date (optional)

        Returns:
            Dict with at minimum:
                - external_campaign_id: The ad server's campaign ID
                - status: Creation status (e.g., "created")
                - ad_server: The ad server platform name
        """
        ...

    @abstractmethod
    def upload_creative(self, asset_data: dict[str, Any]) -> dict[str, Any]:
        """Upload a creative asset to the ad server.

        Args:
            asset_data: Dict containing creative details. Expected keys:
                - asset_id: Internal creative asset identifier
                - asset_name: Human-readable name
                - asset_type: Media type (video, display, etc.)
                - format_spec: Format-specific metadata dict
                - source_url: URL to the creative file

        Returns:
            Dict with at minimum:
                - external_creative_id: The ad server's creative ID
                - status: Upload status (e.g., "uploaded")
                - ad_server: The ad server platform name
        """
        ...

    @abstractmethod
    def assign_creative_to_line(
        self,
        creative_id: str,
        line_id: str,
        rotation_config: dict[str, Any],
    ) -> dict[str, Any]:
        """Assign a creative to a line item / placement on the ad server.

        Args:
            creative_id: The ad server's creative ID.
            line_id: The ad server's line item or placement ID.
            rotation_config: Rotation/weighting configuration. Expected keys:
                - rotation_type: Type of rotation (even, weighted, sequential)
                - weight: Weight for weighted rotation (0-100)

        Returns:
            Dict with at minimum:
                - assignment_id: The ad server's assignment ID
                - status: Assignment status (e.g., "assigned")
                - creative_id: Echo of the creative ID
                - line_id: Echo of the line ID
        """
        ...

    @abstractmethod
    def get_delivery_data(self, campaign_id: str) -> dict[str, Any]:
        """Retrieve delivery metrics from the ad server.

        Args:
            campaign_id: The ad server's campaign ID.

        Returns:
            Dict with delivery metrics. Common keys:
                - impressions: Total impressions served
                - spend: Total spend amount
                - ad_server: The ad server platform name
            Platform-specific keys vary (e.g., completions for CTV,
            clicks for display).
        """
        ...

    @abstractmethod
    def sync_status(self, campaign_id: str) -> dict[str, Any]:
        """Get the current status of a campaign on the ad server.

        Args:
            campaign_id: The ad server's campaign ID.

        Returns:
            Dict with at minimum:
                - status: Current campaign status
                - campaign_id: Echo of the campaign ID
                - ad_server: The ad server platform name
        """
        ...
