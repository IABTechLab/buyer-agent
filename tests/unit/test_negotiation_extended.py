# Author: Agent Range
# Donated to IAB Tech Lab

"""Extended tests for negotiation services — coverage gaps and edge cases.

Covers lines and branches NOT covered by the existing test_negotiation.py:
- NegotiationClient with api_key (auth headers)
- auto_negotiate: seller immediately accepts first offer
- auto_negotiate: seller accepts mid-loop counter-offer
- auto_negotiate: buyer accepts seller's improved counter mid-loop
- auto_negotiate: seller rejects mid-loop
- Concession step larger than gap to max_cpm
- Negative and zero prices
- Multiple rounds of concession tracking
- NegotiationRound model field defaults
- NegotiationResult with WALKED_AWAY and ERROR outcomes
- NegotiationOutcome enum completeness
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from ad_buyer.negotiation.client import NegotiationClient
from ad_buyer.negotiation.models import (
    NegotiationOutcome,
    NegotiationResult,
    NegotiationRound,
    NegotiationSession,
)
from ad_buyer.negotiation.strategy import NegotiationContext, NegotiationStrategy
from ad_buyer.negotiation.strategies.simple_threshold import SimpleThresholdStrategy


# =========================================================================
# Helper: build mock httpx responses
# =========================================================================


def _make_mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """Create a MagicMock mimicking an httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


def _patch_httpx(responses):
    """Return a context manager that patches httpx.AsyncClient with canned responses.

    `responses` can be a single MagicMock or a list (used as side_effect).
    """
    mock_client = AsyncMock()
    if isinstance(responses, list):
        mock_client.post.side_effect = responses
    else:
        mock_client.post.return_value = responses
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    patcher = patch("httpx.AsyncClient", return_value=mock_client)
    return patcher, mock_client


# =========================================================================
# NegotiationClient: api_key auth
# =========================================================================


class TestNegotiationClientAuth:
    """Verify that NegotiationClient includes X-API-Key when configured."""

    def test_build_headers_without_api_key(self):
        """Client with no api_key should have Content-Type only."""
        client = NegotiationClient()
        headers = client._build_headers()
        assert "Content-Type" in headers
        assert "X-API-Key" not in headers

    def test_build_headers_with_api_key(self):
        """Client with api_key should include X-API-Key header."""
        client = NegotiationClient(api_key="buyer-secret-key")
        headers = client._build_headers()
        assert headers["X-API-Key"] == "buyer-secret-key"
        assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_start_negotiation_sends_api_key(self):
        """start_negotiation should pass X-API-Key in headers."""
        client = NegotiationClient(api_key="my-secret")
        strategy = SimpleThresholdStrategy(
            target_cpm=20.0, max_cpm=30.0, concession_step=2.0, max_rounds=5
        )

        resp = _make_mock_response({
            "negotiation_id": "neg-auth",
            "seller_price": 35.0,
            "round_number": 1,
            "action": "counter",
        })

        patcher, mock_http = _patch_httpx(resp)
        with patcher:
            await client.start_negotiation(
                seller_url="http://seller.test",
                proposal_id="prop-auth",
                initial_price=20.0,
                strategy=strategy,
            )

        # Verify the POST call included the api key header
        call_kwargs = mock_http.post.call_args
        headers = call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {}))
        assert headers.get("X-API-Key") == "my-secret"


# =========================================================================
# auto_negotiate: seller immediately accepts first offer (line 263)
# =========================================================================


class TestAutoNegotiateSellerAcceptsImmediately:
    """Cover the branch where seller accepts the buyer's very first offer."""

    @pytest.mark.asyncio
    async def test_seller_accepts_on_first_round(self):
        """If seller responds to the first counter with action='accept',
        auto_negotiate should return ACCEPTED immediately."""
        strategy = SimpleThresholdStrategy(
            target_cpm=20.0, max_cpm=30.0, concession_step=2.0, max_rounds=5
        )

        # Seller's response to first counter: immediately accepts
        start_resp = _make_mock_response({
            "negotiation_id": "neg-instant",
            "seller_price": 20.0,
            "round_number": 1,
            "action": "accept",
        })

        client = NegotiationClient()
        patcher, _ = _patch_httpx(start_resp)
        with patcher:
            result = await client.auto_negotiate(
                seller_url="http://seller.test",
                proposal_id="prop-instant",
                strategy=strategy,
            )

        assert result.outcome == NegotiationOutcome.ACCEPTED
        assert result.final_price == 20.0
        assert result.rounds_count == 1


# =========================================================================
# auto_negotiate: seller accepts mid-loop counter (line 328)
# =========================================================================


class TestAutoNegotiateSellerAcceptsMidLoop:
    """Cover the branch where seller accepts a counter during the loop."""

    @pytest.mark.asyncio
    async def test_seller_accepts_counter_in_loop(self):
        """Seller rejects first round (price too high), then accepts
        the buyer's second counter-offer."""
        strategy = SimpleThresholdStrategy(
            target_cpm=20.0, max_cpm=30.0, concession_step=3.0, max_rounds=5
        )

        # Round 1: buyer offers 20, seller counters at 40 (too high)
        start_resp = _make_mock_response({
            "negotiation_id": "neg-loop",
            "seller_price": 40.0,
            "round_number": 1,
            "action": "counter",
        })

        # Round 2: buyer offers 23 (20 + 3), seller counters at 35 (still moving)
        counter_resp_1 = _make_mock_response({
            "seller_price": 35.0,
            "round_number": 2,
            "action": "counter",
        })

        # Round 3: buyer offers 26 (23 + 3), seller accepts
        counter_resp_2 = _make_mock_response({
            "seller_price": 26.0,
            "round_number": 3,
            "action": "accept",
        })

        client = NegotiationClient()
        patcher, _ = _patch_httpx([start_resp, counter_resp_1, counter_resp_2])
        with patcher:
            result = await client.auto_negotiate(
                seller_url="http://seller.test",
                proposal_id="prop-loop",
                strategy=strategy,
            )

        assert result.outcome == NegotiationOutcome.ACCEPTED
        assert result.final_price == 26.0
        assert result.rounds_count == 3


# =========================================================================
# auto_negotiate: buyer accepts seller's improved counter mid-loop (lines 344-345)
# =========================================================================


class TestAutoNegotiateBuyerAcceptsMidLoop:
    """Cover the branch where the buyer decides to accept during the loop."""

    @pytest.mark.asyncio
    async def test_buyer_accepts_seller_counter_in_loop(self):
        """Seller's first counter is too high, but after one round the
        seller drops below max_cpm, so the buyer accepts."""
        strategy = SimpleThresholdStrategy(
            target_cpm=20.0, max_cpm=30.0, concession_step=2.0, max_rounds=5
        )

        # Round 1: buyer offers 20, seller counters at 35 (above max_cpm=30)
        start_resp = _make_mock_response({
            "negotiation_id": "neg-buyeracc",
            "seller_price": 35.0,
            "round_number": 1,
            "action": "counter",
        })

        # Round 2: buyer offers 22, seller drops to 29 (below max_cpm=30)
        # Strategy should_accept(29) returns True since 29 <= 30
        counter_resp = _make_mock_response({
            "seller_price": 29.0,
            "round_number": 2,
            "action": "counter",
        })

        # Accept response
        accept_resp = _make_mock_response({
            "status": "accepted",
            "deal_price": 29.0,
        })

        client = NegotiationClient()
        patcher, _ = _patch_httpx([start_resp, counter_resp, accept_resp])
        with patcher:
            result = await client.auto_negotiate(
                seller_url="http://seller.test",
                proposal_id="prop-buyeracc",
                strategy=strategy,
            )

        assert result.outcome == NegotiationOutcome.ACCEPTED
        assert result.final_price == 29.0
        assert result.rounds_count == 2


# =========================================================================
# auto_negotiate: seller rejects mid-loop (line 355)
# =========================================================================


class TestAutoNegotiateSellerRejectsMidLoop:
    """Cover the branch where seller sends action='reject' during the loop."""

    @pytest.mark.asyncio
    async def test_seller_rejects_in_loop(self):
        """Seller counters once, then rejects (walks away from their side)."""
        strategy = SimpleThresholdStrategy(
            target_cpm=20.0, max_cpm=30.0, concession_step=2.0, max_rounds=10
        )

        # Round 1: seller counters at 35
        start_resp = _make_mock_response({
            "negotiation_id": "neg-reject",
            "seller_price": 35.0,
            "round_number": 1,
            "action": "counter",
        })

        # Round 2: seller rejects outright
        counter_resp = _make_mock_response({
            "seller_price": 35.0,
            "round_number": 2,
            "action": "reject",
        })

        client = NegotiationClient()
        patcher, _ = _patch_httpx([start_resp, counter_resp])
        with patcher:
            result = await client.auto_negotiate(
                seller_url="http://seller.test",
                proposal_id="prop-reject",
                strategy=strategy,
            )

        assert result.outcome == NegotiationOutcome.WALKED_AWAY
        assert result.final_price is None
        assert result.rounds_count == 2


# =========================================================================
# NegotiationClient: custom timeout
# =========================================================================


class TestNegotiationClientTimeout:
    """Verify timeout parameter is passed through."""

    def test_custom_timeout(self):
        """Custom timeout should be stored."""
        client = NegotiationClient(timeout=60.0)
        assert client._timeout == 60.0

    def test_default_timeout(self):
        """Default timeout should be 30.0."""
        client = NegotiationClient()
        assert client._timeout == 30.0


# =========================================================================
# SimpleThresholdStrategy: extended edge cases
# =========================================================================


class TestSimpleThresholdStrategyExtended:
    """Additional edge cases for the threshold strategy."""

    def test_concession_step_exceeds_gap_to_max(self):
        """When concession step would push above max_cpm, cap at max_cpm."""
        strategy = SimpleThresholdStrategy(
            target_cpm=25.0, max_cpm=27.0, concession_step=5.0, max_rounds=5
        )
        ctx = NegotiationContext(
            rounds_completed=1,
            seller_last_price=40.0,
            our_last_offer=25.0,
        )
        # 25 + 5 = 30, but max is 27
        assert strategy.next_offer(40.0, ctx) == 27.0

    def test_multiple_concession_rounds(self):
        """Track concession across multiple rounds."""
        strategy = SimpleThresholdStrategy(
            target_cpm=10.0, max_cpm=20.0, concession_step=2.0, max_rounds=10
        )

        offers = []
        last_offer = None
        for round_num in range(6):
            ctx = NegotiationContext(
                rounds_completed=round_num,
                seller_last_price=25.0,
                our_last_offer=last_offer,
            )
            offer = strategy.next_offer(25.0, ctx)
            offers.append(offer)
            last_offer = offer

        # First offer: 10 (target), then 12, 14, 16, 18, 20
        assert offers == [10.0, 12.0, 14.0, 16.0, 18.0, 20.0]

    def test_accept_below_target(self):
        """Accept when seller price is below target (great deal)."""
        strategy = SimpleThresholdStrategy(
            target_cpm=20.0, max_cpm=30.0, concession_step=2.0, max_rounds=5
        )
        ctx = NegotiationContext(
            rounds_completed=0,
            seller_last_price=15.0,
            our_last_offer=None,
        )
        assert strategy.should_accept(15.0, ctx) is True

    def test_walk_away_seller_raised_price(self):
        """Walk away if seller raised their price (worse than previous)."""
        strategy = SimpleThresholdStrategy(
            target_cpm=20.0, max_cpm=30.0, concession_step=2.0, max_rounds=10
        )
        ctx = NegotiationContext(
            rounds_completed=2,
            seller_last_price=36.0,
            our_last_offer=22.0,
            seller_previous_price=35.0,
        )
        # Seller raised price from 35 to 36, should walk away
        assert strategy.should_walk_away(36.0, ctx) is True

    def test_walk_away_not_triggered_on_first_round_with_no_previous(self):
        """First round with zero rounds completed does not walk away."""
        strategy = SimpleThresholdStrategy(
            target_cpm=20.0, max_cpm=30.0, concession_step=2.0, max_rounds=5
        )
        ctx = NegotiationContext(
            rounds_completed=0,
            seller_last_price=50.0,
            our_last_offer=None,
            seller_previous_price=None,
        )
        assert strategy.should_walk_away(50.0, ctx) is False

    def test_very_large_max_rounds(self):
        """Strategy with large max_rounds stays in negotiation."""
        strategy = SimpleThresholdStrategy(
            target_cpm=20.0, max_cpm=30.0, concession_step=0.5, max_rounds=100
        )
        ctx = NegotiationContext(
            rounds_completed=50,
            seller_last_price=35.0,
            our_last_offer=28.0,
            seller_previous_price=36.0,  # seller is still moving down
        )
        assert strategy.should_walk_away(35.0, ctx) is False


# =========================================================================
# NegotiationModel edge cases
# =========================================================================


class TestNegotiationModelsExtended:
    """Extended tests for negotiation Pydantic/data models."""

    def test_negotiation_round_defaults(self):
        """NegotiationRound should have sensible defaults."""
        rnd = NegotiationRound(
            round_number=1,
            buyer_price=20.0,
            seller_price=35.0,
            action="counter",
        )
        assert rnd.rationale == ""
        assert isinstance(rnd.timestamp, datetime)

    def test_negotiation_round_with_rationale(self):
        """NegotiationRound with explicit rationale."""
        rnd = NegotiationRound(
            round_number=2,
            buyer_price=22.0,
            seller_price=32.0,
            action="counter",
            rationale="Offered midpoint between positions",
        )
        assert rnd.rationale == "Offered midpoint between positions"

    def test_negotiation_session_no_prior_offer(self):
        """Session created without a last offer."""
        session = NegotiationSession(
            proposal_id="prop-new",
            seller_url="http://seller.test",
            negotiation_id="neg-new",
            current_seller_price=40.0,
        )
        assert session.our_last_offer is None
        assert session.rounds == []
        assert isinstance(session.started_at, datetime)

    def test_negotiation_result_walked_away(self):
        """NegotiationResult for a walked-away negotiation."""
        result = NegotiationResult(
            proposal_id="prop-walk",
            outcome=NegotiationOutcome.WALKED_AWAY,
            final_price=None,
            rounds_count=5,
        )
        assert result.outcome == NegotiationOutcome.WALKED_AWAY
        assert result.final_price is None
        assert result.rounds == []

    def test_negotiation_result_error_outcome(self):
        """NegotiationResult can represent an error outcome."""
        result = NegotiationResult(
            proposal_id="prop-err",
            outcome=NegotiationOutcome.ERROR,
            final_price=None,
            rounds_count=0,
        )
        assert result.outcome == NegotiationOutcome.ERROR

    def test_negotiation_result_declined_outcome(self):
        """NegotiationResult for a declined negotiation."""
        result = NegotiationResult(
            proposal_id="prop-dec",
            outcome=NegotiationOutcome.DECLINED,
            final_price=None,
            rounds_count=0,
        )
        assert result.outcome == NegotiationOutcome.DECLINED

    def test_outcome_enum_values(self):
        """All expected NegotiationOutcome values exist."""
        assert NegotiationOutcome.ACCEPTED == "accepted"
        assert NegotiationOutcome.WALKED_AWAY == "walked_away"
        assert NegotiationOutcome.DECLINED == "declined"
        assert NegotiationOutcome.ERROR == "error"
        assert len(NegotiationOutcome) == 4

    def test_session_rounds_are_ordered(self):
        """Rounds appended to session maintain order."""
        session = NegotiationSession(
            proposal_id="prop-ord",
            seller_url="http://seller.test",
            negotiation_id="neg-ord",
            current_seller_price=35.0,
        )
        for i in range(1, 4):
            session.rounds.append(
                NegotiationRound(
                    round_number=i,
                    buyer_price=20.0 + i,
                    seller_price=35.0 - i,
                    action="counter",
                )
            )
        assert [r.round_number for r in session.rounds] == [1, 2, 3]

    def test_negotiation_result_with_rounds_list(self):
        """NegotiationResult can store the full round history."""
        rounds = [
            NegotiationRound(
                round_number=1, buyer_price=20.0, seller_price=35.0, action="counter"
            ),
            NegotiationRound(
                round_number=2, buyer_price=22.0, seller_price=30.0, action="accept"
            ),
        ]
        result = NegotiationResult(
            proposal_id="prop-hist",
            outcome=NegotiationOutcome.ACCEPTED,
            final_price=30.0,
            rounds_count=2,
            rounds=rounds,
        )
        assert len(result.rounds) == 2
        assert result.rounds[0].buyer_price == 20.0
        assert result.rounds[1].action == "accept"


# =========================================================================
# NegotiationContext extended
# =========================================================================


class TestNegotiationContextExtended:
    """Additional tests for NegotiationContext."""

    def test_context_with_all_fields(self):
        """Context with every field populated."""
        ctx = NegotiationContext(
            rounds_completed=5,
            seller_last_price=28.0,
            our_last_offer=26.0,
            seller_previous_price=30.0,
        )
        assert ctx.rounds_completed == 5
        assert ctx.seller_last_price == 28.0
        assert ctx.our_last_offer == 26.0
        assert ctx.seller_previous_price == 30.0

    def test_context_zero_rounds(self):
        """Context at the very start of negotiation."""
        ctx = NegotiationContext(
            rounds_completed=0,
            seller_last_price=0.0,
            our_last_offer=None,
        )
        assert ctx.rounds_completed == 0
        assert ctx.our_last_offer is None
        assert ctx.seller_previous_price is None


# =========================================================================
# Custom strategy implementation (verify ABC works)
# =========================================================================


class AlwaysAcceptStrategy(NegotiationStrategy):
    """Test strategy that always accepts."""

    def should_accept(self, seller_price: float, context: NegotiationContext) -> bool:
        return True

    def next_offer(self, seller_price: float, context: NegotiationContext) -> float:
        return seller_price

    def should_walk_away(self, seller_price: float, context: NegotiationContext) -> bool:
        return False


class AlwaysWalkAwayStrategy(NegotiationStrategy):
    """Test strategy that always walks away."""

    def should_accept(self, seller_price: float, context: NegotiationContext) -> bool:
        return False

    def next_offer(self, seller_price: float, context: NegotiationContext) -> float:
        return 1.0  # lowball

    def should_walk_away(self, seller_price: float, context: NegotiationContext) -> bool:
        return True


class TestCustomStrategies:
    """Verify custom strategy implementations work with the ABC."""

    def test_always_accept_strategy(self):
        """AlwaysAcceptStrategy should accept any price."""
        strategy = AlwaysAcceptStrategy()
        ctx = NegotiationContext(rounds_completed=0, seller_last_price=100.0)
        assert strategy.should_accept(100.0, ctx) is True
        assert strategy.should_walk_away(100.0, ctx) is False
        assert strategy.next_offer(100.0, ctx) == 100.0

    def test_always_walk_away_strategy(self):
        """AlwaysWalkAwayStrategy should always walk away."""
        strategy = AlwaysWalkAwayStrategy()
        ctx = NegotiationContext(rounds_completed=0, seller_last_price=1.0)
        assert strategy.should_accept(1.0, ctx) is False
        assert strategy.should_walk_away(1.0, ctx) is True

    @pytest.mark.asyncio
    async def test_auto_negotiate_with_always_accept(self):
        """auto_negotiate with AlwaysAcceptStrategy should accept immediately."""
        strategy = AlwaysAcceptStrategy()

        # Seller responds with a counter
        start_resp = _make_mock_response({
            "negotiation_id": "neg-alwaysacc",
            "seller_price": 50.0,
            "round_number": 1,
            "action": "counter",
        })

        accept_resp = _make_mock_response({
            "status": "accepted",
            "deal_price": 50.0,
        })

        client = NegotiationClient()
        patcher, _ = _patch_httpx([start_resp, accept_resp])
        with patcher:
            result = await client.auto_negotiate(
                seller_url="http://seller.test",
                proposal_id="prop-alwaysacc",
                strategy=strategy,
            )

        assert result.outcome == NegotiationOutcome.ACCEPTED
        assert result.final_price == 50.0

    @pytest.mark.asyncio
    async def test_auto_negotiate_with_always_walk_away(self):
        """auto_negotiate with AlwaysWalkAwayStrategy should walk away after first round."""
        strategy = AlwaysWalkAwayStrategy()

        # Seller responds with a counter (price too high)
        start_resp = _make_mock_response({
            "negotiation_id": "neg-alwayswalk",
            "seller_price": 50.0,
            "round_number": 1,
            "action": "counter",
        })

        decline_resp = _make_mock_response({"status": "declined"})

        client = NegotiationClient()
        patcher, _ = _patch_httpx([start_resp, decline_resp])
        with patcher:
            result = await client.auto_negotiate(
                seller_url="http://seller.test",
                proposal_id="prop-alwayswalk",
                strategy=strategy,
            )

        assert result.outcome == NegotiationOutcome.WALKED_AWAY
        assert result.final_price is None


# =========================================================================
# NegotiationClient: counter_offer updates session state
# =========================================================================


class TestCounterOfferSessionState:
    """Verify counter_offer correctly updates the session."""

    @pytest.mark.asyncio
    async def test_counter_offer_updates_session_price(self):
        """counter_offer should update session.current_seller_price."""
        client = NegotiationClient()
        session = NegotiationSession(
            proposal_id="prop-state",
            seller_url="http://seller.test",
            negotiation_id="neg-state",
            current_seller_price=40.0,
            our_last_offer=20.0,
        )

        resp = _make_mock_response({
            "seller_price": 33.0,
            "round_number": 2,
            "action": "counter",
        })

        patcher, _ = _patch_httpx(resp)
        with patcher:
            rnd = await client.counter_offer(session, price=22.0)

        assert session.current_seller_price == 33.0
        assert session.our_last_offer == 22.0
        assert len(session.rounds) == 1
        assert rnd.round_number == 2

    @pytest.mark.asyncio
    async def test_multiple_counter_offers_accumulate_rounds(self):
        """Multiple counter_offers should accumulate rounds in session."""
        client = NegotiationClient()
        session = NegotiationSession(
            proposal_id="prop-multi",
            seller_url="http://seller.test",
            negotiation_id="neg-multi",
            current_seller_price=40.0,
            our_last_offer=20.0,
        )

        responses = [
            _make_mock_response({"seller_price": 36.0, "round_number": 2, "action": "counter"}),
            _make_mock_response({"seller_price": 33.0, "round_number": 3, "action": "counter"}),
            _make_mock_response({"seller_price": 30.0, "round_number": 4, "action": "counter"}),
        ]

        for i, resp in enumerate(responses):
            patcher, _ = _patch_httpx(resp)
            with patcher:
                await client.counter_offer(session, price=22.0 + i * 2)

        assert len(session.rounds) == 3
        assert session.current_seller_price == 30.0
        assert session.our_last_offer == 26.0


# =========================================================================
# NegotiationClient: start_negotiation records first round
# =========================================================================


class TestStartNegotiationRoundTracking:
    """Verify start_negotiation records the first round correctly."""

    @pytest.mark.asyncio
    async def test_first_round_recorded_in_session(self):
        """start_negotiation should add a round to the session."""
        client = NegotiationClient()
        strategy = SimpleThresholdStrategy(
            target_cpm=20.0, max_cpm=30.0, concession_step=2.0, max_rounds=5
        )

        resp = _make_mock_response({
            "negotiation_id": "neg-track",
            "seller_price": 38.0,
            "round_number": 1,
            "action": "counter",
            "rationale": "Starting at $38 CPM",
        })

        patcher, _ = _patch_httpx(resp)
        with patcher:
            session = await client.start_negotiation(
                seller_url="http://seller.test",
                proposal_id="prop-track",
                initial_price=20.0,
                strategy=strategy,
            )

        assert len(session.rounds) == 1
        rnd = session.rounds[0]
        assert rnd.round_number == 1
        assert rnd.buyer_price == 20.0
        assert rnd.seller_price == 38.0
        assert rnd.action == "counter"
        assert rnd.rationale == "Starting at $38 CPM"

    @pytest.mark.asyncio
    async def test_start_negotiation_fallback_defaults(self):
        """start_negotiation handles missing fields in seller response."""
        client = NegotiationClient()
        strategy = SimpleThresholdStrategy(
            target_cpm=20.0, max_cpm=30.0, concession_step=2.0, max_rounds=5
        )

        # Minimal response (missing negotiation_id, seller_price, etc.)
        resp = _make_mock_response({
            "current_price": 35.0,
        })

        patcher, _ = _patch_httpx(resp)
        with patcher:
            session = await client.start_negotiation(
                seller_url="http://seller.test",
                proposal_id="prop-fallback",
                initial_price=20.0,
                strategy=strategy,
            )

        # Should use fallback negotiation_id
        assert session.negotiation_id == "neg-prop-fallback"
        # Should use current_price since seller_price is missing
        assert session.current_seller_price == 35.0
