# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""A2A (Agent-to-Agent) client for IAB agentic-direct server."""

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx


@dataclass
class A2AResponse:
    """Parsed response from A2A server."""

    raw: dict[str, Any]
    text: str = ""
    data: list[dict[str, Any]] = field(default_factory=list)
    task_id: str = ""
    context_id: str = ""
    success: bool = True
    error: str = ""

    @classmethod
    def from_result(cls, result: dict[str, Any]) -> "A2AResponse":
        """Parse A2A JSON-RPC result into structured response."""
        response = cls(raw=result)

        # Check for error
        if "error" in result:
            response.success = False
            response.error = result["error"].get("message", "Unknown error")
            return response

        # Extract from result
        msg = result.get("result", {})
        response.task_id = msg.get("taskId", "")
        response.context_id = msg.get("contextId", "")

        # Extract parts (text and data)
        parts = msg.get("parts", [])
        text_parts = []
        for part in parts:
            kind = part.get("kind", "")
            if kind == "text":
                text_parts.append(part.get("text", ""))
            elif kind == "data":
                response.data.append(part.get("data", {}))

        response.text = "\n".join(text_parts)
        return response


class A2AClient:
    """Client for A2A v0.3.0 protocol used by IAB agentic-direct.

    The A2A protocol uses JSON-RPC 2.0 over HTTP with natural language
    messages that are processed by AI to select and execute MCP tools.

    The IAB server returns responses synchronously (not as tasks to poll).
    """

    def __init__(
        self,
        base_url: str = "https://agentic-direct-server-hwgrypmndq-uk.a.run.app",
        agent_type: str = "buyer",
        timeout: float = 60.0,
    ):
        """Initialize the A2A client.

        Args:
            base_url: Base URL for the A2A server
            agent_type: Type of agent to use ('buyer' or 'seller')
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.agent_type = agent_type
        self.jsonrpc_url = f"{self.base_url}/a2a/{agent_type}/jsonrpc"
        self.agent_card_url = f"{self.base_url}/a2a/{agent_type}/.well-known/agent-card.json"
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )
        self._context_id: str = ""  # For multi-turn conversations

    async def get_agent_card(self) -> dict[str, Any]:
        """Get the agent card with capabilities and skills.

        Returns:
            Agent card JSON with name, skills, capabilities, etc.
        """
        response = await self._client.get(self.agent_card_url)
        response.raise_for_status()
        return response.json()

    async def get_mcp_info(self) -> dict[str, Any]:
        """Get MCP server info including available tools.

        Returns:
            MCP info with tool list
        """
        response = await self._client.get(f"{self.base_url}/mcp/info")
        response.raise_for_status()
        return response.json()

    async def send_message(self, message: str, context_id: str = None) -> A2AResponse:
        """Send a natural language message to the agent.

        The agent will use AI to interpret the message and execute
        appropriate MCP tools. The IAB server returns responses synchronously.

        Args:
            message: Natural language request
            context_id: Optional context ID to continue an existing conversation

        Returns:
            A2AResponse with parsed text and data
        """
        request_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())

        params: dict[str, Any] = {
            "message": {
                "messageId": message_id,
                "role": "user",
                "parts": [{"kind": "text", "text": message}],
            }
        }

        # Include context ID if continuing a conversation
        ctx = context_id or self._context_id
        if ctx:
            params["contextId"] = ctx

        payload = {
            "jsonrpc": "2.0",
            "method": "message/send",
            "params": params,
            "id": request_id,
        }

        response = await self._client.post(self.jsonrpc_url, json=payload)
        response.raise_for_status()
        result = response.json()

        parsed = A2AResponse.from_result(result)

        # Store context for multi-turn conversations
        if parsed.context_id:
            self._context_id = parsed.context_id

        if not parsed.success:
            raise A2AError(parsed.error)

        return parsed

    async def send_raw(self, message: str, context_id: str = None) -> dict[str, Any]:
        """Send a message and return raw JSON-RPC response.

        Args:
            message: Natural language request
            context_id: Optional context ID

        Returns:
            Raw JSON-RPC response dict
        """
        request_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())

        params: dict[str, Any] = {
            "message": {
                "messageId": message_id,
                "role": "user",
                "parts": [{"kind": "text", "text": message}],
            }
        }

        ctx = context_id or self._context_id
        if ctx:
            params["contextId"] = ctx

        payload = {
            "jsonrpc": "2.0",
            "method": "message/send",
            "params": params,
            "id": request_id,
        }

        response = await self._client.post(self.jsonrpc_url, json=payload)
        response.raise_for_status()
        result = response.json()

        # Update context
        ctx_id = result.get("result", {}).get("contextId")
        if ctx_id:
            self._context_id = ctx_id

        return result

    async def list_products(self) -> A2AResponse:
        """List available advertising products."""
        return await self.send_message("List all available advertising products")

    async def search_products(self, criteria: str) -> A2AResponse:
        """Search for products matching criteria.

        Args:
            criteria: Search criteria in natural language
        """
        return await self.send_message(f"Search for advertising products: {criteria}")

    async def create_account(self, name: str, advertiser_id: str) -> A2AResponse:
        """Create a new account.

        Args:
            name: Account name
            advertiser_id: Advertiser organization ID
        """
        return await self.send_message(
            f"Create an account named '{name}' for advertiser {advertiser_id}"
        )

    async def create_order(
        self,
        account_id: str,
        name: str,
        budget: float,
        start_date: str,
        end_date: str,
    ) -> A2AResponse:
        """Create a new order.

        Args:
            account_id: Account ID
            name: Order name
            budget: Budget in USD
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
        """
        return await self.send_message(
            f"Create an order named '{name}' for account {account_id} "
            f"with budget ${budget:,.2f} from {start_date} to {end_date}"
        )

    async def create_line(
        self,
        order_id: str,
        product_id: str,
        name: str,
        quantity: int,
        start_date: str,
        end_date: str,
    ) -> A2AResponse:
        """Create a new line item.

        Args:
            order_id: Order ID
            product_id: Product ID
            name: Line name
            quantity: Impressions/quantity to book
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
        """
        return await self.send_message(
            f"Create a line item named '{name}' for order {order_id} "
            f"using product {product_id} with {quantity:,} impressions "
            f"from {start_date} to {end_date}"
        )

    async def book_line(self, line_id: str) -> A2AResponse:
        """Book a line item (confirm the reservation).

        Args:
            line_id: Line ID to book
        """
        return await self.send_message(f"Book line item {line_id}")

    async def check_availability(
        self,
        product_id: str,
        quantity: int,
        start_date: str = None,
        end_date: str = None,
    ) -> A2AResponse:
        """Check product availability.

        Args:
            product_id: Product ID
            quantity: Requested impressions
            start_date: Optional start date
            end_date: Optional end date
        """
        msg = f"Check availability for product {product_id} with {quantity:,} impressions"
        if start_date and end_date:
            msg += f" from {start_date} to {end_date}"
        return await self.send_message(msg)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> "A2AClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


class A2AError(Exception):
    """Error from A2A protocol."""

    pass
