# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Test MCP client using Streamable HTTP transport."""

import asyncio
import sys
sys.path.insert(0, "/Users/bjt/Documents/crewaiTest/ad_buyer_system/src")

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main():
    print("=" * 60)
    print("MCP Streamable HTTP Client Test")
    print("=" * 60)

    # The server uses StreamableHTTPServerTransport at /mcp/sse
    url = "https://agentic-direct-server-hwgrypmndq-uk.a.run.app/mcp/sse"
    print(f"\nConnecting to: {url}")

    try:
        print("Creating streamablehttp_client...")

        async with streamablehttp_client(url) as (read_stream, write_stream, get_session_id):
            print("Connection established!")
            print(f"Session ID getter: {get_session_id}")

            session_id = get_session_id()
            print(f"Session ID: {session_id}")

            print("\nCreating ClientSession...")
            async with ClientSession(read_stream, write_stream) as session:
                print("ClientSession created, initializing...")

                # Initialize the session
                result = await session.initialize()
                print(f"Session initialized!")
                print(f"Server info: {result.serverInfo if hasattr(result, 'serverInfo') else result}")

                # List available tools
                print("\n[1] Listing tools...")
                tools_result = await session.list_tools()
                print(f"  Found {len(tools_result.tools)} tools:")
                for tool in tools_result.tools[:8]:
                    desc = (tool.description[:40] + '...') if tool.description and len(tool.description) > 40 else (tool.description or 'No description')
                    print(f"    - {tool.name}: {desc}")
                if len(tools_result.tools) > 8:
                    print(f"    ... and {len(tools_result.tools) - 8} more")

                # Call list_products
                print("\n[2] Calling list_products...")
                result = await session.call_tool("list_products", {})
                print(f"  Success! Content items: {len(result.content)}")
                for content in result.content:
                    if content.type == "text":
                        print(f"  Result: {content.text}")

                # Call create_account
                print("\n[3] Calling create_account...")
                result = await session.call_tool("create_account", {
                    "name": "MCP Direct Test Account",
                    "type": "advertiser",
                })
                print(f"  Success!")

                # Parse account ID
                account_id = None
                for content in result.content:
                    if content.type == "text":
                        import json
                        try:
                            data = json.loads(content.text)
                            account_id = data.get("id")
                            print(f"  Account created: {data}")
                        except json.JSONDecodeError:
                            print(f"  Text: {content.text}")

                # Create order
                if account_id:
                    print("\n[4] Calling create_order...")
                    result = await session.call_tool("create_order", {
                        "accountId": account_id,
                        "name": "MCP Direct Test Order",
                        "budget": 20000,
                    })
                    print(f"  Success!")
                    for content in result.content:
                        if content.type == "text":
                            print(f"  Order: {content.text}")

        print("\n✓ Connection closed cleanly")

    except Exception as e:
        print(f"\n✗ Error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 60)
    print("Test Complete")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
