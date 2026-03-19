# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Flashtalking ad server client (display creative serving).

Stub implementation that returns mock responses matching the expected
Flashtalking API shape. Designed with production-ready interfaces so
this can be swapped for real API calls when Flashtalking partnership
is established.

Flashtalking is display-dominant and provides:
  - Creative upload (HTML5, image, rich media)
  - Campaign creation and placement binding
  - Dynamic Creative Optimization (DCO) feed management
  - Cross-device tracking and viewability reporting

References:
  - Campaign Automation Strategic Plan, Section 7.5 (Flashtalking Integration)

bead: buyer-7m8
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from ...models.campaign import AdServerType
from .base import AdServerClient

logger = logging.getLogger(__name__)


class FlashtalkingClient(AdServerClient):
    """Stub client for Flashtalking display ad server.

    All methods return mock data shaped like expected Flashtalking API
    responses. The mock data uses realistic field names and value ranges
    to support integration testing.

    When real Flashtalking API access is available, replace the method
    bodies with actual HTTP calls while keeping the same interface.
    """

    @property
    def ad_server_type(self) -> AdServerType:
        """Return FLASHTALKING as the ad server type."""
        return AdServerType.FLASHTALKING

    def create_campaign(self, campaign_data: dict[str, Any]) -> dict[str, Any]:
        """Create a Flashtalking campaign (stub).

        Simulates ``POST /campaigns`` on the Flashtalking API.

        Args:
            campaign_data: Campaign details including campaign_id, name,
                advertiser, budget, start_date, end_date.

        Returns:
            Mock response with external_campaign_id.
        """
        external_id = f"ft-camp-{uuid.uuid4().hex[:8]}"
        logger.info(
            "Flashtalking stub: created campaign %s for %s",
            external_id,
            campaign_data.get("campaign_id", "unknown"),
        )
        return {
            "external_campaign_id": external_id,
            "status": "created",
            "ad_server": "FLASHTALKING",
            "campaign_name": campaign_data.get("name", ""),
            "advertiser": campaign_data.get("advertiser", ""),
        }

    def upload_creative(self, asset_data: dict[str, Any]) -> dict[str, Any]:
        """Upload a creative to Flashtalking (stub).

        Simulates ``POST /ads`` on the Flashtalking API.
        Supports HTML5, image, rich media, and DCO template uploads.

        Args:
            asset_data: Creative details including asset_id, asset_name,
                asset_type, format_spec, source_url.

        Returns:
            Mock response with external_creative_id.
        """
        external_id = f"ft-cr-{uuid.uuid4().hex[:8]}"
        format_spec = asset_data.get("format_spec", {})
        logger.info(
            "Flashtalking stub: uploaded creative %s (%sx%s %s)",
            external_id,
            format_spec.get("width", "?"),
            format_spec.get("height", "?"),
            format_spec.get("format", "unknown"),
        )
        return {
            "external_creative_id": external_id,
            "status": "uploaded",
            "ad_server": "FLASHTALKING",
            "asset_name": asset_data.get("asset_name", ""),
            "width": format_spec.get("width"),
            "height": format_spec.get("height"),
            "format": format_spec.get("format"),
            "dco_enabled": format_spec.get("dco_enabled", False),
        }

    def assign_creative_to_line(
        self,
        creative_id: str,
        line_id: str,
        rotation_config: dict[str, Any],
    ) -> dict[str, Any]:
        """Assign a creative to a Flashtalking placement (stub).

        Simulates ``POST /campaigns/{id}/placements`` on the Flashtalking API.

        Args:
            creative_id: Flashtalking creative (ad) ID.
            line_id: Flashtalking placement ID.
            rotation_config: Rotation configuration (type, weight).

        Returns:
            Mock response with assignment_id.
        """
        assignment_id = f"ft-assign-{uuid.uuid4().hex[:8]}"
        logger.info(
            "Flashtalking stub: assigned creative %s to placement %s (assignment %s)",
            creative_id,
            line_id,
            assignment_id,
        )
        return {
            "assignment_id": assignment_id,
            "status": "assigned",
            "creative_id": creative_id,
            "line_id": line_id,
            "rotation_type": rotation_config.get("rotation_type", "even"),
            "weight": rotation_config.get("weight", 100),
        }

    def get_delivery_data(self, campaign_id: str) -> dict[str, Any]:
        """Get delivery metrics from Flashtalking (stub).

        Simulates ``GET /campaigns/{id}/reports`` on the Flashtalking API.
        Returns display-specific metrics including click-through rate
        and viewability.

        Args:
            campaign_id: Flashtalking campaign ID.

        Returns:
            Mock delivery metrics dict.
        """
        logger.info(
            "Flashtalking stub: fetching delivery data for %s", campaign_id
        )
        return {
            "campaign_id": campaign_id,
            "ad_server": "FLASHTALKING",
            "impressions": 250000,
            "clicks": 1250,
            "ctr": 0.005,
            "spend": 2500.00,
            "cpm": 10.00,
            "viewability_rate": 0.68,
            "cross_device_reach": 180000,
        }

    def sync_status(self, campaign_id: str) -> dict[str, Any]:
        """Get current campaign status from Flashtalking (stub).

        Args:
            campaign_id: Flashtalking campaign ID.

        Returns:
            Mock status response.
        """
        logger.info(
            "Flashtalking stub: syncing status for %s", campaign_id
        )
        return {
            "campaign_id": campaign_id,
            "ad_server": "FLASHTALKING",
            "status": "active",
            "last_sync": "2026-03-19T12:00:00Z",
        }
