# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Webhook receiver API endpoints.

Provides REST endpoints for receiving webhooks from sellers and other systems.
"""

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from .handlers import (
    handle_aamp_webhook,
    handle_deal_webhook,
    handle_inventory_webhook,
    handle_negotiation_webhook,
    handle_proposal_webhook,
)
from ..models.webhooks import (
    AAMPWebhookEvent,
    DealWebhookEvent,
    InventoryWebhookEvent,
    NegotiationWebhookEvent,
    ProposalWebhookEvent,
)
from .secrets import get_secret_store
from .verification import verify_webhook_signature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


# In-memory idempotency cache (24 hour TTL in production)
_processed_events: set[str] = set()


def is_duplicate(event_id: str) -> bool:
    """Check if event was already processed (idempotency).

    Args:
        event_id: Event ID to check

    Returns:
        True if event was already processed
    """
    if event_id in _processed_events:
        return True
    _processed_events.add(event_id)

    # Simple cache eviction: keep only last 10,000 events
    if len(_processed_events) > 10000:
        _processed_events.clear()

    return False


async def verify_signature_from_request(
    request: Request,
    payload: dict[str, Any],
    x_webhook_signature: str | None,
) -> bool:
    """Verify webhook signature from request headers.

    Args:
        request: FastAPI request
        payload: Webhook payload
        x_webhook_signature: Signature from header

    Returns:
        True if signature is valid or verification is disabled

    Raises:
        HTTPException: If signature is invalid
    """
    if not x_webhook_signature:
        logger.warning("Webhook received without signature")
        raise HTTPException(status_code=401, detail="Missing X-Webhook-Signature header")

    # Extract seller URL from payload or headers
    seller_url = payload.get("seller_url") or request.headers.get("X-Seller-URL")
    if not seller_url:
        logger.warning("Cannot verify signature: seller URL not provided")
        # Allow webhook but log warning (fail-open for development)
        return True

    # Get secret for this seller
    secret_store = get_secret_store()
    secret = secret_store.get_secret(seller_url)

    if not secret:
        logger.warning("No secret configured for seller: %s", seller_url)
        # Allow webhook but log warning (fail-open for development)
        return True

    # Verify signature
    if not verify_webhook_signature(payload, x_webhook_signature, secret):
        logger.error("Invalid webhook signature from seller: %s", seller_url)
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    return True


@router.post("/deal-updates")
async def receive_deal_update(
    event: DealWebhookEvent,
    background_tasks: BackgroundTasks,
    request: Request,
    x_webhook_signature: str | None = Header(None),
):
    """Receive deal status updates from sellers.

    Example payload:
    ```json
    {
      "webhook_id": "wh-abc123",
      "event_type": "deal.registered",
      "event_id": "evt-789",
      "timestamp": "2026-04-01T10:00:00Z",
      "deal_id": "DEMO-ABC123",
      "payload": {
        "ad_server": "freewheel",
        "order_id": "ord-456"
      }
    }
    ```
    """
    # Verify signature
    await verify_signature_from_request(
        request, event.model_dump(), x_webhook_signature
    )

    # Check idempotency
    if is_duplicate(event.event_id):
        logger.info("Duplicate webhook event ignored: %s", event.event_id)
        return {"status": "duplicate", "event_id": event.event_id}

    # Queue for async processing
    background_tasks.add_task(handle_deal_webhook, event)

    logger.info("Deal webhook accepted: %s", event.event_id)
    return {"status": "accepted", "event_id": event.event_id}


@router.post("/inventory")
async def receive_inventory_update(
    event: InventoryWebhookEvent,
    background_tasks: BackgroundTasks,
    request: Request,
    x_webhook_signature: str | None = Header(None),
):
    """Receive inventory availability notifications from sellers.

    Example payload:
    ```json
    {
      "webhook_id": "wh-abc123",
      "event_type": "inventory.available",
      "event_id": "evt-790",
      "timestamp": "2026-04-01T10:05:00Z",
      "payload": {
        "product_id": "prod-ctv-premium",
        "inventory_type": "ctv",
        "available_impressions": 5000000,
        "rate_card_cpm": 25.00
      }
    }
    ```
    """
    # Verify signature
    await verify_signature_from_request(
        request, event.model_dump(), x_webhook_signature
    )

    # Check idempotency
    if is_duplicate(event.event_id):
        return {"status": "duplicate", "event_id": event.event_id}

    # Queue for async processing
    background_tasks.add_task(handle_inventory_webhook, event)

    logger.info("Inventory webhook accepted: %s", event.event_id)
    return {"status": "accepted", "event_id": event.event_id}


@router.post("/negotiation")
async def receive_negotiation_update(
    event: NegotiationWebhookEvent,
    background_tasks: BackgroundTasks,
    request: Request,
    x_webhook_signature: str | None = Header(None),
):
    """Receive negotiation updates from sellers.

    Example payload:
    ```json
    {
      "webhook_id": "wh-abc123",
      "event_type": "negotiation.seller_counter",
      "event_id": "evt-791",
      "timestamp": "2026-04-01T10:10:00Z",
      "payload": {
        "negotiation_id": "neg-123",
        "round": 2,
        "seller_cpm": 28.00,
        "status": "active"
      }
    }
    ```
    """
    # Verify signature
    await verify_signature_from_request(
        request, event.model_dump(), x_webhook_signature
    )

    # Check idempotency
    if is_duplicate(event.event_id):
        return {"status": "duplicate", "event_id": event.event_id}

    # Queue for async processing
    background_tasks.add_task(handle_negotiation_webhook, event)

    logger.info("Negotiation webhook accepted: %s", event.event_id)
    return {"status": "accepted", "event_id": event.event_id}


@router.post("/proposals")
async def receive_proposal_update(
    event: ProposalWebhookEvent,
    background_tasks: BackgroundTasks,
    request: Request,
    x_webhook_signature: str | None = Header(None),
):
    """Receive proposal status updates from sellers.

    Example payload:
    ```json
    {
      "webhook_id": "wh-abc123",
      "event_type": "proposal.accepted",
      "event_id": "evt-792",
      "timestamp": "2026-04-01T10:15:00Z",
      "proposal_id": "prop-456",
      "deal_id": "DEMO-XYZ789",
      "payload": {
        "final_cpm": 26.50,
        "approved_by": "seller-agent"
      }
    }
    ```
    """
    # Verify signature
    await verify_signature_from_request(
        request, event.model_dump(), x_webhook_signature
    )

    # Check idempotency
    if is_duplicate(event.event_id):
        return {"status": "duplicate", "event_id": event.event_id}

    # Queue for async processing
    background_tasks.add_task(handle_proposal_webhook, event)

    logger.info("Proposal webhook accepted: %s", event.event_id)
    return {"status": "accepted", "event_id": event.event_id}


@router.post("/registry-updates")
async def receive_registry_update(
    event: AAMPWebhookEvent,
    background_tasks: BackgroundTasks,
):
    """Receive AAMP registry updates (agent revocation, verification, etc.).

    Example payload:
    ```json
    {
      "event_type": "agent.revoked",
      "event_id": "evt-793",
      "timestamp": "2026-04-01T10:20:00Z",
      "agent_url": "http://malicious-seller.com",
      "agent_id": "agent-999",
      "reason": "Security violation",
      "details": {
        "violation_type": "fraud",
        "reported_by": "aamp-registry"
      }
    }
    ```
    """
    # Check idempotency
    if is_duplicate(event.event_id):
        return {"status": "duplicate", "event_id": event.event_id}

    # Queue for async processing (high priority)
    background_tasks.add_task(handle_aamp_webhook, event)

    logger.warning("AAMP registry webhook accepted: %s", event.event_id)
    return {"status": "accepted", "event_id": event.event_id}


@router.post("/events")
async def receive_generic_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_signature: str | None = Header(None),
):
    """Generic webhook receiver for any event type.

    Routes to appropriate handler based on event_type field.
    """
    payload = await request.json()

    # Verify signature
    await verify_signature_from_request(request, payload, x_webhook_signature)

    event_type = payload.get("event_type", "")
    event_id = payload.get("event_id", "")

    # Check idempotency
    if is_duplicate(event_id):
        return {"status": "duplicate", "event_id": event_id}

    # Route to appropriate handler based on event type
    if event_type.startswith("deal."):
        event = DealWebhookEvent(**payload)
        background_tasks.add_task(handle_deal_webhook, event)
    elif event_type.startswith("inventory."):
        event = InventoryWebhookEvent(**payload)
        background_tasks.add_task(handle_inventory_webhook, event)
    elif event_type.startswith("negotiation."):
        event = NegotiationWebhookEvent(**payload)
        background_tasks.add_task(handle_negotiation_webhook, event)
    elif event_type.startswith("proposal."):
        event = ProposalWebhookEvent(**payload)
        background_tasks.add_task(handle_proposal_webhook, event)
    elif event_type.startswith("agent."):
        event = AAMPWebhookEvent(**payload)
        background_tasks.add_task(handle_aamp_webhook, event)
    else:
        logger.warning("Unknown webhook event type: %s", event_type)
        return {"status": "unknown_event_type", "event_type": event_type}

    logger.info("Generic webhook accepted: %s (%s)", event_id, event_type)
    return {"status": "accepted", "event_id": event_id, "event_type": event_type}
