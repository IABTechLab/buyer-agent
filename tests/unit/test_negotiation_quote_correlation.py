# Author: Agent Range
# Donated to IAB Tech Lab

"""Tests that a negotiated price actually reaches the deal that gets booked.

Two independent halves of the same defect:

1. **Correlation on the wire.** ``DealBookingRequest`` carries a ``quote_id``
   and no ``negotiation_id``, so the quote is the only key the booking
   contract offers for tying an agreed price to the deal being struck. The
   buyer must therefore send ``NegotiationMessage.quote_id`` on the messages
   of a negotiation it opened about a quote. It previously sent only
   ``proposal_id``/``negotiation_id``, so the id arrived nowhere and nothing
   could correlate the negotiation to the quote.

   These tests assert on the REAL serialized request body (via
   ``httpx.MockTransport``) and re-validate it with the shared
   ``NegotiationMessage``, so a field rename on either side fails the test
   rather than passing it.

2. **Assertion, not a bound, on the booked price.** The only post-negotiation
   guard used to be ``final_cpm is None or final_cpm > max_cpm``: a ceiling
   test that passes for ANY price under the buyer's limit, including the
   seller's undiscounted list price. A negotiation whose agreed price was
   silently dropped therefore booked at list price and logged as a success.
   The buyer now also asserts the bookable quote's price EQUALS the agreed
   price, BEFORE the quote can be booked, and the two guards stay separate.

   Equality is on integer micros -- the unit money is defined in on the wire
   -- with no tolerance window. Two prices are equal if and only if they are
   the same number of micros, so the only delta this path can produce (the
   sub-micro quantization of a ragged float) is absorbed, while a real
   difference of one micro and upward is refused.
"""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from iab_agentic_primitives.protocol import NegotiationMessage as WireNegotiationMessage

from ad_buyer.booking.quote_normalizer import QuoteNormalizer
from ad_buyer.models.deals import (
    AvailabilityInfo,
    DealResponse,
    OpenRTBParams,
    PricingInfo,
    ProductInfo,
    QuoteResponse,
    TermsInfo,
)
from ad_buyer.negotiation.client import NegotiationClient
from ad_buyer.negotiation.models import NegotiationRound, NegotiationSession
from ad_buyer.negotiation.strategies.simple_threshold import SimpleThresholdStrategy
from ad_buyer.orchestration.multi_seller import (
    DealParams,
    InventoryRequirements,
    MultiSellerOrchestrator,
    NegotiationConfig,
)
from ad_buyer.registry.models import AgentCapability, AgentCard, TrustLevel

SELLER_URL = "http://seller-a.example.com"
QUOTE_ID = "qt-original-001"
CEILING = 12.0
TARGET = 10.0
AGREED = 11.5

# A "ragged" agreed price: a float carrying more than six decimal places. The
# micros quantization is the ONLY lossy step on the money path, so this is the
# only delta the path can actually produce -- and it is sub-micro. 34/3 is
# 11.333333333333334, which is 11_333_333 micros, so a seller that echoes the
# target back through a micros round trip legitimately returns 11.333333.
# These two are NOT equal as floats, and MUST compare equal as money.
RAGGED_AGREED = 34 / 3
RAGGED_ROUND_TRIPPED = 11.333333
# The same price plus exactly one micro: 11_333_334. The smallest difference
# money can actually express, and a real one, so it must be refused.
RAGGED_PLUS_ONE_MICRO = 11.333334


# ---------------------------------------------------------------------------
# Part 1: quote_id on the negotiation wire
# ---------------------------------------------------------------------------


class _BodyCapture:
    """Collects the real serialized JSON body of every outbound request."""

    def __init__(self) -> None:
        self.bodies: list[dict] = []

    def handler(self, response_json: dict):
        def _handle(request: httpx.Request) -> httpx.Response:
            self.bodies.append(json.loads(request.content))
            return httpx.Response(200, json=response_json)

        return _handle

    @property
    def last(self) -> dict:
        return self.bodies[-1]


def _patched_httpx(handler):
    """Patch httpx.AsyncClient so the client's own POST hits a mock transport.

    The client under test constructs its own ``httpx.AsyncClient``; this keeps
    a REAL client (so pydantic serialization, JSON encoding and the request
    body are all genuine) and swaps only the transport.
    """
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    return patch("httpx.AsyncClient", factory)


def _round_response(action: str = "counter", seller_price: float = 13.0) -> dict:
    return {
        "negotiation_id": "neg-1",
        "round_number": 2,
        "buyer_price": TARGET,
        "seller_price": seller_price,
        "action": action,
        "rationale": "",
    }


def _session(quote_id: str | None = QUOTE_ID) -> NegotiationSession:
    return NegotiationSession(
        proposal_id="prop-001",
        seller_url=SELLER_URL,
        negotiation_id="neg-1",
        current_seller_price=AGREED,
        our_last_offer=TARGET,
        quote_id=quote_id,
    )


def _revalidate(body: dict) -> WireNegotiationMessage:
    """Re-parse the captured body with the SHARED contract model.

    This is what makes the assertion rename-proof: the body has to still be a
    valid shared ``NegotiationMessage`` and the quote id has to land on the
    shared model's ``quote_id`` field, not merely on some key we chose.
    """
    return WireNegotiationMessage.model_validate(body)


class TestQuoteIdOnTheNegotiationWire:
    @pytest.mark.asyncio
    async def test_opening_message_body_carries_the_quote_id(self):
        """start_negotiation sends quote_id, and records it on the session."""
        capture = _BodyCapture()
        client = NegotiationClient()
        strategy = SimpleThresholdStrategy(
            target_cpm=TARGET, max_cpm=CEILING, concession_step=1.0, max_rounds=3
        )

        with _patched_httpx(capture.handler(_round_response())):
            session = await client.start_negotiation(
                seller_url=SELLER_URL,
                proposal_id="prop-001",
                initial_price=TARGET,
                strategy=strategy,
                quote_id=QUOTE_ID,
            )

        assert capture.last["quote_id"] == QUOTE_ID
        assert _revalidate(capture.last).quote_id == QUOTE_ID
        # The opening message still leads with the proposal: the seller's
        # negotiation surface is proposal-led and keys off proposal_id first,
        # so adding quote_id must not displace it.
        assert capture.last["proposal_id"] == "prop-001"
        # The session remembers the quote so later messages can repeat it.
        assert session.quote_id == QUOTE_ID

    @pytest.mark.asyncio
    async def test_counter_offer_body_carries_the_quote_id(self):
        capture = _BodyCapture()
        client = NegotiationClient()

        with _patched_httpx(capture.handler(_round_response())):
            await client.counter_offer(_session(), price=TARGET)

        assert capture.last["quote_id"] == QUOTE_ID
        assert _revalidate(capture.last).quote_id == QUOTE_ID
        assert capture.last["proposal_id"] == "prop-001"
        assert capture.last["negotiation_id"] == "neg-1"

    @pytest.mark.asyncio
    async def test_accept_body_carries_the_quote_id(self):
        """The accept is the message that matters most: it fixes the price."""
        capture = _BodyCapture()
        client = NegotiationClient()

        with _patched_httpx(capture.handler({"status": "accepted"})):
            await client.accept(_session())

        assert capture.last["quote_id"] == QUOTE_ID
        message = _revalidate(capture.last)
        assert message.quote_id == QUOTE_ID
        # All three ids ride together; the shared model permits it (its
        # validator rejects only the all-None case) and the seller's
        # proposal-first key derivation is unaffected.
        assert message.proposal_id == "prop-001"
        assert message.negotiation_id == "neg-1"

    @pytest.mark.asyncio
    async def test_decline_body_carries_the_quote_id(self):
        capture = _BodyCapture()
        client = NegotiationClient()

        with _patched_httpx(capture.handler({"status": "rejected"})):
            await client.decline(_session())

        assert capture.last["quote_id"] == QUOTE_ID
        message = _revalidate(capture.last)
        assert message.quote_id == QUOTE_ID
        # Contract rule preserved: a reject carries no price.
        assert message.buyer_price is None

    @pytest.mark.asyncio
    async def test_auto_negotiate_threads_the_quote_id_through_the_loop(self):
        """Every message of a full auto-negotiation carries the quote id."""
        capture = _BodyCapture()
        client = NegotiationClient()
        # Seller opens at the ceiling, so the strategy accepts immediately:
        # two messages (open + accept), both about the same quote.
        strategy = SimpleThresholdStrategy(
            target_cpm=TARGET, max_cpm=CEILING, concession_step=1.0, max_rounds=3
        )

        with _patched_httpx(capture.handler(_round_response(seller_price=CEILING))):
            await client.auto_negotiate(
                seller_url=SELLER_URL,
                proposal_id="prop-001",
                strategy=strategy,
                quote_id=QUOTE_ID,
            )

        assert len(capture.bodies) >= 2
        assert all(body["quote_id"] == QUOTE_ID for body in capture.bodies)
        assert all(_revalidate(body).quote_id == QUOTE_ID for body in capture.bodies)

    @pytest.mark.asyncio
    async def test_no_quote_id_omits_the_field_entirely(self):
        """A buyer with no quote in hand sends a valid message without it.

        Regression guard for the pure proposal-led path: the field is additive
        and must not appear as an explicit null.
        """
        capture = _BodyCapture()
        client = NegotiationClient()

        with _patched_httpx(capture.handler(_round_response())):
            await client.counter_offer(_session(quote_id=None), price=TARGET)

        assert "quote_id" not in capture.last
        assert _revalidate(capture.last).quote_id is None


# ---------------------------------------------------------------------------
# Part 2: the booked price must EQUAL the agreed price
# ---------------------------------------------------------------------------


def _seller_card() -> AgentCard:
    return AgentCard(
        agent_id="seller-a",
        name="Seller A",
        url=SELLER_URL,
        protocols=["a2a", "deals-api-v1"],
        capabilities=[AgentCapability(name="display", description="display inventory")],
        trust_level=TrustLevel.VERIFIED,
    )


def _quote(*, quote_id: str = QUOTE_ID, final_cpm: float = 15.0) -> QuoteResponse:
    return QuoteResponse(
        quote_id=quote_id,
        status="available",
        product=ProductInfo(product_id="prod-display-001", name="Premium Display"),
        pricing=PricingInfo(base_cpm=15.0, final_cpm=final_cpm),
        terms=TermsInfo(
            impressions=500_000,
            flight_start="2026-08-01",
            flight_end="2026-08-31",
            guaranteed=False,
        ),
        availability=AvailabilityInfo(inventory_available=True, estimated_fill_rate=0.85),
        seller_id="seller-a",
        buyer_tier="agency",
    )


def _deal(*, quote_id: str, final_cpm: float) -> DealResponse:
    return DealResponse(
        deal_id="deal-001",
        deal_type="PD",
        status="active",
        quote_id=quote_id,
        product=ProductInfo(product_id="prod-display-001", name="Premium Display"),
        pricing=PricingInfo(base_cpm=15.0, final_cpm=final_cpm),
        terms=TermsInfo(impressions=500_000, guaranteed=False),
        buyer_tier="agency",
        openrtb_params=OpenRTBParams(id="deal-001", bidfloor=final_cpm, bidfloorcur="USD"),
    )


def _proposal_response(proposed_price: float | None = 13.0) -> dict:
    return {
        "proposal_id": "prop-1",
        "recommendation": "counter",
        "status": "counter_pending",
        "counter_terms": {
            "proposed_price": proposed_price,
            "floor_price": 10.0,
            "negotiation_id": "neg-1",
            "round_number": 1,
            "action": "counter",
        },
    }


def _agree_at(negotiation_client, price: float) -> None:
    """Make the negotiation settle at exactly ``price``.

    The seller's proposal response is round 1, and the orchestrator accepts the
    first seller price at or below the ceiling, so putting ``price`` in
    ``counter_terms.proposed_price`` makes it the agreed price verbatim -- the
    orchestrator does ``float(raw_price)`` and no arithmetic, so a ragged float
    survives into ``agreed`` bit for bit.
    """
    negotiation_client.submit_proposal.return_value = _proposal_response(proposed_price=price)


@pytest.fixture
def registry_client():
    client = AsyncMock()
    client.discover_sellers = AsyncMock(return_value=[_seller_card()])
    return client


@pytest.fixture
def deals_client_factory():
    clients: dict[str, AsyncMock] = {}

    def factory(seller_url: str, **kwargs) -> AsyncMock:
        if seller_url not in clients:
            mock = AsyncMock()
            mock.seller_url = seller_url
            mock.request_quote = AsyncMock(return_value=None)
            mock.book_deal = AsyncMock(return_value=None)
            mock.close = AsyncMock()
            clients[seller_url] = mock
        return clients[seller_url]

    factory._clients = clients
    return factory


@pytest.fixture
def event_bus():
    bus = AsyncMock()
    bus.publish = AsyncMock()
    return bus


@pytest.fixture
def negotiation_client():
    client = AsyncMock()
    client.submit_proposal = AsyncMock(return_value=_proposal_response())
    client.counter_offer = AsyncMock(
        return_value=NegotiationRound(
            round_number=2, buyer_price=TARGET, seller_price=AGREED, action="counter"
        )
    )
    client.accept = AsyncMock(return_value={})
    client.decline = AsyncMock(return_value=None)
    return client


def _orchestrator(registry, deals_factory, bus, neg_client) -> MultiSellerOrchestrator:
    return MultiSellerOrchestrator(
        registry_client=registry,
        deals_client_factory=deals_factory,
        event_bus=bus,
        quote_normalizer=QuoteNormalizer(),
        quote_timeout=5.0,
        negotiation_client=neg_client,
        negotiation_config=NegotiationConfig(),
    )


async def _run(registry, deals_factory, bus, neg_client, *, max_cpm: float = CEILING):
    orch = _orchestrator(registry, deals_factory, bus, neg_client)
    return await orch.orchestrate(
        inventory_requirements=InventoryRequirements(
            media_type="display", deal_types=["PD"], max_cpm=max_cpm
        ),
        deal_params=DealParams(
            product_id="prod-display-001",
            deal_type="PD",
            impressions=500_000,
            flight_start="2026-08-01",
            flight_end="2026-08-31",
            target_cpm=TARGET,
            media_type="display",
        ),
        budget=100_000.0,
        max_deals=1,
    )


class TestBookedPriceEqualsAgreedPrice:
    @pytest.mark.asyncio
    async def test_requote_at_the_agreed_price_books(
        self, registry_client, deals_client_factory, event_bus, negotiation_client
    ):
        """The green case: agreed $11.50, re-quote $11.50, deal booked."""
        client = deals_client_factory(SELLER_URL)
        client.request_quote.side_effect = [
            _quote(final_cpm=15.0),
            _quote(quote_id="qt-negotiated", final_cpm=AGREED),
        ]
        client.book_deal.return_value = _deal(quote_id="qt-negotiated", final_cpm=AGREED)

        result = await _run(registry_client, deals_client_factory, event_bus, negotiation_client)

        assert len(result.selection.booked_deals) == 1
        assert result.selection.booked_deals[0].pricing.final_cpm == AGREED
        assert result.negotiations[0]["outcome"] == "accepted"
        assert result.negotiations[0]["agreed_cpm"] == AGREED
        # Positive control for the "never reaches the ranked list" assertion
        # in the mismatch test below: on the happy path it DOES reach it.
        assert "qt-negotiated" in [q.quote_id for q in result.ranked_quotes]

    @pytest.mark.asyncio
    async def test_requote_below_agreed_refuses_to_book(
        self, registry_client, deals_client_factory, event_bus, negotiation_client
    ):
        """A price BELOW the agreed one is still a mismatch, and still refused.

        Cheaper is not safer: it means the price the buyer books was not the
        price either side agreed to, so the record cannot be trusted.
        """
        client = deals_client_factory(SELLER_URL)
        client.request_quote.side_effect = [
            _quote(final_cpm=15.0),
            _quote(quote_id="qt-negotiated", final_cpm=9.0),
        ]

        result = await _run(registry_client, deals_client_factory, event_bus, negotiation_client)

        assert result.selection.booked_deals == []
        client.book_deal.assert_not_awaited()
        assert result.negotiations[0]["outcome"] == "requote_price_mismatch"
        assert "9.00" in result.negotiations[0]["error"]
        assert "11.50" in result.negotiations[0]["error"]

    @pytest.mark.asyncio
    async def test_requote_at_list_price_under_the_ceiling_refuses_to_book(
        self, registry_client, deals_client_factory, event_bus, negotiation_client
    ):
        """THE defect, reproduced: a price the old ceiling test waved through.

        Agreed $11.50; the seller ignores the negotiation and returns $11.90.
        That is under the $12 ceiling, so the ceiling guard passes it -- and
        the buyer used to book it and log "Negotiation succeeded". The equality
        assertion is what makes this loud.
        """
        client = deals_client_factory(SELLER_URL)
        client.request_quote.side_effect = [
            _quote(final_cpm=15.0),
            _quote(quote_id="qt-negotiated", final_cpm=11.9),
        ]

        result = await _run(registry_client, deals_client_factory, event_bus, negotiation_client)

        # Under the ceiling, so the ceiling guard alone would NOT have caught it.
        assert 11.9 <= CEILING
        assert result.selection.booked_deals == []
        client.book_deal.assert_not_awaited()
        assert result.negotiations[0]["outcome"] == "requote_price_mismatch"

    @pytest.mark.asyncio
    async def test_the_assertion_fires_before_the_booking_call(
        self, registry_client, deals_client_factory, event_bus, negotiation_client
    ):
        """Decision D3: refuse to book, do not discover it after booking.

        The mismatched quote must never reach the ranked list, so nothing can
        book it and no money is committed.
        """
        client = deals_client_factory(SELLER_URL)
        client.request_quote.side_effect = [
            _quote(final_cpm=15.0),
            _quote(quote_id="qt-negotiated", final_cpm=11.9),
        ]

        result = await _run(registry_client, deals_client_factory, event_bus, negotiation_client)

        client.book_deal.assert_not_awaited()
        assert result.negotiations[0]["requoted_quote_id"] is None
        assert "qt-negotiated" not in [q.quote_id for q in result.ranked_quotes]

    @pytest.mark.asyncio
    async def test_ragged_agreed_price_round_tripped_through_micros_books(
        self, registry_client, deals_client_factory, event_bus, negotiation_client
    ):
        """The one delta the money path can actually produce must still book.

        Money crosses the wire as integer micros, so an agreed price carrying
        more than six decimal places comes back quantized. That is a genuine
        representation difference, it is sub-micro, and it is the ONLY one this
        path can create. Agreed 11.333333333333334, re-quoted 11.333333: not
        equal as floats, the same money, so the deal books.
        """
        _agree_at(negotiation_client, RAGGED_AGREED)
        client = deals_client_factory(SELLER_URL)
        client.request_quote.side_effect = [
            _quote(final_cpm=15.0),
            _quote(quote_id="qt-negotiated", final_cpm=RAGGED_ROUND_TRIPPED),
        ]
        client.book_deal.return_value = _deal(
            quote_id="qt-negotiated", final_cpm=RAGGED_ROUND_TRIPPED
        )

        result = await _run(registry_client, deals_client_factory, event_bus, negotiation_client)

        # The premise: a bit-exact float `!=` WOULD have refused this booking.
        assert RAGGED_AGREED != RAGGED_ROUND_TRIPPED
        assert result.negotiations[0]["agreed_cpm"] == RAGGED_AGREED
        assert len(result.selection.booked_deals) == 1
        assert result.negotiations[0]["outcome"] == "accepted"

    @pytest.mark.asyncio
    async def test_one_micro_price_difference_is_a_mismatch(
        self, registry_client, deals_client_factory, event_bus, negotiation_client
    ):
        """The smallest difference money can express is still a difference.

        11_333_333 micros agreed, 11_333_334 micros re-quoted. One micro apart
        is a real pricing difference, not representation noise, so it is
        refused -- and it is well under the ceiling, so only the equality
        assertion can catch it.
        """
        _agree_at(negotiation_client, RAGGED_AGREED)
        client = deals_client_factory(SELLER_URL)
        client.request_quote.side_effect = [
            _quote(final_cpm=15.0),
            _quote(quote_id="qt-negotiated", final_cpm=RAGGED_PLUS_ONE_MICRO),
        ]

        result = await _run(registry_client, deals_client_factory, event_bus, negotiation_client)

        # Under the ceiling, so the ceiling guard alone would NOT have caught it.
        assert RAGGED_PLUS_ONE_MICRO <= CEILING
        assert result.selection.booked_deals == []
        client.book_deal.assert_not_awaited()
        assert result.negotiations[0]["outcome"] == "requote_price_mismatch"

    @pytest.mark.asyncio
    async def test_a_tenth_of_a_cent_difference_is_a_mismatch(
        self, registry_client, deals_client_factory, event_bus, negotiation_client
    ):
        """A tenth of a cent is a PRICE difference, and is refused.

        This input used to be accepted, on the theory that it was
        representation noise. It is not: it is a thousand micros, two thousand
        times the largest delta the money path can produce, and it moves real
        money (cost is computed from the booked price, so $0.001 CPM is $1 per
        million impressions). Under the micros rule it is a mismatch.
        """
        client = deals_client_factory(SELLER_URL)
        client.request_quote.side_effect = [
            _quote(final_cpm=15.0),
            _quote(quote_id="qt-negotiated", final_cpm=AGREED + 0.001),
        ]

        result = await _run(registry_client, deals_client_factory, event_bus, negotiation_client)

        assert AGREED + 0.001 <= CEILING
        assert result.selection.booked_deals == []
        client.book_deal.assert_not_awaited()
        assert result.negotiations[0]["outcome"] == "requote_price_mismatch"


class TestCeilingGuardStaysIndependent:
    """The two guards must not collapse into one.

    The ceiling guard answers "never pay more than the buyer's limit"; the
    equality guard answers "never pay a price we did not agree". They fail for
    different reasons and are reported differently.
    """

    @pytest.mark.asyncio
    async def test_requote_above_ceiling_is_reported_as_a_ceiling_breach(
        self, registry_client, deals_client_factory, event_bus, negotiation_client
    ):
        """Agreed $11.50, re-quote $13 against a $12 ceiling: ceiling breach."""
        client = deals_client_factory(SELLER_URL)
        client.request_quote.side_effect = [
            _quote(final_cpm=15.0),
            _quote(quote_id="qt-negotiated", final_cpm=13.0),
        ]

        result = await _run(registry_client, deals_client_factory, event_bus, negotiation_client)

        assert result.selection.booked_deals == []
        client.book_deal.assert_not_awaited()
        assert result.negotiations[0]["outcome"] == "requote_above_ceiling"

    @pytest.mark.asyncio
    async def test_unpriced_requote_is_reported_as_a_ceiling_breach(
        self, registry_client, deals_client_factory, event_bus, negotiation_client
    ):
        """An unpriced re-quote is refused by the ceiling guard, not by equality."""
        client = deals_client_factory(SELLER_URL)
        client.request_quote.side_effect = [
            _quote(final_cpm=15.0),
            _quote(quote_id="qt-negotiated", final_cpm=None),
        ]

        result = await _run(registry_client, deals_client_factory, event_bus, negotiation_client)

        assert result.selection.booked_deals == []
        client.book_deal.assert_not_awaited()
        assert result.negotiations[0]["outcome"] == "requote_above_ceiling"


class TestOrchestratorSendsTheQuoteIdItNegotiatedAbout:
    @pytest.mark.asyncio
    async def test_session_carries_the_original_quote_id(
        self, registry_client, deals_client_factory, event_bus, negotiation_client
    ):
        """Every session the orchestrator hands the client names the quote.

        The quote id must be the ORIGINAL above-ceiling quote the negotiation
        is about, not the fresh re-quote (which does not exist yet when the
        negotiation runs).
        """
        client = deals_client_factory(SELLER_URL)
        client.request_quote.side_effect = [
            _quote(quote_id=QUOTE_ID, final_cpm=15.0),
            _quote(quote_id="qt-negotiated", final_cpm=AGREED),
        ]
        client.book_deal.return_value = _deal(quote_id="qt-negotiated", final_cpm=AGREED)

        await _run(registry_client, deals_client_factory, event_bus, negotiation_client)

        sessions = [call.args[0] for call in negotiation_client.counter_offer.call_args_list]
        sessions += [call.args[0] for call in negotiation_client.accept.call_args_list]
        assert sessions, "negotiation ran without any message-surface call"
        for session in sessions:
            assert session.quote_id == QUOTE_ID
