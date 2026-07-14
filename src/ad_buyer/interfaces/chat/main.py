# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab
# ruff: noqa: E501  (long lines unavoidable in docstrings/string literals)

"""Chat interface for the Ad Buyer System.

Booking goes through the ONE canonical pipeline (bead ar-j2nw): the chat
agent's tools are thin wrappers over MultiSellerOrchestrator (discover ->
quote -> rank -> select_and_book against the real quotes -> deals
contract). The former inline seller-protocol tools (MultiSellerSearchTool,
CallSellerToolTool, BookPGDealTool, CreatePMPDealTool) were deleted --
chat never speaks a bespoke booking dialect again; every deal id shown to
the user is SELLER-issued.
"""

from dataclasses import dataclass, field
from typing import Any

from crewai import LLM, Agent, Crew, Task
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...async_utils import run_async
from ...config.settings import settings
from ...flows.deal_booking_flow import build_default_orchestrator
from ...orchestration.multi_seller import (
    DealParams,
    InventoryRequirements,
    MultiSellerOrchestrator,
)
from ...registry.models import AgentCard, TrustLevel


class ConversationMessage:
    """A message in the conversation."""

    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content


@dataclass
class SellerConnection:
    """Health-checked connection info for a configured seller agent."""

    url: str
    name: str = ""
    capabilities: dict[str, Any] = field(default_factory=dict)
    connected: bool = False
    error: str = ""

    def check_health(self) -> bool:
        """Synchronously check if seller is reachable and discover tools."""
        import httpx

        try:
            response = httpx.get(f"{self.url}/health", timeout=5.0)
            if response.status_code == 200:
                # Try to get server info
                try:
                    info_response = httpx.get(f"{self.url}/", timeout=5.0)
                    if info_response.status_code == 200:
                        info = info_response.json()
                        self.name = info.get("name", f"Seller ({self.url})")
                except (httpx.HTTPError, ValueError):
                    self.name = f"Seller ({self.url})"

                # Try to get tools from /mcp/tools
                try:
                    tools_response = httpx.get(f"{self.url}/mcp/tools", timeout=5.0)
                    if tools_response.status_code == 200:
                        data = tools_response.json()
                        tools = data.get("tools", data) if isinstance(data, dict) else data
                        if isinstance(tools, list):
                            tool_names = [t.get("name") for t in tools if t.get("name")]
                            self.capabilities = {"tools": tool_names}
                except (httpx.HTTPError, ValueError):
                    self.capabilities = {"tools": ["list_products", "get_pricing"]}

                self.connected = True
                return True
        except httpx.HTTPError as e:
            self.error = str(e)
            self.connected = False
        return False



class _ConfiguredSellersRegistry:
    """Registry adapter exposing configured SELLER_ENDPOINTS as AgentCards.

    Chat deployments configure sellers via SELLER_ENDPOINTS rather than a
    live agent registry. This adapter feeds those endpoints into the
    canonical MultiSellerOrchestrator's discovery stage; quoting and
    booking then run through the real quotes -> deals contract exactly as
    on every other path.
    """

    def __init__(self, sellers: list[SellerConnection]):
        self._sellers = sellers

    async def discover_sellers(
        self, capabilities_filter: list[str] | None = None
    ) -> list[AgentCard]:
        return [
            AgentCard(
                agent_id=seller.name or seller.url,
                name=seller.name or seller.url,
                url=seller.url,
                trust_level=TrustLevel.VERIFIED,
            )
            for seller in self._sellers
            if seller.connected
        ]


def _format_orchestration_result(result: Any) -> str:
    """Render an OrchestrationResult conversationally."""
    lines: list[str] = []
    selection = result.selection

    if selection.booked_deals:
        lines.append(f"BOOKED {len(selection.booked_deals)} deal(s):")
        for deal in selection.booked_deals:
            cpm = (
                f"${deal.pricing.final_cpm:.2f} CPM"
                if deal.pricing.final_cpm is not None
                else "CPM on request"
            )
            impressions = deal.terms.impressions or 0
            lines.append(
                f"  - Deal ID {deal.deal_id} (seller-issued) | quote {deal.quote_id} | "
                f"{deal.deal_type} | {cpm} | {impressions:,} impressions"
            )
        lines.append(f"Total spend: ${selection.total_spend:,.2f}")
        lines.append(f"Remaining budget: ${selection.remaining_budget:,.2f}")
    else:
        lines.append("No deals were booked.")

    if selection.failed_bookings:
        lines.append(f"Failed bookings ({len(selection.failed_bookings)}):")
        for failure in selection.failed_bookings:
            lines.append(f"  - {failure}")

    lines.append(
        f"(Sellers discovered: {len(result.discovered_sellers)}, "
        f"quotes ranked: {len(result.ranked_quotes)})"
    )
    return "\n".join(lines)


class RequestQuotesInput(BaseModel):
    """Input for requesting quotes across sellers (no money committed)."""

    product_id: str = Field(..., description="Product ID to request quotes for")
    media_type: str = Field(
        default="display", description="Media type: ctv, display, audio, native, mobile"
    )
    deal_type: str = Field(default="PD", description="Deal type: PG, PD, or PA")
    impressions: int = Field(..., description="Desired impression volume")
    flight_start: str = Field(default="", description="Flight start date (YYYY-MM-DD)")
    flight_end: str = Field(default="", description="Flight end date (YYYY-MM-DD)")
    max_cpm: float = Field(default=0, description="Maximum acceptable CPM (0 = no cap)")


class RequestQuotesTool(BaseTool):
    """Thin wrapper: canonical discover -> quote -> rank (no booking)."""

    name: str = "request_quotes"
    description: str = """Request price quotes from ALL connected sellers for a product.
    Runs the canonical multi-seller pipeline: discover sellers, request quotes in
    parallel, then normalize and rank them (best first). No money is committed.
    Use this BEFORE book_deals to compare the market."""
    args_schema: type[BaseModel] = RequestQuotesInput

    def __init__(self, orchestrator: MultiSellerOrchestrator, **kwargs):
        super().__init__(**kwargs)
        self._orchestrator = orchestrator

    def _run(self, **kwargs: Any) -> str:
        return run_async(self._arun(**kwargs))

    async def _arun(
        self,
        product_id: str,
        media_type: str = "display",
        deal_type: str = "PD",
        impressions: int = 0,
        flight_start: str = "",
        flight_end: str = "",
        max_cpm: float = 0,
    ) -> str:
        requirements = InventoryRequirements(
            media_type=media_type,
            deal_types=[deal_type],
            max_cpm=max_cpm if max_cpm > 0 else None,
        )
        deal_params = DealParams(
            product_id=product_id,
            deal_type=deal_type,
            impressions=impressions,
            flight_start=flight_start,
            flight_end=flight_end,
            media_type=media_type,
        )

        sellers = await self._orchestrator.discover_sellers(requirements)
        if not sellers:
            return "No sellers available. Configure SELLER_ENDPOINTS in .env"

        quote_results = await self._orchestrator.request_quotes_parallel(sellers, deal_params)
        ranked = await self._orchestrator.evaluate_and_rank(
            quote_results, max_cpm=requirements.max_cpm
        )

        if not ranked:
            failures = [r.error for r in quote_results if r.error]
            return f"No viable quotes. Seller errors: {failures or 'none'}"

        lines = [f"Ranked quotes from {len(sellers)} seller(s), best first:"]
        for nq in ranked:
            cpm = f"${nq.effective_cpm:.2f}" if nq.effective_cpm is not None else "on request"
            lines.append(
                f"  - quote {nq.quote_id} | seller {nq.seller_id} | {nq.deal_type} | "
                f"{cpm} effective CPM | score {nq.score:.1f}"
            )
        return "\n".join(lines)


class BookDealsInput(BaseModel):
    """Input for booking deals through the canonical pipeline."""

    product_id: str = Field(..., description="Product ID to book")
    budget: float = Field(..., description="Budget ceiling for this booking (hard bound)")
    impressions: int = Field(..., description="Desired impression volume")
    media_type: str = Field(
        default="display", description="Media type: ctv, display, audio, native, mobile"
    )
    deal_type: str = Field(default="PD", description="Deal type: PG, PD, or PA")
    flight_start: str = Field(default="", description="Flight start date (YYYY-MM-DD)")
    flight_end: str = Field(default="", description="Flight end date (YYYY-MM-DD)")
    max_cpm: float = Field(default=0, description="Maximum acceptable CPM (0 = no cap)")
    max_deals: int = Field(default=1, description="Maximum number of deals to book")


class BookDealsTool(BaseTool):
    """Thin wrapper over the canonical booking pipeline.

    Delegates to MultiSellerOrchestrator.orchestrate: discover -> quote ->
    rank -> select_and_book. Deal IDs in the result are SELLER-issued.
    """

    name: str = "book_deals"
    description: str = """Book advertising deals through the canonical multi-seller
    pipeline (discover sellers -> request quotes -> rank -> book best within budget).
    The budget is a hard bound; returned Deal IDs are issued by the seller.
    Use request_quotes first to preview the market."""
    args_schema: type[BaseModel] = BookDealsInput

    def __init__(self, orchestrator: MultiSellerOrchestrator, **kwargs):
        super().__init__(**kwargs)
        self._orchestrator = orchestrator

    def _run(self, **kwargs: Any) -> str:
        return run_async(self._arun(**kwargs))

    async def _arun(
        self,
        product_id: str,
        budget: float,
        impressions: int,
        media_type: str = "display",
        deal_type: str = "PD",
        flight_start: str = "",
        flight_end: str = "",
        max_cpm: float = 0,
        max_deals: int = 1,
    ) -> str:
        requirements = InventoryRequirements(
            media_type=media_type,
            deal_types=[deal_type],
            max_cpm=max_cpm if max_cpm > 0 else None,
        )
        deal_params = DealParams(
            product_id=product_id,
            deal_type=deal_type,
            impressions=impressions,
            flight_start=flight_start,
            flight_end=flight_end,
            target_cpm=max_cpm if max_cpm > 0 else None,
            media_type=media_type,
        )

        result = await self._orchestrator.orchestrate(
            inventory_requirements=requirements,
            deal_params=deal_params,
            budget=budget,
            max_deals=max_deals,
        )
        return _format_orchestration_result(result)


class ChatInterface:
    """Conversational interface for the ad buyer agent system.

    Connects to multiple seller agents configured in SELLER_ENDPOINTS.
    Uses IAB OpenDirect/AdCOM standards for interoperability.
    """

    def __init__(self):
        """Initialize the chat interface."""
        self.conversation_history: list[ConversationMessage] = []
        self.context: dict[str, Any] = {}
        self._sellers: list[SellerConnection] = []
        self._tools: list[BaseTool] = []

        # Connect to all configured sellers
        self._initialize_sellers()

        # Build seller list for agent context
        seller_info = self._get_seller_info()

        # Create chat agent
        self._chat_agent = Agent(
            role="Ad Buying Assistant",
            goal="""Help users plan, execute, and optimize their advertising
campaigns through natural conversation. Query multiple seller agents to find
the best inventory and negotiate deals using IAB OpenDirect standards.""",
            backstory=f"""You are a friendly and knowledgeable advertising
assistant with deep expertise in programmatic advertising, media buying,
and IAB Tech Lab standards (OpenDirect, AdCOM, OpenRTB).

You are connected to the following seller agents:
{seller_info}

You have tools to:
1. **request_quotes** - Get ranked price quotes from ALL connected sellers
   (canonical discover -> quote -> rank pipeline; commits no money)
2. **book_deals** - Book deals through the canonical pipeline within a hard
   budget bound; Deal IDs in the result are issued by the seller

WORKFLOW FOR BOOKING:
1. Use request_quotes to compare the market for a product_id
2. Calculate impressions from budget: impressions = (budget / cpm) * 1000
3. Use book_deals with the user's budget to execute the booking
4. Return the seller-issued Deal ID(s) to the user

When a user wants to book a deal, DO IT - use book_deals directly.
Don't just explain how to do it, actually execute the booking.

Be conversational but professional. Ask clarifying questions when needed.
Provide specific, actionable recommendations based on user requirements.""",
            llm=LLM(
                model=settings.default_llm_model,
                temperature=0.7,
            ),
            tools=self._tools,
            verbose=False,
            memory=True,
        )

    def _initialize_sellers(self) -> None:
        """Connect to all configured seller endpoints."""
        endpoints = settings.get_seller_endpoints()

        if not endpoints:
            # Fall back to legacy single endpoint if no sellers configured
            if settings.opendirect_base_url:
                endpoints = [settings.opendirect_base_url]

        # Synchronously check health of each seller
        for url in endpoints:
            seller = SellerConnection(url=url)
            seller.check_health()
            self._sellers.append(seller)

        # Thin wrappers over the canonical booking pipeline. When sellers
        # are configured via SELLER_ENDPOINTS, discovery is seeded from
        # them; otherwise the default registry-backed orchestrator is used.
        if self._sellers:
            self._orchestrator = MultiSellerOrchestrator(
                registry_client=_ConfiguredSellersRegistry(self._sellers),
                deals_client_factory=self._make_deals_client,
            )
        else:
            self._orchestrator = build_default_orchestrator()
        self._tools = [
            RequestQuotesTool(orchestrator=self._orchestrator),
            BookDealsTool(orchestrator=self._orchestrator),
        ]

    @staticmethod
    def _make_deals_client(seller_url: str, **kwargs: Any) -> Any:
        """DealsClient factory for the canonical orchestrator."""
        from ...clients.deals_client import DealsClient

        return DealsClient(seller_url, **kwargs)

    def _get_seller_info(self) -> str:
        """Get formatted info about connected sellers."""
        if not self._sellers:
            return "No sellers configured. Add SELLER_ENDPOINTS to .env"

        lines = []
        for i, seller in enumerate(self._sellers, 1):
            status = "Connected" if seller.connected else f"Failed: {seller.error}"
            caps = (
                ", ".join(seller.capabilities.get("tools", [])[:5])
                if seller.capabilities
                else "N/A"
            )  # noqa: E501
            lines.append(f"{i}. {seller.url}")
            lines.append(f"   Status: {status}")
            if seller.connected:
                lines.append(f"   Tools: {caps}...")

        return "\n".join(lines)

    def process_message(self, user_message: str) -> str:
        """Process a user message and generate a response.

        Args:
            user_message: The user's input message

        Returns:
            The agent's response
        """
        self.conversation_history.append(ConversationMessage(role="user", content=user_message))

        # Build context from conversation history
        history_text = self._format_history()

        # Create task for this conversation turn
        task = Task(
            description=f"""
Conversation History:
{history_text}

Current user message: {user_message}

Respond to the user's message. If they are asking about:

- Searching inventory: Use the search_all_sellers tool to query ALL connected sellers
- Comparing options: Show results from multiple sellers side-by-side
- Checking availability: Use tools to get real data from sellers
- Planning a campaign: Ask about objectives, budget, timeline, and channels
- Booking deals: Explain the OpenDirect process and offer to help
- General questions: Provide helpful, accurate information

Be conversational and helpful. When you use tools, summarize the results
in a user-friendly comparison format. Highlight the best options based on
the user's requirements.
""",
            expected_output="""A helpful, conversational response that:
1. Directly addresses the user's question or request
2. Provides specific information from seller agents when relevant
3. Compares options from multiple sellers when applicable
4. Asks clarifying questions if needed""",
            agent=self._chat_agent,
        )

        # Create crew for this turn
        crew = Crew(
            agents=[self._chat_agent],
            tasks=[task],
            verbose=False,
        )

        # Execute
        result = crew.kickoff()
        response = str(result)

        # Store response
        self.conversation_history.append(ConversationMessage(role="assistant", content=response))

        return response

    def _format_history(self) -> str:
        """Format conversation history for context."""
        if not self.conversation_history:
            return "(No previous messages)"

        # Keep last 10 messages for context
        recent = self.conversation_history[-10:]
        lines = []
        for msg in recent:
            prefix = "User" if msg.role == "user" else "Assistant"
            lines.append(f"{prefix}: {msg.content}")

        return "\n".join(lines)

    def clear_history(self) -> None:
        """Clear conversation history."""
        self.conversation_history = []
        self.context = {}

    def get_summary(self) -> str:
        """Get a summary of the conversation.

        Returns:
            Summary string
        """
        if not self.conversation_history:
            return "No conversation yet."

        msg_count = len(self.conversation_history)
        last_msg = self.conversation_history[-1].content[:50]
        return f"Conversation with {msg_count} messages. Last: {last_msg}..."

    def get_connected_sellers(self) -> list[dict[str, Any]]:
        """Get list of connected sellers.

        Returns:
            List of seller info dicts
        """
        return [
            {
                "url": s.url,
                "name": s.name,
                "connected": s.connected,
                "error": s.error,
                "capabilities": s.capabilities,
            }
            for s in self._sellers
        ]

    async def close(self) -> None:
        """Close the interface (per-seller clients are managed per call)."""
        return None
