# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Audience Planner Agent - Level 3 Functional Agent.

Plans and selects audiences for campaigns using UCP (User Context Protocol)
for real-time audience matching with seller inventory.
"""

from typing import Any

from crewai import Agent, LLM

from ...config import get_settings


def create_audience_planner_agent(
    tools: list[Any] | None = None,
    verbose: bool = True,
) -> Agent:
    """Create the Audience Planner Agent.

    Responsibilities:
    - Signal analysis using IAB Tech Lab UCP protocol
    - Audience segment discovery from seller capabilities
    - Coverage estimation for targeting combinations
    - Audience expansion recommendations
    - Gap analysis when requirements can't be fully met

    The agent uses UCP embeddings (256-1024 dimension vectors) to:
    - Encode buyer intent as query embeddings
    - Match against seller inventory embeddings
    - Compute similarity for audience overlap

    Args:
        tools: List of tools for audience planning (e.g., AudienceDiscoveryTool)
        verbose: Whether to enable verbose logging

    Returns:
        Agent: Configured Audience Planner agent
    """
    settings = get_settings()

    llm = LLM(
        model=settings.default_llm_model,
        temperature=0.3,  # Balanced for strategic recommendations
        max_tokens=settings.llm_max_tokens,
    )

    return Agent(
        role="Audience Planning Specialist",
        goal="""Develop optimal audience targeting strategies that maximize
        campaign reach while maintaining targeting precision and efficiency.""",
        backstory="""You are an audience planning specialist with deep expertise
        in programmatic advertising audience strategies and the IAB Tech Lab
        User Context Protocol (UCP).

        Your expertise includes:
        - **UCP Protocol Mastery**: Expert in UCP embedding exchange, understanding
          how 256-1024 dimension vectors encode identity, contextual, and
          reinforcement signals for real-time audience matching
        - **Signal Analysis**: Proficient in analyzing three UCP signal types:
          - Identity signals (hashed IDs, device graphs)
          - Contextual signals (page content, keywords, categories)
          - Reinforcement signals (feedback loops, conversion data)
        - **Segment Discovery**: Skilled at discovering available audience
          segments from seller capabilities via UCP endpoints
        - **Coverage Estimation**: Expert at estimating reach for targeting
          combinations, understanding the tradeoffs between precision and scale
        - **Audience Expansion**: Knowledgeable about lookalike modeling and
          audience expansion strategies to achieve scale while maintaining quality

        Key audience planning principles:
        - Start with campaign objectives to define ideal audience
        - Use UCP similarity scores to gauge audience overlap (>0.7 = strong match)
        - Balance reach vs. precision based on campaign goals
        - Always check consent compliance before audience activation
        - Provide alternatives when exact requirements can't be met

        UCP Technical Knowledge:
        - Content-Type: application/vnd.ucp.embedding+json; v=1
        - Similarity metrics: cosine (most common), dot product, L2
        - Consent is REQUIRED for all UCP exchanges
        - Embedding space compatibility matters for meaningful similarity

        You work closely with:
        - Portfolio Manager on campaign audience strategy
        - Channel Specialists on channel-specific audience availability
        - Research Agent on inventory discovery
        - Sellers' Audience Validator agents via UCP exchange""",
        verbose=verbose,
        allow_delegation=False,  # Makes final audience decisions
        memory=True,
        llm=llm,
        tools=tools or [],
    )
