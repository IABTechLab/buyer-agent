# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Models for agent registry discovery.

Provides data models for:
- AgentCard: represents a discovered agent (seller or buyer)
- AgentCapability: a declared capability of an agent
- AgentTrustInfo: trust verification result from the registry
- TrustLevel: trust status enum
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TrustLevel(str, Enum):
    """Trust level of an agent in the registry.

    Determines how much access and data an agent receives.
    """

    UNKNOWN = "unknown"  # Not found in any registry
    REGISTERED = "registered"  # Found in AAMP registry
    VERIFIED = "verified"  # Verified by registry operator
    PREFERRED = "preferred"  # Strategic partner
    BLOCKED = "blocked"  # Explicitly blocked


class AgentCapability(BaseModel):
    """A declared capability of an agent.

    Capabilities describe what an agent can do, such as serving
    CTV inventory, display ads, or supporting specific deal types.
    """

    name: str
    description: str
    tags: list[str] = Field(default_factory=list)


class AgentCard(BaseModel):
    """Represents a discovered agent in the registry.

    An agent card contains the identity, connection details,
    supported protocols, and capabilities of a seller or buyer agent.
    Modeled after the A2A agent card served at .well-known/agent.json.
    """

    agent_id: str
    name: str
    url: str
    protocols: list[str] = Field(default_factory=list)
    capabilities: list[AgentCapability] = Field(default_factory=list)
    trust_level: TrustLevel = TrustLevel.UNKNOWN


class AgentTrustInfo(BaseModel):
    """Result of verifying an agent's trust status in the registry."""

    agent_url: str
    is_registered: bool
    trust_level: TrustLevel = TrustLevel.UNKNOWN
    registry_id: Optional[str] = None
