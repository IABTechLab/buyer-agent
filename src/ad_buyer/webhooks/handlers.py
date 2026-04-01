# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Webhook event handlers.

Processes incoming webhook events from sellers and other systems,
emitting corresponding events to the buyer's event bus.
"""

import logging
from typing import Any

from ..events.helpers import emit_event
from ..events.models import EventType
from ..models.webhooks import (
    AAMPWebhookEvent,
    DealWebhookEvent,
    InventoryWebhookEvent,
    NegotiationWebhookEvent,
    ProposalWebhookEvent,
)

logger = logging.getLogger(__name__)


async def handle_deal_webhook(event: DealWebhookEvent) -> None:
    """Process deal update webhook from seller.

    Args:
        event: Deal webhook event
    """
    logger.info(
        "Processing deal webhook: %s (deal_id=%s)",
        event.event_type,
        event.deal_id,
    )

    # Map seller event types to buyer event types
    event_type_map = {
        "deal.created": None,  # Buyer initiated, no need to emit
        "deal.registered": EventType.DEAL_BOOKED,
        "deal.synced": EventType.DEAL_BOOKED,
    }

    buyer_event_type = event_type_map.get(event.event_type)

    if buyer_event_type:
        await emit_event(
            event_type=buyer_event_type,
            deal_id=event.deal_id,
            payload={
                **event.payload,
                "webhook_source": event.webhook_id,
                "seller_event_type": event.event_type,
            },
            metadata={"source": "webhook", "webhook_event_id": event.event_id},
        )
    else:
        logger.debug("No buyer event emitted for seller event: %s", event.event_type)


async def handle_inventory_webhook(event: InventoryWebhookEvent) -> None:
    """Process inventory availability webhook from seller.

    Args:
        event: Inventory webhook event
    """
    logger.info("Processing inventory webhook: %s", event.event_type)

    product = event.payload

    # Emit inventory discovered event
    await emit_event(
        event_type=EventType.INVENTORY_DISCOVERED,
        payload={
            **product,
            "webhook_source": event.webhook_id,
            "discovery_source": "webhook",
        },
        metadata={"source": "webhook", "webhook_event_id": event.event_id},
    )

    # TODO: Check if matches active campaign needs
    # TODO: Auto-request quote if matching criteria
    logger.debug("Inventory discovered via webhook: %s", product.get("product_id"))


async def handle_negotiation_webhook(event: NegotiationWebhookEvent) -> None:
    """Process negotiation update webhook from seller.

    Args:
        event: Negotiation webhook event
    """
    logger.info("Processing negotiation webhook: %s", event.event_type)

    # Map seller negotiation events to buyer events
    if event.event_type in ("negotiation.seller_counter", "negotiation.round"):
        await emit_event(
            event_type=EventType.NEGOTIATION_ROUND,
            payload={
                **event.payload,
                "webhook_source": event.webhook_id,
                "seller_event_type": event.event_type,
            },
            metadata={"source": "webhook", "webhook_event_id": event.event_id},
        )
    elif event.event_type == "negotiation.concluded":
        await emit_event(
            event_type=EventType.NEGOTIATION_CONCLUDED,
            payload={
                **event.payload,
                "webhook_source": event.webhook_id,
            },
            metadata={"source": "webhook", "webhook_event_id": event.event_id},
        )


async def handle_proposal_webhook(event: ProposalWebhookEvent) -> None:
    """Process proposal status webhook from seller.

    Args:
        event: Proposal webhook event
    """
    logger.info(
        "Processing proposal webhook: %s (proposal_id=%s)",
        event.event_type,
        event.proposal_id,
    )

    # Emit corresponding buyer event based on proposal status
    # Note: Buyer doesn't have proposal-specific event types yet,
    # so we log and store metadata for now
    logger.info(
        "Proposal %s from seller: %s",
        event.event_type.split(".")[-1],  # "evaluated", "accepted", "rejected"
        event.proposal_id,
    )

    # If proposal was accepted and a deal was created, emit deal event
    if event.event_type == "proposal.accepted" and event.deal_id:
        await emit_event(
            event_type=EventType.DEAL_BOOKED,
            deal_id=event.deal_id,
            payload={
                **event.payload,
                "proposal_id": event.proposal_id,
                "webhook_source": event.webhook_id,
            },
            metadata={"source": "webhook", "webhook_event_id": event.event_id},
        )


async def handle_aamp_webhook(event: AAMPWebhookEvent) -> None:
    """Process AAMP registry update webhook.

    Args:
        event: AAMP webhook event
    """
    logger.warning(
        "AAMP registry event: %s for agent %s (reason: %s)",
        event.event_type,
        event.agent_url,
        event.reason,
    )

    if event.event_type == "agent.revoked":
        # TODO: Block agent immediately
        # TODO: Cancel pending deals with this seller
        logger.critical(
            "SECURITY ALERT: Agent %s has been revoked: %s",
            event.agent_url,
            event.reason,
        )

        # Emit security alert event
        # Note: Buyer doesn't have SECURITY_ALERT event type yet,
        # so we use a generic event for now
        logger.error(
            "Agent revocation detected - manual intervention required: %s",
            event.agent_url,
        )

    elif event.event_type == "agent.updated":
        logger.info("Agent %s metadata updated", event.agent_url)

    elif event.event_type == "agent.verified":
        logger.info("Agent %s verification status updated", event.agent_url)
