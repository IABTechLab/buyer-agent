# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Integration tests for flow + DealStore persistence.

These tests verify that:
1. DealBookingFlow with store=None works unchanged (backward compatibility)
2. DealBookingFlow with a store persists deal and booking data
3. DSPDealFlow with a store persists deal data
4. API job tracking writes to the store via _persist_job
"""

from unittest.mock import MagicMock

import pytest

from ad_buyer.flows.deal_booking_flow import DealBookingFlow
from ad_buyer.flows.dsp_deal_flow import DSPDealFlow, DSPFlowStatus
from ad_buyer.models.buyer_identity import (
    BuyerContext,
    BuyerIdentity,
    DealType,
)
from ad_buyer.models.flow_state import (
    BookingState,
    ChannelAllocation,
    ProductRecommendation,
)
from ad_buyer.storage import DealStore

# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------


@pytest.fixture
def deal_store():
    """Create a DealStore backed by in-memory SQLite."""
    store = DealStore("sqlite:///:memory:")
    store.connect()
    yield store
    store.disconnect()


@pytest.fixture
def mock_opendirect_client():
    """Create a mock OpenDirectClient."""
    client = MagicMock()
    client.base_url = "http://seller.example.com"
    return client


@pytest.fixture
def mock_unified_client():
    """Create a mock UnifiedClient."""
    client = MagicMock()
    client.base_url = "http://seller.example.com"
    return client


@pytest.fixture
def buyer_context():
    """Create a BuyerContext for DSP flow tests."""
    identity = BuyerIdentity(
        buyer_id="test-buyer-001",
        organization="Test Org",
        contact_email="test@example.com",
    )
    return BuyerContext(
        identity=identity,
        is_authenticated=True,
        preferred_deal_types=[DealType.PREFERRED_DEAL],
    )


# -----------------------------------------------------------------------
# DealBookingFlow backward compatibility (store=None)
# -----------------------------------------------------------------------


class TestDealBookingFlowNoStore:
    """Verify DealBookingFlow works identically when store=None."""

    def test_init_without_store(self, mock_opendirect_client):
        """Flow can be created without a store argument."""
        flow = DealBookingFlow(mock_opendirect_client)
        assert flow._store is None

    def test_brief_validation_no_store(self, mock_opendirect_client):
        """Brief validation works without a store."""
        flow = DealBookingFlow(mock_opendirect_client)
        flow._state = BookingState(
            campaign_brief={
                "objectives": ["awareness"],
                "budget": 10000,
                "start_date": "2026-04-01",
                "end_date": "2026-04-30",
                "target_audience": {"age": "25-45"},
            }
        )
        result = flow.receive_campaign_brief()
        assert result["status"] == "success"

    def test_brief_validation_failure_no_store(self, mock_opendirect_client):
        """Brief validation failure works without a store."""
        flow = DealBookingFlow(mock_opendirect_client)
        flow._state = BookingState(campaign_brief={"budget": 10000})
        result = flow.receive_campaign_brief()
        assert result["status"] == "failed"

    def test_consolidate_no_store(self, mock_opendirect_client):
        """Consolidation works without a store and creates no DB records."""
        flow = DealBookingFlow(mock_opendirect_client)
        flow._state = BookingState(
            campaign_brief={
                "objectives": ["awareness"],
                "budget": 10000,
                "start_date": "2026-04-01",
                "end_date": "2026-04-30",
                "target_audience": {},
            }
        )

        # Set up allocations and recommendations so consolidation triggers
        flow.state.budget_allocations["branding"] = ChannelAllocation(
            channel="branding",
            budget=10000,
            percentage=100,
            rationale="Test",
        )
        flow.state.channel_recommendations["branding"] = [
            ProductRecommendation(
                product_id="prod_1",
                product_name="Banner Ad",
                publisher="Publisher A",
                channel="branding",
                impressions=100000,
                cpm=15.0,
                cost=1500.0,
            )
        ]

        result = flow.consolidate_recommendations({"channel": "branding", "status": "success"})
        assert result["status"] == "ready_for_approval"
        assert result["total_recommendations"] == 1

    def test_execute_bookings_no_store(self, mock_opendirect_client):
        """Booking execution works without a store."""
        flow = DealBookingFlow(mock_opendirect_client)
        flow._state = BookingState(
            campaign_brief={
                "objectives": ["awareness"],
                "budget": 10000,
                "start_date": "2026-04-01",
                "end_date": "2026-04-30",
                "target_audience": {},
            }
        )

        rec = ProductRecommendation(
            product_id="prod_1",
            product_name="Banner Ad",
            publisher="Publisher A",
            channel="branding",
            impressions=100000,
            cpm=15.0,
            cost=1500.0,
            status="approved",
        )
        flow.state.pending_approvals = [rec]

        result = flow.approve_all()
        assert result["status"] == "success"
        assert result["booked"] == 1


# -----------------------------------------------------------------------
# DealBookingFlow with store
# -----------------------------------------------------------------------


class TestDealBookingFlowWithStore:
    """Verify DealBookingFlow persists data when store is provided."""

    def test_init_with_store(self, mock_opendirect_client, deal_store):
        """Flow accepts and stores the DealStore reference."""
        flow = DealBookingFlow(mock_opendirect_client, store=deal_store)
        assert flow._store is deal_store

    def test_consolidate_persists_deals(self, mock_opendirect_client, deal_store):
        """Consolidation creates deal records in the store."""
        flow = DealBookingFlow(mock_opendirect_client, store=deal_store)
        flow._state = BookingState(
            campaign_brief={
                "objectives": ["awareness"],
                "budget": 10000,
                "start_date": "2026-04-01",
                "end_date": "2026-04-30",
                "target_audience": {},
            }
        )

        flow.state.budget_allocations["branding"] = ChannelAllocation(
            channel="branding",
            budget=10000,
            percentage=100,
            rationale="Test",
        )
        flow.state.channel_recommendations["branding"] = [
            ProductRecommendation(
                product_id="prod_1",
                product_name="Banner Ad",
                publisher="Publisher A",
                channel="branding",
                impressions=100000,
                cpm=15.0,
                cost=1500.0,
            ),
            ProductRecommendation(
                product_id="prod_2",
                product_name="Video Ad",
                publisher="Publisher B",
                channel="branding",
                impressions=50000,
                cpm=25.0,
                cost=1250.0,
            ),
        ]

        result = flow.consolidate_recommendations({"channel": "branding", "status": "success"})
        assert result["status"] == "ready_for_approval"

        # Verify deals were persisted
        deals = deal_store.list_deals()
        assert len(deals) == 2
        assert deals[0]["status"] == "awaiting_approval"
        assert deals[1]["status"] == "awaiting_approval"

        # Verify status transitions were recorded
        for deal in deals:
            history = deal_store.get_status_history("deal", deal["id"])
            assert len(history) >= 1

    def test_execute_bookings_persists_records(self, mock_opendirect_client, deal_store):
        """Booking execution persists booking records and updates deal status."""
        flow = DealBookingFlow(mock_opendirect_client, store=deal_store)
        flow._state = BookingState(
            campaign_brief={
                "objectives": ["awareness"],
                "budget": 10000,
                "start_date": "2026-04-01",
                "end_date": "2026-04-30",
                "target_audience": {},
            }
        )

        # First create a deal in the store (simulating consolidation)
        deal_id = deal_store.save_deal(
            seller_url="Publisher A",
            product_id="prod_1",
            product_name="Banner Ad",
            deal_type="PD",
            status="awaiting_approval",
        )

        rec = ProductRecommendation(
            product_id="prod_1",
            product_name="Banner Ad",
            publisher="Publisher A",
            channel="branding",
            impressions=100000,
            cpm=15.0,
            cost=1500.0,
            status="approved",
        )
        # Attach the store deal ID
        rec._store_deal_id = deal_id  # type: ignore[attr-defined]
        flow.state.pending_approvals = [rec]

        result = flow.approve_all()
        assert result["status"] == "success"
        assert result["booked"] == 1

        # Verify booking record was persisted
        bookings = deal_store.get_booking_records(deal_id)
        assert len(bookings) == 1
        assert bookings[0]["channel"] == "branding"
        assert bookings[0]["impressions"] == 100000
        assert bookings[0]["cost"] == 1500.0

        # Verify deal status was updated to "booked"
        deal = deal_store.get_deal(deal_id)
        assert deal["status"] == "booked"

    def test_store_failure_does_not_break_flow(self, mock_opendirect_client):
        """Flow completes successfully even when store raises exceptions."""
        # Create a store and then break it
        broken_store = DealStore("sqlite:///:memory:")
        broken_store.connect()
        broken_store.disconnect()  # Close connection to cause errors

        flow = DealBookingFlow(mock_opendirect_client, store=broken_store)
        flow._state = BookingState(
            campaign_brief={
                "objectives": ["awareness"],
                "budget": 10000,
                "start_date": "2026-04-01",
                "end_date": "2026-04-30",
                "target_audience": {},
            }
        )

        flow.state.budget_allocations["branding"] = ChannelAllocation(
            channel="branding",
            budget=10000,
            percentage=100,
            rationale="Test",
        )
        flow.state.channel_recommendations["branding"] = [
            ProductRecommendation(
                product_id="prod_1",
                product_name="Banner Ad",
                publisher="Publisher A",
                channel="branding",
                impressions=100000,
                cpm=15.0,
                cost=1500.0,
            ),
        ]

        # This should NOT raise despite the broken store
        result = flow.consolidate_recommendations({"channel": "branding", "status": "success"})
        assert result["status"] == "ready_for_approval"


# -----------------------------------------------------------------------
# DSPDealFlow backward compatibility (store=None)
# -----------------------------------------------------------------------


class TestDSPDealFlowNoStore:
    """Verify DSPDealFlow works identically when store=None."""

    def test_init_without_store(self, mock_unified_client, buyer_context):
        """Flow can be created without a store argument."""
        flow = DSPDealFlow(mock_unified_client, buyer_context)
        assert flow._store is None

    def test_receive_request_no_store(self, mock_unified_client, buyer_context):
        """Request reception works without a store."""
        flow = DSPDealFlow(mock_unified_client, buyer_context)
        flow.state.request = "Premium video inventory for Q2"
        result = flow.receive_request()
        assert result["status"] == "success"
        assert flow.state.status == DSPFlowStatus.REQUEST_RECEIVED


# -----------------------------------------------------------------------
# DSPDealFlow with store
# -----------------------------------------------------------------------


class TestDSPDealFlowWithStore:
    """Verify DSPDealFlow persists data when store is provided."""

    def test_init_with_store(self, mock_unified_client, buyer_context, deal_store):
        """Flow accepts and stores the DealStore reference."""
        flow = DSPDealFlow(mock_unified_client, buyer_context, store=deal_store)
        assert flow._store is deal_store

    def test_receive_request_persists_deal(self, mock_unified_client, buyer_context, deal_store):
        """Request reception creates a draft deal in the store."""
        flow = DSPDealFlow(mock_unified_client, buyer_context, store=deal_store)
        flow.state.request = "Premium video inventory for Q2"
        flow.state.deal_type = DealType.PREFERRED_DEAL
        flow.state.impressions = 500000
        flow.state.flight_start = "2026-04-01"
        flow.state.flight_end = "2026-06-30"

        result = flow.receive_request()
        assert result["status"] == "success"

        # Verify deal was persisted
        deals = deal_store.list_deals()
        assert len(deals) == 1
        deal = deals[0]
        assert deal["status"] == "draft"
        assert deal["impressions"] == 500000
        assert deal["flight_start"] == "2026-04-01"
        assert deal["flight_end"] == "2026-06-30"

        # Verify the flow remembers the store deal ID
        assert flow._store_deal_id is not None
        assert flow._store_deal_id == deal["id"]

    def test_store_failure_does_not_break_dsp_flow(self, mock_unified_client, buyer_context):
        """DSP flow completes even when store raises exceptions."""
        broken_store = DealStore("sqlite:///:memory:")
        broken_store.connect()
        broken_store.disconnect()

        flow = DSPDealFlow(mock_unified_client, buyer_context, store=broken_store)
        flow.state.request = "Premium video inventory"

        # Should not raise
        result = flow.receive_request()
        assert result["status"] == "success"


# -----------------------------------------------------------------------
# API _persist_job integration
# -----------------------------------------------------------------------


class TestAPIPersistJob:
    """Test the API's _persist_job helper writes to the store."""

    def test_persist_job_writes_to_store(self, deal_store):
        """_persist_job writes a job dict to the store."""
        # Import the function
        from ad_buyer.interfaces.api import main as api_main

        # Temporarily replace the store
        original_store = api_main._deal_store
        api_main._deal_store = deal_store

        try:
            job = {
                "status": "running",
                "progress": 0.5,
                "brief": {"name": "Test Campaign", "budget": 50000},
                "auto_approve": False,
                "budget_allocations": {"branding": {"budget": 20000}},
                "recommendations": [{"id": "r1"}],
                "booked_lines": [],
                "errors": [],
                "created_at": "2026-03-10T00:00:00",
                "updated_at": "2026-03-10T00:00:00",
            }

            api_main._persist_job("test-job-001", job)

            # Verify in store
            stored = deal_store.get_job("test-job-001")
            assert stored is not None
            assert stored["status"] == "running"
            assert stored["progress"] == 0.5
            assert stored["brief"]["name"] == "Test Campaign"
            assert stored["auto_approve"] is False
        finally:
            api_main._deal_store = original_store

    def test_persist_job_upserts(self, deal_store):
        """_persist_job updates existing job records."""
        from ad_buyer.interfaces.api import main as api_main

        original_store = api_main._deal_store
        api_main._deal_store = deal_store

        try:
            job_v1 = {
                "status": "pending",
                "progress": 0.0,
                "brief": {"name": "Campaign"},
                "auto_approve": False,
                "budget_allocations": {},
                "recommendations": [],
                "booked_lines": [],
                "errors": [],
            }
            api_main._persist_job("job-002", job_v1)

            job_v2 = {
                "status": "completed",
                "progress": 1.0,
                "brief": {"name": "Campaign"},
                "auto_approve": True,
                "budget_allocations": {"branding": {"budget": 5000}},
                "recommendations": [{"id": "r1"}],
                "booked_lines": [{"id": "b1"}],
                "errors": [],
            }
            api_main._persist_job("job-002", job_v2)

            stored = deal_store.get_job("job-002")
            assert stored["status"] == "completed"
            assert stored["progress"] == 1.0
            assert stored["auto_approve"] is True
        finally:
            api_main._deal_store = original_store

    def test_persist_job_with_no_store_is_noop(self):
        """_persist_job does nothing when no store is available."""
        from ad_buyer.interfaces.api import main as api_main

        original_store = api_main._deal_store
        api_main._deal_store = None

        try:
            # Should not raise
            api_main._persist_job("job-003", {"status": "pending"})
        finally:
            api_main._deal_store = original_store
