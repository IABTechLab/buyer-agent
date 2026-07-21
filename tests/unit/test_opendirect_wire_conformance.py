# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""OpenDirect 2.1 Tier-1 wire-dialect conformance.

The OpenDirect v2.1 normative attribute tables use ALL-LOWERCASE field
names (``productid``, ``startdate``, ``enddate``, ...); the buyer's wire
aliases historically used camelCase. These tests pin the corrected
Tier-1 dialect:

* every spec-named field serializes under its spec-lowercase name;
* the old camelCase names are NO LONGER emitted (and no longer parsed —
  the alias is the only wire name besides the Python field name);
* enum spellings match the spec: ``deliverytype`` values ``exclusive`` /
  ``guaranteed``; ``bookingstatus`` uses spec ``Canceled`` (not the
  historical ``Cancelled``);
* ``Line.quantity`` serializes as spec ``qty``.

The ``BUYER_AVAILS_REQUEST_WIRE`` / ``SELLER_AVAILS_RESPONSE_WIRE``
payloads are mirrored byte-for-byte in the seller repo
(``tests/unit/test_opendirect_wire_conformance.py`` there): the buyer
must EMIT exactly what the seller accepts, and PARSE exactly what the
seller emits. Keep the two files in lockstep.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ad_buyer.models.opendirect import (
    Account,
    Assignment,
    AvailsRequest,
    AvailsResponse,
    Creative,
    DeliveryType,
    Line,
    LineBookingStatus,
    Order,
    OrderStatus,
    Product,
    RateType,
)

START = datetime(2026, 8, 1, tzinfo=UTC)
END = datetime(2026, 8, 31, 23, 59, 59, tzinfo=UTC)

# --- Mirrored cross-repo payloads (identical constants in the seller repo) ---

BUYER_AVAILS_REQUEST_WIRE = {
    "productid": "prod-display-001",
    "startdate": "2026-08-01T00:00:00Z",
    "enddate": "2026-08-31T23:59:59Z",
    "requestedImpressions": 500000,
    "budget": 6000.0,
    "targeting": {"geo": ["US"], "device": ["mobile"]},
}

SELLER_AVAILS_RESPONSE_WIRE = {
    "productid": "prod-display-001",
    "availableImpressions": 750000,
    "guaranteedImpressions": 500000,
    "estimatedCpm": 12.0,
    "totalCost": 6000.0,
    "deliveryConfidence": None,
    "availableTargeting": None,
}


class TestAvailsRoundTrip:
    """Buyer emits/accepts exactly the coordinated avails dialect."""

    def test_avails_request_emits_exact_wire_shape(self):
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

    def test_avails_response_parses_seller_wire_shape(self):
        resp = AvailsResponse.model_validate(SELLER_AVAILS_RESPONSE_WIRE)
        assert resp.product_id == "prod-display-001"
        assert resp.available_impressions == 750000

    def test_old_camelcase_avails_response_is_rejected(self):
        legacy = dict(SELLER_AVAILS_RESPONSE_WIRE)
        legacy["productId"] = legacy.pop("productid")
        with pytest.raises(ValidationError):
            AvailsResponse.model_validate(legacy)


class TestSpecLowercaseAliases:
    """Every spec-named field serializes under its spec-lowercase name."""

    def _assert_dialect(self, dump: dict, renames: dict[str, str]):
        for old, new in renames.items():
            assert new in dump, f"expected spec name '{new}' on the wire"
            assert old not in dump, f"legacy name '{old}' must not be emitted"

    def test_account_wire_names(self):
        account = Account(advertiser_id="adv-1", buyer_id="buy-1", name="ACME")
        dump = account.model_dump(by_alias=True, exclude_none=True)
        self._assert_dialect(
            dump, {"advertiserId": "advertiserid", "buyerId": "buyerid"}
        )

    def test_order_wire_names(self):
        order = Order(
            name="Q3",
            account_id="acct-1",
            publisher_id="pub-1",
            budget=1000.0,
            start_date=START,
            end_date=END,
            order_status=OrderStatus.PENDING,
        )
        dump = order.model_dump(by_alias=True, exclude_none=True)
        self._assert_dialect(
            dump,
            {
                "accountId": "accountid",
                "publisherId": "publisherid",
                "startDate": "startdate",
                "endDate": "enddate",
                "orderStatus": "orderstatus",
            },
        )

    def test_line_wire_names_and_qty(self):
        line = Line(
            order_id="ord-1",
            product_id="prod-1",
            name="Line",
            start_date=START,
            end_date=END,
            rate_type=RateType.CPM,
            rate=12.0,
            quantity=500000,
            booking_status=LineBookingStatus.DRAFT,
        )
        dump = line.model_dump(by_alias=True, exclude_none=True)
        self._assert_dialect(
            dump,
            {
                "orderId": "orderid",
                "productId": "productid",
                "startDate": "startdate",
                "endDate": "enddate",
                "rateType": "ratetype",
                "bookingStatus": "bookingstatus",
                "quantity": "qty",
            },
        )
        assert dump["qty"] == 500000

    def test_creative_wire_names(self):
        creative = Creative(
            account_id="acct-1",
            name="Banner",
            creative_approvals=[{"status": "Pending"}],
        )
        dump = creative.model_dump(by_alias=True, exclude_none=True)
        self._assert_dialect(
            dump,
            {"accountId": "accountid", "creativeApprovals": "creativeapprovals"},
        )

    def test_assignment_wire_names(self):
        assignment = Assignment(creative_id="cr-1", line_id="line-1")
        dump = assignment.model_dump(by_alias=True, exclude_none=True)
        self._assert_dialect(dump, {"creativeId": "creativeid"})

    def test_product_wire_names(self):
        product = Product(
            publisher_id="pub-1",
            name="Homepage",
            base_price=12.0,
            rate_type=RateType.CPM,
            delivery_type=DeliveryType.GUARANTEED,
            ad_unit={"w": 300, "h": 250},
        )
        dump = product.model_dump(by_alias=True, exclude_none=True)
        self._assert_dialect(
            dump,
            {
                "publisherId": "publisherid",
                "basePrice": "baseprice",
                "rateType": "ratetype",
                "deliveryType": "deliverytype",
                "adUnit": "adunit",
            },
        )


class TestEnumSpecSpellings:
    """Enum values match the spec's published spellings."""

    def test_delivery_type_spec_lowercase(self):
        assert DeliveryType.EXCLUSIVE.value == "exclusive"
        assert DeliveryType.GUARANTEED.value == "guaranteed"
        # PMP is a project extension value (no spec equivalent); unchanged.
        assert DeliveryType.PMP.value == "PMP"

    def test_delivery_type_on_the_wire(self):
        product = Product(
            publisher_id="pub-1",
            name="Homepage",
            base_price=12.0,
            rate_type=RateType.CPM,
            delivery_type=DeliveryType.GUARANTEED,
        )
        dump = product.model_dump(by_alias=True, exclude_none=True)
        assert dump["deliverytype"] == "guaranteed"

    def test_booking_status_canceled_spec_spelling(self):
        assert LineBookingStatus.CANCELLED.value == "Canceled"

    def test_old_enum_spellings_not_parseable(self):
        with pytest.raises(ValueError):
            DeliveryType("Guaranteed")
        with pytest.raises(ValueError):
            DeliveryType("Exclusive")
        with pytest.raises(ValueError):
            LineBookingStatus("Cancelled")

    def test_python_field_names_still_populate(self):
        """populate_by_name=True keeps snake_case construction working."""
        line = Line(
            order_id="ord-1",
            product_id="prod-1",
            name="Line",
            start_date=START,
            end_date=END,
            rate_type=RateType.CPM,
            rate=12.0,
            quantity=1,
        )
        assert line.quantity == 1
