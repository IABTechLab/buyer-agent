#!/usr/bin/env python3
# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Basic MCP usage example.

Demonstrates using the UnifiedClient with MCP protocol for direct tool calls.
This is the fastest and most deterministic way to interact with the IAB server.

Usage:
    python examples/basic_mcp_usage.py
"""

import asyncio
from ad_buyer.clients import UnifiedClient, Protocol


async def main():
    # MCP mode - direct tool calls (faster, deterministic)
    async with UnifiedClient(protocol=Protocol.MCP) as client:
        # List available products
        result = await client.list_products()
        print(f"Products: {result.data}")

        # Create an account
        result = await client.create_account(name="My Advertiser")
        account_id = result.data["id"]
        print(f"Created account: {account_id}")

        # Create an order
        result = await client.create_order(
            account_id=account_id,
            name="Q1 Campaign",
            budget=50000
        )
        order_id = result.data["id"]
        print(f"Created order: {order_id}")

        # List orders for the account
        result = await client.list_orders(account_id=account_id)
        print(f"Orders: {result.data}")


if __name__ == "__main__":
    asyncio.run(main())
