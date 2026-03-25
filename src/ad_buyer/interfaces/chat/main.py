# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Chat interface for the Ad Buyer System.

Supports connecting to multiple seller agents via MCP/A2A protocols.
Each seller should implement IAB Tech Lab OpenDirect/AdCOM standards.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

from crewai import Agent, Crew, LLM, Task
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...clients.mcp_client import SimpleMCPClient
from ...config.settings import settings


class ConversationMessage:
    """A message in the conversation."""

    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content


@dataclass
class SellerConnection:
    """Connection to a seller agent."""

    url: str
    name: str = ""
    client: Optional[SimpleMCPClient] = None
    capabilities: dict[str, Any] = field(default_factory=dict)
    connected: bool = False
    error: str = ""
    _initialized: bool = False

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

    async def ensure_client(self) -> Optional[SimpleMCPClient]:
        """Lazily create client on first use."""
        if self.client is None and self.connected:
            self.client = SimpleMCPClient(base_url=self.url)
            # Don't call connect() since we already checked health
        return self.client

    async def close(self) -> None:
        """Close the connection."""
        if self.client:
            await self.client.close()
            self.client = None


class MultiSellerSearchInput(BaseModel):
    """Input for searching across multiple sellers."""

    query: str = Field(default="", description="Natural language search query")
    channel: str = Field(default="", description="Channel filter: ctv, display, video, mobile")
    max_cpm: float = Field(default=0, description="Maximum CPM price filter")


class MultiSellerSearchTool(BaseTool):
    """Tool to search inventory across all connected sellers."""

    name: str = "search_all_sellers"
    description: str = """Search for advertising inventory across ALL connected seller agents.
    Use this to find products, check availability, and compare offerings from multiple publishers/SSPs.
    Returns aggregated results from all sellers."""
    args_schema: type[BaseModel] = MultiSellerSearchInput

    def __init__(self, sellers: list[SellerConnection], **kwargs):
        super().__init__(**kwargs)
        self._sellers = sellers

    def _run(self, query: str = "", channel: str = "", max_cpm: float = 0) -> str:
        """Synchronous wrapper."""
        return asyncio.get_event_loop().run_until_complete(
            self._arun(query, channel, max_cpm)
        )

    async def _arun(self, query: str = "", channel: str = "", max_cpm: float = 0) -> str:
        """Search all sellers asynchronously."""
        results = []

        for seller in self._sellers:
            if not seller.connected:
                continue

            try:
                # Lazily create client
                client = await seller.ensure_client()
                if not client:
                    continue

                # Use list_products from SimpleMCPClient
                result = await client.list_products()

                if result.success and result.data:
                    products = result.data
                    # Handle nested result structure
                    if isinstance(products, dict) and "products" in products:
                        products = products["products"]

                    # Apply filters if specified
                    if channel and isinstance(products, list):
                        products = [p for p in products if p.get("channel", "").lower() == channel.lower()]
                    if max_cpm > 0 and isinstance(products, list):
                        products = [p for p in products if p.get("base_cpm", p.get("floor_cpm", 0)) <= max_cpm]

                    results.append({
                        "seller": seller.name,
                        "url": seller.url,
                        "products": products,
                    })
            except (OSError, ValueError, KeyError) as e:
                results.append({
                    "seller": seller.name,
                    "url": seller.url,
                    "error": str(e),
                })

        if not results:
            return "No sellers connected. Configure SELLER_ENDPOINTS in .env"

        # Format results
        output = [f"Found inventory from {len(results)} seller(s):\n"]
        for r in results:
            output.append(f"\n=== {r['seller']} ===")
            if "error" in r:
                output.append(f"  Error: {r['error']}")
            elif "products" in r:
                products = r["products"]
                if isinstance(products, list):
                    for p in products[:5]:  # Limit to 5 per seller
                        name = p.get("name", "Unknown")
                        # Try various price field names
                        price = p.get("base_cpm", p.get("floor_cpm", p.get("basePrice", p.get("price", "N/A"))))
                        channel = p.get("channel", "")
                        publisher = p.get("publisher", "")
                        avail = p.get("available_impressions", 0)
                        avail_str = f"{avail/1_000_000:.0f}M" if avail else ""
                        output.append(f"  - {name} | {publisher} | {channel} | ${price} CPM | {avail_str} avail")
                else:
                    output.append(f"  {products}")

        return "\n".join(output)


class CallSellerToolInput(BaseModel):
    """Input for calling any tool on a seller."""

    seller_name: str = Field(..., description="Name of the seller agent (e.g., 'Publisher Seller Agent')")
    tool_name: str = Field(..., description="Name of the tool to call (e.g., 'book_programmatic_guaranteed')")
    arguments: str = Field(default="{}", description="JSON string of arguments to pass to the tool")


class CallSellerToolTool(BaseTool):
    """Tool to call any tool on any connected seller agent."""

    name: str = "call_seller_tool"
    description: str = """Call any tool on a specific seller agent. Use this to:
    - Book programmatic guaranteed deals (book_programmatic_guaranteed)
    - Create PMP deals (create_pmp_deal)
    - Check availability (check_availability)
    - Get pricing (get_pricing)
    - Create campaigns (create_performance_campaign, create_mobile_campaign)
    - Attach deals to DSP (attach_deal)

    First use search_all_sellers to find inventory and seller names, then use this tool to execute actions."""
    args_schema: type[BaseModel] = CallSellerToolInput

    def __init__(self, sellers: list[SellerConnection], **kwargs):
        super().__init__(**kwargs)
        self._sellers = sellers

    def _run(self, seller_name: str, tool_name: str, arguments: str = "{}") -> str:
        """Synchronous wrapper."""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(self._arun(seller_name, tool_name, arguments))

    async def _arun(self, seller_name: str, tool_name: str, arguments: str = "{}") -> str:
        """Call a tool on a specific seller."""
        import json as json_module

        # Find the seller
        seller = None
        for s in self._sellers:
            if seller_name.lower() in s.name.lower() or seller_name.lower() in s.url.lower():
                seller = s
                break

        if not seller:
            available = [s.name for s in self._sellers if s.connected]
            return f"Seller '{seller_name}' not found. Available sellers: {', '.join(available)}"

        if not seller.connected:
            return f"Seller '{seller.name}' is not connected: {seller.error}"

        # Parse arguments
        try:
            args = json_module.loads(arguments) if arguments else {}
        except json_module.JSONDecodeError as e:
            return f"Invalid JSON arguments: {e}"

        # Get client and call tool
        try:
            client = await seller.ensure_client()
            if not client:
                return f"Could not connect to seller '{seller.name}'"

            result = await client.call_tool(tool_name, args)

            if result.success:
                return f"SUCCESS - {seller.name} - {tool_name}:\n{json_module.dumps(result.data, indent=2)}"
            else:
                return f"FAILED - {seller.name} - {tool_name}: {result.error}"
        except (OSError, ValueError, RuntimeError) as e:
            return f"Error calling {tool_name} on {seller.name}: {e}"


class BookPGDealInput(BaseModel):
    """Input for booking a Programmatic Guaranteed deal."""

    seller_name: str = Field(..., description="Name of the seller agent")
    product_id: str = Field(..., description="Product ID to book (e.g., 'ctv-hbo-max-001')")
    impressions: int = Field(..., description="Number of impressions to book")
    cpm_price: float = Field(..., description="CPM price for the deal")
    start_date: str = Field(default="", description="Start date (YYYY-MM-DD)")
    end_date: str = Field(default="", description="End date (YYYY-MM-DD)")
    advertiser_name: str = Field(default="Demo Advertiser", description="Advertiser name")
    campaign_name: str = Field(default="Demo Campaign", description="Campaign name")


class BookPGDealTool(BaseTool):
    """Tool to book a Programmatic Guaranteed deal with a seller."""

    name: str = "book_pg_deal"
    description: str = """Book a Programmatic Guaranteed (PG) deal with a seller agent.
    PG deals have fixed pricing and guaranteed delivery.
    Use search_all_sellers first to find products and their IDs."""
    args_schema: type[BaseModel] = BookPGDealInput

    def __init__(self, sellers: list[SellerConnection], **kwargs):
        super().__init__(**kwargs)
        self._sellers = sellers

    def _run(self, seller_name: str, product_id: str, impressions: int, cpm_price: float,
             start_date: str = "", end_date: str = "", advertiser_name: str = "Demo Advertiser",
             campaign_name: str = "Demo Campaign") -> str:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(
            self._arun(seller_name, product_id, impressions, cpm_price, start_date, end_date, advertiser_name, campaign_name)
        )

    async def _arun(self, seller_name: str, product_id: str, impressions: int, cpm_price: float,
                    start_date: str = "", end_date: str = "", advertiser_name: str = "Demo Advertiser",
                    campaign_name: str = "Demo Campaign") -> str:
        import json as json_module
        from datetime import datetime, timedelta

        # Find seller
        seller = None
        for s in self._sellers:
            if seller_name.lower() in s.name.lower() or seller_name.lower() in s.url.lower():
                seller = s
                break

        if not seller or not seller.connected:
            return f"Seller '{seller_name}' not found or not connected"

        # Default dates
        if not start_date:
            start_date = datetime.now().strftime("%Y-%m-%d")
        if not end_date:
            end_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

        # Build arguments
        args = {
            "product_id": product_id,
            "impressions": impressions,
            "cpm_price": cpm_price,
            "start_date": start_date,
            "end_date": end_date,
            "advertiser_name": advertiser_name,
            "campaign_name": campaign_name,
        }

        try:
            client = await seller.ensure_client()
            result = await client.call_tool("book_programmatic_guaranteed", args)

            if result.success:
                return f"✓ PG DEAL BOOKED SUCCESSFULLY!\n\nSeller: {seller.name}\nProduct: {product_id}\nImpressions: {impressions:,}\nCPM: ${cpm_price}\nTotal Cost: ${(impressions/1000)*cpm_price:,.2f}\n\nBooking Details:\n{json_module.dumps(result.data, indent=2)}"
            else:
                return f"✗ Failed to book PG deal: {result.error}"
        except (OSError, ValueError, RuntimeError) as e:
            return f"Error booking PG deal: {e}"


class CreatePMPDealInput(BaseModel):
    """Input for creating a PMP deal."""

    seller_name: str = Field(..., description="Name of the seller agent")
    product_id: str = Field(..., description="Product ID")
    floor_price: float = Field(..., description="Floor CPM price")
    impressions: int = Field(default=0, description="Expected impressions")
    buyer_seat_id: str = Field(default="buyer-seat-001", description="Buyer's DSP seat ID")


class CreatePMPDealTool(BaseTool):
    """Tool to create a Private Marketplace deal."""

    name: str = "create_pmp_deal"
    description: str = """Create a Private Marketplace (PMP) deal with a seller.
    Returns a Deal ID that can be used in DSP platforms."""
    args_schema: type[BaseModel] = CreatePMPDealInput

    def __init__(self, sellers: list[SellerConnection], **kwargs):
        super().__init__(**kwargs)
        self._sellers = sellers

    def _run(self, seller_name: str, product_id: str, floor_price: float,
             impressions: int = 0, buyer_seat_id: str = "buyer-seat-001") -> str:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(
            self._arun(seller_name, product_id, floor_price, impressions, buyer_seat_id)
        )

    async def _arun(self, seller_name: str, product_id: str, floor_price: float,
                    impressions: int = 0, buyer_seat_id: str = "buyer-seat-001") -> str:
        import json as json_module

        # Find seller
        seller = None
        for s in self._sellers:
            if seller_name.lower() in s.name.lower():
                seller = s
                break

        if not seller or not seller.connected:
            return f"Seller '{seller_name}' not found or not connected"

        args = {
            "product_id": product_id,
            "floor_price": floor_price,
            "impressions": impressions,
            "buyer_seat_id": buyer_seat_id,
        }

        try:
            client = await seller.ensure_client()
            result = await client.call_tool("create_pmp_deal", args)

            if result.success:
                deal_data = result.data
                deal_id = deal_data.get("deal", {}).get("deal_id", "N/A") if isinstance(deal_data, dict) else "N/A"
                return f"✓ PMP DEAL CREATED!\n\nDeal ID: {deal_id}\nSeller: {seller.name}\nProduct: {product_id}\nFloor: ${floor_price} CPM\n\nFull Details:\n{json_module.dumps(deal_data, indent=2)}"
            else:
                return f"✗ Failed to create PMP deal: {result.error}"
        except (OSError, ValueError, RuntimeError) as e:
            return f"Error creating PMP deal: {e}"


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
1. **search_all_sellers** - Search inventory across ALL connected sellers
2. **book_pg_deal** - Book Programmatic Guaranteed deals directly with sellers
3. **create_pmp_deal** - Create Private Marketplace deals and get Deal IDs
4. **call_seller_tool** - Call any tool on any seller (for advanced operations)

WORKFLOW FOR BOOKING:
1. Use search_all_sellers to find products and get their product_id
2. Calculate impressions from budget: impressions = (budget / cpm) * 1000
3. Use book_pg_deal or create_pmp_deal to execute the booking
4. Return the confirmation to the user

When a user wants to book a deal, DO IT - use the booking tools directly.
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

        # Create tools for connected sellers
        self._tools = [
            MultiSellerSearchTool(sellers=self._sellers),
            CallSellerToolTool(sellers=self._sellers),
            BookPGDealTool(sellers=self._sellers),
            CreatePMPDealTool(sellers=self._sellers),
        ]

    def _get_seller_info(self) -> str:
        """Get formatted info about connected sellers."""
        if not self._sellers:
            return "No sellers configured. Add SELLER_ENDPOINTS to .env"

        lines = []
        for i, seller in enumerate(self._sellers, 1):
            status = "Connected" if seller.connected else f"Failed: {seller.error}"
            caps = ", ".join(seller.capabilities.get("tools", [])[:5]) if seller.capabilities else "N/A"
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
        self.conversation_history.append(
            ConversationMessage(role="user", content=user_message)
        )

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
        self.conversation_history.append(
            ConversationMessage(role="assistant", content=response)
        )

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
        """Close all seller connections."""
        for seller in self._sellers:
            await seller.close()
