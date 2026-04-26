# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""DSP Deal Discovery Flow - workflow for obtaining Deal IDs for programmatic activation.

Path B of the Audience Planner wiring (proposal §5.3 / bead ar-ts30 §18):
this flow is the brief-driven counterpart to ``CampaignPipeline``. When a
``CampaignBrief`` is supplied to the flow, the Audience Planner runs at
``receive_request`` and the resulting ``AudiencePlan`` is threaded onto the
seller-bound ``DealRequest`` / ``RequestDealTool`` payload so the seller can
match each ref against package capabilities (proposal §5.1).
"""

import logging
import sqlite3
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from crewai import Crew, Task
from crewai.flow.flow import Flow, listen, start
from pydantic import BaseModel, Field

from ..agents.level2.dsp_agent import create_dsp_agent
from ..clients.unified_client import UnifiedClient
from ..models.audience_plan import AudiencePlan
from ..time_utils import utc_now
from ..models.buyer_identity import (
    AccessTier,
    BuyerContext,
    BuyerIdentity,
    DealRequest,
    DealResponse,
    DealType,
)
from ..models.campaign_brief import CampaignBrief
from ..events.helpers import emit_event_sync
from ..events.models import EventType
from ..models.state_machine import BuyerDealStatus, DealStateMachine, InvalidTransitionError
from ..pipelines.audience_planner_step import (
    AudiencePlannerResult,
    run_audience_planner_step,
)
from ..storage.deal_store import DealStore
from ..tools.dsp import DiscoverInventoryTool, GetPricingTool, RequestDealTool

logger = logging.getLogger(__name__)


class BuyerDealFlowStatus(str, Enum):
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


class BuyerDealFlowState(BaseModel):
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

    # Audience plan threaded through the flow. Populated either from an
    # explicit caller-provided plan (preserves source=`explicit`) or by the
    # Audience Planner running at receive_request when a CampaignBrief was
    # supplied. None on legacy paths so behavior is unchanged when the
    # caller did not opt into audience targeting (proposal §5.3 / §18).
    audience_plan: Optional[AudiencePlan] = Field(
        default=None,
        description="Typed AudiencePlan threaded onto seller-bound calls",
    )

    # Execution tracking
    status: BuyerDealFlowStatus = Field(
        default=BuyerDealFlowStatus.INITIALIZED,
        description="Current flow status",
    )
    errors: list[str] = Field(default_factory=list)

    # Metadata
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class BuyerDealFlow(Flow[BuyerDealFlowState]):
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
        brief: Optional[CampaignBrief] = None,
    ):
        """Initialize the flow with client, buyer context, and optional persistence.

        Args:
            client: UnifiedClient for seller communication
            buyer_context: BuyerContext with identity for tiered access
            store: Optional DealStore for persisting deal state. When None,
                the flow behaves identically to before (in-memory only).
            brief: Optional ``CampaignBrief``. When provided, the Audience
                Planner runs at ``receive_request`` and the resulting
                ``AudiencePlan`` is threaded onto seller-bound calls
                (proposal §5.3 / bead ar-ts30 §18). When None, the flow
                stays audience-blind for backward compatibility.
        """
        super().__init__()
        self._client = client
        self._buyer_context = buyer_context
        self._store = store
        self._store_deal_id: Optional[str] = None
        # Cached brief for audience planning. The planner runs once at
        # receive_request; the resulting plan is held on flow state so
        # downstream stages can reference it without re-running the loop.
        self._brief: Optional[CampaignBrief] = brief
        # Cached planner result for tests / observability (mirrors
        # CampaignPipeline.get_audience_planner_result on Path A).
        self._audience_planner_result: Optional[AudiencePlannerResult] = None

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
        except (sqlite3.Error, OSError, ValueError, AttributeError):
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
        except (sqlite3.Error, OSError, ValueError, AttributeError):
            logger.exception(
                "Failed to persist status %s for deal %s",
                new_status,
                self._store_deal_id,
            )

    @start()
    def receive_request(self) -> dict[str, Any]:
        """Entry point: validate, parse deal request, and plan audience.

        When a ``CampaignBrief`` was passed at construction, this stage
        also runs the Audience Planner step (proposal §5.3 / bead
        ar-ts30 §18) so the resulting ``AudiencePlan`` is available to
        every subsequent stage. Callers may instead seed
        ``state.audience_plan`` directly (e.g. when threading a plan
        from a parent pipeline that already ran the planner); in that
        case we preserve the plan verbatim and skip the planner run.
        """
        request = self.state.request

        if not request:
            self.state.errors.append("No deal request provided")
            self.state.status = BuyerDealFlowStatus.FAILED
            return {"status": "failed", "errors": self.state.errors}

        # Audience planning: run BEFORE any seller-bound call so the plan
        # rides on the deal request. Preserve a caller-supplied plan
        # verbatim; otherwise call run_audience_planner_step on the brief
        # if one was provided. The planner is the same one used by
        # CampaignPipeline -- proposal §18 row dictates parity.
        if self.state.audience_plan is None and self._brief is not None:
            try:
                planner_result = run_audience_planner_step(self._brief)
                self._audience_planner_result = planner_result
                self.state.audience_plan = planner_result.plan
                if planner_result.plan is not None:
                    logger.info(
                        "buyer_deal_flow: audience plan resolved "
                        "(audience_plan_id=%s)",
                        planner_result.plan.audience_plan_id,
                    )
            except Exception as e:  # noqa: BLE001 - audience is additive; do not abort the deal flow
                # Audience planning is additive on this path. Failure must
                # not break the deal flow -- record the warning and keep
                # going audience-blind so legacy callers see no regression.
                logger.warning(
                    "buyer_deal_flow: audience planner failed (%s); "
                    "continuing audience-blind",
                    e,
                )
                self.state.errors.append(f"Audience planner warning: {e}")

        # Store buyer context in state
        self.state.buyer_context = self._buyer_context.model_dump()

        self.state.status = BuyerDealFlowStatus.REQUEST_RECEIVED
        self.state.updated_at = utc_now()

        # Emit quote.requested event
        emit_event_sync(
            EventType.QUOTE_REQUESTED,
            flow_type="dsp_deal",
            payload={
                "request": request[:200],
                "deal_type": self.state.deal_type.value,
                "impressions": self.state.impressions,
                "max_cpm": self.state.max_cpm,
            },
        )

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
            self.state.status = BuyerDealFlowStatus.DISCOVERING_INVENTORY

            # Extract filters from request
            discovery_result = self._discover_tool._run(
                query=self.state.request,
                max_cpm=self.state.max_cpm,
                min_impressions=self.state.impressions,
            )

            # Parse discovery results (simplified - in production would parse structured data)
            # For now, store raw results and let the agent process
            self.state.updated_at = utc_now()

            # Emit inventory.discovered event
            emit_event_sync(
                EventType.INVENTORY_DISCOVERED,
                flow_type="dsp_deal",
                payload={"query": self.state.request[:200]},
            )

            return {
                "status": "success",
                "discovery_result": discovery_result,
            }

        except Exception as e:  # noqa: BLE001 - flow step must capture any failure from CrewAI
            self.state.errors.append(f"Inventory discovery failed: {e}")
            self.state.status = BuyerDealFlowStatus.FAILED
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
            self.state.status = BuyerDealFlowStatus.EVALUATING_PRICING

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

                # Emit quote.received event
                emit_event_sync(
                    EventType.QUOTE_RECEIVED,
                    flow_type="dsp_deal",
                    payload={"product_id": product_id},
                )

                # Get detailed pricing
                pricing_result = self._pricing_tool._run(
                    product_id=product_id,
                    volume=self.state.impressions,
                    deal_type=self.state.deal_type.value,
                    flight_start=self.state.flight_start,
                    flight_end=self.state.flight_end,
                )
                self.state.pricing_details = {"raw": pricing_result}

            self.state.updated_at = utc_now()

            return {
                "status": "success",
                "selected_product_id": product_id,
                "selection_rationale": result_str,
            }

        except Exception as e:  # noqa: BLE001 - flow step must capture any failure from CrewAI
            self.state.errors.append(f"Product selection failed: {e}")
            self.state.status = BuyerDealFlowStatus.FAILED
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
            self.state.status = BuyerDealFlowStatus.FAILED
            return {"status": "failed", "error": "No product selected"}

        try:
            self.state.status = BuyerDealFlowStatus.REQUESTING_DEAL

            # Forward the AudiencePlan (when present) so the seller-bound
            # call carries the typed plan onto the wire per the §5
            # field additions. Tests assert the plan survives the flow ->
            # tool boundary.
            audience_plan_payload: AudiencePlan | None = self.state.audience_plan
            deal_result = self._deal_tool._run(
                product_id=product_id,
                deal_type=self.state.deal_type.value,
                impressions=self.state.impressions,
                flight_start=self.state.flight_start,
                flight_end=self.state.flight_end,
                audience_plan=audience_plan_payload,
            )

            # Store deal response
            self.state.deal_response = {"raw": deal_result}
            self.state.status = BuyerDealFlowStatus.DEAL_CREATED
            self.state.updated_at = utc_now()

            # Persist deal creation status
            self._persist_deal_status("deal_created")

            # Emit deal.booked event
            emit_event_sync(
                EventType.DEAL_BOOKED,
                flow_type="dsp_deal",
                deal_id=self._store_deal_id or "",
                payload={
                    "product_id": product_id,
                    "deal_type": self.state.deal_type.value,
                },
            )

            return {
                "status": "success",
                "deal_result": deal_result,
            }

        except Exception as e:  # noqa: BLE001 - flow step must capture any failure from CrewAI
            self.state.errors.append(f"Deal request failed: {e}")
            self.state.status = BuyerDealFlowStatus.FAILED
            self._persist_deal_status("failed")
            return {"status": "failed", "error": str(e)}

    def get_status(self) -> dict[str, Any]:
        """Get current flow status.

        Returns:
            Current state summary
        """
        plan = self.state.audience_plan
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
            # Surface the audience_plan_id when one was resolved so callers
            # can correlate logs / audit trails by hash (proposal §5.1).
            "audience_plan_id": (
                plan.audience_plan_id if plan is not None else None
            ),
        }

    def get_audience_planner_result(self) -> Optional[AudiencePlannerResult]:
        """Return the Audience Planner output for this flow run, if any.

        Populated by ``receive_request`` when a brief was supplied at
        construction. Mirrors ``CampaignPipeline.get_audience_planner_result``
        on Path A so tests can introspect the planner identically across
        both paths.
        """

        return self._audience_planner_result


async def run_buyer_deal_flow(
    request: str,
    buyer_identity: BuyerIdentity,
    deal_type: DealType = DealType.PREFERRED_DEAL,
    impressions: Optional[int] = None,
    max_cpm: Optional[float] = None,
    flight_start: Optional[str] = None,
    flight_end: Optional[str] = None,
    base_url: Optional[str] = None,
    store: Optional[DealStore] = None,
    brief: Optional[CampaignBrief] = None,
    audience_plan: Optional[AudiencePlan] = None,
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
        brief: Optional ``CampaignBrief`` -- when supplied the Audience
            Planner runs and threads an ``AudiencePlan`` onto seller
            calls (proposal §5.3 / bead ar-ts30 §18).
        audience_plan: Optional pre-built ``AudiencePlan``. Takes
            precedence over ``brief`` -- used when the parent pipeline
            already ran the planner and wants to pass the resolved plan
            verbatim into BuyerDealFlow.

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
        flow = BuyerDealFlow(
            client=client,
            buyer_context=buyer_context,
            store=store,
            brief=brief,
        )

        # Set initial state
        flow.state.request = request
        flow.state.deal_type = deal_type
        flow.state.impressions = impressions
        flow.state.max_cpm = max_cpm
        flow.state.flight_start = flight_start
        flow.state.flight_end = flight_end
        # An explicit caller-supplied plan takes precedence over the brief
        # path -- the planner is skipped and the plan is preserved verbatim.
        if audience_plan is not None:
            flow.state.audience_plan = audience_plan

        # Run flow
        result = flow.kickoff()

        return {
            "result": result,
            "status": flow.get_status(),
        }
