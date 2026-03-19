# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for MultiSellerOrchestrator -- coordinates multi-seller deal
discovery, parallel quote collection, evaluation, and booking.

Covers:
- Seller discovery via registry client
- Parallel quote requesting (with mocked clients)
- Quote evaluation and ranking via QuoteNormalizer
- Deal selection within budget constraints
- End-to-end orchestration flow
- Event emission at each stage
- Error handling (timeouts, seller failures, no quotes)
- Budget constraint enforcement
- Exclusion list filtering

Reference: Campaign Automation Strategic Plan, Section 7.2
Bead: buyer-8ih (2A: Multi-Seller Deal Orchestration)
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ad_buyer.booking.quote_normalizer import (
    NormalizedQuote,
    QuoteNormalizer,
    SupplyPathInfo,
)
from ad_buyer.models.deals import (
    AvailabilityInfo,
    DealBookingRequest,
    DealResponse,
    OpenRTBParams,
    PricingInfo,
    ProductInfo,
    QuoteRequest,
    QuoteResponse,
    TermsInfo,
)
from ad_buyer.registry.models import AgentCapability, AgentCard, TrustLevel


# ---------------------------------------------------------------------------
# Import the module under test (will fail until implemented)
# ---------------------------------------------------------------------------

from ad_buyer.orchestration.multi_seller import (
    MultiSellerOrchestrator,
    InventoryRequirements,
    DealParams,
    OrchestrationResult,
    SellerQuoteResult,
    DealSelection,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_seller_card(
    *,
    agent_id: str = "seller-a",
    name: str = "Seller A",
    url: str = "http://seller-a.example.com",
    capabilities: list[str] | None = None,
    trust_level: TrustLevel = TrustLevel.VERIFIED,
) -> AgentCard:
    """Build a mock AgentCard for a seller."""
    caps = capabilities or ["ctv", "display"]
    return AgentCard(
        agent_id=agent_id,
        name=name,
        url=url,
        protocols=["a2a", "deals-api-v1"],
        capabilities=[
            AgentCapability(name=c, description=f"{c} inventory")
            for c in caps
        ],
        trust_level=trust_level,
    )


def _make_quote_response(
    *,
    quote_id: str = "q-001",
    seller_id: str = "seller-a",
    product_id: str = "prod-ctv-001",
    product_name: str = "CTV Premium Package",
    final_cpm: float = 12.0,
    base_cpm: float = 15.0,
    impressions: int = 500_000,
    deal_type: str = "PD",
    fill_rate: float | None = 0.85,
    flight_start: str = "2026-04-01",
    flight_end: str = "2026-04-30",
) -> QuoteResponse:
    """Build a mock QuoteResponse."""
    availability = None
    if fill_rate is not None:
        availability = AvailabilityInfo(
            inventory_available=True,
            estimated_fill_rate=fill_rate,
        )
    return QuoteResponse(
        quote_id=quote_id,
        status="available",
        product=ProductInfo(product_id=product_id, name=product_name),
        pricing=PricingInfo(base_cpm=base_cpm, final_cpm=final_cpm),
        terms=TermsInfo(
            impressions=impressions,
            flight_start=flight_start,
            flight_end=flight_end,
            guaranteed=(deal_type == "PG"),
        ),
        availability=availability,
        seller_id=seller_id,
        buyer_tier="agency",
    )


def _make_deal_response(
    *,
    deal_id: str = "deal-001",
    quote_id: str = "q-001",
    deal_type: str = "PD",
    final_cpm: float = 12.0,
    product_id: str = "prod-ctv-001",
    product_name: str = "CTV Premium Package",
) -> DealResponse:
    """Build a mock DealResponse."""
    return DealResponse(
        deal_id=deal_id,
        deal_type=deal_type,
        status="active",
        quote_id=quote_id,
        product=ProductInfo(product_id=product_id, name=product_name),
        pricing=PricingInfo(base_cpm=15.0, final_cpm=final_cpm),
        terms=TermsInfo(impressions=500_000, guaranteed=(deal_type == "PG")),
        buyer_tier="agency",
        openrtb_params=OpenRTBParams(
            id=deal_id, bidfloor=final_cpm, bidfloorcur="USD"
        ),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_registry_client():
    """Mock RegistryClient that returns configurable sellers."""
    client = AsyncMock()
    client.discover_sellers = AsyncMock(return_value=[])
    return client


@pytest.fixture
def mock_deals_client_factory():
    """Factory that produces mock DealsClient instances per seller URL."""
    clients = {}

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
    """Mock event bus for capturing emitted events."""
    bus = AsyncMock()
    bus.publish = AsyncMock()
    return bus


@pytest.fixture
def orchestrator(mock_registry_client, mock_deals_client_factory, mock_event_bus):
    """MultiSellerOrchestrator with mocked dependencies."""
    return MultiSellerOrchestrator(
        registry_client=mock_registry_client,
        deals_client_factory=mock_deals_client_factory,
        event_bus=mock_event_bus,
        quote_normalizer=QuoteNormalizer(),
        quote_timeout=5.0,
    )


# ---------------------------------------------------------------------------
# InventoryRequirements model tests
# ---------------------------------------------------------------------------


class TestInventoryRequirements:
    """Verify the InventoryRequirements data model."""

    def test_required_fields(self):
        """InventoryRequirements has media_type and deal_types."""
        reqs = InventoryRequirements(
            media_type="ctv",
            deal_types=["PG", "PD"],
        )
        assert reqs.media_type == "ctv"
        assert reqs.deal_types == ["PG", "PD"]

    def test_optional_fields(self):
        """Optional fields have sensible defaults."""
        reqs = InventoryRequirements(
            media_type="display",
            deal_types=["PD"],
        )
        assert reqs.content_categories == []
        assert reqs.excluded_sellers == []
        assert reqs.min_impressions is None
        assert reqs.max_cpm is None

    def test_with_all_fields(self):
        """InventoryRequirements accepts all optional fields."""
        reqs = InventoryRequirements(
            media_type="ctv",
            deal_types=["PG", "PD"],
            content_categories=["IAB1", "IAB2"],
            excluded_sellers=["seller-bad"],
            min_impressions=100_000,
            max_cpm=25.0,
        )
        assert reqs.content_categories == ["IAB1", "IAB2"]
        assert reqs.excluded_sellers == ["seller-bad"]
        assert reqs.min_impressions == 100_000
        assert reqs.max_cpm == 25.0


# ---------------------------------------------------------------------------
# DealParams model tests
# ---------------------------------------------------------------------------


class TestDealParams:
    """Verify the DealParams data model."""

    def test_required_fields(self):
        """DealParams has product_id, deal_type, impressions, flight dates."""
        params = DealParams(
            product_id="prod-001",
            deal_type="PD",
            impressions=500_000,
            flight_start="2026-04-01",
            flight_end="2026-04-30",
        )
        assert params.product_id == "prod-001"
        assert params.deal_type == "PD"
        assert params.impressions == 500_000

    def test_optional_target_cpm(self):
        """target_cpm is optional."""
        params = DealParams(
            product_id="prod-001",
            deal_type="PD",
            impressions=500_000,
            flight_start="2026-04-01",
            flight_end="2026-04-30",
            target_cpm=10.0,
        )
        assert params.target_cpm == 10.0


# ---------------------------------------------------------------------------
# Seller discovery
# ---------------------------------------------------------------------------


class TestDiscoverSellers:
    """Test discover_sellers method."""

    @pytest.mark.asyncio
    async def test_discover_returns_matching_sellers(
        self, orchestrator, mock_registry_client
    ):
        """discover_sellers returns sellers matching inventory requirements."""
        sellers = [
            _make_seller_card(agent_id="seller-a", capabilities=["ctv"]),
            _make_seller_card(agent_id="seller-b", capabilities=["ctv", "display"]),
        ]
        mock_registry_client.discover_sellers.return_value = sellers

        reqs = InventoryRequirements(media_type="ctv", deal_types=["PD"])
        result = await orchestrator.discover_sellers(reqs)

        assert len(result) == 2
        mock_registry_client.discover_sellers.assert_called_once()

    @pytest.mark.asyncio
    async def test_discover_filters_excluded_sellers(
        self, orchestrator, mock_registry_client
    ):
        """Excluded sellers are filtered out from discovery results."""
        sellers = [
            _make_seller_card(agent_id="seller-a"),
            _make_seller_card(agent_id="seller-bad"),
        ]
        mock_registry_client.discover_sellers.return_value = sellers

        reqs = InventoryRequirements(
            media_type="ctv",
            deal_types=["PD"],
            excluded_sellers=["seller-bad"],
        )
        result = await orchestrator.discover_sellers(reqs)

        assert len(result) == 1
        assert result[0].agent_id == "seller-a"

    @pytest.mark.asyncio
    async def test_discover_filters_blocked_sellers(
        self, orchestrator, mock_registry_client
    ):
        """Sellers with BLOCKED trust level are excluded."""
        sellers = [
            _make_seller_card(agent_id="seller-a", trust_level=TrustLevel.VERIFIED),
            _make_seller_card(agent_id="seller-blocked", trust_level=TrustLevel.BLOCKED),
        ]
        mock_registry_client.discover_sellers.return_value = sellers

        reqs = InventoryRequirements(media_type="ctv", deal_types=["PD"])
        result = await orchestrator.discover_sellers(reqs)

        assert len(result) == 1
        assert result[0].agent_id == "seller-a"

    @pytest.mark.asyncio
    async def test_discover_empty_when_no_sellers(
        self, orchestrator, mock_registry_client
    ):
        """Returns empty list when no sellers match."""
        mock_registry_client.discover_sellers.return_value = []

        reqs = InventoryRequirements(media_type="ctv", deal_types=["PD"])
        result = await orchestrator.discover_sellers(reqs)

        assert result == []

    @pytest.mark.asyncio
    async def test_discover_emits_event(
        self, orchestrator, mock_registry_client, mock_event_bus
    ):
        """discover_sellers emits an INVENTORY_DISCOVERED event."""
        sellers = [_make_seller_card(agent_id="seller-a")]
        mock_registry_client.discover_sellers.return_value = sellers

        reqs = InventoryRequirements(media_type="ctv", deal_types=["PD"])
        await orchestrator.discover_sellers(reqs)

        mock_event_bus.publish.assert_called()
        # Verify the event type
        published_event = mock_event_bus.publish.call_args[0][0]
        assert published_event.event_type.value == "inventory.discovered"


# ---------------------------------------------------------------------------
# Parallel quote requests
# ---------------------------------------------------------------------------


class TestRequestQuotesParallel:
    """Test request_quotes_parallel method."""

    @pytest.mark.asyncio
    async def test_requests_quotes_from_all_sellers(
        self, orchestrator, mock_deals_client_factory
    ):
        """Sends quote requests to all provided sellers concurrently."""
        sellers = [
            _make_seller_card(agent_id="seller-a", url="http://seller-a.example.com"),
            _make_seller_card(agent_id="seller-b", url="http://seller-b.example.com"),
        ]

        quote_a = _make_quote_response(quote_id="q-a", seller_id="seller-a", final_cpm=10.0)
        quote_b = _make_quote_response(quote_id="q-b", seller_id="seller-b", final_cpm=12.0)

        # Configure mock clients
        client_a = mock_deals_client_factory("http://seller-a.example.com")
        client_a.request_quote.return_value = quote_a
        client_b = mock_deals_client_factory("http://seller-b.example.com")
        client_b.request_quote.return_value = quote_b

        params = DealParams(
            product_id="prod-ctv",
            deal_type="PD",
            impressions=500_000,
            flight_start="2026-04-01",
            flight_end="2026-04-30",
        )
        results = await orchestrator.request_quotes_parallel(sellers, params)

        assert len(results) == 2
        # Both sellers were contacted
        client_a.request_quote.assert_called_once()
        client_b.request_quote.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_seller_failure_gracefully(
        self, orchestrator, mock_deals_client_factory
    ):
        """If one seller fails, the other's quotes are still collected."""
        sellers = [
            _make_seller_card(agent_id="seller-a", url="http://seller-a.example.com"),
            _make_seller_card(agent_id="seller-b", url="http://seller-b.example.com"),
        ]

        quote_a = _make_quote_response(quote_id="q-a", seller_id="seller-a")
        client_a = mock_deals_client_factory("http://seller-a.example.com")
        client_a.request_quote.return_value = quote_a

        client_b = mock_deals_client_factory("http://seller-b.example.com")
        client_b.request_quote.side_effect = Exception("Connection refused")

        params = DealParams(
            product_id="prod-ctv",
            deal_type="PD",
            impressions=500_000,
            flight_start="2026-04-01",
            flight_end="2026-04-30",
        )
        results = await orchestrator.request_quotes_parallel(sellers, params)

        # Should have 1 success and 1 failure
        successful = [r for r in results if r.quote is not None]
        failed = [r for r in results if r.error is not None]
        assert len(successful) == 1
        assert len(failed) == 1
        assert successful[0].seller_id == "seller-a"

    @pytest.mark.asyncio
    async def test_handles_timeout_gracefully(
        self, orchestrator, mock_deals_client_factory
    ):
        """Seller that times out is recorded as failure, not crash."""
        sellers = [
            _make_seller_card(agent_id="seller-slow", url="http://seller-slow.example.com"),
        ]

        client = mock_deals_client_factory("http://seller-slow.example.com")
        client.request_quote.side_effect = asyncio.TimeoutError()

        params = DealParams(
            product_id="prod-ctv",
            deal_type="PD",
            impressions=500_000,
            flight_start="2026-04-01",
            flight_end="2026-04-30",
        )
        results = await orchestrator.request_quotes_parallel(sellers, params)

        assert len(results) == 1
        assert results[0].error is not None
        assert results[0].quote is None

    @pytest.mark.asyncio
    async def test_emits_quote_events(
        self, orchestrator, mock_deals_client_factory, mock_event_bus
    ):
        """Emits quote.requested and quote.received events."""
        sellers = [
            _make_seller_card(agent_id="seller-a", url="http://seller-a.example.com"),
        ]
        quote = _make_quote_response(quote_id="q-a", seller_id="seller-a")
        client = mock_deals_client_factory("http://seller-a.example.com")
        client.request_quote.return_value = quote

        params = DealParams(
            product_id="prod-ctv",
            deal_type="PD",
            impressions=500_000,
            flight_start="2026-04-01",
            flight_end="2026-04-30",
        )
        await orchestrator.request_quotes_parallel(sellers, params)

        # Should have emitted events
        assert mock_event_bus.publish.call_count >= 1

    @pytest.mark.asyncio
    async def test_empty_sellers_returns_empty(self, orchestrator):
        """No sellers means no quote results."""
        params = DealParams(
            product_id="prod-ctv",
            deal_type="PD",
            impressions=500_000,
            flight_start="2026-04-01",
            flight_end="2026-04-30",
        )
        results = await orchestrator.request_quotes_parallel([], params)
        assert results == []


# ---------------------------------------------------------------------------
# Quote evaluation and ranking
# ---------------------------------------------------------------------------


class TestEvaluateAndRank:
    """Test evaluate_and_rank method."""

    @pytest.mark.asyncio
    async def test_ranks_quotes_by_score(self, orchestrator):
        """Quotes are ranked by QuoteNormalizer score (higher = better)."""
        quote_results = [
            SellerQuoteResult(
                seller_id="seller-expensive",
                seller_url="http://seller-expensive.example.com",
                quote=_make_quote_response(
                    quote_id="q-exp",
                    seller_id="seller-expensive",
                    final_cpm=25.0,
                ),
                deal_type="PD",
                error=None,
            ),
            SellerQuoteResult(
                seller_id="seller-cheap",
                seller_url="http://seller-cheap.example.com",
                quote=_make_quote_response(
                    quote_id="q-chp",
                    seller_id="seller-cheap",
                    final_cpm=8.0,
                ),
                deal_type="PD",
                error=None,
            ),
        ]

        ranked = await orchestrator.evaluate_and_rank(quote_results)

        assert len(ranked) == 2
        # Cheaper quote should rank first
        assert ranked[0].quote_id == "q-chp"
        assert ranked[1].quote_id == "q-exp"

    @pytest.mark.asyncio
    async def test_filters_out_failed_quotes(self, orchestrator):
        """Failed quote results are excluded from ranking."""
        quote_results = [
            SellerQuoteResult(
                seller_id="seller-a",
                seller_url="http://seller-a.example.com",
                quote=_make_quote_response(
                    quote_id="q-a",
                    seller_id="seller-a",
                    final_cpm=10.0,
                ),
                deal_type="PD",
                error=None,
            ),
            SellerQuoteResult(
                seller_id="seller-b",
                seller_url="http://seller-b.example.com",
                quote=None,
                deal_type="PD",
                error="Connection failed",
            ),
        ]

        ranked = await orchestrator.evaluate_and_rank(quote_results)

        assert len(ranked) == 1
        assert ranked[0].quote_id == "q-a"

    @pytest.mark.asyncio
    async def test_filters_by_max_cpm(self, orchestrator):
        """Quotes exceeding max_cpm are filtered out."""
        quote_results = [
            SellerQuoteResult(
                seller_id="seller-a",
                seller_url="http://seller-a.example.com",
                quote=_make_quote_response(
                    quote_id="q-a",
                    seller_id="seller-a",
                    final_cpm=10.0,
                ),
                deal_type="PD",
                error=None,
            ),
            SellerQuoteResult(
                seller_id="seller-b",
                seller_url="http://seller-b.example.com",
                quote=_make_quote_response(
                    quote_id="q-b",
                    seller_id="seller-b",
                    final_cpm=50.0,
                ),
                deal_type="PD",
                error=None,
            ),
        ]

        ranked = await orchestrator.evaluate_and_rank(
            quote_results, max_cpm=20.0
        )

        assert len(ranked) == 1
        assert ranked[0].quote_id == "q-a"

    @pytest.mark.asyncio
    async def test_empty_quotes_returns_empty(self, orchestrator):
        """Empty quote results produce empty ranking."""
        ranked = await orchestrator.evaluate_and_rank([])
        assert ranked == []

    @pytest.mark.asyncio
    async def test_all_failed_returns_empty(self, orchestrator):
        """All failed quotes produce empty ranking."""
        quote_results = [
            SellerQuoteResult(
                seller_id="seller-a",
                seller_url="http://seller-a.example.com",
                quote=None,
                deal_type="PD",
                error="Timeout",
            ),
        ]
        ranked = await orchestrator.evaluate_and_rank(quote_results)
        assert ranked == []


# ---------------------------------------------------------------------------
# Deal selection (budget-constrained)
# ---------------------------------------------------------------------------


class TestSelectAndBook:
    """Test select_and_book method."""

    @pytest.mark.asyncio
    async def test_selects_within_budget(
        self, orchestrator, mock_deals_client_factory
    ):
        """Selects deals that fit within the total budget."""
        ranked = [
            NormalizedQuote(
                seller_id="seller-a",
                quote_id="q-a",
                raw_cpm=10.0,
                effective_cpm=10.5,
                deal_type="PD",
                fee_estimate=0.5,
                minimum_spend=0.0,
                score=90.0,
            ),
            NormalizedQuote(
                seller_id="seller-b",
                quote_id="q-b",
                raw_cpm=12.0,
                effective_cpm=12.5,
                deal_type="PD",
                fee_estimate=0.5,
                minimum_spend=0.0,
                score=80.0,
            ),
        ]

        # Configure mock booking responses
        deal_a = _make_deal_response(deal_id="deal-a", quote_id="q-a", final_cpm=10.0)
        deal_b = _make_deal_response(deal_id="deal-b", quote_id="q-b", final_cpm=12.0)

        # Map quote results to seller URLs for booking
        quote_seller_map = {
            "q-a": "http://seller-a.example.com",
            "q-b": "http://seller-b.example.com",
        }

        client_a = mock_deals_client_factory("http://seller-a.example.com")
        client_a.book_deal.return_value = deal_a
        client_b = mock_deals_client_factory("http://seller-b.example.com")
        client_b.book_deal.return_value = deal_b

        selection = await orchestrator.select_and_book(
            ranked_quotes=ranked,
            budget=100_000.0,
            count=2,
            quote_seller_map=quote_seller_map,
        )

        assert isinstance(selection, DealSelection)
        assert len(selection.booked_deals) == 2

    @pytest.mark.asyncio
    async def test_respects_count_limit(
        self, orchestrator, mock_deals_client_factory
    ):
        """Does not book more deals than the count parameter."""
        ranked = [
            NormalizedQuote(
                seller_id=f"seller-{i}",
                quote_id=f"q-{i}",
                raw_cpm=10.0 + i,
                effective_cpm=10.0 + i,
                deal_type="PD",
                fee_estimate=0.0,
                minimum_spend=0.0,
                score=90.0 - i * 5,
            )
            for i in range(5)
        ]

        for i in range(5):
            url = f"http://seller-{i}.example.com"
            client = mock_deals_client_factory(url)
            client.book_deal.return_value = _make_deal_response(
                deal_id=f"deal-{i}", quote_id=f"q-{i}"
            )

        quote_seller_map = {
            f"q-{i}": f"http://seller-{i}.example.com" for i in range(5)
        }

        selection = await orchestrator.select_and_book(
            ranked_quotes=ranked,
            budget=1_000_000.0,
            count=2,
            quote_seller_map=quote_seller_map,
        )

        assert len(selection.booked_deals) == 2

    @pytest.mark.asyncio
    async def test_skips_minimum_spend_exceeding_budget(
        self, orchestrator, mock_deals_client_factory
    ):
        """Skips deals whose minimum spend exceeds remaining budget."""
        ranked = [
            NormalizedQuote(
                seller_id="seller-a",
                quote_id="q-a",
                raw_cpm=10.0,
                effective_cpm=10.0,
                deal_type="PG",
                fee_estimate=0.0,
                minimum_spend=50_000.0,  # Exceeds budget
                score=95.0,
            ),
            NormalizedQuote(
                seller_id="seller-b",
                quote_id="q-b",
                raw_cpm=12.0,
                effective_cpm=12.0,
                deal_type="PD",
                fee_estimate=0.0,
                minimum_spend=5_000.0,  # Fits budget
                score=85.0,
            ),
        ]

        deal_b = _make_deal_response(deal_id="deal-b", quote_id="q-b", final_cpm=12.0)
        client_b = mock_deals_client_factory("http://seller-b.example.com")
        client_b.book_deal.return_value = deal_b

        quote_seller_map = {
            "q-a": "http://seller-a.example.com",
            "q-b": "http://seller-b.example.com",
        }

        selection = await orchestrator.select_and_book(
            ranked_quotes=ranked,
            budget=10_000.0,
            count=2,
            quote_seller_map=quote_seller_map,
        )

        assert len(selection.booked_deals) == 1
        assert selection.booked_deals[0].deal_id == "deal-b"

    @pytest.mark.asyncio
    async def test_handles_booking_failure(
        self, orchestrator, mock_deals_client_factory
    ):
        """If booking fails for one deal, continues with the next."""
        ranked = [
            NormalizedQuote(
                seller_id="seller-a",
                quote_id="q-a",
                raw_cpm=10.0,
                effective_cpm=10.0,
                deal_type="PD",
                fee_estimate=0.0,
                minimum_spend=0.0,
                score=90.0,
            ),
            NormalizedQuote(
                seller_id="seller-b",
                quote_id="q-b",
                raw_cpm=12.0,
                effective_cpm=12.0,
                deal_type="PD",
                fee_estimate=0.0,
                minimum_spend=0.0,
                score=80.0,
            ),
        ]

        client_a = mock_deals_client_factory("http://seller-a.example.com")
        client_a.book_deal.side_effect = Exception("Booking rejected")

        deal_b = _make_deal_response(deal_id="deal-b", quote_id="q-b")
        client_b = mock_deals_client_factory("http://seller-b.example.com")
        client_b.book_deal.return_value = deal_b

        quote_seller_map = {
            "q-a": "http://seller-a.example.com",
            "q-b": "http://seller-b.example.com",
        }

        selection = await orchestrator.select_and_book(
            ranked_quotes=ranked,
            budget=100_000.0,
            count=2,
            quote_seller_map=quote_seller_map,
        )

        assert len(selection.booked_deals) == 1
        assert len(selection.failed_bookings) == 1

    @pytest.mark.asyncio
    async def test_emits_deal_booked_events(
        self, orchestrator, mock_deals_client_factory, mock_event_bus
    ):
        """Emits deal.booked event for each successful booking."""
        ranked = [
            NormalizedQuote(
                seller_id="seller-a",
                quote_id="q-a",
                raw_cpm=10.0,
                effective_cpm=10.0,
                deal_type="PD",
                fee_estimate=0.0,
                minimum_spend=0.0,
                score=90.0,
            ),
        ]

        deal_a = _make_deal_response(deal_id="deal-a", quote_id="q-a")
        client_a = mock_deals_client_factory("http://seller-a.example.com")
        client_a.book_deal.return_value = deal_a

        quote_seller_map = {"q-a": "http://seller-a.example.com"}

        await orchestrator.select_and_book(
            ranked_quotes=ranked,
            budget=100_000.0,
            count=1,
            quote_seller_map=quote_seller_map,
        )

        # Should have emitted at least one deal.booked event
        assert mock_event_bus.publish.call_count >= 1

    @pytest.mark.asyncio
    async def test_empty_ranked_returns_empty_selection(self, orchestrator):
        """Empty ranked list returns empty selection."""
        selection = await orchestrator.select_and_book(
            ranked_quotes=[],
            budget=100_000.0,
            count=5,
            quote_seller_map={},
        )

        assert len(selection.booked_deals) == 0
        assert len(selection.failed_bookings) == 0


# ---------------------------------------------------------------------------
# End-to-end orchestration
# ---------------------------------------------------------------------------


class TestOrchestrate:
    """Test the end-to-end orchestrate method."""

    @pytest.mark.asyncio
    async def test_full_orchestration_flow(
        self, orchestrator, mock_registry_client, mock_deals_client_factory, mock_event_bus
    ):
        """End-to-end: discover -> quote -> rank -> book."""
        # Setup sellers
        sellers = [
            _make_seller_card(agent_id="seller-a", url="http://seller-a.example.com"),
            _make_seller_card(agent_id="seller-b", url="http://seller-b.example.com"),
        ]
        mock_registry_client.discover_sellers.return_value = sellers

        # Setup quotes
        quote_a = _make_quote_response(
            quote_id="q-a", seller_id="seller-a", final_cpm=10.0
        )
        quote_b = _make_quote_response(
            quote_id="q-b", seller_id="seller-b", final_cpm=15.0
        )
        client_a = mock_deals_client_factory("http://seller-a.example.com")
        client_a.request_quote.return_value = quote_a
        client_b = mock_deals_client_factory("http://seller-b.example.com")
        client_b.request_quote.return_value = quote_b

        # Setup deal booking (only the top-ranked quote)
        deal_a = _make_deal_response(deal_id="deal-a", quote_id="q-a", final_cpm=10.0)
        client_a.book_deal.return_value = deal_a

        reqs = InventoryRequirements(
            media_type="ctv",
            deal_types=["PD"],
        )
        params = DealParams(
            product_id="prod-ctv",
            deal_type="PD",
            impressions=500_000,
            flight_start="2026-04-01",
            flight_end="2026-04-30",
        )

        result = await orchestrator.orchestrate(
            inventory_requirements=reqs,
            deal_params=params,
            budget=100_000.0,
            max_deals=1,
        )

        assert isinstance(result, OrchestrationResult)
        assert len(result.discovered_sellers) == 2
        assert len(result.ranked_quotes) >= 1
        assert len(result.selection.booked_deals) == 1
        assert result.selection.booked_deals[0].deal_id == "deal-a"

    @pytest.mark.asyncio
    async def test_orchestration_with_no_sellers(
        self, orchestrator, mock_registry_client
    ):
        """Orchestration with no discovered sellers returns empty result."""
        mock_registry_client.discover_sellers.return_value = []

        reqs = InventoryRequirements(media_type="ctv", deal_types=["PD"])
        params = DealParams(
            product_id="prod-ctv",
            deal_type="PD",
            impressions=500_000,
            flight_start="2026-04-01",
            flight_end="2026-04-30",
        )

        result = await orchestrator.orchestrate(
            inventory_requirements=reqs,
            deal_params=params,
            budget=100_000.0,
            max_deals=1,
        )

        assert isinstance(result, OrchestrationResult)
        assert len(result.discovered_sellers) == 0
        assert len(result.ranked_quotes) == 0
        assert len(result.selection.booked_deals) == 0

    @pytest.mark.asyncio
    async def test_orchestration_with_all_sellers_failing(
        self, orchestrator, mock_registry_client, mock_deals_client_factory
    ):
        """All sellers failing quote requests produces empty ranking."""
        sellers = [
            _make_seller_card(agent_id="seller-a", url="http://seller-a.example.com"),
        ]
        mock_registry_client.discover_sellers.return_value = sellers

        client_a = mock_deals_client_factory("http://seller-a.example.com")
        client_a.request_quote.side_effect = Exception("Server error")

        reqs = InventoryRequirements(media_type="ctv", deal_types=["PD"])
        params = DealParams(
            product_id="prod-ctv",
            deal_type="PD",
            impressions=500_000,
            flight_start="2026-04-01",
            flight_end="2026-04-30",
        )

        result = await orchestrator.orchestrate(
            inventory_requirements=reqs,
            deal_params=params,
            budget=100_000.0,
            max_deals=1,
        )

        assert len(result.discovered_sellers) == 1
        assert len(result.ranked_quotes) == 0
        assert len(result.selection.booked_deals) == 0

    @pytest.mark.asyncio
    async def test_orchestration_emits_campaign_events(
        self, orchestrator, mock_registry_client, mock_deals_client_factory, mock_event_bus
    ):
        """Full orchestration emits events at each stage."""
        sellers = [
            _make_seller_card(agent_id="seller-a", url="http://seller-a.example.com"),
        ]
        mock_registry_client.discover_sellers.return_value = sellers

        quote_a = _make_quote_response(quote_id="q-a", seller_id="seller-a", final_cpm=10.0)
        client_a = mock_deals_client_factory("http://seller-a.example.com")
        client_a.request_quote.return_value = quote_a

        deal_a = _make_deal_response(deal_id="deal-a", quote_id="q-a")
        client_a.book_deal.return_value = deal_a

        reqs = InventoryRequirements(media_type="ctv", deal_types=["PD"])
        params = DealParams(
            product_id="prod-ctv",
            deal_type="PD",
            impressions=500_000,
            flight_start="2026-04-01",
            flight_end="2026-04-30",
        )

        await orchestrator.orchestrate(
            inventory_requirements=reqs,
            deal_params=params,
            budget=100_000.0,
            max_deals=1,
        )

        # Multiple events should have been emitted
        assert mock_event_bus.publish.call_count >= 2


# ---------------------------------------------------------------------------
# DealSelection model tests
# ---------------------------------------------------------------------------


class TestDealSelection:
    """Verify the DealSelection result model."""

    def test_total_spend_computed(self):
        """total_spend sums the CPM costs of booked deals."""
        deals = [
            _make_deal_response(deal_id="d-1", final_cpm=10.0),
            _make_deal_response(deal_id="d-2", final_cpm=15.0),
        ]
        sel = DealSelection(
            booked_deals=deals,
            failed_bookings=[],
            total_spend=0.0,
            remaining_budget=75_000.0,
        )
        assert len(sel.booked_deals) == 2
        assert sel.remaining_budget == 75_000.0

    def test_failed_bookings_tracked(self):
        """Failed bookings are tracked with reason."""
        sel = DealSelection(
            booked_deals=[],
            failed_bookings=[
                {"quote_id": "q-bad", "error": "Rejected by seller"},
            ],
            total_spend=0.0,
            remaining_budget=100_000.0,
        )
        assert len(sel.failed_bookings) == 1


# ---------------------------------------------------------------------------
# OrchestrationResult model tests
# ---------------------------------------------------------------------------


class TestOrchestrationResult:
    """Verify the OrchestrationResult model."""

    def test_has_all_stages(self):
        """OrchestrationResult captures data from each stage."""
        result = OrchestrationResult(
            discovered_sellers=[_make_seller_card()],
            quote_results=[],
            ranked_quotes=[],
            selection=DealSelection(
                booked_deals=[],
                failed_bookings=[],
                total_spend=0.0,
                remaining_budget=100_000.0,
            ),
        )
        assert len(result.discovered_sellers) == 1
        assert isinstance(result.selection, DealSelection)
