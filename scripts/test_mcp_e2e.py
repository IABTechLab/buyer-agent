# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""End-to-end test of MCP client against IAB hosted servers."""

import asyncio
import sys
sys.path.insert(0, "/Users/bjt/Documents/crewaiTest/ad_buyer_system/src")

from ad_buyer.clients.mcp_client import IABMCPClient, MCPToolResult, MCPClientError


async def main():
    print("=" * 60)
    print("MCP Client End-to-End Test with IAB Hosted Servers")
    print("=" * 60)

    async with IABMCPClient(
        base_url="https://agentic-direct-server-hwgrypmndq-uk.a.run.app",
    ) as client:
        # Test 1: Check available tools
        print(f"\n[1] Available MCP Tools: {len(client.tools)}")
        for name in sorted(client.tools.keys())[:8]:
            print(f"    - {name}")
        print(f"    ... and {len(client.tools) - 8} more")

        # Test 2: List products
        print("\n[2] Testing: list_products...")
        result = await client.list_products()
        print(f"  Success: {result.success}")
        if result.success:
            print(f"  Data: {result.data}")
        else:
            print(f"  Error: {result.error}")

        # Test 3: Create an account
        print("\n[3] Testing: create_account...")
        result = await client.create_account(
            name="MCP E2E Test Advertiser",
            account_type="advertiser",
        )
        print(f"  Success: {result.success}")
        if result.success:
            print(f"  Data: {result.data}")
            account_id = result.data.get("id") if isinstance(result.data, dict) else None
        else:
            print(f"  Error: {result.error}")
            account_id = None

        # Test 4: Create an order
        print("\n[4] Testing: create_order...")
        if account_id:
            result = await client.create_order(
                account_id=account_id,
                name="Q1 2025 MCP Test Campaign",
                budget=25000.0,
                start_date="2025-02-01",
                end_date="2025-02-28",
            )
            print(f"  Success: {result.success}")
            if result.success:
                print(f"  Data: {result.data}")
                order_id = result.data.get("id") if isinstance(result.data, dict) else None
            else:
                print(f"  Error: {result.error}")
                order_id = None
        else:
            print("  Skipped (no account)")
            order_id = None

        # Test 5: Create a line item
        print("\n[5] Testing: create_line...")
        if order_id:
            result = await client.create_line(
                order_id=order_id,
                product_id="mock-product",
                name="Display Banner Line",
                quantity=500000,
                start_date="2025-02-01",
                end_date="2025-02-28",
            )
            print(f"  Success: {result.success}")
            if result.success:
                print(f"  Data: {result.data}")
            else:
                print(f"  Error: {result.error}")
        else:
            print("  Skipped (no order)")

    print("\n" + "=" * 60)
    print("MCP End-to-End Test Complete")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
