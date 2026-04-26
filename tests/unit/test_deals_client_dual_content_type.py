# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Buyer-side dual content-type emission + plan_id logging on deal booking.

Implements bead ar-y6ki (proposal §5.1 Step 2 + §5.6 + §6 row 14b).

Coverage:
- POST /api/v1/deals with an audience_plan emits both wire-format media
  types (legacy UCP `Content-Type` + Agentic Audiences in `Accept`).
- Buyer logs the plan's `audience_plan_id` hash at INFO via
  `ad_buyer.audience.booking` so the seller-side log can be cross-correlated.
- The seller's response with `audience_plan_snapshot` +
  `audience_match_summary` parses cleanly into the typed `DealResponse`.
- Bookings without an audience_plan keep the legacy `application/json`
  headers (no regression on the non-audience path).
"""

from __future__ import annotations

import logging

import httpx
import pytest

from ad_buyer.clients.deals_client import DealsClient
from ad_buyer.models.audience_plan import AudiencePlan, AudienceRef
from ad_buyer.models.deals import (
    DealBookingRequest,
    DealResponse,
)

SELLER_URL = "http://seller.example.com"

# Wire-format media types per docs/api/audience_plan_wire_format.md §8.
_UCP = "application/vnd.ucp.embedding+json; v=1"
_AGENTIC = "application/vnd.iab.agentic-audiences+json; v=1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_audience_plan() -> AudiencePlan:
    """Minimal valid AudiencePlan exercising hash computation."""

    primary = AudienceRef(
        type="standard",
        identifier="3-7",
        taxonomy="iab-audience",
        version="1.1",
        source="explicit",
    )
    return AudiencePlan(primary=primary, rationale="test plan")


def _deal_response_json(plan: AudiencePlan | None = None) -> dict:
    """Minimal valid DealResponse JSON, optionally with snapshot fields."""

    body: dict = {
        "deal_id": "DEMO-A1B2C3D4E5F6",
        "deal_type": "PD",
        "status": "proposed",
        "quote_id": "qt-abc123",
        "product": {
            "product_id": "ctv-premium-sports",
            "name": "Premium CTV - Sports",
            "inventory_type": "ctv",
        },
        "pricing": {
            "base_cpm": 35.00,
            "tier_discount_pct": 15.0,
            "volume_discount_pct": 5.0,
            "final_cpm": 28.26,
            "currency": "USD",
            "pricing_model": "cpm",
            "rationale": "Base $35 | -15% tier | -5% volume => $28.26",
        },
        "terms": {
            "impressions": 5000000,
            "flight_start": "2026-04-01",
            "flight_end": "2026-04-30",
            "guaranteed": False,
        },
        "buyer_tier": "advertiser",
        "expires_at": "2026-04-08T00:00:00Z",
        "activation_instructions": {},
        "openrtb_params": {
            "id": "DEMO-A1B2C3D4E5F6",
            "bidfloor": 28.26,
            "bidfloorcur": "USD",
            "at": 3,
        },
        "created_at": "2026-03-08T14:30:00Z",
    }
    if plan is not None:
        body["audience_plan_snapshot"] = plan.model_dump(mode="json")
        body["audience_match_summary"] = {
            "primary": {"match": "STRONG", "score": 0.91},
            "constraints": [],
            "extensions": [],
            "exclusions": [],
        }
    return body


class _Capture:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json=_deal_response_json(_make_audience_plan()))

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]


def _make_client(handler) -> DealsClient:
    """Build a DealsClient backed by an httpx.MockTransport."""

    c = DealsClient(seller_url=SELLER_URL, timeout=5.0)
    transport = httpx.MockTransport(handler)
    c._client = httpx.AsyncClient(
        transport=transport,
        base_url=SELLER_URL,
        headers=dict(c._client.headers),
        timeout=5.0,
    )
    return c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDualContentTypeEmission:
    """Buyer emits both wire-format media types when audience_plan present."""

    @pytest.mark.asyncio
    async def test_emits_dual_content_type_when_audience_plan_present(self):
        """Content-Type is the legacy UCP name; Accept lists both."""

        capture = _Capture()
        c = _make_client(capture)
        plan = _make_audience_plan()
        booking = DealBookingRequest(quote_id="qt-abc123", audience_plan=plan)

        await c.book_deal(booking)
        await c.close()

        sent = capture.last
        # Legacy UCP carrier remains the emit name (proposal §5.6 lock #1).
        assert sent.headers.get("content-type") == _UCP
        # Accept advertises both names so the seller can respond with either.
        accept = sent.headers.get("accept", "")
        assert _UCP in accept
        assert _AGENTIC in accept

    @pytest.mark.asyncio
    async def test_no_audience_plan_keeps_legacy_application_json(self):
        """Bookings without an audience_plan keep the default JSON headers."""

        capture = _Capture()
        c = _make_client(capture)
        booking = DealBookingRequest(quote_id="qt-no-audience")

        await c.book_deal(booking)
        await c.close()

        sent = capture.last
        # No audience plan -> default JSON contract.
        assert sent.headers.get("content-type") == "application/json"
        assert sent.headers.get("accept") == "application/json"


class TestPlanIdLogging:
    """Buyer logs audience_plan_id at booking time for forensic correlation."""

    @pytest.mark.asyncio
    async def test_logs_audience_plan_id_at_info(self, caplog):
        """A booking with an audience_plan emits a log line carrying the id."""

        capture = _Capture()
        c = _make_client(capture)
        plan = _make_audience_plan()
        booking = DealBookingRequest(quote_id="qt-abc123", audience_plan=plan)

        with caplog.at_level(logging.INFO, logger="ad_buyer.audience.booking"):
            await c.book_deal(booking)
        await c.close()

        # Exactly one record on the booking logger; carries the canonical id.
        records = [
            r for r in caplog.records if r.name == "ad_buyer.audience.booking"
        ]
        assert len(records) == 1
        msg = records[0].getMessage()
        assert plan.audience_plan_id in msg
        assert plan.audience_plan_id.startswith("sha256:")
        # Quote id surfaces too so log-time correlation is unambiguous.
        assert "qt-abc123" in msg

    @pytest.mark.asyncio
    async def test_no_audience_plan_does_not_log(self, caplog):
        """Bookings without an audience_plan do not log on the booking logger."""

        capture = _Capture()
        c = _make_client(capture)
        booking = DealBookingRequest(quote_id="qt-plain")

        with caplog.at_level(logging.INFO, logger="ad_buyer.audience.booking"):
            await c.book_deal(booking)
        await c.close()

        assert [
            r for r in caplog.records if r.name == "ad_buyer.audience.booking"
        ] == []


class TestSnapshotResponseParsing:
    """Seller responses with snapshot fields parse into the typed DealResponse."""

    @pytest.mark.asyncio
    async def test_response_parses_snapshot_and_match_summary(self):
        """audience_plan_snapshot + audience_match_summary land on DealResponse."""

        capture = _Capture()
        c = _make_client(capture)
        plan = _make_audience_plan()
        booking = DealBookingRequest(quote_id="qt-abc123", audience_plan=plan)

        result = await c.book_deal(booking)
        await c.close()

        assert isinstance(result, DealResponse)
        assert result.audience_plan_snapshot is not None
        # Snapshot id round-trips: same canonical hash on both sides.
        assert result.audience_plan_snapshot.audience_plan_id == plan.audience_plan_id
        assert result.audience_match_summary is not None
        assert result.audience_match_summary.primary is not None
        assert result.audience_match_summary.primary.match == "STRONG"
        assert result.audience_match_summary.primary.score == pytest.approx(0.91)
