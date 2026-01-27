# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Test MCP client using the official MCP SDK with proper SSE session management."""

import asyncio
import sys
sys.path.insert(0, "/Users/bjt/Documents/crewaiTest/ad_buyer_system/src")

from mcp import ClientSession
from mcp.client.sse import sse_client


async def main():
    print("=" * 60)
    print("MCP SDK Test with Proper SSE Session Management")
    print("=" * 60)

    url = "https://agentic-direct-server-hwgrypmndq-uk.a.run.app/mcp/sse"
    print(f"\nConnecting to: {url}")

    try:
        async with sse_client(url) as (read_stream, write_stream):
            print("SSE connection established")

            async with ClientSession(read_stream, write_stream) as session:
                print("ClientSession created, initializing...")

                # Initialize the session
                await session.initialize()
                print("Session initialized!")

                # List available tools
                print("\n[1] Listing tools...")
                tools_result = await session.list_tools()
                print(f"  Found {len(tools_result.tools)} tools:")
                for tool in tools_result.tools[:5]:
                    print(f"    - {tool.name}: {tool.description[:50] if tool.description else 'No description'}...")
                if len(tools_result.tools) > 5:
                    print(f"    ... and {len(tools_result.tools) - 5} more")

                # Call list_products
                print("\n[2] Calling list_products...")
                result = await session.call_tool("list_products", {})
                print(f"  Result: {result}")

                # Call create_account
                print("\n[3] Calling create_account...")
                result = await session.call_tool("create_account", {
                    "name": "MCP SDK Test Account",
                    "type": "advertiser",
                })
                print(f"  Result: {result}")

                # Parse account ID from result
                account_id = None
                for content in result.content:
                    if content.type == "text":
                        import json
                        try:
                            data = json.loads(content.text)
                            account_id = data.get("id")
                            print(f"  Account ID: {account_id}")
                        except:
                            print(f"  Text: {content.text}")

                # Create order if we have account
                if account_id:
                    print("\n[4] Calling create_order...")
                    result = await session.call_tool("create_order", {
                        "accountId": account_id,
                        "name": "MCP SDK Test Order",
                        "budget": 10000,
                    })
                    print(f"  Result: {result}")

        print("\nSSE connection closed cleanly")

    except Exception as e:
        print(f"\nError: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 60)
    print("Test Complete")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
