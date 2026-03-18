# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Event data models for the buyer event bus.

Defines buyer-specific event types and the Event model used across
the event bus, helpers, and API endpoints.
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """Types of events emitted by the buyer system."""

    # Quote lifecycle
    QUOTE_REQUESTED = "quote.requested"
    QUOTE_RECEIVED = "quote.received"

    # Deal lifecycle
    DEAL_BOOKED = "deal.booked"
    DEAL_CANCELLED = "deal.cancelled"

    # Campaign lifecycle
    CAMPAIGN_CREATED = "campaign.created"

    # Budget lifecycle
    BUDGET_ALLOCATED = "budget.allocated"

    # Booking lifecycle
    BOOKING_SUBMITTED = "booking.submitted"

    # Inventory lifecycle
    INVENTORY_DISCOVERED = "inventory.discovered"

    # Negotiation lifecycle
    NEGOTIATION_STARTED = "negotiation.started"
    NEGOTIATION_ROUND = "negotiation.round"
    NEGOTIATION_CONCLUDED = "negotiation.concluded"

    # Session lifecycle
    SESSION_CREATED = "session.created"
    SESSION_CLOSED = "session.closed"

    # DealJockey - Phase 1
    DEAL_IMPORTED = "deal.imported"
    DEAL_TEMPLATE_CREATED = "deal.template_created"
    PORTFOLIO_INSPECTED = "portfolio.inspected"
    DEAL_MANUAL_ACTION_REQUIRED = "deal.manual_action_required"


class Event(BaseModel):
    """An event emitted by the buyer system."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: EventType
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    flow_id: str = ""
    flow_type: str = ""
    deal_id: str = ""
    session_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
