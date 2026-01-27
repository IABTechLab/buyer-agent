#!/usr/bin/env python3
# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Protocol switching example.

Demonstrates switching between MCP and A2A protocols within a single workflow.
Use MCP for fast, deterministic operations and A2A for natural language queries.

Usage:
    python examples/protocol_switching.py
"""

import asyncio
from ad_buyer.clients import UnifiedClient, Protocol


async def main():
    async with UnifiedClient(protocol=Protocol.MCP) as client:
        # Connect to both protocols
        await client.connect_both()
        print("Connected to both MCP and A2A protocols")

        # Use MCP for fast, deterministic operations
        products = await client.list_products(protocol=Protocol.MCP)
        print(f"[MCP] Found {len(products.data)} products")

        # Use A2A for natural language queries
        recommendations = await client.send_natural_language(
            "What products would work best for a brand awareness campaign?"
        )
        print(f"[A2A] Recommendations: {recommendations.data}")

        # Mixed workflow: Create entities with MCP, get advice with A2A
        account = await client.create_account(
            name="Mixed Protocol Account",
            protocol=Protocol.MCP  # Fast, direct
        )
        account_id = account.data["id"]
        print(f"[MCP] Created account: {account_id}")

        order = await client.create_order(
            account_id=account_id,
            name="Q1 Brand Campaign",
            budget=75000,
            protocol=Protocol.MCP
        )
        order_id = order.data["id"]
        print(f"[MCP] Created order: {order_id}")

        # Now use A2A for strategic advice
        strategy = await client.send_natural_language(
            f"I just created order {order_id} with a $75,000 budget for brand awareness. "
            "What targeting strategies and inventory types should I consider?"
        )
        print(f"[A2A] Strategy advice: {strategy.data}")


if __name__ == "__main__":
    asyncio.run(main())
