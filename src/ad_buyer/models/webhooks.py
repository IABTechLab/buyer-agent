# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Webhook data models for buyer-agent.

Defines models for receiving webhooks from sellers and other systems.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DealWebhookEvent(BaseModel):
    """Webhook event from seller about deal status."""

    webhook_id: str
    event_type: str  # "deal.created", "deal.registered", "deal.synced"
    event_id: str
    timestamp: str
    deal_id: str = ""
    proposal_id: str = ""
    session_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class InventoryWebhookEvent(BaseModel):
    """Webhook event about new inventory availability."""

    webhook_id: str
    event_type: str  # "inventory.available", "inventory.price_drop"
    event_id: str
    timestamp: str
    payload: dict[str, Any] = Field(default_factory=dict)


class NegotiationWebhookEvent(BaseModel):
    """Webhook event about negotiation updates."""

    webhook_id: str
    event_type: str  # "negotiation.seller_counter", "negotiation.concluded"
    event_id: str
    timestamp: str
    payload: dict[str, Any] = Field(default_factory=dict)


class AAMPWebhookEvent(BaseModel):
    """Webhook event from AAMP registry."""

    event_type: str  # "agent.revoked", "agent.updated", "agent.verified"
    event_id: str
    timestamp: str
    agent_url: str
    agent_id: str
    reason: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class ProposalWebhookEvent(BaseModel):
    """Webhook event about proposal status."""

    webhook_id: str
    event_type: str  # "proposal.evaluated", "proposal.accepted", "proposal.rejected"
    event_id: str
    timestamp: str
    proposal_id: str
    deal_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class WebhookSecretConfig(BaseModel):
    """Webhook secret configuration per seller."""

    seller_url: str
    secret: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
