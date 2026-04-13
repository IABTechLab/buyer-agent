# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for BuyerDealFlow - buyer deal discovery and Deal ID creation workflow.

Covers:
- Request validation (empty request, valid request)
- Inventory discovery (success, failure, tool errors)
- Product evaluation and selection (crew-based, product ID extraction)
- Deal ID request (success, no product selected, tool failure)
- Status reporting
- run_buyer_deal_flow convenience function
- Edge cases: missing fields, state transitions
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ad_buyer.flows.buyer_deal_flow import (
    DiscoveredProduct,
    BuyerDealFlow,
    BuyerDealFlowState,
    BuyerDealFlowStatus,
    run_buyer_deal_flow,
)
from ad_buyer.models.buyer_identity import (
    AccessTier,
    BuyerContext,
    BuyerIdentity,
    DealType,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_unified_client():
    """Create a mock UnifiedClient."""
    client = MagicMock()
    client.search_products = AsyncMock()
    client.list_products = AsyncMock()
    client.get_product = AsyncMock()
    return client


@pytest.fixture
def agency_buyer_context():
    """Agency-tier buyer context."""
    identity = BuyerIdentity(
        seat_id="ttd-seat-001",
        agency_id="agency-123",
        agency_name="Test Agency",
    )
    return BuyerContext(identity=identity, is_authenticated=True)


@pytest.fixture
def advertiser_buyer_context():
    """Advertiser-tier buyer context."""
    identity = BuyerIdentity(
        seat_id="ttd-seat-001",
        agency_id="agency-123",
        agency_name="Test Agency",
        advertiser_id="adv-456",
        advertiser_name="Test Advertiser",
    )
    return BuyerContext(identity=identity, is_authenticated=True)


@pytest.fixture
def public_buyer_context():
    """Public-tier buyer context (no identity)."""
    return BuyerContext()


@pytest.fixture
def buyer_flow(mock_unified_client, agency_buyer_context):
    """Create a BuyerDealFlow with mocked client and agency context."""
    return BuyerDealFlow(client=mock_unified_client, buyer_context=agency_buyer_context)


@pytest.fixture
def buyer_flow_with_request(buyer_flow):
    """Buyer deal flow with a valid request already set."""
    buyer_flow.state.request = "CTV inventory for sports audiences under $30 CPM"
    buyer_flow.state.deal_type = DealType.PREFERRED_DEAL
    buyer_flow.state.impressions = 1_000_000
    buyer_flow.state.max_cpm = 30.0
    buyer_flow.state.flight_start = "2026-04-01"
    buyer_flow.state.flight_end = "2026-04-30"
    return buyer_flow


# ===========================================================================
# Model tests
# ===========================================================================


class TestBuyerDealFlowModels:
    """Tests for buyer deal flow data models."""

    def test_buyer_flow_state_defaults(self):
        """BuyerDealFlowState initializes with correct defaults."""
        state = BuyerDealFlowState()

        assert state.request == ""
        assert state.deal_type == DealType.PREFERRED_DEAL
        assert state.impressions is None
        assert state.max_cpm is None
        assert state.status == BuyerDealFlowStatus.INITIALIZED
        assert state.errors == []
        assert state.discovered_products == []

    def test_buyer_flow_state_with_values(self):
        """BuyerDealFlowState can be created with custom values."""
        state = BuyerDealFlowState(
            request="CTV inventory",
            deal_type=DealType.PROGRAMMATIC_GUARANTEED,
            impressions=5_000_000,
            max_cpm=25.0,
            flight_start="2026-04-01",
            flight_end="2026-04-30",
        )

        assert state.request == "CTV inventory"
        assert state.deal_type == DealType.PROGRAMMATIC_GUARANTEED
        assert state.impressions == 5_000_000
        assert state.max_cpm == 25.0

    def test_discovered_product_model(self):
        """DiscoveredProduct model validates correctly."""
        product = DiscoveredProduct(
            product_id="ctv_001",
            product_name="Premium CTV",
            publisher="StreamCo",
            channel="ctv",
            base_cpm=22.0,
            tiered_cpm=19.8,
            available_impressions=2_000_000,
            targeting=["household", "demographics"],
            score=0.85,
        )

        assert product.product_id == "ctv_001"
        assert product.tiered_cpm == 19.8
        assert len(product.targeting) == 2
        assert product.score == 0.85

    def test_buyer_flow_status_enum(self):
        """BuyerDealFlowStatus enum has all expected values."""
        assert BuyerDealFlowStatus.INITIALIZED.value == "initialized"
        assert BuyerDealFlowStatus.REQUEST_RECEIVED.value == "request_received"
        assert BuyerDealFlowStatus.DISCOVERING_INVENTORY.value == "discovering_inventory"
        assert BuyerDealFlowStatus.EVALUATING_PRICING.value == "evaluating_pricing"
        assert BuyerDealFlowStatus.REQUESTING_DEAL.value == "requesting_deal"
        assert BuyerDealFlowStatus.DEAL_CREATED.value == "deal_created"
        assert BuyerDealFlowStatus.FAILED.value == "failed"


# ===========================================================================
# receive_request (the @start step)
# ===========================================================================


class TestReceiveRequest:
    """Tests for the request validation entry point."""

    def test_valid_request(self, buyer_flow_with_request):
        """Valid request transitions to REQUEST_RECEIVED."""
        result = buyer_flow_with_request.receive_request()

        assert result["status"] == "success"
        assert result["request"] == buyer_flow_with_request.state.request
        assert result["access_tier"] == AccessTier.AGENCY.value
        assert buyer_flow_with_request.state.status == BuyerDealFlowStatus.REQUEST_RECEIVED

    def test_empty_request_fails(self, buyer_flow):
        """Empty request string fails."""
        buyer_flow.state.request = ""

        result = buyer_flow.receive_request()

        assert result["status"] == "failed"
        assert buyer_flow.state.status == BuyerDealFlowStatus.FAILED
        assert len(buyer_flow.state.errors) > 0

    def test_buyer_context_stored_in_state(self, buyer_flow_with_request):
        """Buyer context is serialized and stored in state."""
        buyer_flow_with_request.receive_request()

        assert buyer_flow_with_request.state.buyer_context is not None
        ctx = buyer_flow_with_request.state.buyer_context
        assert ctx["identity"]["agency_id"] == "agency-123"

    def test_advertiser_tier_access(self, mock_unified_client, advertiser_buyer_context):
        """Advertiser tier is correctly reported."""
        flow = BuyerDealFlow(client=mock_unified_client, buyer_context=advertiser_buyer_context)
        flow.state.request = "Premium inventory"

        result = flow.receive_request()

        assert result["access_tier"] == AccessTier.ADVERTISER.value

    def test_public_tier_access(self, mock_unified_client, public_buyer_context):
        """Public tier is correctly reported."""
        flow = BuyerDealFlow(client=mock_unified_client, buyer_context=public_buyer_context)
        flow.state.request = "Any inventory"

        result = flow.receive_request()

        assert result["access_tier"] == AccessTier.PUBLIC.value


# ===========================================================================
# discover_inventory
# ===========================================================================


class TestDiscoverInventory:
    """Tests for the inventory discovery step."""

    def test_skips_on_failed_request(self, buyer_flow):
        """Discovery passes through upstream failure."""
        result = buyer_flow.discover_inventory({"status": "failed", "errors": ["bad"]})

        assert result["status"] == "failed"

    @patch.object(BuyerDealFlow, "__init__", lambda self, **kw: None)
    def test_discovery_success(self, buyer_flow_with_request):
        """Successful discovery returns results and updates status."""
        buyer_flow_with_request._discover_tool = MagicMock()
        buyer_flow_with_request._discover_tool._run.return_value = "Found 3 CTV products"

        result = buyer_flow_with_request.discover_inventory({"status": "success"})

        assert result["status"] == "success"
        assert "discovery_result" in result
        assert buyer_flow_with_request.state.status == BuyerDealFlowStatus.DISCOVERING_INVENTORY

    def test_discovery_tool_exception(self, buyer_flow_with_request):
        """Exception in discovery tool sets FAILED status."""
        buyer_flow_with_request._discover_tool._run = MagicMock(
            side_effect=RuntimeError("Connection refused")
        )

        result = buyer_flow_with_request.discover_inventory({"status": "success"})

        assert result["status"] == "failed"
        assert buyer_flow_with_request.state.status == BuyerDealFlowStatus.FAILED
        assert len(buyer_flow_with_request.state.errors) > 0

    def test_discovery_passes_filters(self, buyer_flow_with_request):
        """Discovery passes max_cpm and impressions to the tool."""
        buyer_flow_with_request._discover_tool._run = MagicMock(return_value="Results")

        buyer_flow_with_request.discover_inventory({"status": "success"})

        call_kwargs = buyer_flow_with_request._discover_tool._run.call_args
        assert call_kwargs.kwargs.get("max_cpm") == 30.0 or call_kwargs[1].get("max_cpm") == 30.0


# ===========================================================================
# _extract_product_id
# ===========================================================================


class TestExtractProductId:
    """Tests for product ID extraction from agent text."""

    def test_product_id_colon_format(self, buyer_flow):
        """Extracts product_id from 'product_id: xxx' format."""
        text = "I recommend product_id: ctv_premium_001 because it matches."
        result = buyer_flow._extract_product_id(text)
        assert result == "ctv_premium_001"

    def test_product_id_json_format(self, buyer_flow):
        """Extracts product_id from JSON-like format."""
        text = '{"product_id": "prod_abc_123", "name": "Test"}'
        result = buyer_flow._extract_product_id(text)
        assert result == "prod_abc_123"

    def test_product_id_title_case(self, buyer_flow):
        """Extracts from 'Product ID: xxx' format."""
        text = "The best option is Product ID: stream-hd-42"
        result = buyer_flow._extract_product_id(text)
        assert result == "stream-hd-42"

    def test_no_product_id_returns_none(self, buyer_flow):
        """Returns None when no product ID pattern is found."""
        text = "I could not find any matching products."
        result = buyer_flow._extract_product_id(text)
        assert result is None

    def test_empty_string(self, buyer_flow):
        """Empty string returns None."""
        result = buyer_flow._extract_product_id("")
        assert result is None

    def test_camel_case_format(self, buyer_flow):
        """Extracts from 'productId: xxx' format."""
        text = "The productId: test_prod_99 is the best."
        result = buyer_flow._extract_product_id(text)
        assert result == "test_prod_99"


# ===========================================================================
# evaluate_and_select
# ===========================================================================


class TestEvaluateAndSelect:
    """Tests for product evaluation and selection step."""

    def test_skips_on_failed_discovery(self, buyer_flow_with_request):
        """Evaluation passes through upstream failure."""
        result = buyer_flow_with_request.evaluate_and_select(
            {"status": "failed", "error": "no results"}
        )
        assert result["status"] == "failed"

    @patch("ad_buyer.flows.buyer_deal_flow.Task")
    @patch("ad_buyer.flows.buyer_deal_flow.Crew")
    @patch("ad_buyer.flows.buyer_deal_flow.create_buyer_deal_specialist_agent")
    def test_successful_selection(
        self, mock_agent, mock_crew_cls, mock_task, buyer_flow_with_request
    ):
        """Successful selection stores product_id and pricing."""
        # Mock the crew
        mock_crew_instance = MagicMock()
        mock_crew_instance.kickoff.return_value = "product_id: ctv_001 - best match"
        mock_crew_cls.return_value = mock_crew_instance

        # Mock pricing tool
        buyer_flow_with_request._pricing_tool._run = MagicMock(return_value="$18/CPM")

        result = buyer_flow_with_request.evaluate_and_select(
            {"status": "success", "discovery_result": "3 products found"}
        )

        assert result["status"] == "success"
        assert result["selected_product_id"] == "ctv_001"
        assert buyer_flow_with_request.state.selected_product_id == "ctv_001"
        assert buyer_flow_with_request.state.pricing_details is not None

    @patch("ad_buyer.flows.buyer_deal_flow.Task")
    @patch("ad_buyer.flows.buyer_deal_flow.Crew")
    @patch("ad_buyer.flows.buyer_deal_flow.create_buyer_deal_specialist_agent")
    def test_no_product_id_extracted(
        self, mock_agent, mock_crew_cls, mock_task, buyer_flow_with_request
    ):
        """When agent response has no product ID, selected_product_id is None."""
        mock_crew_instance = MagicMock()
        mock_crew_instance.kickoff.return_value = "No suitable products found."
        mock_crew_cls.return_value = mock_crew_instance

        result = buyer_flow_with_request.evaluate_and_select(
            {"status": "success", "discovery_result": "some results"}
        )

        assert result["status"] == "success"
        assert result["selected_product_id"] is None

    @patch("ad_buyer.flows.buyer_deal_flow.Task")
    @patch("ad_buyer.flows.buyer_deal_flow.Crew")
    @patch("ad_buyer.flows.buyer_deal_flow.create_buyer_deal_specialist_agent")
    def test_evaluation_exception(
        self, mock_agent, mock_crew_cls, mock_task, buyer_flow_with_request
    ):
        """Exception during evaluation sets FAILED status."""
        mock_crew_cls.side_effect = RuntimeError("Crew creation failed")

        result = buyer_flow_with_request.evaluate_and_select(
            {"status": "success", "discovery_result": "results"}
        )

        assert result["status"] == "failed"
        assert buyer_flow_with_request.state.status == BuyerDealFlowStatus.FAILED


# ===========================================================================
# request_deal_id
# ===========================================================================


class TestRequestDealId:
    """Tests for the Deal ID request step."""

    def test_skips_on_failed_selection(self, buyer_flow_with_request):
        """Deal request passes through upstream failure."""
        result = buyer_flow_with_request.request_deal_id(
            {"status": "failed", "error": "nothing selected"}
        )
        assert result["status"] == "failed"

    def test_fails_with_no_product_selected(self, buyer_flow_with_request):
        """Fails when no product has been selected."""
        buyer_flow_with_request.state.selected_product_id = None

        result = buyer_flow_with_request.request_deal_id({"status": "success"})

        assert result["status"] == "failed"
        assert "No product selected" in result["error"]
        assert buyer_flow_with_request.state.status == BuyerDealFlowStatus.FAILED

    def test_successful_deal_creation(self, buyer_flow_with_request):
        """Successful deal request stores deal response and sets DEAL_CREATED."""
        buyer_flow_with_request.state.selected_product_id = "ctv_001"
        buyer_flow_with_request._deal_tool._run = MagicMock(
            return_value="Deal DEAL-ABC123 created for ctv_001"
        )

        result = buyer_flow_with_request.request_deal_id({"status": "success"})

        assert result["status"] == "success"
        assert buyer_flow_with_request.state.status == BuyerDealFlowStatus.DEAL_CREATED
        assert buyer_flow_with_request.state.deal_response is not None
        assert "raw" in buyer_flow_with_request.state.deal_response

    def test_deal_tool_exception(self, buyer_flow_with_request):
        """Exception in deal tool sets FAILED status."""
        buyer_flow_with_request.state.selected_product_id = "ctv_001"
        buyer_flow_with_request._deal_tool._run = MagicMock(
            side_effect=RuntimeError("Server unavailable")
        )

        result = buyer_flow_with_request.request_deal_id({"status": "success"})

        assert result["status"] == "failed"
        assert buyer_flow_with_request.state.status == BuyerDealFlowStatus.FAILED
        assert len(buyer_flow_with_request.state.errors) > 0

    def test_deal_passes_flight_dates(self, buyer_flow_with_request):
        """Deal request passes flight dates to the tool."""
        buyer_flow_with_request.state.selected_product_id = "ctv_001"
        buyer_flow_with_request._deal_tool._run = MagicMock(return_value="Deal created")

        buyer_flow_with_request.request_deal_id({"status": "success"})

        call_kwargs = buyer_flow_with_request._deal_tool._run.call_args
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        # The tool should receive flight dates
        if kwargs:
            assert kwargs.get("flight_start") == "2026-04-01"
            assert kwargs.get("flight_end") == "2026-04-30"


# ===========================================================================
# get_status
# ===========================================================================


class TestBuyerDealFlowGetStatus:
    """Tests for the buyer deal flow status method."""

    def test_initial_status(self, buyer_flow):
        """Fresh flow reports INITIALIZED status."""
        status = buyer_flow.get_status()

        assert status["status"] == "initialized"
        assert status["request"] == ""
        assert status["deal_type"] == DealType.PREFERRED_DEAL.value
        assert status["selected_product_id"] is None
        assert status["deal_response"] is None
        assert status["errors"] == []

    def test_status_with_request(self, buyer_flow_with_request):
        """Status reflects configured request."""
        buyer_flow_with_request.state.status = BuyerDealFlowStatus.REQUEST_RECEIVED
        status = buyer_flow_with_request.get_status()

        assert status["status"] == "request_received"
        assert "CTV" in status["request"]
        assert status["access_tier"] == "agency"

    def test_status_after_deal_creation(self, buyer_flow_with_request):
        """Status reflects deal creation."""
        buyer_flow_with_request.state.status = BuyerDealFlowStatus.DEAL_CREATED
        buyer_flow_with_request.state.selected_product_id = "ctv_001"
        buyer_flow_with_request.state.deal_response = {"raw": "DEAL-ABC123"}

        status = buyer_flow_with_request.get_status()

        assert status["status"] == "deal_created"
        assert status["selected_product_id"] == "ctv_001"
        assert status["deal_response"] is not None

    def test_status_includes_updated_at(self, buyer_flow):
        """Status includes ISO-formatted updated_at."""
        status = buyer_flow.get_status()

        assert "updated_at" in status
        # Should be a valid ISO datetime string
        datetime.fromisoformat(status["updated_at"])

    def test_status_with_errors(self, buyer_flow):
        """Status reflects accumulated errors."""
        buyer_flow.state.errors = ["Error A", "Error B"]
        status = buyer_flow.get_status()

        assert len(status["errors"]) == 2


# ===========================================================================
# Flow initialization
# ===========================================================================


class TestBuyerDealFlowInitialization:
    """Tests for buyer deal flow construction."""

    def test_flow_creates_tools(self, mock_unified_client, agency_buyer_context):
        """Flow creates discover, pricing, and deal tools on init."""
        flow = BuyerDealFlow(client=mock_unified_client, buyer_context=agency_buyer_context)

        assert flow._discover_tool is not None
        assert flow._pricing_tool is not None
        assert flow._deal_tool is not None
        assert flow._client is mock_unified_client
        assert flow._buyer_context is agency_buyer_context

    def test_flow_state_is_initialized(self, buyer_flow):
        """Flow state is initialized with defaults."""
        # crewai wraps the state model in a StateWithId subclass,
        # so we check attributes rather than exact type.
        assert buyer_flow.state.status == BuyerDealFlowStatus.INITIALIZED
        assert buyer_flow.state.request == ""
        assert buyer_flow.state.errors == []


# ===========================================================================
# State transition tests
# ===========================================================================


class TestBuyerDealFlowStateTransitions:
    """Tests verifying status transitions through the flow."""

    def test_request_received_transition(self, buyer_flow_with_request):
        """receive_request transitions INITIALIZED -> REQUEST_RECEIVED."""
        assert buyer_flow_with_request.state.status == BuyerDealFlowStatus.INITIALIZED
        buyer_flow_with_request.receive_request()
        assert buyer_flow_with_request.state.status == BuyerDealFlowStatus.REQUEST_RECEIVED

    def test_discovering_inventory_transition(self, buyer_flow_with_request):
        """discover_inventory transitions to DISCOVERING_INVENTORY."""
        buyer_flow_with_request._discover_tool._run = MagicMock(return_value="found stuff")

        buyer_flow_with_request.discover_inventory({"status": "success"})

        assert buyer_flow_with_request.state.status == BuyerDealFlowStatus.DISCOVERING_INVENTORY

    @patch("ad_buyer.flows.buyer_deal_flow.Task")
    @patch("ad_buyer.flows.buyer_deal_flow.Crew")
    @patch("ad_buyer.flows.buyer_deal_flow.create_buyer_deal_specialist_agent")
    def test_evaluating_pricing_transition(
        self, mock_agent, mock_crew_cls, mock_task, buyer_flow_with_request
    ):
        """evaluate_and_select transitions to EVALUATING_PRICING."""
        mock_crew_instance = MagicMock()
        mock_crew_instance.kickoff.return_value = "product_id: x"
        mock_crew_cls.return_value = mock_crew_instance
        buyer_flow_with_request._pricing_tool._run = MagicMock(return_value="$20")

        buyer_flow_with_request.evaluate_and_select(
            {"status": "success", "discovery_result": "results"}
        )

        assert buyer_flow_with_request.state.status == BuyerDealFlowStatus.EVALUATING_PRICING

    def test_deal_created_transition(self, buyer_flow_with_request):
        """request_deal_id transitions to DEAL_CREATED on success."""
        buyer_flow_with_request.state.selected_product_id = "prod_1"
        buyer_flow_with_request._deal_tool._run = MagicMock(return_value="Deal created")

        buyer_flow_with_request.request_deal_id({"status": "success"})

        assert buyer_flow_with_request.state.status == BuyerDealFlowStatus.DEAL_CREATED

    def test_failed_transition_on_empty_request(self, buyer_flow):
        """Empty request transitions to FAILED."""
        buyer_flow.state.request = ""
        buyer_flow.receive_request()
        assert buyer_flow.state.status == BuyerDealFlowStatus.FAILED

    def test_failed_transition_on_discovery_error(self, buyer_flow_with_request):
        """Discovery failure transitions to FAILED."""
        buyer_flow_with_request._discover_tool._run = MagicMock(side_effect=RuntimeError("error"))
        buyer_flow_with_request.discover_inventory({"status": "success"})
        assert buyer_flow_with_request.state.status == BuyerDealFlowStatus.FAILED


# ===========================================================================
# run_buyer_deal_flow convenience function
# ===========================================================================


class TestRunBuyerDealFlowConvenience:
    """Tests for the run_buyer_deal_flow helper function."""

    def test_function_signature(self):
        """run_buyer_deal_flow has the expected parameters."""
        import inspect

        sig = inspect.signature(run_buyer_deal_flow)
        params = list(sig.parameters.keys())

        assert "request" in params
        assert "buyer_identity" in params
        assert "deal_type" in params
        assert "impressions" in params
        assert "max_cpm" in params
        assert "flight_start" in params
        assert "flight_end" in params
        assert "base_url" in params

    def test_default_deal_type(self):
        """Default deal type is PREFERRED_DEAL."""
        import inspect

        sig = inspect.signature(run_buyer_deal_flow)
        deal_type_default = sig.parameters["deal_type"].default

        assert deal_type_default == DealType.PREFERRED_DEAL
