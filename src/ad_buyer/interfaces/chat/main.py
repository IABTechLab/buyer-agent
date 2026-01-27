# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Chat interface for the Ad Buyer System."""

from typing import Any

from crewai import Agent, Crew, LLM, Task

from ...clients.opendirect_client import OpenDirectClient
from ...config.settings import settings
from ...tools.research.avails_check import AvailsCheckTool
from ...tools.research.product_search import ProductSearchTool


class ConversationMessage:
    """A message in the conversation."""

    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content


class ChatInterface:
    """Conversational interface for the ad buyer agent system.

    Uses a dedicated chat agent that can help users with:
    - Searching for inventory
    - Planning campaigns
    - Understanding ad tech concepts
    - Executing bookings (with confirmation)
    """

    def __init__(self):
        """Initialize the chat interface."""
        self.conversation_history: list[ConversationMessage] = []
        self.context: dict[str, Any] = {}

        # Create client and tools
        self._client = OpenDirectClient(
            base_url=settings.opendirect_base_url,
            oauth_token=settings.opendirect_token,
            api_key=settings.opendirect_api_key,
        )

        self._tools = [
            ProductSearchTool(self._client),
            AvailsCheckTool(self._client),
        ]

        # Create chat agent
        self._chat_agent = Agent(
            role="Ad Buying Assistant",
            goal="""Help users plan, execute, and optimize their advertising
campaigns through natural conversation. Understand their needs, provide
recommendations, and execute actions when requested.""",
            backstory="""You are a friendly and knowledgeable advertising
assistant with deep expertise in programmatic advertising, media buying,
and the IAB OpenDirect standards. You can help users:

1. Search for advertising inventory across publishers
2. Check availability and pricing for specific products
3. Plan campaign budgets and channel allocations
4. Understand advertising concepts and best practices
5. Guide them through the booking process

You have access to tools that let you search products and check availability.
When users ask about inventory, use these tools to provide real information.

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

- Searching inventory: Use the search_advertising_products tool to find options
- Checking availability: Use the check_inventory_availability tool
- Planning a campaign: Ask about their objectives, budget, and timeline
- Booking deals: Explain the process and offer to help plan
- General questions: Provide helpful, accurate information

Be conversational and helpful. If you use tools, summarize the results
in a user-friendly way. Ask follow-up questions to better understand
their needs.
""",
            expected_output="""A helpful, conversational response that:
1. Directly addresses the user's question or request
2. Provides specific information or recommendations
3. Uses tool results when relevant
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
