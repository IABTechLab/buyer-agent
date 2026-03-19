# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Innovid ad server client (CTV creative serving).

Stub implementation that returns mock responses matching the expected
Innovid API shape. Designed with production-ready interfaces so this
can be swapped for real API calls when Innovid partnership is established.

Innovid is CTV-dominant and provides:
  - Creative upload (VAST tags and video files)
  - Campaign creation and line item binding
  - Household-level attribution and engagement data
  - Interactive overlay support

References:
  - Campaign Automation Strategic Plan, Section 7.5 (Innovid Integration)

bead: buyer-7m8
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from ...models.campaign import AdServerType
from .base import AdServerClient

logger = logging.getLogger(__name__)


class InnovidClient(AdServerClient):
    """Stub client for Innovid CTV ad server.

    All methods return mock data shaped like expected Innovid API
    responses. The mock data uses realistic field names and value
    ranges to support integration testing.

    When real Innovid API access is available, replace the method
    bodies with actual HTTP calls while keeping the same interface.
    """

    @property
    def ad_server_type(self) -> AdServerType:
        """Return INNOVID as the ad server type."""
        return AdServerType.INNOVID

    def create_campaign(self, campaign_data: dict[str, Any]) -> dict[str, Any]:
        """Create an Innovid campaign (stub).

        Simulates ``POST /campaigns`` on the Innovid API.

        Args:
            campaign_data: Campaign details including campaign_id, name,
                advertiser, budget, start_date, end_date.

        Returns:
            Mock response with external_campaign_id.
        """
        external_id = f"innov-camp-{uuid.uuid4().hex[:8]}"
        logger.info(
            "Innovid stub: created campaign %s for %s",
            external_id,
            campaign_data.get("campaign_id", "unknown"),
        )
        return {
            "external_campaign_id": external_id,
            "status": "created",
            "ad_server": "INNOVID",
            "campaign_name": campaign_data.get("name", ""),
            "advertiser": campaign_data.get("advertiser", ""),
        }

    def upload_creative(self, asset_data: dict[str, Any]) -> dict[str, Any]:
        """Upload a creative to Innovid (stub).

        Simulates ``POST /creatives`` on the Innovid API.
        Supports VAST tags and direct video file uploads.

        Args:
            asset_data: Creative details including asset_id, asset_name,
                asset_type, format_spec, source_url.

        Returns:
            Mock response with external_creative_id.
        """
        external_id = f"innov-cr-{uuid.uuid4().hex[:8]}"
        format_spec = asset_data.get("format_spec", {})
        logger.info(
            "Innovid stub: uploaded creative %s (VAST %s, %ds)",
            external_id,
            format_spec.get("vast_version", "unknown"),
            format_spec.get("duration_sec", 0),
        )
        return {
            "external_creative_id": external_id,
            "status": "uploaded",
            "ad_server": "INNOVID",
            "asset_name": asset_data.get("asset_name", ""),
            "vast_version": format_spec.get("vast_version"),
            "duration_sec": format_spec.get("duration_sec"),
            "creative_type": "vast_wrapper" if format_spec.get("vast_url") else "video",
        }

    def assign_creative_to_line(
        self,
        creative_id: str,
        line_id: str,
        rotation_config: dict[str, Any],
    ) -> dict[str, Any]:
        """Assign a creative to an Innovid line item (stub).

        Simulates ``POST /campaigns/{id}/lines`` on the Innovid API.

        Args:
            creative_id: Innovid creative ID.
            line_id: Innovid line item ID.
            rotation_config: Rotation configuration (type, weight).

        Returns:
            Mock response with assignment_id.
        """
        assignment_id = f"innov-assign-{uuid.uuid4().hex[:8]}"
        logger.info(
            "Innovid stub: assigned creative %s to line %s (assignment %s)",
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
        """Get delivery metrics from Innovid (stub).

        Simulates ``GET /campaigns/{id}/reporting`` on the Innovid API.
        Returns CTV-specific metrics including completion rate and
        household reach.

        Args:
            campaign_id: Innovid campaign ID.

        Returns:
            Mock delivery metrics dict.
        """
        logger.info("Innovid stub: fetching delivery data for %s", campaign_id)
        return {
            "campaign_id": campaign_id,
            "ad_server": "INNOVID",
            "impressions": 125000,
            "completions": 93750,
            "completion_rate": 0.75,
            "spend": 6250.00,
            "cpm": 50.00,
            "household_reach": 45000,
            "frequency": 2.78,
            "engagement_rate": 0.023,
        }

    def sync_status(self, campaign_id: str) -> dict[str, Any]:
        """Get current campaign status from Innovid (stub).

        Args:
            campaign_id: Innovid campaign ID.

        Returns:
            Mock status response.
        """
        logger.info("Innovid stub: syncing status for %s", campaign_id)
        return {
            "campaign_id": campaign_id,
            "ad_server": "INNOVID",
            "status": "active",
            "last_sync": "2026-03-19T12:00:00Z",
        }
