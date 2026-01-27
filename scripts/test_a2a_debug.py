# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Debug A2A response structure."""

import asyncio
import json
import sys
sys.path.insert(0, "/Users/bjt/Documents/crewaiTest/ad_buyer_system/src")

import httpx


async def main():
    print("A2A Response Debug")
    print("=" * 60)

    base_url = "https://agentic-direct-server-hwgrypmndq-uk.a.run.app"

    async with httpx.AsyncClient(timeout=120.0) as client:
        # Send a simple message
        import uuid
        request_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())

        payload = {
            "jsonrpc": "2.0",
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": message_id,
                    "role": "user",
                    "parts": [{"kind": "text", "text": "List available products"}],
                }
            },
            "id": request_id,
        }

        print(f"Sending to: {base_url}/a2a/buyer/jsonrpc")
        print(f"Payload: {json.dumps(payload, indent=2)}")
        print("-" * 60)

        response = await client.post(
            f"{base_url}/a2a/buyer/jsonrpc",
            json=payload,
            headers={"Content-Type": "application/json"},
        )

        print(f"Status: {response.status_code}")
        print(f"Response:\n{json.dumps(response.json(), indent=2)}")


if __name__ == "__main__":
    asyncio.run(main())
