# Author: Agent Range
# Donated to IAB Tech Lab

"""Negotiation client for multi-turn buyer-seller negotiation.

Handles HTTP communication with the seller's negotiation endpoints
and drives the negotiation loop using a pluggable NegotiationStrategy.
"""

import logging
from typing import Any

import httpx

from .models import (
    NegotiationOutcome,
    NegotiationResult,
    NegotiationRound,
    NegotiationSession,
)
from .strategy import NegotiationContext, NegotiationStrategy

logger = logging.getLogger(__name__)


class NegotiationClient:
    """Client for multi-turn negotiation with ad sellers.

    Supports both manual step-by-step negotiation and fully automatic
    negotiation via the auto_negotiate method. Uses a pluggable
    NegotiationStrategy to decide accept/counter/walk-away.

    Example (auto):
        client = NegotiationClient()
        strategy = SimpleThresholdStrategy(target_cpm=20, max_cpm=30, ...)
        result = await client.auto_negotiate(seller_url, proposal_id, strategy)

    Example (manual):
        session = await client.start_negotiation(seller_url, proposal_id, 20.0, strategy)
        round_result = await client.counter_offer(session, 22.0)
        if round_result.action == "accept":
            await client.accept(session)
    """

    def __init__(
        self,
        timeout: float = 30.0,
        api_key: str | None = None,
    ) -> None:
        """Initialize the negotiation client.

        Args:
            timeout: HTTP request timeout in seconds.
            api_key: Optional API key for authenticated requests.
        """
        self._timeout = timeout
        self._api_key = api_key

    def _build_headers(self) -> dict[str, str]:
        """Build request headers, including auth if configured."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        return headers

    async def start_negotiation(
        self,
        seller_url: str,
        proposal_id: str,
        initial_price: float,
        strategy: NegotiationStrategy,
        negotiation_enabled: bool = True,
    ) -> NegotiationSession:
        """Start a negotiation by sending the first counter-offer.

        Posts to the seller's proposals/{id}/counter endpoint with
        the buyer's initial offer (typically the strategy's target price).

        Args:
            seller_url: Base URL of the seller API.
            proposal_id: The proposal to negotiate.
            initial_price: Our opening offer.
            strategy: The negotiation strategy (stored for reference).
            negotiation_enabled: Whether the seller's package allows
                negotiation.  When False, raises ValueError.

        Returns:
            A NegotiationSession tracking the negotiation state.

        Raises:
            ValueError: If negotiation is not enabled on the package.
        """
        if not negotiation_enabled:
            raise ValueError(
                f"Negotiation is not enabled on package for proposal "
                f"{proposal_id}. Book at the listed price instead."
            )

        url = f"{seller_url}/proposals/{proposal_id}/counter"
        payload = {"price": initial_price}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, json=payload, headers=self._build_headers())
            response.raise_for_status()
            data = response.json()

        session = NegotiationSession(
            proposal_id=proposal_id,
            seller_url=seller_url,
            negotiation_id=data.get("negotiation_id", f"neg-{proposal_id}"),
            current_seller_price=data.get("seller_price", data.get("current_price", 0.0)),
            our_last_offer=initial_price,
            rounds=[],
        )

        # Record the first round
        round_result = NegotiationRound(
            round_number=data.get("round_number", 1),
            buyer_price=initial_price,
            seller_price=data.get("seller_price", data.get("current_price", 0.0)),
            action=data.get("action", "counter"),
            rationale=data.get("rationale", ""),
        )
        session.rounds.append(round_result)

        logger.info(
            "Negotiation started: %s | proposal=%s | our_offer=$%.2f | seller=$%.2f",
            session.negotiation_id,
            proposal_id,
            initial_price,
            session.current_seller_price,
        )
        return session

    async def counter_offer(
        self,
        session: NegotiationSession,
        price: float,
    ) -> NegotiationRound:
        """Send a counter-offer to the seller.

        Args:
            session: Active negotiation session.
            price: Our counter-offer price.

        Returns:
            The seller's response as a NegotiationRound.
        """
        url = f"{session.seller_url}/proposals/{session.proposal_id}/counter"
        payload = {"price": price}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, json=payload, headers=self._build_headers())
            response.raise_for_status()
            data = response.json()

        round_result = NegotiationRound(
            round_number=data.get("round_number", len(session.rounds) + 1),
            buyer_price=price,
            seller_price=data.get("seller_price", data.get("current_price", 0.0)),
            action=data.get("action", "counter"),
            rationale=data.get("rationale", ""),
        )

        # Update session state
        session.current_seller_price = round_result.seller_price
        session.our_last_offer = price
        session.rounds.append(round_result)

        logger.info(
            "Counter-offer round %d: our=$%.2f | seller=$%.2f | action=%s",
            round_result.round_number,
            price,
            round_result.seller_price,
            round_result.action,
        )
        return round_result

    async def accept(self, session: NegotiationSession) -> dict[str, Any]:
        """Accept the seller's current offer.

        Posts acceptance to the seller. The deal price is the seller's
        last stated price.

        Args:
            session: Active negotiation session.

        Returns:
            Seller's confirmation response.
        """
        url = f"{session.seller_url}/proposals/{session.proposal_id}/counter"
        payload = {
            "price": session.current_seller_price,
            "action": "accept",
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, json=payload, headers=self._build_headers())
            response.raise_for_status()
            data = response.json()

        logger.info(
            "Negotiation accepted: %s at $%.2f",
            session.negotiation_id,
            session.current_seller_price,
        )
        return data

    async def decline(self, session: NegotiationSession) -> None:
        """Decline/walk away from the negotiation.

        Args:
            session: Active negotiation session.
        """
        url = f"{session.seller_url}/proposals/{session.proposal_id}/counter"
        payload = {"action": "decline"}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, json=payload, headers=self._build_headers())
            response.raise_for_status()

        logger.info("Negotiation declined: %s", session.negotiation_id)

    async def auto_negotiate(
        self,
        seller_url: str,
        proposal_id: str,
        strategy: NegotiationStrategy,
        negotiation_enabled: bool = True,
    ) -> NegotiationResult:
        """Run a full negotiation loop automatically using the strategy.

        The loop:
        1. Send initial offer (strategy.next_offer with no prior context)
        2. Check seller's response
        3. If strategy.should_accept -> accept and return
        4. If strategy.should_walk_away -> decline and return
        5. Otherwise, compute next_offer and counter
        6. Repeat until resolution

        Args:
            seller_url: Base URL of the seller API.
            proposal_id: The proposal to negotiate.
            strategy: The negotiation strategy to use.
            negotiation_enabled: Whether the seller's package allows
                negotiation.  When False, returns DECLINED immediately.

        Returns:
            NegotiationResult with the outcome and history.
        """
        # Guard: reject negotiation when the seller has disabled it (ar-9xi)
        if not negotiation_enabled:
            logger.info(
                "Negotiation not enabled for proposal %s; declining.",
                proposal_id,
            )
            return NegotiationResult(
                proposal_id=proposal_id,
                outcome=NegotiationOutcome.DECLINED,
                final_price=None,
                rounds_count=0,
                rounds=[],
            )

        # Build initial context (no prior rounds)
        initial_context = NegotiationContext(
            rounds_completed=0,
            seller_last_price=0.0,
            our_last_offer=None,
        )
        initial_price = strategy.next_offer(0.0, initial_context)

        # Start the negotiation
        session = await self.start_negotiation(
            seller_url=seller_url,
            proposal_id=proposal_id,
            initial_price=initial_price,
            strategy=strategy,
        )

        # Check if seller already accepted or if we should accept their counter
        last_round = session.rounds[-1]
        if last_round.action == "accept":
            return NegotiationResult(
                proposal_id=proposal_id,
                outcome=NegotiationOutcome.ACCEPTED,
                final_price=last_round.seller_price,
                rounds_count=len(session.rounds),
                rounds=list(session.rounds),
            )

        # Build context after first round
        context = NegotiationContext(
            rounds_completed=len(session.rounds),
            seller_last_price=session.current_seller_price,
            our_last_offer=session.our_last_offer,
        )

        # Check if we should accept the seller's first counter
        if strategy.should_accept(session.current_seller_price, context):
            await self.accept(session)
            return NegotiationResult(
                proposal_id=proposal_id,
                outcome=NegotiationOutcome.ACCEPTED,
                final_price=session.current_seller_price,
                rounds_count=len(session.rounds),
                rounds=list(session.rounds),
            )

        # Negotiation loop
        seller_previous_price = None
        while True:
            # Check walk-away conditions
            walk_context = NegotiationContext(
                rounds_completed=len(session.rounds),
                seller_last_price=session.current_seller_price,
                our_last_offer=session.our_last_offer,
                seller_previous_price=seller_previous_price,
            )

            if strategy.should_walk_away(session.current_seller_price, walk_context):
                await self.decline(session)
                return NegotiationResult(
                    proposal_id=proposal_id,
                    outcome=NegotiationOutcome.WALKED_AWAY,
                    final_price=None,
                    rounds_count=len(session.rounds),
                    rounds=list(session.rounds),
                )

            # Calculate and send next offer
            offer_context = NegotiationContext(
                rounds_completed=len(session.rounds),
                seller_last_price=session.current_seller_price,
                our_last_offer=session.our_last_offer,
                seller_previous_price=seller_previous_price,
            )
            next_price = strategy.next_offer(session.current_seller_price, offer_context)

            # Track previous seller price before getting new response
            seller_previous_price = session.current_seller_price

            round_result = await self.counter_offer(session, next_price)

            # Check if seller accepted
            if round_result.action == "accept":
                return NegotiationResult(
                    proposal_id=proposal_id,
                    outcome=NegotiationOutcome.ACCEPTED,
                    final_price=round_result.seller_price,
                    rounds_count=len(session.rounds),
                    rounds=list(session.rounds),
                )

            # Check if we should accept seller's new counter
            new_context = NegotiationContext(
                rounds_completed=len(session.rounds),
                seller_last_price=session.current_seller_price,
                our_last_offer=session.our_last_offer,
                seller_previous_price=seller_previous_price,
            )
            if strategy.should_accept(session.current_seller_price, new_context):
                await self.accept(session)
                return NegotiationResult(
                    proposal_id=proposal_id,
                    outcome=NegotiationOutcome.ACCEPTED,
                    final_price=session.current_seller_price,
                    rounds_count=len(session.rounds),
                    rounds=list(session.rounds),
                )

            # Check if seller rejected (walk-away from their side)
            if round_result.action == "reject":
                return NegotiationResult(
                    proposal_id=proposal_id,
                    outcome=NegotiationOutcome.WALKED_AWAY,
                    final_price=None,
                    rounds_count=len(session.rounds),
                    rounds=list(session.rounds),
                )
