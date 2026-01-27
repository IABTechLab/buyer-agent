#!/usr/bin/env python3
# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Individual clients example.

Demonstrates using IABMCPClient and A2AClient directly instead of UnifiedClient.
Use this approach when you need lower-level control over the protocol clients.

Usage:
    python examples/individual_clients.py
"""

import asyncio
from ad_buyer.clients import IABMCPClient, A2AClient


async def mcp_example():
    """Direct MCP client usage."""
    print("=" * 50)
    print("MCP Client - Direct Tool Access")
    print("=" * 50)

    async with IABMCPClient() as client:
        # See all available tools
        print(f"Available tools: {len(client.tools)}")
        for tool in client.tools[:5]:  # Show first 5
            print(f"  - {tool.name}: {tool.description[:50]}...")

        # Call tools directly
        result = await client.call_tool("list_products", {})
        print(f"\nProducts: {result}")

        result = await client.call_tool("create_account", {"name": "Direct MCP Account"})
        print(f"Created account: {result}")


async def a2a_example():
    """Direct A2A client usage."""
    print("\n" + "=" * 50)
    print("A2A Client - Natural Language")
    print("=" * 50)

    async with A2AClient() as client:
        # Send natural language messages
        response = await client.send_message("List available products")
        print(f"Response text: {response.text}")
        print(f"Response data: {response.data}")

        response = await client.send_message(
            "Create an account for Acme Corporation"
        )
        print(f"Account creation: {response.text}")


async def main():
    await mcp_example()
    await a2a_example()


if __name__ == "__main__":
    asyncio.run(main())
