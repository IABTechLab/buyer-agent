# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Webhook receiver system for buyer-agent.

Enables the buyer to receive HTTP POST notifications from sellers and
other systems when events occur, including:
- Deal status updates (created, registered, synced)
- Inventory availability alerts
- Negotiation round updates
- Proposal status changes
- AAMP registry updates (agent revocation, verification)
"""

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
    WebhookSecretConfig,
)
from .receiver import router as webhook_router
from .secrets import WebhookSecretStore, get_secret_store
from .verification import generate_signature, verify_webhook_signature

__all__ = [
    # Models
    "DealWebhookEvent",
    "InventoryWebhookEvent",
    "NegotiationWebhookEvent",
    "ProposalWebhookEvent",
    "AAMPWebhookEvent",
    "WebhookSecretConfig",
    # Handlers
    "handle_deal_webhook",
    "handle_inventory_webhook",
    "handle_negotiation_webhook",
    "handle_proposal_webhook",
    "handle_aamp_webhook",
    # Router
    "webhook_router",
    # Secrets
    "WebhookSecretStore",
    "get_secret_store",
    # Verification
    "verify_webhook_signature",
    "generate_signature",
]
