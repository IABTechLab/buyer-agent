# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""End-to-end test of A2A client against IAB hosted servers."""

import asyncio
import json
import sys
sys.path.insert(0, "/Users/bjt/Documents/crewaiTest/ad_buyer_system/src")

from ad_buyer.clients.a2a_client import A2AClient, A2AError, A2AResponse


async def main():
    print("=" * 60)
    print("A2A Client End-to-End Test with IAB Hosted Servers")
    print("=" * 60)

    async with A2AClient(
        base_url="https://agentic-direct-server-hwgrypmndq-uk.a.run.app",
        agent_type="buyer",
        timeout=120.0,
    ) as client:
        # Test 1: Get MCP Info
        print("\n[1] Testing MCP Info endpoint...")
        try:
            mcp_info = await client.get_mcp_info()
            print(f"  MCP Info retrieved")
            print(f"  Server: {mcp_info.get('name', 'unknown')}")
            print(f"  Tools: {mcp_info.get('tools', 'N/A')}")
            tools_list = mcp_info.get('toolsList', [])
            print(f"  Available tools: {', '.join(tools_list[:8])}...")
        except Exception as e:
            print(f"  Error: {e}")

        # Test 2: Get Agent Card
        print("\n[2] Testing Agent Card endpoint...")
        try:
            agent_card = await client.get_agent_card()
            print(f"  Name: {agent_card.get('name', 'unknown')}")
            skills = agent_card.get('skills', [])
            print(f"  Skills: {len(skills)}")
            for skill in skills:
                print(f"    - {skill.get('name', 'unknown')}")
        except Exception as e:
            print(f"  Error: {e}")

        # Test 3: List products
        print("\n[3] Testing: List Products...")
        try:
            response = await client.list_products()
            print(f"  Success: {response.success}")
            print(f"  Text: {response.text}")
            print(f"  Data items: {len(response.data)}")
            for item in response.data[:3]:
                print(f"    - {item.get('name', item.get('id', 'unknown'))}: {item}")
        except A2AError as e:
            print(f"  A2A Error: {e}")
        except Exception as e:
            print(f"  Error: {e}")
            import traceback
            traceback.print_exc()

        # Test 4: Create an account
        print("\n[4] Testing: Create Account...")
        try:
            response = await client.create_account(
                name="Test Advertiser Account",
                advertiser_id="adv_test_001"
            )
            print(f"  Success: {response.success}")
            print(f"  Text: {response.text}")
            print(f"  Data: {response.data}")
            account_id = response.data[0].get("id") if response.data else None
            print(f"  Account ID: {account_id}")
        except A2AError as e:
            print(f"  A2A Error: {e}")
            account_id = None
        except Exception as e:
            print(f"  Error: {e}")
            account_id = None

        # Test 5: Create an order (if we have an account)
        print("\n[5] Testing: Create Order...")
        if account_id:
            try:
                response = await client.create_order(
                    account_id=account_id,
                    name="Q1 2025 Brand Campaign",
                    budget=50000.0,
                    start_date="2025-02-01",
                    end_date="2025-02-28"
                )
                print(f"  Success: {response.success}")
                print(f"  Text: {response.text}")
                print(f"  Data: {response.data}")
                order_id = response.data[0].get("id") if response.data else None
                print(f"  Order ID: {order_id}")
            except A2AError as e:
                print(f"  A2A Error: {e}")
                order_id = None
        else:
            print("  Skipped (no account)")
            order_id = None

        # Test 6: Create a line item
        print("\n[6] Testing: Create Line Item...")
        if order_id:
            try:
                response = await client.create_line(
                    order_id=order_id,
                    product_id="mock-product-id",
                    name="Homepage Banner Line",
                    quantity=1000000,
                    start_date="2025-02-01",
                    end_date="2025-02-28"
                )
                print(f"  Success: {response.success}")
                print(f"  Text: {response.text}")
                print(f"  Data: {response.data}")
            except A2AError as e:
                print(f"  A2A Error: {e}")
        else:
            print("  Skipped (no order)")

        print("\n" + "=" * 60)
        print("End-to-End Test Complete")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
