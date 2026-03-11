# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""DSP Deal Discovery Flow - workflow for obtaining Deal IDs for programmatic activation."""

import logging
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from crewai import Crew, Task
from crewai.flow.flow import Flow, listen, start
from pydantic import BaseModel, Field

from ..agents.level2.dsp_agent import create_dsp_agent
from ..clients.unified_client import UnifiedClient
from ..models.buyer_identity import (
    AccessTier,
    BuyerContext,
    BuyerIdentity,
    DealRequest,
    DealResponse,
    DealType,
)
from ..models.state_machine import BuyerDealStatus, DealStateMachine, InvalidTransitionError
from ..storage.deal_store import DealStore
from ..tools.dsp import DiscoverInventoryTool, GetPricingTool, RequestDealTool

logger = logging.getLogger(__name__)


class DSPFlowStatus(str, Enum):
    """Status values for the DSP deal flow."""

    INITIALIZED = "initialized"
    REQUEST_RECEIVED = "request_received"
    DISCOVERING_INVENTORY = "discovering_inventory"
    EVALUATING_PRICING = "evaluating_pricing"
    REQUESTING_DEAL = "requesting_deal"
    DEAL_CREATED = "deal_created"
    FAILED = "failed"


class DiscoveredProduct(BaseModel):
    """A product discovered during inventory search."""

    product_id: str
    product_name: str
    publisher: str
    channel: Optional[str] = None
    base_cpm: float
    tiered_cpm: float
    available_impressions: Optional[int] = None
    targeting: list[str] = Field(default_factory=list)
    score: float = Field(default=0.0, description="Match score for the request")


class DSPFlowState(BaseModel):
    """State model for the DSP deal discovery flow."""

    # Input
    request: str = Field(default="", description="Natural language deal request")
    deal_type: DealType = Field(
        default=DealType.PREFERRED_DEAL,
        description="Requested deal type",
    )
    impressions: Optional[int] = Field(
        default=None,
        description="Requested impression volume",
    )
    max_cpm: Optional[float] = Field(
        default=None,
        description="Maximum CPM budget",
    )
    flight_start: Optional[str] = Field(
        default=None,
        description="Deal start date",
    )
    flight_end: Optional[str] = Field(
        default=None,
        description="Deal end date",
    )

    # Buyer context
    buyer_context: Optional[dict[str, Any]] = Field(
        default=None,
        description="Serialized buyer context",
    )

    # Discovery results
    discovered_products: list[DiscoveredProduct] = Field(
        default_factory=list,
        description="Products found during discovery",
    )
    selected_product_id: Optional[str] = Field(
        default=None,
        description="Product selected for deal creation",
    )

    # Pricing
    pricing_details: Optional[dict[str, Any]] = Field(
        default=None,
        description="Pricing information for selected product",
    )

    # Deal result
    deal_response: Optional[dict[str, Any]] = Field(
        default=None,
        description="Created deal information",
    )

    # Execution tracking
    status: DSPFlowStatus = Field(
        default=DSPFlowStatus.INITIALIZED,
        description="Current flow status",
    )
    errors: list[str] = Field(default_factory=list)

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class DSPDealFlow(Flow[DSPFlowState]):
    """Event-driven flow for DSP deal discovery and Deal ID creation.

    This flow enables the DSP use case where:
    1. Buyer discovers available inventory with identity-based pricing
    2. Buyer selects inventory and requests a Deal ID
    3. Deal ID is returned for activation in traditional DSPs

    Flow steps:
    1. Receive and validate deal request
    2. Discover inventory matching criteria
    3. Get tiered pricing for candidate products
    4. Request Deal ID for selected product
    5. Return Deal ID with activation instructions
    """

    def __init__(
        self,
        client: UnifiedClient,
        buyer_context: BuyerContext,
        store: Optional[DealStore] = None,
    ):
        """Initialize the flow with client, buyer context, and optional persistence.

        Args:
            client: UnifiedClient for seller communication
            buyer_context: BuyerContext with identity for tiered access
            store: Optional DealStore for persisting deal state. When None,
                the flow behaves identically to before (in-memory only).
        """
        super().__init__()
        self._client = client
        self._buyer_context = buyer_context
        self._store = store
        self._store_deal_id: Optional[str] = None

        # Create tools
        self._discover_tool = DiscoverInventoryTool(
            client=client,
            buyer_context=buyer_context,
        )
        self._pricing_tool = GetPricingTool(
            client=client,
            buyer_context=buyer_context,
        )
        self._deal_tool = RequestDealTool(
            client=client,
            buyer_context=buyer_context,
        )

    # ------------------------------------------------------------------
    # Persistence helpers (best-effort dual-write)
    # ------------------------------------------------------------------

    def _persist_deal(self, deal_data: dict[str, Any]) -> None:
        """Best-effort persist a deal record to the store.

        Never raises -- logs and continues on failure.

        Args:
            deal_data: Dict of keyword args for DealStore.save_deal().
        """
        if self._store is None:
            return
        try:
            self._store_deal_id = self._store.save_deal(**deal_data)
        except Exception:
            logger.exception("Failed to persist deal %s", deal_data.get("product_id"))

    def _persist_deal_status(self, new_status: str) -> None:
        """Best-effort update deal status in the store.

        Uses DealStore.update_deal_status() which enforces state machine
        transitions when both statuses are valid BuyerDealStatus values.

        Args:
            new_status: New status value.
        """
        if self._store is None or self._store_deal_id is None:
            return
        try:
            ok = self._store.update_deal_status(
                self._store_deal_id, new_status, triggered_by="system"
            )
            if not ok:
                logger.warning(
                    "State machine rejected transition to %s for deal %s",
                    new_status,
                    self._store_deal_id,
                )
        except Exception:
            logger.exception(
                "Failed to persist status %s for deal %s",
                new_status,
                self._store_deal_id,
            )

    @start()
    def receive_request(self) -> dict[str, Any]:
        """Entry point: validate and parse deal request."""
        request = self.state.request

        if not request:
            self.state.errors.append("No deal request provided")
            self.state.status = DSPFlowStatus.FAILED
            return {"status": "failed", "errors": self.state.errors}

        # Store buyer context in state
        self.state.buyer_context = self._buyer_context.model_dump()

        self.state.status = DSPFlowStatus.REQUEST_RECEIVED
        self.state.updated_at = datetime.utcnow()

        # Persist initial deal record
        import json as _json

        self._persist_deal(
            dict(
                seller_url=getattr(self._client, "base_url", ""),
                product_id=self.state.request[:80],  # placeholder until discovered
                product_name=self.state.request[:120],
                deal_type=self.state.deal_type.value if self.state.deal_type else "PD",
                status="draft",
                impressions=self.state.impressions,
                flight_start=self.state.flight_start,
                flight_end=self.state.flight_end,
                buyer_context=_json.dumps(self.state.buyer_context)
                if self.state.buyer_context
                else None,
            )
        )

        return {
            "status": "success",
            "request": request,
            "access_tier": self._buyer_context.get_access_tier().value,
        }

    @listen(receive_request)
    def discover_inventory(self, request_result: dict[str, Any]) -> dict[str, Any]:
        """Discover inventory matching the request criteria."""
        if request_result.get("status") != "success":
            return request_result

        try:
            self.state.status = DSPFlowStatus.DISCOVERING_INVENTORY

            # Extract filters from request
            discovery_result = self._discover_tool._run(
                query=self.state.request,
                max_cpm=self.state.max_cpm,
                min_impressions=self.state.impressions,
            )

            # Parse discovery results (simplified - in production would parse structured data)
            # For now, store raw results and let the agent process
            self.state.updated_at = datetime.utcnow()

            return {
                "status": "success",
                "discovery_result": discovery_result,
            }

        except Exception as e:
            self.state.errors.append(f"Inventory discovery failed: {e}")
            self.state.status = DSPFlowStatus.FAILED
            return {"status": "failed", "error": str(e)}

    @listen(discover_inventory)
    def evaluate_and_select(self, discovery_result: dict[str, Any]) -> dict[str, Any]:
        """Evaluate discovered products and select best match.

        In a full implementation, this would use the DSP agent to
        intelligently select the best product. For now, we use a
        simplified selection based on the first available product.
        """
        if discovery_result.get("status") != "success":
            return discovery_result

        try:
            self.state.status = DSPFlowStatus.EVALUATING_PRICING

            # Create crew for intelligent selection
            dsp_agent = create_dsp_agent(
                tools=[self._discover_tool, self._pricing_tool],
            )

            selection_task = Task(
                description=f"""Analyze the discovery results and select the best product
for the following request: {self.state.request}

Discovery results:
{discovery_result.get('discovery_result', 'No results')}

Criteria:
- Deal type: {self.state.deal_type.value}
- Max CPM: {self.state.max_cpm or 'No limit'}
- Volume: {self.state.impressions or 'Flexible'}

Return the product_id of the best matching product and explain why.""",
                expected_output="Product ID and selection rationale",
                agent=dsp_agent,
            )

            crew = Crew(
                agents=[dsp_agent],
                tasks=[selection_task],
                verbose=True,
            )

            result = crew.kickoff()
            result_str = str(result)

            # Extract product ID (simplified - look for patterns)
            # In production, this would be more robust
            product_id = self._extract_product_id(result_str)

            if product_id:
                self.state.selected_product_id = product_id
                self._persist_deal_status("evaluating_pricing")

                # Get detailed pricing
                pricing_result = self._pricing_tool._run(
                    product_id=product_id,
                    volume=self.state.impressions,
                    deal_type=self.state.deal_type.value,
                    flight_start=self.state.flight_start,
                    flight_end=self.state.flight_end,
                )
                self.state.pricing_details = {"raw": pricing_result}

            self.state.updated_at = datetime.utcnow()

            return {
                "status": "success",
                "selected_product_id": product_id,
                "selection_rationale": result_str,
            }

        except Exception as e:
            self.state.errors.append(f"Product selection failed: {e}")
            self.state.status = DSPFlowStatus.FAILED
            return {"status": "failed", "error": str(e)}

    def _extract_product_id(self, text: str) -> Optional[str]:
        """Extract product ID from agent response."""
        import re

        # Look for common patterns
        patterns = [
            r'product_id["\s:]+([a-zA-Z0-9_-]+)',
            r'Product ID["\s:]+([a-zA-Z0-9_-]+)',
            r'productId["\s:]+([a-zA-Z0-9_-]+)',
            r'id["\s:]+([a-zA-Z0-9_-]+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)

        return None

    @listen(evaluate_and_select)
    def request_deal_id(self, selection_result: dict[str, Any]) -> dict[str, Any]:
        """Request Deal ID for the selected product."""
        if selection_result.get("status") != "success":
            return selection_result

        product_id = self.state.selected_product_id
        if not product_id:
            self.state.errors.append("No product selected for deal creation")
            self.state.status = DSPFlowStatus.FAILED
            return {"status": "failed", "error": "No product selected"}

        try:
            self.state.status = DSPFlowStatus.REQUESTING_DEAL

            deal_result = self._deal_tool._run(
                product_id=product_id,
                deal_type=self.state.deal_type.value,
                impressions=self.state.impressions,
                flight_start=self.state.flight_start,
                flight_end=self.state.flight_end,
            )

            # Store deal response
            self.state.deal_response = {"raw": deal_result}
            self.state.status = DSPFlowStatus.DEAL_CREATED
            self.state.updated_at = datetime.utcnow()

            # Persist deal creation status
            self._persist_deal_status("deal_created")

            return {
                "status": "success",
                "deal_result": deal_result,
            }

        except Exception as e:
            self.state.errors.append(f"Deal request failed: {e}")
            self.state.status = DSPFlowStatus.FAILED
            self._persist_deal_status("failed")
            return {"status": "failed", "error": str(e)}

    def get_status(self) -> dict[str, Any]:
        """Get current flow status.

        Returns:
            Current state summary
        """
        return {
            "status": self.state.status.value,
            "request": self.state.request,
            "deal_type": self.state.deal_type.value,
            "access_tier": (
                self._buyer_context.get_access_tier().value
                if self._buyer_context
                else "unknown"
            ),
            "selected_product_id": self.state.selected_product_id,
            "deal_response": self.state.deal_response,
            "errors": self.state.errors,
            "updated_at": self.state.updated_at.isoformat(),
        }


async def run_dsp_deal_flow(
    request: str,
    buyer_identity: BuyerIdentity,
    deal_type: DealType = DealType.PREFERRED_DEAL,
    impressions: Optional[int] = None,
    max_cpm: Optional[float] = None,
    flight_start: Optional[str] = None,
    flight_end: Optional[str] = None,
    base_url: Optional[str] = None,
    store: Optional[DealStore] = None,
) -> dict[str, Any]:
    """Convenience function to run the DSP deal flow.

    Args:
        request: Natural language deal request
        buyer_identity: BuyerIdentity for tiered pricing
        deal_type: Type of deal to request
        impressions: Requested impression volume
        max_cpm: Maximum CPM budget
        flight_start: Deal start date
        flight_end: Deal end date
        base_url: Server URL (defaults to Settings.iab_server_url)
        store: Optional DealStore for persistence.

    Returns:
        Flow result with Deal ID and activation instructions
    """
    # Resolve server URL from Settings if not provided
    if base_url is None:
        from ..config.settings import get_settings
        base_url = get_settings().iab_server_url

    # Create buyer context
    buyer_context = BuyerContext(
        identity=buyer_identity,
        is_authenticated=True,
        preferred_deal_types=[deal_type],
    )

    # Create client
    async with UnifiedClient(base_url=base_url) as client:
        # Create and run flow
        flow = DSPDealFlow(
            client=client,
            buyer_context=buyer_context,
            store=store,
        )

        # Set initial state
        flow.state.request = request
        flow.state.deal_type = deal_type
        flow.state.impressions = impressions
        flow.state.max_cpm = max_cpm
        flow.state.flight_start = flight_start
        flow.state.flight_end = flight_end

        # Run flow
        result = flow.kickoff()

        return {
            "result": result,
            "status": flow.get_status(),
        }
