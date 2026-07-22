# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""OpenDirect 2.1 spec dialect on the buyer's avails client (convergence).

The published OpenDirect 2.1 avails request is ``ProductAvailsSearch``
(multi-product ``productids`` array + required ``accountid``/
``advertiserbrandid``, all-lowercase names, no non-spec top-level
fields); the response is the ``avails`` collection envelope of ``Avails``
records. Spec source: OpenDirect v2.1 final, normative attribute tables
(https://github.com/InteractiveAdvertisingBureau/OpenDirect).

Convergence contract (shared iab-agentic-primitives avails module):

* when the client is configured with the spec-required account context
  (``account_id`` + ``advertiser_brand_id``), ``check_avails`` EMITS the
  strictly spec-shaped request — extension fields travel as minted
  Investment ``producttargeting`` entries and the AdCOM Segment
  ``targeting`` array — and parses either response dialect;
* a 422 from a pre-convergence seller (v2.1.0-v2.2.1 rejects the
  ``productids`` array form) triggers ONE legacy-dialect retry, so the
  converged buyer still interoperates with every shipped seller;
* without account context the client stays on the legacy dialect
  byte-for-byte (the spec form cannot be emitted honestly — accountid
  and advertiserbrandid are spec-required and must not be fabricated).

Uses the client's ``httpx.MockTransport`` injection seam so the full wire
path runs deterministically.
"""

import json
from datetime import UTC, datetime

import httpx
import pytest

from ad_buyer.clients.opendirect_client import OpenDirectClient
from ad_buyer.models.opendirect import AvailsRequest

# The spec ProductAvailsSearch top-level attribute set (normative table):
# the emitted spec-dialect body must never carry anything else.
SPEC_REQUEST_FIELDS = {
    "productids",
    "targeting",
    "producttargeting",
    "accountid",
    "currency",
    "advertiserbrandid",
    "availabilityfields",
    "grouping",
    "startdate",
    "enddate",
}

LEGACY_RESPONSE_PAYLOAD = {
    "productid": "prod_1",
    "availableImpressions": 500_000,
    "guaranteedImpressions": 500_000,
    "estimatedCpm": 8.5,
    "totalCost": 4250.0,
}

SPEC_ENVELOPE_PAYLOAD = {
    "avails": [
        {
            "productid": "prod_1",
            "accountid": "acct-42",
            "availability": 400_000,
            "currency": "USD",
            "price": 8.5,
            "startdate": "2026-08-01T00:00:00Z",
            "enddate": "2026-08-31T23:59:59Z",
            "availsstatus": {
                "status": "Partially Available",
                "reason": "Booked",
                "producttargeting": [
                    {
                        "name": "Inventory",
                        "type": "Audience",
                        "datasource": "iab-agentic-primitives",
                        "target": "impressions",
                        "targetvalues": ["400000"],
                        "selectable": False,
                    }
                ],
            },
        }
    ]
}


def _request(**overrides) -> AvailsRequest:
    kwargs = dict(
        product_id="prod_1",
        start_date=datetime(2026, 8, 1, tzinfo=UTC),
        end_date=datetime(2026, 8, 31, 23, 59, 59, tzinfo=UTC),
        requested_impressions=500_000,
        budget=4250.0,
    )
    kwargs.update(overrides)
    return AvailsRequest(**kwargs)


def _spec_client(handler) -> OpenDirectClient:
    client = OpenDirectClient(
        base_url="http://test.local",
        api_key="test_key",
        account_id="acct-42",
        advertiser_brand_id="brand-orchard-7",
    )
    client._transport = httpx.MockTransport(handler)
    return client


class TestSpecDialectEmission:
    @pytest.mark.asyncio
    async def test_emits_strictly_spec_shaped_body(self):
        """With account context, the wire body validates against the
        published ProductAvailsSearch table: spec fields only."""
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=SPEC_ENVELOPE_PAYLOAD)

        client = _spec_client(handler)
        await client.check_avails(_request())

        [body] = captured
        assert set(body) <= SPEC_REQUEST_FIELDS
        assert body["productids"] == ["prod_1"]
        assert body["accountid"] == "acct-42"
        assert body["advertiserbrandid"] == "brand-orchard-7"
        assert body["startdate"] == "2026-08-01T00:00:00Z"
        # The legacy extension fields must NOT appear at the top level.
        assert "productid" not in body
        assert "requestedImpressions" not in body
        assert "budget" not in body

    @pytest.mark.asyncio
    async def test_volume_travels_as_investment_producttargeting(self):
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=SPEC_ENVELOPE_PAYLOAD)

        client = _spec_client(handler)
        await client.check_avails(_request())

        [body] = captured
        targets = {pt["target"]: pt for pt in body["producttargeting"]}
        assert targets["requestedimpressions"]["targetvalues"] == ["500000"]
        assert targets["requestedimpressions"]["name"] == "Investment"
        assert targets["budget"]["targetvalues"] == ["4250.0"]

    @pytest.mark.asyncio
    async def test_parses_the_spec_envelope_into_the_legacy_shape(self):
        """The internal flow keeps consuming AvailsResponse; the client
        bridges the spec record (availability -> availableImpressions,
        price -> estimatedCpm)."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=SPEC_ENVELOPE_PAYLOAD)

        client = _spec_client(handler)
        avails = await client.check_avails(_request())

        assert avails.product_id == "prod_1"
        assert avails.available_impressions == 400_000
        assert avails.estimated_cpm == 8.5
        assert avails.total_cost == 3400.0  # 400000 / 1000 * 8.5

    @pytest.mark.asyncio
    async def test_pre_convergence_seller_422_triggers_one_legacy_retry(self):
        """v2.1.0-v2.2.1 sellers reject the productids array form with a
        validation error; the client falls back to the legacy dialect."""
        bodies: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            bodies.append(body)
            if "productids" in body:
                return httpx.Response(422, json={"detail": "productid required"})
            return httpx.Response(200, json=LEGACY_RESPONSE_PAYLOAD)

        client = _spec_client(handler)
        avails = await client.check_avails(_request())

        assert len(bodies) == 2
        assert "productids" in bodies[0]
        # The retry is the byte-for-byte legacy dialect.
        assert bodies[1]["productid"] == "prod_1"
        assert bodies[1]["requestedImpressions"] == 500_000
        assert avails.available_impressions == 500_000


class TestLegacyDialectUnchanged:
    @pytest.mark.asyncio
    async def test_without_account_context_the_legacy_body_is_unchanged(self):
        """No account context -> the shipped simplified profile,
        byte-for-byte (spec accountid/advertiserbrandid are required and
        never fabricated)."""
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=LEGACY_RESPONSE_PAYLOAD)

        client = OpenDirectClient(base_url="http://test.local", api_key="test_key")
        client._transport = httpx.MockTransport(handler)
        avails = await client.check_avails(_request())

        [body] = captured
        assert body == {
            "productid": "prod_1",
            "startdate": "2026-08-01T00:00:00Z",
            "enddate": "2026-08-31T23:59:59Z",
            "requestedImpressions": 500_000,
            "budget": 4250.0,
        }
        assert avails.available_impressions == 500_000

    @pytest.mark.asyncio
    async def test_legacy_dialect_also_parses_a_spec_envelope(self):
        """Response dialect is decided by the SELLER's reply; a legacy
        emitter must still parse a spec envelope defensively."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=SPEC_ENVELOPE_PAYLOAD)

        client = OpenDirectClient(base_url="http://test.local", api_key="test_key")
        client._transport = httpx.MockTransport(handler)
        avails = await client.check_avails(_request())

        assert avails.available_impressions == 400_000
