# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Test SSE connection directly to understand what's happening."""

import asyncio
import httpx


async def main():
    print("Testing raw SSE connection to IAB server...")

    url = "https://agentic-direct-server-hwgrypmndq-uk.a.run.app/mcp/sse"

    async with httpx.AsyncClient(timeout=30.0) as client:
        print(f"\nGET {url}")
        print("Headers: Accept: text/event-stream")

        try:
            async with client.stream(
                "GET",
                url,
                headers={"Accept": "text/event-stream"},
            ) as response:
                print(f"Status: {response.status_code}")
                print(f"Headers: {dict(response.headers)}")

                # Read first few events
                count = 0
                async for line in response.aiter_lines():
                    print(f"Line: {line}")
                    count += 1
                    if count > 10:
                        print("... (stopping after 10 lines)")
                        break

        except httpx.ReadTimeout:
            print("Read timeout - server may not be sending events")
        except Exception as e:
            print(f"Error: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
