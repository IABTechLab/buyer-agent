# Tests for the multi-turn negotiation client (buyer-llu)

"""Test suite for negotiation strategy, client, and models.

Covers:
- SimpleThresholdStrategy accept/counter/walk-away logic
- NegotiationClient with mocked HTTP
- auto_negotiate full loop
- Strategy swapping (same client, different strategies)
- Edge cases
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ad_buyer.negotiation.client import NegotiationClient
from ad_buyer.negotiation.models import (
    NegotiationOutcome,
    NegotiationResult,
    NegotiationRound,
    NegotiationSession,
)
from ad_buyer.negotiation.strategies.adaptive import AdaptiveStrategy
from ad_buyer.negotiation.strategies.competitive import CompetitiveStrategy
from ad_buyer.negotiation.strategies.simple_threshold import SimpleThresholdStrategy
from ad_buyer.negotiation.strategy import NegotiationContext
from ad_buyer.negotiation.strategy import NegotiationStrategy as NegotiationStrategyABC

# =========================================================================
# SimpleThresholdStrategy tests
# =========================================================================


class TestSimpleThresholdStrategy:
    """Tests for the v1 threshold-based negotiation strategy."""

    def setup_method(self):
        self.strategy = SimpleThresholdStrategy(
            target_cpm=20.0,
            max_cpm=30.0,
            concession_step=2.0,
            max_rounds=5,
        )

    def test_should_accept_at_or_below_max_cpm(self):
        """Accept when seller's price is at or below max_cpm."""
        ctx = NegotiationContext(
            rounds_completed=1,
            seller_last_price=28.0,
            our_last_offer=22.0,
        )
        assert self.strategy.should_accept(28.0, ctx) is True

    def test_should_accept_at_max_cpm_boundary(self):
        """Accept exactly at max_cpm."""
        ctx = NegotiationContext(
            rounds_completed=0,
            seller_last_price=30.0,
            our_last_offer=20.0,
        )
        assert self.strategy.should_accept(30.0, ctx) is True

    def test_should_not_accept_above_max_cpm(self):
        """Reject when seller's price exceeds max_cpm."""
        ctx = NegotiationContext(
            rounds_completed=1,
            seller_last_price=35.0,
            our_last_offer=22.0,
        )
        assert self.strategy.should_accept(35.0, ctx) is False

    def test_next_offer_starts_at_target(self):
        """First offer should be at target_cpm."""
        ctx = NegotiationContext(
            rounds_completed=0,
            seller_last_price=40.0,
            our_last_offer=None,
        )
        offer = self.strategy.next_offer(40.0, ctx)
        assert offer == 20.0  # target_cpm

    def test_next_offer_concedes_by_step(self):
        """Subsequent offers increase by concession_step."""
        ctx = NegotiationContext(
            rounds_completed=1,
            seller_last_price=35.0,
            our_last_offer=20.0,
        )
        offer = self.strategy.next_offer(35.0, ctx)
        assert offer == 22.0  # 20 + 2 (concession_step)

    def test_next_offer_never_exceeds_max_cpm(self):
        """Offers are capped at max_cpm."""
        ctx = NegotiationContext(
            rounds_completed=10,
            seller_last_price=50.0,
            our_last_offer=29.0,
        )
        offer = self.strategy.next_offer(50.0, ctx)
        assert offer == 30.0  # capped at max_cpm

    def test_should_walk_away_after_max_rounds(self):
        """Walk away when max_rounds are exhausted."""
        ctx = NegotiationContext(
            rounds_completed=5,
            seller_last_price=35.0,
            our_last_offer=28.0,
        )
        assert self.strategy.should_walk_away(35.0, ctx) is True

    def test_should_not_walk_away_before_max_rounds(self):
        """Stay in negotiation before max_rounds."""
        ctx = NegotiationContext(
            rounds_completed=2,
            seller_last_price=35.0,
            our_last_offer=22.0,
        )
        assert self.strategy.should_walk_away(35.0, ctx) is False

    def test_should_walk_away_seller_not_moving(self):
        """Walk away if seller hasn't moved (same price as last round)."""
        ctx = NegotiationContext(
            rounds_completed=2,
            seller_last_price=35.0,
            our_last_offer=22.0,
            seller_previous_price=35.0,
        )
        assert self.strategy.should_walk_away(35.0, ctx) is True

    def test_should_not_walk_away_if_seller_is_moving(self):
        """Stay if seller is conceding."""
        ctx = NegotiationContext(
            rounds_completed=2,
            seller_last_price=33.0,
            our_last_offer=22.0,
            seller_previous_price=35.0,
        )
        assert self.strategy.should_walk_away(33.0, ctx) is False


# =========================================================================
# NegotiationContext tests
# =========================================================================


class TestNegotiationContext:
    """Tests for the NegotiationContext model."""

    def test_context_creation(self):
        ctx = NegotiationContext(
            rounds_completed=2,
            seller_last_price=30.0,
            our_last_offer=22.0,
        )
        assert ctx.rounds_completed == 2
        assert ctx.seller_last_price == 30.0
        assert ctx.our_last_offer == 22.0
        assert ctx.seller_previous_price is None
        assert ctx.started_at is not None

    def test_context_with_seller_previous_price(self):
        ctx = NegotiationContext(
            rounds_completed=3,
            seller_last_price=28.0,
            our_last_offer=24.0,
            seller_previous_price=30.0,
        )
        assert ctx.seller_previous_price == 30.0


# =========================================================================
# NegotiationClient tests (mocked HTTP)
# =========================================================================


class TestNegotiationClient:
    """Tests for the NegotiationClient with mocked HTTP calls."""

    def setup_method(self):
        self.client = NegotiationClient()

    @pytest.mark.asyncio
    async def test_start_negotiation(self):
        """start_negotiation should POST to proposals/{id}/counter and return a session."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "negotiation_id": "neg-abc123",
            "proposal_id": "prop-001",
            "status": "active",
            "current_price": 35.0,
            "round_number": 1,
        }
        mock_response.raise_for_status = MagicMock()

        strategy = SimpleThresholdStrategy(
            target_cpm=20.0,
            max_cpm=30.0,
            concession_step=2.0,
            max_rounds=5,
        )

        with patch("httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.post.return_value = mock_response
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client_instance

            session = await self.client.start_negotiation(
                seller_url="http://localhost:8000",
                proposal_id="prop-001",
                initial_price=20.0,
                strategy=strategy,
            )

            assert isinstance(session, NegotiationSession)
            assert session.proposal_id == "prop-001"
            assert session.seller_url == "http://localhost:8000"

    @pytest.mark.asyncio
    async def test_counter_offer(self):
        """counter_offer should POST to proposals/{id}/counter."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "round_number": 2,
            "buyer_price": 22.0,
            "seller_price": 32.0,
            "action": "counter",
            "rationale": "Counter at $32.00 CPM",
        }
        mock_response.raise_for_status = MagicMock()

        session = NegotiationSession(
            proposal_id="prop-001",
            seller_url="http://localhost:8000",
            negotiation_id="neg-abc123",
            current_seller_price=35.0,
            our_last_offer=20.0,
            rounds=[],
        )

        with patch("httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.post.return_value = mock_response
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client_instance

            round_result = await self.client.counter_offer(session, price=22.0)

            assert isinstance(round_result, NegotiationRound)
            assert round_result.buyer_price == 22.0
            assert round_result.seller_price == 32.0
            assert round_result.action == "counter"

    @pytest.mark.asyncio
    async def test_accept(self):
        """accept should POST acceptance to the seller."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "accepted",
            "deal_price": 28.0,
            "proposal_id": "prop-001",
        }
        mock_response.raise_for_status = MagicMock()

        session = NegotiationSession(
            proposal_id="prop-001",
            seller_url="http://localhost:8000",
            negotiation_id="neg-abc123",
            current_seller_price=28.0,
            our_last_offer=26.0,
            rounds=[],
        )

        with patch("httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.post.return_value = mock_response
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client_instance

            result = await self.client.accept(session)
            assert result["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_decline(self):
        """decline should POST decline to the seller."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "declined"}
        mock_response.raise_for_status = MagicMock()

        session = NegotiationSession(
            proposal_id="prop-001",
            seller_url="http://localhost:8000",
            negotiation_id="neg-abc123",
            current_seller_price=35.0,
            our_last_offer=28.0,
            rounds=[],
        )

        with patch("httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.post.return_value = mock_response
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client_instance

            await self.client.decline(session)
            mock_client_instance.post.assert_called_once()


# =========================================================================
# auto_negotiate tests
# =========================================================================


class TestAutoNegotiate:
    """Tests for the auto_negotiate full loop."""

    @pytest.mark.asyncio
    async def test_auto_negotiate_accepts_good_price(self):
        """Auto-negotiate should accept when seller price is within range."""
        strategy = SimpleThresholdStrategy(
            target_cpm=20.0,
            max_cpm=30.0,
            concession_step=2.0,
            max_rounds=5,
        )

        # Seller starts at 28, which is <= max_cpm, so buyer accepts after first round
        start_response = MagicMock()
        start_response.status_code = 200
        start_response.json.return_value = {
            "negotiation_id": "neg-abc123",
            "proposal_id": "prop-001",
            "status": "active",
            "current_price": 28.0,
            "round_number": 1,
            "action": "counter",
            "seller_price": 28.0,
            "buyer_price": 20.0,
        }
        start_response.raise_for_status = MagicMock()

        accept_response = MagicMock()
        accept_response.status_code = 200
        accept_response.json.return_value = {
            "status": "accepted",
            "deal_price": 28.0,
            "proposal_id": "prop-001",
        }
        accept_response.raise_for_status = MagicMock()

        client = NegotiationClient()

        with patch("httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.post.side_effect = [start_response, accept_response]
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client_instance

            result = await client.auto_negotiate(
                seller_url="http://localhost:8000",
                proposal_id="prop-001",
                strategy=strategy,
            )

            assert isinstance(result, NegotiationResult)
            assert result.outcome == NegotiationOutcome.ACCEPTED

    @pytest.mark.asyncio
    async def test_auto_negotiate_walks_away(self):
        """Auto-negotiate should walk away when seller won't budge."""
        strategy = SimpleThresholdStrategy(
            target_cpm=20.0,
            max_cpm=25.0,
            concession_step=1.0,
            max_rounds=3,
        )

        # Seller always responds with 40 (won't budge)
        responses = []
        for i in range(4):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "negotiation_id": "neg-abc123",
                "proposal_id": "prop-001",
                "status": "active",
                "current_price": 40.0,
                "round_number": i + 1,
                "action": "counter",
                "seller_price": 40.0,
                "buyer_price": 20.0 + i,
            }
            resp.raise_for_status = MagicMock()
            responses.append(resp)

        # Add a decline response
        decline_resp = MagicMock()
        decline_resp.status_code = 200
        decline_resp.json.return_value = {"status": "declined"}
        decline_resp.raise_for_status = MagicMock()
        responses.append(decline_resp)

        client = NegotiationClient()

        with patch("httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.post.side_effect = responses
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client_instance

            result = await client.auto_negotiate(
                seller_url="http://localhost:8000",
                proposal_id="prop-001",
                strategy=strategy,
            )

            assert isinstance(result, NegotiationResult)
            assert result.outcome == NegotiationOutcome.WALKED_AWAY


# =========================================================================
# Strategy swapping tests
# =========================================================================


class TestStrategySwapping:
    """Verify the same client works with different strategy implementations."""

    def test_strategies_are_swappable(self):
        """All strategies share the same ABC interface."""
        strategies = [
            SimpleThresholdStrategy(
                target_cpm=20.0, max_cpm=30.0, concession_step=2.0, max_rounds=5
            ),
        ]
        for s in strategies:
            assert isinstance(s, NegotiationStrategyABC)
            assert hasattr(s, "should_accept")
            assert hasattr(s, "next_offer")
            assert hasattr(s, "should_walk_away")

    def test_different_strategies_give_different_results(self):
        """Two different strategies should produce different decisions for same input."""
        conservative = SimpleThresholdStrategy(
            target_cpm=15.0, max_cpm=20.0, concession_step=1.0, max_rounds=3
        )
        aggressive = SimpleThresholdStrategy(
            target_cpm=15.0, max_cpm=35.0, concession_step=5.0, max_rounds=10
        )
        ctx = NegotiationContext(
            rounds_completed=1,
            seller_last_price=25.0,
            our_last_offer=15.0,
        )

        # Conservative rejects at 25, aggressive accepts
        assert conservative.should_accept(25.0, ctx) is False
        assert aggressive.should_accept(25.0, ctx) is True

    def test_adaptive_strategy_raises_not_implemented(self):
        """AdaptiveStrategy stub should raise NotImplementedError."""
        strategy = AdaptiveStrategy()
        ctx = NegotiationContext(
            rounds_completed=0,
            seller_last_price=30.0,
            our_last_offer=None,
        )
        with pytest.raises(NotImplementedError):
            strategy.should_accept(30.0, ctx)
        with pytest.raises(NotImplementedError):
            strategy.next_offer(30.0, ctx)
        with pytest.raises(NotImplementedError):
            strategy.should_walk_away(30.0, ctx)

    def test_competitive_strategy_raises_not_implemented(self):
        """CompetitiveStrategy stub should raise NotImplementedError."""
        strategy = CompetitiveStrategy()
        ctx = NegotiationContext(
            rounds_completed=0,
            seller_last_price=30.0,
            our_last_offer=None,
        )
        with pytest.raises(NotImplementedError):
            strategy.should_accept(30.0, ctx)
        with pytest.raises(NotImplementedError):
            strategy.next_offer(30.0, ctx)
        with pytest.raises(NotImplementedError):
            strategy.should_walk_away(30.0, ctx)


# =========================================================================
# Edge case tests
# =========================================================================


class TestEdgeCases:
    """Edge case tests for negotiation logic."""

    def test_zero_concession_step(self):
        """Strategy with zero concession_step always offers target."""
        strategy = SimpleThresholdStrategy(
            target_cpm=20.0,
            max_cpm=30.0,
            concession_step=0.0,
            max_rounds=5,
        )
        ctx = NegotiationContext(
            rounds_completed=3,
            seller_last_price=35.0,
            our_last_offer=20.0,
        )
        assert strategy.next_offer(35.0, ctx) == 20.0

    def test_max_cpm_equals_target(self):
        """When max_cpm == target, only accept at target or below."""
        strategy = SimpleThresholdStrategy(
            target_cpm=25.0,
            max_cpm=25.0,
            concession_step=1.0,
            max_rounds=3,
        )
        ctx = NegotiationContext(
            rounds_completed=0,
            seller_last_price=25.0,
            our_last_offer=None,
        )
        assert strategy.should_accept(25.0, ctx) is True
        assert strategy.should_accept(25.01, ctx) is False

    def test_first_round_walk_away_not_triggered(self):
        """Walk-away should not trigger on first round (no previous price)."""
        strategy = SimpleThresholdStrategy(
            target_cpm=20.0,
            max_cpm=30.0,
            concession_step=2.0,
            max_rounds=5,
        )
        ctx = NegotiationContext(
            rounds_completed=0,
            seller_last_price=35.0,
            our_last_offer=None,
            seller_previous_price=None,
        )
        assert strategy.should_walk_away(35.0, ctx) is False

    def test_negotiation_session_tracks_rounds(self):
        """NegotiationSession should track rounds as they're added."""
        session = NegotiationSession(
            proposal_id="prop-001",
            seller_url="http://localhost:8000",
            negotiation_id="neg-abc",
            current_seller_price=35.0,
            our_last_offer=20.0,
            rounds=[],
        )
        assert len(session.rounds) == 0

        session.rounds.append(
            NegotiationRound(
                round_number=1,
                buyer_price=20.0,
                seller_price=33.0,
                action="counter",
            )
        )
        assert len(session.rounds) == 1

    def test_negotiation_result_model(self):
        """NegotiationResult captures full negotiation outcome."""
        result = NegotiationResult(
            proposal_id="prop-001",
            outcome=NegotiationOutcome.ACCEPTED,
            final_price=28.0,
            rounds_count=3,
            rounds=[],
        )
        assert result.outcome == NegotiationOutcome.ACCEPTED
        assert result.final_price == 28.0
