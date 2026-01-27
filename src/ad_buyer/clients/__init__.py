# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Client implementations for ad buyer system."""

from .opendirect_client import OpenDirectClient
from .a2a_client import A2AClient, A2AResponse, A2AError
from .mcp_client import IABMCPClient, MCPToolResult, MCPClientError
from .unified_client import UnifiedClient, UnifiedResult, Protocol
from .ucp_client import UCPClient, UCPExchangeResult


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
]
