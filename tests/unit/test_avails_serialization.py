# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Regression: OpenDirectClient.check_avails must JSON-serialize its request.

Real-mode failure: the buyer's availability-check tool crashed
8x at runtime with ``Object of type datetime is not JSON serializable``. The
POST /products/avails body was built with
``AvailsRequest.model_dump(by_alias=True, exclude_none=True)`` WITHOUT
``mode="json"``, so the ``start_date``/``end_date`` fields stayed as Python
``datetime`` objects. httpx's json encoder cannot serialize those, so the
request NEVER reached the seller (0 avails calls on every seller log) — the
buyer could not confirm inventory, marked every product "Technical Error", and
walked with no_booking.

The fix mirrors the working wire-serialization pattern used elsewhere in the
codebase (deals_client, negotiation/client, ucp_client): ``mode="json"`` so
``date``/``datetime`` fields go on the wire as ISO-8601 strings, while
``by_alias=True`` keeps the spec-lowercase field names
(``startdate``/``enddate``) the seller's avails endpoint expects.

Uses the client's ``httpx.MockTransport`` injection seam so the FULL wire
serialization path runs deterministically with no network/credits.
"""

import json
from datetime import datetime

import httpx
import pytest

from ad_buyer.clients.opendirect_client import OpenDirectClient
from ad_buyer.models.opendirect import AvailsRequest

AVAILS_RESPONSE_PAYLOAD = {
    "productid": "prod_1",
    "availableImpressions": 1_000_000,
    "guaranteedImpressions": 900_000,
    "estimatedCpm": 8.5,
    "totalCost": 8500.0,
    "deliveryConfidence": 95.0,
    "availableTargeting": ["geo", "device"],
}


class TestAvailsRequestSerialization:
    """check_avails must serialize date/datetime fields to ISO-8601 strings."""

    @pytest.mark.asyncio
    async def test_check_avails_serializes_datetime_to_iso_strings(self):
        """The POST body must be valid JSON with ISO-string dates.

        Before the fix this raised
        ``TypeError: Object of type datetime is not JSON serializable`` at the
        httpx serialization boundary (the request never left the client).
        """
        client = OpenDirectClient(base_url="http://test.local", api_key="test_key")
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["content"] = request.content
            return httpx.Response(200, json=AVAILS_RESPONSE_PAYLOAD)

        client._transport = httpx.MockTransport(handler)

        request = AvailsRequest(
            product_id="prod_1",
            start_date=datetime(2025, 2, 1),
            end_date=datetime(2025, 2, 28, 23, 59, 59),
            requested_impressions=1_000_000,
            budget=10_000.0,
        )

        avails = await client.check_avails(request)

        # The request actually reached the (mock) seller and parsed back.
        assert avails.product_id == "prod_1"
        assert avails.available_impressions == 1_000_000

        # The body httpx put on the wire is valid JSON...
        body = json.loads(captured["content"])

        # ...with the spec-lowercase field names (by_alias)...
        assert "startdate" in body
        assert "enddate" in body
        assert "productid" in body

        # ...and ISO-8601 STRING dates (not datetime objects).
        assert isinstance(body["startdate"], str)
        assert isinstance(body["enddate"], str)
        assert body["startdate"].startswith("2025-02-01")
        assert body["enddate"].startswith("2025-02-28")
