#!/usr/bin/env python3
# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Example: DSP Deal Discovery Workflow

This example demonstrates the DSP use case where:
1. Buyer presents identity for tiered pricing
2. Discovers available inventory
3. Gets tier-specific pricing
4. Requests a Deal ID for programmatic activation

The Deal ID can then be entered into traditional DSP platforms
(The Trade Desk, DV360, Amazon DSP, etc.) for campaign activation.

Usage:
    cd ad_buyer_system
    pip install -e .
    python examples/dsp_deal_discovery.py
"""

import asyncio
import sys

try:
    from ad_buyer.clients.unified_client import UnifiedClient
    from ad_buyer.models.buyer_identity import (
        BuyerContext,
        BuyerIdentity,
        DealType,
    )
    from ad_buyer.tools.dsp import DiscoverInventoryTool, GetPricingTool, RequestDealTool
except ImportError as e:
    print(f"Error: {e}")
    print("\nPlease install the ad_buyer package first:")
    print("  cd ad_buyer_system")
    print("  pip install -e .")
    sys.exit(1)


async def demo_tiered_pricing():
    """Demonstrate how identity affects pricing."""
    print("=" * 60)
    print("DEMO: Identity-Based Tiered Pricing")
    print("=" * 60)

    # Different identity configurations
    identities = [
        ("Public (no identity)", BuyerIdentity()),
        ("Seat only", BuyerIdentity(seat_id="ttd-seat-123")),
        ("Agency", BuyerIdentity(
            seat_id="ttd-seat-123",
            agency_id="omnicom-456",
            agency_name="OMD",
        )),
        ("Advertiser", BuyerIdentity(
            seat_id="ttd-seat-123",
            agency_id="omnicom-456",
            agency_name="OMD",
            advertiser_id="coca-cola-789",
            advertiser_name="Coca-Cola",
        )),
    ]

    base_price = 20.00

    print(f"\nBase CPM: ${base_price:.2f}")
    print("-" * 40)

    for name, identity in identities:
        tier = identity.get_access_tier()
        discount = identity.get_discount_percentage()
        final_price = base_price * (1 - discount / 100)

        print(f"\n{name}:")
        print(f"  Tier: {tier.value}")
        print(f"  Discount: {discount}%")
        print(f"  Price: ${final_price:.2f}")


async def demo_dsp_workflow():
    """Demonstrate the full DSP deal discovery workflow."""
    print("\n" + "=" * 60)
    print("DEMO: DSP Deal Discovery Workflow")
    print("=" * 60)

    # Create buyer identity (agency + advertiser for best pricing)
    identity = BuyerIdentity(
        seat_id="ttd-seat-123",
        seat_name="The Trade Desk",
        agency_id="omnicom-456",
        agency_name="OMD",
        agency_holding_company="Omnicom",
        advertiser_id="coca-cola-789",
        advertiser_name="Coca-Cola",
        advertiser_industry="CPG",
    )

    buyer_context = BuyerContext(
        identity=identity,
        is_authenticated=True,
        preferred_deal_types=[DealType.PREFERRED_DEAL],
    )

    print(f"\nBuyer Identity:")
    print(f"  Agency: {identity.agency_name} ({identity.agency_holding_company})")
    print(f"  Advertiser: {identity.advertiser_name}")
    print(f"  Access Tier: {buyer_context.get_access_tier().value}")
    print(f"  Can Negotiate: {buyer_context.can_negotiate()}")
    print(f"  Premium Access: {buyer_context.can_access_premium_inventory()}")

    # Connect to IAB Tech Lab server
    async with UnifiedClient(buyer_identity=identity) as client:
        print("\n" + "-" * 40)
        print("Step 1: Discover Inventory")
        print("-" * 40)

        # Create tools
        discover_tool = DiscoverInventoryTool(
            client=client,
            buyer_context=buyer_context,
        )

        # Discover CTV inventory
        discovery_result = await discover_tool._arun(
            query="CTV streaming inventory",
            channel="ctv",
            max_cpm=30.0,
        )
        print(discovery_result)

        print("\n" + "-" * 40)
        print("Step 2: Get Tiered Pricing")
        print("-" * 40)

        pricing_tool = GetPricingTool(
            client=client,
            buyer_context=buyer_context,
        )

        # Get products first to find an ID
        products_result = await client.list_products()
        if products_result.success and products_result.data:
            products = products_result.data
            if isinstance(products, list) and len(products) > 0:
                product_id = products[0].get("id", "ctv_001")
            else:
                product_id = "ctv_001"

            pricing_result = await pricing_tool._arun(
                product_id=product_id,
                volume=5_000_000,
                deal_type="PD",
                flight_start="2026-02-01",
                flight_end="2026-02-28",
            )
            print(pricing_result)

            print("\n" + "-" * 40)
            print("Step 3: Request Deal ID")
            print("-" * 40)

            deal_tool = RequestDealTool(
                client=client,
                buyer_context=buyer_context,
            )

            deal_result = await deal_tool._arun(
                product_id=product_id,
                deal_type="PD",
                impressions=5_000_000,
                flight_start="2026-02-01",
                flight_end="2026-02-28",
            )
            print(deal_result)
        else:
            print("No products available from server.")


async def demo_unified_client_dsp_methods():
    """Demonstrate using UnifiedClient's DSP methods directly."""
    print("\n" + "=" * 60)
    print("DEMO: UnifiedClient DSP Methods")
    print("=" * 60)

    # Create identity
    identity = BuyerIdentity(
        agency_id="mediacom-123",
        agency_name="MediaCom",
        advertiser_id="nike-456",
        advertiser_name="Nike",
    )

    # Create client with identity
    async with UnifiedClient(buyer_identity=identity) as client:
        print(f"\nAccess Tier: {client.get_access_tier()}")

        # Discover inventory
        print("\n--- Discovering Inventory ---")
        result = await client.discover_inventory(
            channel="display",
            max_cpm=25.0,
        )
        if result.success:
            print(f"Found inventory: {result.data}")
        else:
            print(f"Discovery error: {result.error}")

        # Get products for pricing demo
        products = await client.list_products()
        if products.success and products.data:
            product_list = products.data if isinstance(products.data, list) else [products.data]
            if product_list:
                product_id = product_list[0].get("id", "prod_001")

                # Get pricing
                print("\n--- Getting Tiered Pricing ---")
                pricing = await client.get_pricing(
                    product_id=product_id,
                    volume=2_000_000,
                )
                if pricing.success and pricing.data:
                    data = pricing.data
                    print(f"Product: {data.get('name', 'Unknown')}")
                    if "pricing" in data:
                        p = data["pricing"]
                        print(f"Base Price: ${p.get('base_price', 0):.2f}")
                        print(f"Tier: {p.get('tier', 'unknown')}")
                        print(f"Tier Discount: {p.get('tier_discount', 0)}%")
                        print(f"Tiered Price: ${p.get('tiered_price', 0):.2f}")

                # Request deal
                print("\n--- Requesting Deal ID ---")
                deal = await client.request_deal(
                    product_id=product_id,
                    deal_type="PD",
                    impressions=2_000_000,
                    flight_start="2026-03-01",
                    flight_end="2026-03-31",
                )
                if deal.success and deal.data:
                    d = deal.data
                    print(f"Deal ID: {d.get('deal_id')}")
                    print(f"Price: ${d.get('price', 0):.2f} CPM")
                    print(f"Tier: {d.get('access_tier')}")
                    print("\nActivation Instructions:")
                    for platform, instruction in d.get("activation_instructions", {}).items():
                        print(f"  {platform.upper()}: {instruction}")


async def main():
    """Run all demos."""
    print("\n" + "#" * 60)
    print("# DSP Deal Discovery - Example Usage")
    print("#" * 60)

    # Demo 1: Show how identity affects pricing
    await demo_tiered_pricing()

    # Demo 2: Full DSP workflow with tools
    await demo_dsp_workflow()

    # Demo 3: Using UnifiedClient DSP methods
    await demo_unified_client_dsp_methods()

    print("\n" + "#" * 60)
    print("# Demo Complete")
    print("#" * 60)


if __name__ == "__main__":
    asyncio.run(main())
