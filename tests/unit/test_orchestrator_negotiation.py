# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for negotiation in the real booking path (bead ar-cc3n).

When a seller's quote comes back ABOVE the buyer's max_cpm ceiling but
within a configurable negotiation band (default: quote <= ceiling x 1.25,
mirroring the reference SDK's ``negotiation_band_per_mille=1250``), the
MultiSellerOrchestrator attempts a deterministic negotiation via the
seller's negotiation surface before discarding the quote:

- open at the buyer's target CPM (never above the ceiling),
- evaluate the seller's counters, accept the FIRST offer <= ceiling,
- walk after a bounded number of rounds or when the seller's best price
  stays above the ceiling,
- on agreement, re-quote at the agreed price (``target_cpm``) and book
  the fresh quote -- never the original above-ceiling one.

Money invariants under test: the buyer never offers above its ceiling,
never accepts above its ceiling, and never books above its ceiling --
even if the post-negotiation re-quote comes back high.

Historic example from run #11 (AVAILS_GAP_STORY 7.4): Premium Display
base $15 vs a $12 ceiling with floor $10 -- bookable, but the buyer
walked. These tests use those numbers.
"""

from unittest.mock import AsyncMock

import pytest

from ad_buyer.booking.quote_normalizer import QuoteNormalizer
from ad_buyer.events.models import EventType
from ad_buyer.models.deals import (
    AvailabilityInfo,
    DealResponse,
    OpenRTBParams,
    PricingInfo,
    ProductInfo,
    QuoteResponse,
    TermsInfo,
)
from ad_buyer.negotiation.models import NegotiationRound
from ad_buyer.orchestration.multi_seller import (
    DealParams,
    InventoryRequirements,
    MultiSellerOrchestrator,
    NegotiationConfig,
)
from ad_buyer.registry.models import AgentCapability, AgentCard, TrustLevel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SELLER_URL = "http://seller-a.example.com"
CEILING = 12.0
TARGET = 10.0


def _seller_card(agent_id: str = "seller-a", url: str = SELLER_URL) -> AgentCard:
    return AgentCard(
        agent_id=agent_id,
        name="Seller A",
        url=url,
        protocols=["a2a", "deals-api-v1"],
        capabilities=[AgentCapability(name="display", description="display inventory")],
        trust_level=TrustLevel.VERIFIED,
    )


def _quote(
    *,
    quote_id: str = "q-high",
    final_cpm: float = 15.0,
    seller_id: str = "seller-a",
    product_id: str = "prod-display-001",
) -> QuoteResponse:
    return QuoteResponse(
        quote_id=quote_id,
        status="available",
        product=ProductInfo(product_id=product_id, name="Premium Display"),
        pricing=PricingInfo(base_cpm=15.0, final_cpm=final_cpm),
        terms=TermsInfo(
            impressions=500_000,
            flight_start="2026-08-01",
            flight_end="2026-08-31",
            guaranteed=False,
        ),
        availability=AvailabilityInfo(inventory_available=True, estimated_fill_rate=0.85),
        seller_id=seller_id,
        buyer_tier="agency",
    )


def _deal(*, deal_id: str = "deal-001", quote_id: str = "q-neg", final_cpm: float = 11.5):
    return DealResponse(
        deal_id=deal_id,
        deal_type="PD",
        status="active",
        quote_id=quote_id,
        product=ProductInfo(product_id="prod-display-001", name="Premium Display"),
        pricing=PricingInfo(base_cpm=15.0, final_cpm=final_cpm),
        terms=TermsInfo(impressions=500_000, guaranteed=False),
        buyer_tier="agency",
        openrtb_params=OpenRTBParams(id=deal_id, bidfloor=final_cpm, bidfloorcur="USD"),
    )


def _round(round_number: int, buyer_price: float, seller_price: float, action: str = "counter"):
    return NegotiationRound(
        round_number=round_number,
        buyer_price=buyer_price,
        seller_price=seller_price,
        action=action,
        rationale="",
    )


def _proposal_response(
    *,
    proposal_id: str = "prop-1",
    recommendation: str = "counter",
    status: str = "counter_pending",
    proposed_price: float | None = 13.0,
) -> dict:
    counter_terms = None
    if proposed_price is not None:
        counter_terms = {
            "proposed_price": proposed_price,
            "floor_price": 10.0,
            "negotiation_id": "neg-1",
            "round_number": 1,
            "action": "counter",
        }
    return {
        "proposal_id": proposal_id,
        "recommendation": recommendation,
        "status": status,
        "counter_terms": counter_terms,
    }


def _requirements(max_cpm: float = CEILING) -> InventoryRequirements:
    return InventoryRequirements(
        media_type="display",
        deal_types=["PD"],
        max_cpm=max_cpm,
    )


def _deal_params(target_cpm: float | None = TARGET) -> DealParams:
    return DealParams(
        product_id="prod-display-001",
        deal_type="PD",
        impressions=500_000,
        flight_start="2026-08-01",
        flight_end="2026-08-31",
        target_cpm=target_cpm,
        media_type="display",
    )


@pytest.fixture
def mock_registry_client():
    client = AsyncMock()
    client.discover_sellers = AsyncMock(return_value=[_seller_card()])
    return client


@pytest.fixture
def mock_deals_client_factory():
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
def mock_event_bus():
    bus = AsyncMock()
    bus.publish = AsyncMock()
    return bus


@pytest.fixture
def mock_negotiation_client():
    client = AsyncMock()
    client.submit_proposal = AsyncMock(return_value=_proposal_response())
    client.counter_offer = AsyncMock()
    client.accept = AsyncMock(return_value={})
    client.decline = AsyncMock(return_value=None)
    return client


def _orchestrator(
    registry,
    deals_factory,
    bus,
    negotiation_client,
    config: NegotiationConfig | None = None,
) -> MultiSellerOrchestrator:
    return MultiSellerOrchestrator(
        registry_client=registry,
        deals_client_factory=deals_factory,
        event_bus=bus,
        quote_normalizer=QuoteNormalizer(),
        quote_timeout=5.0,
        negotiation_client=negotiation_client,
        negotiation_config=config or NegotiationConfig(),
    )


def _published_event_types(bus) -> list[EventType]:
    return [call.args[0].event_type for call in bus.publish.call_args_list]


# ---------------------------------------------------------------------------
# NegotiationConfig defaults
# ---------------------------------------------------------------------------


class TestNegotiationConfig:
    def test_defaults(self):
        cfg = NegotiationConfig()
        assert cfg.enabled is True
        assert cfg.band == 1.25
        assert cfg.max_rounds == 3


# ---------------------------------------------------------------------------
# Above ceiling, within band: negotiate then book
# ---------------------------------------------------------------------------


class TestNegotiateThenBook:
    @pytest.mark.asyncio
    async def test_above_ceiling_within_band_negotiates_and_books(
        self,
        mock_registry_client,
        mock_deals_client_factory,
        mock_event_bus,
        mock_negotiation_client,
    ):
        """$15 quote vs $12 ceiling: seller concedes to $11.50; buyer books it.

        Round 1 is the proposal counter ($13, above ceiling); round 2 the
        seller counters $11.50 <= ceiling; the buyer accepts, re-quotes at
        the agreed price, and books the FRESH quote.
        """
        client = mock_deals_client_factory(SELLER_URL)
        client.request_quote.side_effect = [
            _quote(quote_id="q-high", final_cpm=15.0),
            _quote(quote_id="q-neg", final_cpm=11.5),
        ]
        client.book_deal.return_value = _deal(quote_id="q-neg", final_cpm=11.5)
        mock_negotiation_client.counter_offer.return_value = _round(2, TARGET, 11.5)

        orch = _orchestrator(
            mock_registry_client,
            mock_deals_client_factory,
            mock_event_bus,
            mock_negotiation_client,
        )
        result = await orch.orchestrate(
            inventory_requirements=_requirements(),
            deal_params=_deal_params(),
            budget=100_000.0,
            max_deals=1,
        )

        assert len(result.selection.booked_deals) == 1
        deal = result.selection.booked_deals[0]
        assert deal.pricing.final_cpm == 11.5
        assert deal.pricing.final_cpm <= CEILING

        # The booking used the fresh (negotiated) quote, not the original.
        booking_request = client.book_deal.call_args.args[0]
        assert booking_request.quote_id == "q-neg"

        # The re-quote carried the agreed price as target_cpm.
        requote = client.request_quote.call_args_list[1].args[0]
        assert requote.target_cpm == 11.5

        # The negotiation opened at the buyer's target.
        proposal_kwargs = mock_negotiation_client.submit_proposal.call_args.kwargs
        assert proposal_kwargs["price"] == TARGET

        # Acceptance was signalled to the seller.
        mock_negotiation_client.accept.assert_awaited()

        # Negotiation surfaced in the result.
        assert len(result.negotiations) == 1
        assert result.negotiations[0]["outcome"] == "accepted"
        assert result.negotiations[0]["agreed_cpm"] == 11.5

    @pytest.mark.asyncio
    async def test_proposal_counter_within_ceiling_books_in_one_round(
        self,
        mock_registry_client,
        mock_deals_client_factory,
        mock_event_bus,
        mock_negotiation_client,
    ):
        """Seller's proposal-stage counter is already <= ceiling: accept it.

        This is the degenerate single-round path that works against
        today's real seller (whose /api/v1/negotiations/messages surface
        cannot continue a fresh negotiation -- see contract gap notes).
        """
        client = mock_deals_client_factory(SELLER_URL)
        client.request_quote.side_effect = [
            _quote(quote_id="q-high", final_cpm=15.0),
            _quote(quote_id="q-neg", final_cpm=11.0),
        ]
        client.book_deal.return_value = _deal(quote_id="q-neg", final_cpm=11.0)
        mock_negotiation_client.submit_proposal.return_value = _proposal_response(
            proposed_price=11.0
        )

        orch = _orchestrator(
            mock_registry_client,
            mock_deals_client_factory,
            mock_event_bus,
            mock_negotiation_client,
        )
        result = await orch.orchestrate(
            inventory_requirements=_requirements(),
            deal_params=_deal_params(),
            budget=100_000.0,
            max_deals=1,
        )

        assert len(result.selection.booked_deals) == 1
        assert result.selection.booked_deals[0].pricing.final_cpm == 11.0
        # No message-surface rounds were needed.
        mock_negotiation_client.counter_offer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_negotiation_events_emitted(
        self,
        mock_registry_client,
        mock_deals_client_factory,
        mock_event_bus,
        mock_negotiation_client,
    ):
        """Negotiation emits started/round/concluded in the event-bus idiom."""
        client = mock_deals_client_factory(SELLER_URL)
        client.request_quote.side_effect = [
            _quote(quote_id="q-high", final_cpm=15.0),
            _quote(quote_id="q-neg", final_cpm=11.5),
        ]
        client.book_deal.return_value = _deal(quote_id="q-neg", final_cpm=11.5)
        mock_negotiation_client.counter_offer.return_value = _round(2, TARGET, 11.5)

        orch = _orchestrator(
            mock_registry_client,
            mock_deals_client_factory,
            mock_event_bus,
            mock_negotiation_client,
        )
        await orch.orchestrate(
            inventory_requirements=_requirements(),
            deal_params=_deal_params(),
            budget=100_000.0,
            max_deals=1,
        )

        types = _published_event_types(mock_event_bus)
        assert EventType.NEGOTIATION_STARTED in types
        assert EventType.NEGOTIATION_ROUND in types
        assert EventType.NEGOTIATION_CONCLUDED in types


# ---------------------------------------------------------------------------
# Honest walk-aways
# ---------------------------------------------------------------------------


class TestHonestWalk:
    @pytest.mark.asyncio
    async def test_seller_refuses_to_come_down_walks(
        self,
        mock_registry_client,
        mock_deals_client_factory,
        mock_event_bus,
        mock_negotiation_client,
    ):
        """Seller's counters never reach the ceiling: no booking, decline sent."""
        client = mock_deals_client_factory(SELLER_URL)
        client.request_quote.return_value = _quote(quote_id="q-high", final_cpm=15.0)
        mock_negotiation_client.counter_offer.side_effect = [
            _round(2, TARGET, 14.0),
            _round(3, TARGET, 13.5),
            _round(4, TARGET, 13.2),
        ]

        orch = _orchestrator(
            mock_registry_client,
            mock_deals_client_factory,
            mock_event_bus,
            mock_negotiation_client,
        )
        result = await orch.orchestrate(
            inventory_requirements=_requirements(),
            deal_params=_deal_params(),
            budget=100_000.0,
            max_deals=1,
        )

        assert result.selection.booked_deals == []
        client.book_deal.assert_not_awaited()
        mock_negotiation_client.decline.assert_awaited()
        assert result.negotiations[0]["outcome"] == "walked_away"

    @pytest.mark.asyncio
    async def test_round_bound_is_enforced(
        self,
        mock_registry_client,
        mock_deals_client_factory,
        mock_event_bus,
        mock_negotiation_client,
    ):
        """At most max_rounds seller responses: proposal + (max_rounds - 1) counters."""
        client = mock_deals_client_factory(SELLER_URL)
        client.request_quote.return_value = _quote(quote_id="q-high", final_cpm=15.0)
        mock_negotiation_client.counter_offer.return_value = _round(2, TARGET, 14.0)

        orch = _orchestrator(
            mock_registry_client,
            mock_deals_client_factory,
            mock_event_bus,
            mock_negotiation_client,
            config=NegotiationConfig(max_rounds=3),
        )
        result = await orch.orchestrate(
            inventory_requirements=_requirements(),
            deal_params=_deal_params(),
            budget=100_000.0,
            max_deals=1,
        )

        assert result.selection.booked_deals == []
        # Proposal is round 1; the messages surface carries rounds 2..max.
        assert mock_negotiation_client.counter_offer.await_count == 2

    @pytest.mark.asyncio
    async def test_seller_reject_ends_negotiation(
        self,
        mock_registry_client,
        mock_deals_client_factory,
        mock_event_bus,
        mock_negotiation_client,
    ):
        """A terminal reject from the seller ends the negotiation early."""
        client = mock_deals_client_factory(SELLER_URL)
        client.request_quote.return_value = _quote(quote_id="q-high", final_cpm=15.0)
        mock_negotiation_client.counter_offer.return_value = _round(
            2, TARGET, 14.0, action="reject"
        )

        orch = _orchestrator(
            mock_registry_client,
            mock_deals_client_factory,
            mock_event_bus,
            mock_negotiation_client,
        )
        result = await orch.orchestrate(
            inventory_requirements=_requirements(),
            deal_params=_deal_params(),
            budget=100_000.0,
            max_deals=1,
        )

        assert result.selection.booked_deals == []
        assert mock_negotiation_client.counter_offer.await_count == 1

    @pytest.mark.asyncio
    async def test_negotiation_surface_unavailable_walks(
        self,
        mock_registry_client,
        mock_deals_client_factory,
        mock_event_bus,
        mock_negotiation_client,
    ):
        """Seller errors on the negotiation surface: honest walk, no crash.

        This is exactly what today's real seller does on a fresh
        negotiation (404: proposals are not persisted server-side).
        """
        client = mock_deals_client_factory(SELLER_URL)
        client.request_quote.return_value = _quote(quote_id="q-high", final_cpm=15.0)
        mock_negotiation_client.submit_proposal.side_effect = RuntimeError("404")

        orch = _orchestrator(
            mock_registry_client,
            mock_deals_client_factory,
            mock_event_bus,
            mock_negotiation_client,
        )
        result = await orch.orchestrate(
            inventory_requirements=_requirements(),
            deal_params=_deal_params(),
            budget=100_000.0,
            max_deals=1,
        )

        assert result.selection.booked_deals == []
        client.book_deal.assert_not_awaited()
        assert result.negotiations[0]["outcome"] == "unavailable"


# ---------------------------------------------------------------------------
# Band gating and config gating
# ---------------------------------------------------------------------------


class TestBandAndConfigGates:
    @pytest.mark.asyncio
    async def test_quote_beyond_band_not_negotiated(
        self,
        mock_registry_client,
        mock_deals_client_factory,
        mock_event_bus,
        mock_negotiation_client,
    ):
        """$16 quote vs $12 ceiling x 1.25 band = $15 limit: no attempt."""
        client = mock_deals_client_factory(SELLER_URL)
        client.request_quote.return_value = _quote(quote_id="q-high", final_cpm=16.0)

        orch = _orchestrator(
            mock_registry_client,
            mock_deals_client_factory,
            mock_event_bus,
            mock_negotiation_client,
        )
        result = await orch.orchestrate(
            inventory_requirements=_requirements(),
            deal_params=_deal_params(),
            budget=100_000.0,
            max_deals=1,
        )

        assert result.selection.booked_deals == []
        mock_negotiation_client.submit_proposal.assert_not_awaited()
        assert result.negotiations == []

    @pytest.mark.asyncio
    async def test_below_ceiling_books_without_negotiation(
        self,
        mock_registry_client,
        mock_deals_client_factory,
        mock_event_bus,
        mock_negotiation_client,
    ):
        """At/below-ceiling quotes book exactly as before -- no negotiation."""
        client = mock_deals_client_factory(SELLER_URL)
        client.request_quote.return_value = _quote(quote_id="q-ok", final_cpm=11.0)
        client.book_deal.return_value = _deal(quote_id="q-ok", final_cpm=11.0)

        orch = _orchestrator(
            mock_registry_client,
            mock_deals_client_factory,
            mock_event_bus,
            mock_negotiation_client,
        )
        result = await orch.orchestrate(
            inventory_requirements=_requirements(),
            deal_params=_deal_params(),
            budget=100_000.0,
            max_deals=1,
        )

        assert len(result.selection.booked_deals) == 1
        mock_negotiation_client.submit_proposal.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_disabled_config_restores_legacy_filter(
        self,
        mock_registry_client,
        mock_deals_client_factory,
        mock_event_bus,
        mock_negotiation_client,
    ):
        """negotiation disabled: above-ceiling quote is discarded, no attempt."""
        client = mock_deals_client_factory(SELLER_URL)
        client.request_quote.return_value = _quote(quote_id="q-high", final_cpm=15.0)

        orch = _orchestrator(
            mock_registry_client,
            mock_deals_client_factory,
            mock_event_bus,
            mock_negotiation_client,
            config=NegotiationConfig(enabled=False),
        )
        result = await orch.orchestrate(
            inventory_requirements=_requirements(),
            deal_params=_deal_params(),
            budget=100_000.0,
            max_deals=1,
        )

        assert result.selection.booked_deals == []
        mock_negotiation_client.submit_proposal.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_max_cpm_means_no_negotiation_path(
        self,
        mock_registry_client,
        mock_deals_client_factory,
        mock_event_bus,
        mock_negotiation_client,
    ):
        """Without a ceiling there is nothing to negotiate against."""
        client = mock_deals_client_factory(SELLER_URL)
        client.request_quote.return_value = _quote(quote_id="q-any", final_cpm=15.0)
        client.book_deal.return_value = _deal(quote_id="q-any", final_cpm=15.0)

        orch = _orchestrator(
            mock_registry_client,
            mock_deals_client_factory,
            mock_event_bus,
            mock_negotiation_client,
        )
        result = await orch.orchestrate(
            inventory_requirements=_requirements(max_cpm=None),
            deal_params=_deal_params(),
            budget=100_000.0,
            max_deals=1,
        )

        # Books at face value (no ceiling), never negotiates.
        assert len(result.selection.booked_deals) == 1
        mock_negotiation_client.submit_proposal.assert_not_awaited()


# ---------------------------------------------------------------------------
# Money invariants
# ---------------------------------------------------------------------------


class TestMoneyInvariants:
    @pytest.mark.asyncio
    async def test_buyer_never_offers_above_ceiling(
        self,
        mock_registry_client,
        mock_deals_client_factory,
        mock_event_bus,
        mock_negotiation_client,
    ):
        """Every price the buyer sends is <= its ceiling, even with no target."""
        client = mock_deals_client_factory(SELLER_URL)
        client.request_quote.return_value = _quote(quote_id="q-high", final_cpm=15.0)
        mock_negotiation_client.counter_offer.return_value = _round(2, CEILING, 14.0)

        orch = _orchestrator(
            mock_registry_client,
            mock_deals_client_factory,
            mock_event_bus,
            mock_negotiation_client,
        )
        await orch.orchestrate(
            inventory_requirements=_requirements(),
            deal_params=_deal_params(target_cpm=None),  # no target: open at ceiling
            budget=100_000.0,
            max_deals=1,
        )

        opening = mock_negotiation_client.submit_proposal.call_args.kwargs["price"]
        assert opening <= CEILING
        for call in mock_negotiation_client.counter_offer.call_args_list:
            offered = call.args[1] if len(call.args) > 1 else call.kwargs["price"]
            assert offered <= CEILING

    @pytest.mark.asyncio
    async def test_target_above_ceiling_is_clamped(
        self,
        mock_registry_client,
        mock_deals_client_factory,
        mock_event_bus,
        mock_negotiation_client,
    ):
        """A misconfigured target above the ceiling is clamped down to it."""
        client = mock_deals_client_factory(SELLER_URL)
        client.request_quote.return_value = _quote(quote_id="q-high", final_cpm=15.0)

        orch = _orchestrator(
            mock_registry_client,
            mock_deals_client_factory,
            mock_event_bus,
            mock_negotiation_client,
        )
        await orch.orchestrate(
            inventory_requirements=_requirements(),
            deal_params=_deal_params(target_cpm=14.0),  # above the $12 ceiling
            budget=100_000.0,
            max_deals=1,
        )

        opening = mock_negotiation_client.submit_proposal.call_args.kwargs["price"]
        assert opening == CEILING

    @pytest.mark.asyncio
    async def test_never_books_when_requote_comes_back_above_ceiling(
        self,
        mock_registry_client,
        mock_deals_client_factory,
        mock_event_bus,
        mock_negotiation_client,
    ):
        """Agreement at $11.50 but the re-quote returns $13: refuse to book."""
        client = mock_deals_client_factory(SELLER_URL)
        client.request_quote.side_effect = [
            _quote(quote_id="q-high", final_cpm=15.0),
            _quote(quote_id="q-neg", final_cpm=13.0),  # seller reneged
        ]
        mock_negotiation_client.counter_offer.return_value = _round(2, TARGET, 11.5)

        orch = _orchestrator(
            mock_registry_client,
            mock_deals_client_factory,
            mock_event_bus,
            mock_negotiation_client,
        )
        result = await orch.orchestrate(
            inventory_requirements=_requirements(),
            deal_params=_deal_params(),
            budget=100_000.0,
            max_deals=1,
        )

        assert result.selection.booked_deals == []
        client.book_deal.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_acceptance_price_never_above_ceiling(
        self,
        mock_registry_client,
        mock_deals_client_factory,
        mock_event_bus,
        mock_negotiation_client,
    ):
        """Sanity: a seller counter above ceiling is never treated as agreed."""
        client = mock_deals_client_factory(SELLER_URL)
        client.request_quote.return_value = _quote(quote_id="q-high", final_cpm=15.0)
        # Seller "accepts" -- but at a price above the ceiling. The engine
        # echo on accept is the buyer's own price, so this is a hostile
        # seller shape; the buyer must not book it.
        mock_negotiation_client.counter_offer.return_value = _round(
            2, TARGET, 13.0, action="accept"
        )

        orch = _orchestrator(
            mock_registry_client,
            mock_deals_client_factory,
            mock_event_bus,
            mock_negotiation_client,
        )
        result = await orch.orchestrate(
            inventory_requirements=_requirements(),
            deal_params=_deal_params(),
            budget=100_000.0,
            max_deals=1,
        )

        assert result.selection.booked_deals == []
        client.book_deal.assert_not_awaited()
