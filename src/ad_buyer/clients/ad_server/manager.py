# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Ad server manager for routing operations to the correct client.

Routes ad server operations to Innovid (CTV) or Flashtalking (display)
based on the ad server type associated with a campaign. Handles:
  - Client instance management and routing
  - Campaign creation with AdServerStore persistence
  - Creative upload workflow: upload -> assign -> track binding
  - Delivery data sync and status sync

References:
  - Campaign Automation Strategic Plan, Section 7.5

bead: buyer-7m8
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from ...models.campaign import (
    AdServerBinding,
    AdServerCampaign,
    AdServerCampaignStatus,
    AdServerDelivery,
    AdServerType,
    BindingServingStatus,
)
from ...storage.adserver_store import AdServerStore
from .base import AdServerClient
from .flashtalking import FlashtalkingClient
from .innovid import InnovidClient

logger = logging.getLogger(__name__)

# Map AdServerType to client class
_CLIENT_REGISTRY: dict[AdServerType, type[AdServerClient]] = {
    AdServerType.INNOVID: InnovidClient,
    AdServerType.FLASHTALKING: FlashtalkingClient,
}


class AdServerManager:
    """Routes ad server operations to the correct client.

    Manages client instances and coordinates between the ad server
    clients and the AdServerStore persistence layer. Provides
    high-level workflows for campaign creation, creative upload and
    assignment, delivery sync, and status sync.

    Args:
        store: AdServerStore instance for persisting integration records.
    """

    def __init__(self, store: AdServerStore) -> None:
        self._store = store
        self._clients: dict[AdServerType, AdServerClient] = {}

    def get_client(self, ad_server_type: AdServerType | str) -> AdServerClient:
        """Get or create a client instance for the given ad server type.

        Client instances are cached and reused for the same ad server type.

        Args:
            ad_server_type: The ad server type (enum or string).

        Returns:
            An AdServerClient instance for the specified platform.

        Raises:
            ValueError: If the ad server type is not supported.
        """
        # Normalize string to enum
        if isinstance(ad_server_type, str):
            try:
                ad_server_type = AdServerType(ad_server_type)
            except ValueError:
                raise ValueError(
                    f"Unsupported ad server type: {ad_server_type}. "
                    f"Supported: {', '.join(t.value for t in AdServerType)}"
                )

        if ad_server_type not in self._clients:
            client_cls = _CLIENT_REGISTRY.get(ad_server_type)
            if client_cls is None:
                raise ValueError(
                    f"Unsupported ad server type: {ad_server_type.value}. "
                    f"Supported: {', '.join(t.value for t in AdServerType)}"
                )
            self._clients[ad_server_type] = client_cls()

        return self._clients[ad_server_type]

    def create_ad_server_campaign(
        self,
        campaign_id: str,
        ad_server_type: AdServerType,
        campaign_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a campaign on the ad server and persist the record.

        Calls the ad server client to create the campaign, then saves
        an AdServerCampaign record in the store.

        Args:
            campaign_id: Internal campaign identifier.
            ad_server_type: Which ad server to use.
            campaign_data: Campaign details to send to the ad server.

        Returns:
            Dict with record_id, external_campaign_id, and ad_server.
        """
        client = self.get_client(ad_server_type)

        # Create campaign on ad server
        result = client.create_campaign(campaign_data)
        external_id = result["external_campaign_id"]

        # Create and persist the integration record
        record = AdServerCampaign(
            id=str(uuid.uuid4()),
            campaign_id=campaign_id,
            ad_server=ad_server_type,
            ad_server_campaign_id=external_id,
            status=AdServerCampaignStatus.ACTIVE,
        )
        self._store.save_ad_server_campaign(record)

        logger.info(
            "Created %s campaign record %s (external: %s) for campaign %s",
            ad_server_type.value,
            record.id,
            external_id,
            campaign_id,
        )

        return {
            "record_id": record.id,
            "external_campaign_id": external_id,
            "ad_server": ad_server_type.value,
            "status": "created",
        }

    def upload_and_assign_creative(
        self,
        record_id: str,
        deal_id: str,
        asset_data: dict[str, Any],
        rotation_config: dict[str, Any],
    ) -> dict[str, Any]:
        """Upload a creative and assign it to a line item, creating a binding.

        Workflow:
          1. Look up the AdServerCampaign record
          2. Upload the creative to the ad server
          3. Assign the creative to a line item (using the external campaign
             ID as the line item for stubs)
          4. Create a binding and persist it

        Args:
            record_id: The AdServerCampaign record UUID.
            deal_id: The deal ID this creative serves.
            asset_data: Creative asset details for upload.
            rotation_config: Rotation/weighting configuration.

        Returns:
            Dict with upload and assignment results.

        Raises:
            ValueError: If the record is not found.
        """
        record = self._store.get_ad_server_campaign(record_id)
        if record is None:
            raise ValueError(f"Ad server campaign record '{record_id}' not found")

        client = self.get_client(record.ad_server)

        # Upload creative
        upload_result = client.upload_creative(asset_data)
        external_creative_id = upload_result["external_creative_id"]

        # Assign to line item
        # Use external campaign ID as line context for stub
        assign_result = client.assign_creative_to_line(
            creative_id=external_creative_id,
            line_id=record.ad_server_campaign_id,
            rotation_config=rotation_config,
        )

        # Create binding
        binding = AdServerBinding(
            deal_id=deal_id,
            creative_id=asset_data.get("asset_id", external_creative_id),
            ad_server_line_id=assign_result["assignment_id"],
            serving_status=BindingServingStatus.ACTIVE,
        )

        # Add binding to existing list and persist
        existing_bindings = list(record.bindings)
        existing_bindings.append(binding)
        self._store.update_ad_server_campaign(
            record_id, bindings=existing_bindings
        )

        logger.info(
            "Uploaded and assigned creative %s to %s campaign %s "
            "(deal: %s, assignment: %s)",
            external_creative_id,
            record.ad_server.value,
            record.ad_server_campaign_id,
            deal_id,
            assign_result["assignment_id"],
        )

        return {
            "upload_status": upload_result["status"],
            "assign_status": assign_result["status"],
            "external_creative_id": external_creative_id,
            "assignment_id": assign_result["assignment_id"],
            "deal_id": deal_id,
            "ad_server": record.ad_server.value,
        }

    def sync_delivery(self, record_id: str) -> dict[str, Any]:
        """Fetch delivery data from the ad server and update the record.

        Args:
            record_id: The AdServerCampaign record UUID.

        Returns:
            The delivery data dict from the ad server.

        Raises:
            ValueError: If the record is not found.
        """
        record = self._store.get_ad_server_campaign(record_id)
        if record is None:
            raise ValueError(f"Ad server campaign record '{record_id}' not found")

        client = self.get_client(record.ad_server)
        delivery_data = client.get_delivery_data(record.ad_server_campaign_id)

        # Update the delivery record
        delivery = AdServerDelivery(
            impressions_served=delivery_data.get("impressions", 0),
            spend_reported=delivery_data.get("spend", 0.0),
        )
        self._store.update_ad_server_campaign(
            record_id, delivery=delivery
        )

        logger.info(
            "Synced delivery for %s campaign %s: %d impressions, $%.2f spend",
            record.ad_server.value,
            record.ad_server_campaign_id,
            delivery.impressions_served,
            delivery.spend_reported,
        )

        return delivery_data

    def sync_campaign_status(self, record_id: str) -> dict[str, Any]:
        """Sync campaign status from the ad server and update the record.

        Args:
            record_id: The AdServerCampaign record UUID.

        Returns:
            The status response from the ad server.

        Raises:
            ValueError: If the record is not found.
        """
        record = self._store.get_ad_server_campaign(record_id)
        if record is None:
            raise ValueError(f"Ad server campaign record '{record_id}' not found")

        client = self.get_client(record.ad_server)
        status_data = client.sync_status(record.ad_server_campaign_id)

        # Map ad server status to our status enum
        status_map = {
            "active": AdServerCampaignStatus.ACTIVE,
            "paused": AdServerCampaignStatus.PAUSED,
            "completed": AdServerCampaignStatus.COMPLETED,
            "pending": AdServerCampaignStatus.PENDING,
        }
        new_status = status_map.get(
            status_data.get("status", ""), record.status
        )
        self._store.update_ad_server_campaign(
            record_id, status=new_status
        )

        logger.info(
            "Synced status for %s campaign %s: %s",
            record.ad_server.value,
            record.ad_server_campaign_id,
            new_status.value,
        )

        return status_data
