# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Pytest configuration and fixtures."""

import pytest


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
        "publisherId": "pub_abc",
        "name": "Homepage Banner",
        "currency": "USD",
        "basePrice": 15.00,
        "rateType": "CPM",
        "deliveryType": "Guaranteed",
        "availableImpressions": 1000000,
    }


@pytest.fixture
def sample_order() -> dict:
    """Sample order for testing."""
    return {
        "id": "order_456",
        "name": "Test Order",
        "accountId": "acct_789",
        "budget": 25000,
        "currency": "USD",
        "startDate": "2025-02-01T00:00:00Z",
        "endDate": "2025-02-28T23:59:59Z",
        "orderStatus": "PENDING",
    }
