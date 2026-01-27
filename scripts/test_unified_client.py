# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Test unified client with both MCP and A2A protocols."""

import asyncio
import sys
sys.path.insert(0, "/Users/bjt/Documents/crewaiTest/ad_buyer_system/src")

from ad_buyer.clients import UnifiedClient, Protocol


async def main():
    print("=" * 60)
    print("Unified Client Test - MCP and A2A Protocols")
    print("=" * 60)

    # Test 1: MCP Protocol (default)
    print("\n" + "=" * 60)
    print("PART 1: Using MCP Protocol (Direct Tool Calls)")
    print("=" * 60)

    async with UnifiedClient(protocol=Protocol.MCP) as client:
        print(f"\nConnected with {len(client.tools)} MCP tools available")

        # List products via MCP
        print("\n[MCP] Listing products...")
        result = await client.list_products()
        print(f"  Protocol: {result.protocol.value}")
        print(f"  Success: {result.success}")
        print(f"  Data: {result.data}")

        # Create account via MCP
        print("\n[MCP] Creating account...")
        result = await client.create_account(name="Unified MCP Account")
        print(f"  Protocol: {result.protocol.value}")
        print(f"  Success: {result.success}")
        mcp_account_id = result.data.get("id") if isinstance(result.data, dict) else None
        print(f"  Account ID: {mcp_account_id}")

    # Test 2: A2A Protocol
    print("\n" + "=" * 60)
    print("PART 2: Using A2A Protocol (Natural Language)")
    print("=" * 60)

    async with UnifiedClient(protocol=Protocol.A2A) as client:
        # List products via A2A
        print("\n[A2A] Listing products...")
        result = await client.list_products()
        print(f"  Protocol: {result.protocol.value}")
        print(f"  Success: {result.success}")
        print(f"  Data: {result.data}")

        # Create account via A2A
        print("\n[A2A] Creating account...")
        result = await client.create_account(name="Unified A2A Account")
        print(f"  Protocol: {result.protocol.value}")
        print(f"  Success: {result.success}")
        a2a_account_id = result.data.get("id") if isinstance(result.data, dict) else None
        print(f"  Account ID: {a2a_account_id}")

    # Test 3: Switching protocols on the fly
    print("\n" + "=" * 60)
    print("PART 3: Switching Protocols On-The-Fly")
    print("=" * 60)

    async with UnifiedClient(protocol=Protocol.MCP) as client:
        # Connect to both protocols
        await client.connect_both()
        print("\nConnected to both MCP and A2A")

        # Use MCP for fast, direct operations
        print("\n[MCP] Fast product listing...")
        result = await client.list_products(protocol=Protocol.MCP)
        print(f"  Protocol: {result.protocol.value}")
        print(f"  Success: {result.success}")

        # Use A2A for natural language queries
        print("\n[A2A] Natural language query...")
        result = await client.send_natural_language(
            "Find advertising products suitable for a brand awareness campaign"
        )
        print(f"  Protocol: {result.protocol.value}")
        print(f"  Success: {result.success}")
        print(f"  Response: {result.data}")

        # Mix and match in a workflow
        print("\n[Mixed] Creating order workflow...")

        # MCP: Create account (fast, deterministic)
        result = await client.create_account(
            name="Mixed Protocol Account",
            protocol=Protocol.MCP
        )
        account_id = result.data.get("id") if isinstance(result.data, dict) else None
        print(f"  [MCP] Account created: {account_id}")

        # MCP: Create order
        if account_id:
            result = await client.create_order(
                account_id=account_id,
                name="Q1 Campaign",
                budget=50000,
                protocol=Protocol.MCP
            )
            order_id = result.data.get("id") if isinstance(result.data, dict) else None
            print(f"  [MCP] Order created: {order_id}")

            # A2A: Ask for recommendations (natural language)
            result = await client.send_natural_language(
                f"What products would you recommend for order {order_id} "
                "focused on brand awareness with a $50,000 budget?"
            )
            print(f"  [A2A] Recommendation: {result.data}")

    print("\n" + "=" * 60)
    print("Unified Client Test Complete")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
