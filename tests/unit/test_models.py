# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for OpenDirect models."""

from datetime import datetime

import pytest

from ad_buyer.models.opendirect import (
    Account,
    AvailsRequest,
    AvailsResponse,
    DeliveryType,
    Line,
    LineBookingStatus,
    LineStats,
    Order,
    OrderStatus,
    Product,
    RateType,
)
from ad_buyer.models.flow_state import (
    BookingState,
    ChannelAllocation,
    ExecutionStatus,
    ProductRecommendation,
)


class TestOpenDirectModels:
    """Tests for OpenDirect Pydantic models."""

    def test_product_creation(self):
        """Test creating a Product model."""
        product = Product(
            id="prod_123",
            publisher_id="pub_abc",
            name="Homepage Banner",
            currency="USD",
            base_price=15.00,
            rate_type=RateType.CPM,
            delivery_type=DeliveryType.GUARANTEED,
            available_impressions=1000000,
        )

        assert product.id == "prod_123"
        assert product.publisher_id == "pub_abc"
        assert product.base_price == 15.00
        assert product.rate_type == RateType.CPM
        assert product.delivery_type == DeliveryType.GUARANTEED

    def test_product_from_dict(self):
        """Test creating Product from dict with aliases."""
        data = {
            "id": "prod_456",
            "publisherId": "pub_xyz",
            "name": "Video Ad",
            "currency": "USD",
            "basePrice": 25.00,
            "rateType": "CPM",
            "deliveryType": "PMP",
        }

        product = Product.model_validate(data)
        assert product.publisher_id == "pub_xyz"
        assert product.base_price == 25.00
        assert product.delivery_type == DeliveryType.PMP

    def test_order_creation(self):
        """Test creating an Order model."""
        order = Order(
            id="order_123",
            name="Q1 Campaign",
            account_id="acct_456",
            budget=50000,
            currency="USD",
            start_date=datetime(2025, 2, 1),
            end_date=datetime(2025, 2, 28),
        )

        assert order.id == "order_123"
        assert order.budget == 50000
        assert order.order_status == OrderStatus.PENDING

    def test_line_creation(self):
        """Test creating a Line model."""
        line = Line(
            id="line_123",
            order_id="order_456",
            product_id="prod_789",
            name="Homepage Line",
            start_date=datetime(2025, 2, 1),
            end_date=datetime(2025, 2, 28),
            rate_type=RateType.CPM,
            rate=15.00,
            quantity=500000,
        )

        assert line.id == "line_123"
        assert line.rate == 15.00
        assert line.quantity == 500000
        assert line.booking_status == LineBookingStatus.DRAFT

    def test_account_creation(self):
        """Test creating an Account model."""
        account = Account(
            id="acct_123",
            advertiser_id="adv_456",
            buyer_id="buyer_789",
            name="Test Account",
        )

        assert account.id == "acct_123"
        assert account.advertiser_id == "adv_456"

    def test_avails_request(self):
        """Test creating an AvailsRequest."""
        request = AvailsRequest(
            product_id="prod_123",
            start_date=datetime(2025, 2, 1),
            end_date=datetime(2025, 2, 28),
            requested_impressions=1000000,
            budget=15000,
        )

        assert request.product_id == "prod_123"
        assert request.requested_impressions == 1000000

    def test_avails_response(self):
        """Test creating an AvailsResponse."""
        response = AvailsResponse(
            product_id="prod_123",
            available_impressions=800000,
            guaranteed_impressions=750000,
            estimated_cpm=15.50,
            total_cost=11625.00,
            delivery_confidence=95.0,
        )

        assert response.available_impressions == 800000
        assert response.delivery_confidence == 95.0

    def test_line_stats(self):
        """Test creating LineStats."""
        stats = LineStats(
            line_id="line_123",
            impressions_delivered=250000,
            target_impressions=500000,
            delivery_rate=50.0,
            amount_spent=3750.00,
            budget=7500.00,
            budget_utilization=50.0,
            effective_cpm=15.00,
        )

        assert stats.delivery_rate == 50.0
        assert stats.effective_cpm == 15.00


class TestFlowStateModels:
    """Tests for flow state models."""

    def test_booking_state_creation(self):
        """Test creating a BookingState."""
        state = BookingState(
            campaign_brief={"name": "Test", "budget": 50000},
        )

        assert state.execution_status == ExecutionStatus.INITIALIZED
        assert state.campaign_brief["budget"] == 50000
        assert len(state.pending_approvals) == 0

    def test_channel_allocation(self):
        """Test creating a ChannelAllocation."""
        alloc = ChannelAllocation(
            channel="branding",
            budget=20000,
            percentage=40.0,
            rationale="Upper funnel focus",
        )

        assert alloc.budget == 20000
        assert alloc.percentage == 40.0

    def test_product_recommendation(self):
        """Test creating a ProductRecommendation."""
        rec = ProductRecommendation(
            product_id="prod_123",
            product_name="Premium Banner",
            publisher="Publisher A",
            channel="branding",
            impressions=500000,
            cpm=18.00,
            cost=9000.00,
        )

        assert rec.product_id == "prod_123"
        assert rec.cost == 9000.00
        assert rec.status == "pending"

    def test_execution_status_enum(self):
        """Test ExecutionStatus enum values."""
        assert ExecutionStatus.INITIALIZED.value == "initialized"
        assert ExecutionStatus.AWAITING_APPROVAL.value == "awaiting_approval"
        assert ExecutionStatus.COMPLETED.value == "completed"
