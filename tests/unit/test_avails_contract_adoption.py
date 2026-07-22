# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""EP-12 adoption: the avails models ARE the shared contract types.

The avails wire contract (``POST /products/avails``) now has one
canonical home — ``iab_agentic_primitives.protocol.AvailsRequest`` /
``AvailsResponse``. The buyer's ``ad_buyer.models.opendirect`` names are
aliases of those shared classes, so the contract can no longer drift
between repos.

Wire compatibility is provable, not assumed: the payloads pinned here are
the EXISTING serialized wire shapes (mirrored byte-for-byte with the
seller repo's ``tests/unit/test_opendirect_wire_conformance.py``) and
must round-trip identically through the shared models — including the
legacy null-padded responses the seller emitted through v2.1.x.
"""

import json
from datetime import UTC, datetime

import pytest
from iab_agentic_primitives.protocol import (
    AvailsRequest as SharedAvailsRequest,
)
from iab_agentic_primitives.protocol import (
    AvailsResponse as SharedAvailsResponse,
)
from pydantic import ValidationError

from ad_buyer.models.opendirect import AvailsRequest, AvailsResponse

START = datetime(2026, 8, 1, tzinfo=UTC)
END = datetime(2026, 8, 31, 23, 59, 59, tzinfo=UTC)

BUYER_AVAILS_REQUEST_WIRE = {
    "productid": "prod-display-001",
    "startdate": "2026-08-01T00:00:00Z",
    "enddate": "2026-08-31T23:59:59Z",
    "requestedImpressions": 500000,
    "budget": 6000.0,
    "targeting": {"geo": ["US"], "device": ["mobile"]},
}

# Canonical response per the settled policy: optionals with no value are
# OMITTED (deliveryConfidence has no data source; this product is
# PG-capable so guaranteedImpressions is present).
SELLER_AVAILS_RESPONSE_WIRE = {
    "productid": "prod-display-001",
    "availableImpressions": 750000,
    "guaranteedImpressions": 500000,
    "estimatedCpm": 12.0,
    "totalCost": 6000.0,
}

# What the seller actually emitted through v2.1.x: absent optionals padded
# with explicit nulls. Still parseable (tolerant reader) and still
# round-trips identically.
SELLER_AVAILS_RESPONSE_WIRE_LEGACY_NULLS = {
    **SELLER_AVAILS_RESPONSE_WIRE,
    "deliveryConfidence": None,
    "availableTargeting": None,
}


class TestSharedContractAdoption:
    """The buyer-local names alias the shared contract classes."""

    def test_avails_request_is_the_shared_class(self):
        assert AvailsRequest is SharedAvailsRequest

    def test_avails_response_is_the_shared_class(self):
        assert AvailsResponse is SharedAvailsResponse


class TestExistingPayloadsRoundTripIdentically:
    """Existing serialized payloads survive the adoption byte-for-byte."""

    def test_request_wire_payload_roundtrips(self):
        req = AvailsRequest.model_validate(BUYER_AVAILS_REQUEST_WIRE)
        body = req.model_dump(mode="json", by_alias=True, exclude_none=True)
        assert body == BUYER_AVAILS_REQUEST_WIRE

    def test_request_built_internally_serializes_to_same_wire(self):
        # The exact construction + dump path OpenDirectClient.check_avails
        # uses (snake_case kwargs; mode="json", by_alias, exclude_none).
        req = AvailsRequest(
            product_id="prod-display-001",
            start_date=START,
            end_date=END,
            requested_impressions=500000,
            budget=6000.0,
            targeting={"geo": ["US"], "device": ["mobile"]},
        )
        body = req.model_dump(mode="json", by_alias=True, exclude_none=True)
        assert body == BUYER_AVAILS_REQUEST_WIRE

    def test_canonical_response_roundtrips(self):
        resp = AvailsResponse.model_validate(SELLER_AVAILS_RESPONSE_WIRE)
        assert resp.product_id == "prod-display-001"
        assert resp.guaranteed_impressions == 500000
        wire = resp.model_dump(mode="json", by_alias=True, exclude_none=True)
        assert wire == SELLER_AVAILS_RESPONSE_WIRE

    def test_legacy_null_padded_response_roundtrips(self):
        resp = AvailsResponse.model_validate(SELLER_AVAILS_RESPONSE_WIRE_LEGACY_NULLS)
        assert resp.delivery_confidence is None
        assert resp.available_targeting is None
        wire = json.loads(resp.model_dump_json(by_alias=True))
        assert wire == SELLER_AVAILS_RESPONSE_WIRE_LEGACY_NULLS

    def test_omitting_response_parses_like_null_padded(self):
        """Policy: omitted and legacy-null responses are the same reading."""
        omitted = AvailsResponse.model_validate(SELLER_AVAILS_RESPONSE_WIRE)
        padded = AvailsResponse.model_validate(SELLER_AVAILS_RESPONSE_WIRE_LEGACY_NULLS)
        assert omitted == padded


class TestBuyerGainsSellerSideValidation:
    """Adopting the shared model closes the buyer/seller validation gap.

    The seller has always rejected these at the wire (422); the buyer's
    local model let them through, so bad requests burned a network round
    trip to fail. Now they fail at construction.
    """

    def test_end_date_must_be_after_start_date(self):
        with pytest.raises(ValidationError, match="enddate must be after startdate"):
            AvailsRequest(product_id="p1", start_date=END, end_date=START)

    def test_requested_impressions_cannot_be_negative(self):
        with pytest.raises(ValidationError):
            AvailsRequest(
                product_id="p1",
                start_date=START,
                end_date=END,
                requested_impressions=-1,
            )
