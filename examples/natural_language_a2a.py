#!/usr/bin/env python3
# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Natural language A2A usage example.

Demonstrates using the UnifiedClient with A2A protocol for natural language queries.
The A2A protocol allows conversational interaction with the IAB server.

Usage:
    python examples/natural_language_a2a.py
"""

import asyncio
from ad_buyer.clients import UnifiedClient, Protocol


async def main():
    async with UnifiedClient(protocol=Protocol.A2A) as client:
        # Natural language queries
        result = await client.send_natural_language(
            "Find CTV inventory with household targeting under $30 CPM"
        )
        print(f"Response: {result.data}")

        # Ask for product recommendations
        result = await client.send_natural_language(
            "What products would work best for a brand awareness campaign?"
        )
        print(f"Recommendations: {result.data}")

        # Complex multi-step query
        result = await client.send_natural_language(
            "I have a $100,000 budget for Q2. Suggest an optimal split "
            "between CTV, mobile app, and display inventory."
        )
        print(f"Budget allocation: {result.data}")


if __name__ == "__main__":
    asyncio.run(main())
