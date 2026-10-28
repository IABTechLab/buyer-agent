#!/usr/bin/env python3
# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Natural language A2A usage example.

Demonstrates using the UnifiedClient with A2A protocol for natural language queries.
The A2A protocol allows conversational interaction with the IAB server.

Usage:
    python examples/natural_language_a2a.py

Set SELLER_BASE_URL to use a server other than http://localhost:8000.
"""

import asyncio
import os

from ad_buyer.clients import Protocol, UnifiedClient

SELLER_BASE_URL = os.getenv("SELLER_BASE_URL", "http://localhost:8000")


async def main():
    async with UnifiedClient(base_url=SELLER_BASE_URL, protocol=Protocol.A2A) as client:
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
