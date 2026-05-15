# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Client implementations for ad buyer system."""

from .a2a_client import A2AClient, A2AError, A2AResponse
from .deals_client import DealsClient, DealsClientError
from .mcp_client import IABMCPClient, MCPClientError, MCPToolResult
from .opendirect_client import OpenDirectClient
from .sgp_client import SGPAuthError, SGPClient, SGPClientError
from .ucp_client import UCPClient, UCPExchangeResult
from .unified_client import Protocol, UnifiedClient, UnifiedResult

__all__ = [
    # Unified client (recommended) - supports both MCP and A2A
    "UnifiedClient",
    "UnifiedResult",
    "Protocol",
    # REST client for local mock server
    "OpenDirectClient",
    # A2A client for IAB hosted server (natural language)
    "A2AClient",
    "A2AResponse",
    "A2AError",
    # MCP client for IAB hosted server (direct tool calls)
    "IABMCPClient",
    "MCPToolResult",
    "MCPClientError",
    # UCP client for audience exchange
    "UCPClient",
    "UCPExchangeResult",
    # IAB Deals API v1.0 client (quote-then-book flow)
    "DealsClient",
    "DealsClientError",
    # IAB Diligence Platform (SGP) approval gate
    "SGPClient",
    "SGPClientError",
    "SGPAuthError",
]
