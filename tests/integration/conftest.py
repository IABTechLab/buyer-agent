# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Shared fixtures for integration tests."""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ad_buyer.auth.key_store import ApiKeyStore
from ad_buyer.auth.middleware import AuthMiddleware
from ad_buyer.clients.unified_client import Protocol, UnifiedClient, UnifiedResult
from ad_buyer.identity.strategy import (
    CampaignGoal,
    DealContext,
    IdentityStrategy,
    SellerRelationship,
)
from ad_buyer.models.buyer_identity import (
    AccessTier,
    BuyerContext,
    BuyerIdentity,
    DealType,
)
from ad_buyer.models.flow_state import (
    BookedLine,
    BookingState,
    ChannelAllocation,
    ExecutionStatus,
    ProductRecommendation,
)
from ad_buyer.negotiation.models import (
    NegotiationOutcome,
    NegotiationResult,
    NegotiationRound,
    NegotiationSession,
)
from ad_buyer.negotiation.strategies.simple_threshold import SimpleThresholdStrategy
from ad_buyer.registry.cache import SellerCache
from ad_buyer.registry.models import AgentCapability, AgentCard, TrustLevel


# --- Buyer identity fixtures ---


@pytest.fixture
def public_identity() -> BuyerIdentity:
    """Buyer with no identity fields (public tier)."""
    return BuyerIdentity()


@pytest.fixture
def seat_identity() -> BuyerIdentity:
    """Buyer with seat-level identity."""
    return BuyerIdentity(
        seat_id="ttd-seat-123",
        seat_name="The Trade Desk",
    )


@pytest.fixture
def agency_identity() -> BuyerIdentity:
    """Buyer with agency-level identity."""
    return BuyerIdentity(
        seat_id="ttd-seat-123",
        seat_name="The Trade Desk",
        agency_id="omnicom-456",
        agency_name="OMD",
        agency_holding_company="Omnicom",
    )


@pytest.fixture
def advertiser_identity() -> BuyerIdentity:
    """Buyer with full advertiser-level identity."""
    return BuyerIdentity(
        seat_id="ttd-seat-123",
        seat_name="The Trade Desk",
        agency_id="omnicom-456",
        agency_name="OMD",
        agency_holding_company="Omnicom",
        advertiser_id="coca-cola-789",
        advertiser_name="Coca-Cola",
        advertiser_industry="CPG",
    )


@pytest.fixture
def agency_context(agency_identity: BuyerIdentity) -> BuyerContext:
    """BuyerContext at agency tier."""
    return BuyerContext(
        identity=agency_identity,
        is_authenticated=True,
        preferred_deal_types=[DealType.PREFERRED_DEAL],
    )


@pytest.fixture
def advertiser_context(advertiser_identity: BuyerIdentity) -> BuyerContext:
    """BuyerContext at advertiser tier."""
    return BuyerContext(
        identity=advertiser_identity,
        is_authenticated=True,
        preferred_deal_types=[DealType.PROGRAMMATIC_GUARANTEED, DealType.PREFERRED_DEAL],
    )


# --- Product data fixtures ---


@pytest.fixture
def sample_products() -> list[dict[str, Any]]:
    """Sample product list returned from a seller."""
    return [
        {
            "id": "prod_ctv_001",
            "publisherId": "pub_streaming",
            "name": "Premium CTV 30s Spot",
            "currency": "USD",
            "basePrice": 35.00,
            "rateType": "CPM",
            "deliveryType": "Guaranteed",
            "availableImpressions": 5_000_000,
            "channel": "ctv",
            "targeting": ["household", "geo", "demographic"],
        },
        {
            "id": "prod_display_001",
            "publisherId": "pub_news",
            "name": "Homepage Banner 728x90",
            "currency": "USD",
            "basePrice": 12.00,
            "rateType": "CPM",
            "deliveryType": "PMP",
            "availableImpressions": 10_000_000,
            "channel": "display",
            "targeting": ["contextual", "geo"],
        },
    ]


@pytest.fixture
def sample_campaign_brief() -> dict[str, Any]:
    """Full campaign brief for integration tests."""
    return {
        "name": "Q1 Brand Campaign",
        "objectives": ["brand awareness", "reach"],
        "budget": 100_000,
        "start_date": "2025-03-01",
        "end_date": "2025-03-31",
        "target_audience": {
            "age": "25-54",
            "gender": "all",
            "geo": ["US"],
            "interests": ["sports", "entertainment"],
        },
        "kpis": {"viewability": 70, "completion_rate": 80},
    }


# --- Registry fixtures ---


@pytest.fixture
def seller_agent_cards() -> list[AgentCard]:
    """Sample seller agent cards from registry discovery."""
    return [
        AgentCard(
            agent_id="seller-streaming-001",
            name="StreamCo Ad Server",
            url="http://seller-streaming.example.com",
            protocols=["mcp", "a2a"],
            capabilities=[
                AgentCapability(name="ctv", description="CTV inventory", tags=["video", "streaming"]),
            ],
            trust_level=TrustLevel.VERIFIED,
        ),
        AgentCard(
            agent_id="seller-news-002",
            name="NewsNet Ad Server",
            url="http://seller-news.example.com",
            protocols=["mcp"],
            capabilities=[
                AgentCapability(name="display", description="Display inventory", tags=["banner", "native"]),
            ],
            trust_level=TrustLevel.REGISTERED,
        ),
    ]


# --- Temp directory fixtures ---


@pytest.fixture
def tmp_key_store(tmp_path: Path) -> ApiKeyStore:
    """API key store backed by a temp file."""
    return ApiKeyStore(store_path=tmp_path / "test_keys.json")


@pytest.fixture
def tmp_session_store_path(tmp_path: Path) -> str:
    """Path for a temporary session store file."""
    return str(tmp_path / "test_sessions.json")
