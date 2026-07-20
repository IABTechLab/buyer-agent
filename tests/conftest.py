# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Pytest configuration and fixtures."""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def fake_booking_orchestrator():
    """AsyncMock MultiSellerOrchestrator booking one seller-issued deal per call.

    Canonical-path test double (bead ar-j2nw): echoes the requested
    DealParams back as a seller-confirmed 201 DealResponse with a
    SELLER-issued deal_id and quote_id, mirroring the real
    quotes -> deals contract.
    """
    from ad_buyer.models.deals import DealResponse, PricingInfo, ProductInfo, TermsInfo
    from ad_buyer.orchestration.multi_seller import (
        DealSelection,
        MultiSellerOrchestrator,
        OrchestrationResult,
    )

    async def _fake_orchestrate(inventory_requirements, deal_params, budget, max_deals=3):
        deal = DealResponse(
            deal_id=f"SELLER-DEAL-{deal_params.product_id}",
            deal_type=deal_params.deal_type,
            status="active",
            quote_id=f"quote-{deal_params.product_id}",
            product=ProductInfo(product_id=deal_params.product_id, name=deal_params.product_id),
            pricing=PricingInfo(base_cpm=deal_params.target_cpm, final_cpm=deal_params.target_cpm),
            terms=TermsInfo(
                impressions=deal_params.impressions,
                flight_start=deal_params.flight_start,
                flight_end=deal_params.flight_end,
            ),
        )
        return OrchestrationResult(
            discovered_sellers=[MagicMock(agent_id="seller-1")],
            quote_results=[],
            ranked_quotes=[],
            selection=DealSelection(
                booked_deals=[deal],
                failed_bookings=[],
                total_spend=budget,
                remaining_budget=0.0,
            ),
        )

    orch = AsyncMock(spec=MultiSellerOrchestrator)
    orch.orchestrate.side_effect = _fake_orchestrate
    return orch


@pytest.fixture
def sample_campaign_brief() -> dict:
    """Sample campaign brief for testing."""
    return {
        "name": "Test Campaign",
        "objectives": ["brand awareness", "reach"],
        "budget": 50000,
        "start_date": "2025-02-01",
        "end_date": "2025-02-28",
        "target_audience": {
            "age": "25-54",
            "gender": "all",
            "geo": ["US"],
        },
        "kpis": {
            "viewability": 70,
        },
    }


@pytest.fixture
def sample_product() -> dict:
    """Sample product for testing."""
    return {
        "id": "prod_123",
        "publisherid": "pub_abc",
        "name": "Homepage Banner",
        "currency": "USD",
        "baseprice": 15.00,
        "ratetype": "CPM",
        "deliverytype": "guaranteed",
        "availableImpressions": 1000000,
    }


@pytest.fixture
def sample_order() -> dict:
    """Sample order for testing."""
    return {
        "id": "order_456",
        "name": "Test Order",
        "accountid": "acct_789",
        "budget": 25000,
        "currency": "USD",
        "startdate": "2025-02-01T00:00:00Z",
        "enddate": "2025-02-28T23:59:59Z",
        "orderstatus": "PENDING",
    }
