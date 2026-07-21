# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the chat interface's thin canonical booking wrappers.

The chat agent's booking tools must be thin calls into the canonical
MultiSellerOrchestrator pipeline — no bespoke seller-protocol dialect,
no locally minted deal ids. The four former inline tools
(MultiSellerSearchTool, CallSellerToolTool, BookPGDealTool,
CreatePMPDealTool) are deleted and must stay dead.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ad_buyer.interfaces.chat import main as chat_main
from ad_buyer.interfaces.chat.main import (
    BookDealsTool,
    RequestQuotesTool,
    SellerConnection,
    _ConfiguredSellersRegistry,
)
from ad_buyer.models.deals import DealResponse, PricingInfo, ProductInfo, TermsInfo
from ad_buyer.orchestration.multi_seller import (
    DealSelection,
    MultiSellerOrchestrator,
    OrchestrationResult,
)
from ad_buyer.registry.models import TrustLevel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seller_deal(deal_id: str = "SLR-DEAL-CHAT-01") -> DealResponse:
    return DealResponse(
        deal_id=deal_id,
        deal_type="PD",
        status="active",
        quote_id="SLR-QUOTE-CHAT-01",
        product=ProductInfo(product_id="prod-1", name="Prod 1"),
        pricing=PricingInfo(base_cpm=12.0, final_cpm=12.0),
        terms=TermsInfo(impressions=250_000),
    )


def _orchestration_result() -> OrchestrationResult:
    return OrchestrationResult(
        discovered_sellers=[MagicMock(agent_id="seller-1")],
        quote_results=[],
        ranked_quotes=[],
        selection=DealSelection(
            booked_deals=[_seller_deal()],
            failed_bookings=[],
            total_spend=3_000.0,
            remaining_budget=0.0,
        ),
    )


@pytest.fixture
def mock_orchestrator() -> AsyncMock:
    orch = AsyncMock(spec=MultiSellerOrchestrator)
    orch.orchestrate.return_value = _orchestration_result()
    return orch


# ---------------------------------------------------------------------------
# BookDealsTool: thin delegate to orchestrator.orchestrate
# ---------------------------------------------------------------------------


class TestBookDealsTool:
    @pytest.mark.asyncio
    async def test_delegates_to_canonical_orchestrator(self, mock_orchestrator):
        tool = BookDealsTool(orchestrator=mock_orchestrator)

        result = await tool._arun(
            product_id="prod-1",
            budget=3_000.0,
            impressions=250_000,
            media_type="ctv",
            deal_type="PG",
            max_cpm=15.0,
        )

        assert mock_orchestrator.orchestrate.call_count == 1
        kwargs = mock_orchestrator.orchestrate.call_args.kwargs
        assert kwargs["budget"] == 3_000.0
        assert kwargs["max_deals"] == 1
        assert kwargs["inventory_requirements"].media_type == "ctv"
        assert kwargs["inventory_requirements"].max_cpm == 15.0
        assert kwargs["deal_params"].product_id == "prod-1"
        assert kwargs["deal_params"].impressions == 250_000

        # Seller-issued identifiers surface in the conversational output.
        assert "SLR-DEAL-CHAT-01" in result
        assert "seller-issued" in result
        assert "order_pending" not in result

    @pytest.mark.asyncio
    async def test_no_deals_booked_reported_plainly(self, mock_orchestrator):
        mock_orchestrator.orchestrate.return_value = OrchestrationResult(
            discovered_sellers=[],
            quote_results=[],
            ranked_quotes=[],
            selection=DealSelection(
                booked_deals=[],
                failed_bookings=[{"quote_id": "q1", "error": "budget"}],
                total_spend=0.0,
                remaining_budget=500.0,
            ),
        )
        tool = BookDealsTool(orchestrator=mock_orchestrator)

        result = await tool._arun(product_id="prod-1", budget=500.0, impressions=1_000)

        assert "No deals were booked" in result
        assert "Failed bookings" in result


# ---------------------------------------------------------------------------
# RequestQuotesTool: preview only, never books
# ---------------------------------------------------------------------------


class TestRequestQuotesTool:
    @pytest.mark.asyncio
    async def test_runs_discover_quote_rank_but_never_books(self, mock_orchestrator):
        mock_orchestrator.discover_sellers.return_value = [MagicMock(agent_id="s1")]
        mock_orchestrator.request_quotes_parallel.return_value = []
        mock_orchestrator.evaluate_and_rank.return_value = []
        tool = RequestQuotesTool(orchestrator=mock_orchestrator)

        await tool._arun(product_id="prod-1", impressions=100_000)

        mock_orchestrator.discover_sellers.assert_called_once()
        mock_orchestrator.request_quotes_parallel.assert_called_once()
        mock_orchestrator.evaluate_and_rank.assert_called_once()
        mock_orchestrator.select_and_book.assert_not_called()
        mock_orchestrator.orchestrate.assert_not_called()


# ---------------------------------------------------------------------------
# Configured-sellers registry adapter
# ---------------------------------------------------------------------------


class TestConfiguredSellersRegistry:
    @pytest.mark.asyncio
    async def test_only_connected_sellers_become_agent_cards(self):
        sellers = [
            SellerConnection(url="http://a.test", name="A", connected=True),
            SellerConnection(url="http://b.test", name="B", connected=False),
        ]
        registry = _ConfiguredSellersRegistry(sellers)

        cards = await registry.discover_sellers()

        assert len(cards) == 1
        assert cards[0].url == "http://a.test"
        assert cards[0].trust_level == TrustLevel.VERIFIED


# ---------------------------------------------------------------------------
# The four rival inline tools stay dead
# ---------------------------------------------------------------------------


class TestRivalChatToolsStayDead:
    def test_inline_booking_tools_deleted(self):
        for name in (
            "MultiSellerSearchTool",
            "CallSellerToolTool",
            "BookPGDealTool",
            "CreatePMPDealTool",
        ):
            assert not hasattr(chat_main, name), (
                f"{name} is a deleted rival booking path; "
                "chat must book through the canonical orchestrator wrappers."
            )
