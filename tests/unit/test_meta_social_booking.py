# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Meta Ads social booking path in DealBookingFlow (port of main PR #87).

The social channel books against the Meta Graph API, not the OpenDirect
seller orchestrator:

- ``research_social`` is a first-class research step (skipped without a
  social budget allocation) feeding ``consolidate_recommendations``.
- ``_execute_bookings`` routes approved social/meta recommendations to
  ``_book_via_meta_api`` (campaign + ad set created PAUSED) while every
  other channel still goes through MultiSellerOrchestrator.
- Meta bookings record the META-issued campaign id as the booking's
  ``deal_id`` (externally issued — the buyer still never mints ids),
  ``order_id`` = campaign id, ``line_id`` = ad set id,
  ``seller_id`` = "meta", ``booking_status`` = "paused".
- Duplicate product_ids across channel crews are deduplicated before
  booking (highest impressions wins) to avoid double-booking.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ad_buyer.flows.deal_booking_flow import DealBookingFlow
from ad_buyer.models.deals import DealResponse, PricingInfo, ProductInfo, TermsInfo
from ad_buyer.models.flow_state import ExecutionStatus, ProductRecommendation
from ad_buyer.orchestration.multi_seller import (
    DealSelection,
    MultiSellerOrchestrator,
    OrchestrationResult,
)


def _make_recommendation(product_id, channel, impressions=100000, cpm=10.0):
    return ProductRecommendation(
        product_id=product_id,
        product_name=f"Product {product_id}",
        publisher="Meta" if channel == "social" else "Publisher A",
        channel=channel,
        impressions=impressions,
        cpm=cpm,
        cost=round(impressions * cpm / 1000, 2),
    )


def _make_orchestrator():
    """AsyncMock orchestrator booking one seller-issued deal per call."""

    async def _fake_orchestrate(inventory_requirements, deal_params, budget, max_deals=3):
        deal = DealResponse(
            deal_id=f"SELLER-DEAL-{deal_params.product_id}",
            deal_type=deal_params.deal_type,
            status="active",
            quote_id=f"quote-{deal_params.product_id}",
            product=ProductInfo(product_id=deal_params.product_id, name=deal_params.product_id),
            pricing=PricingInfo(final_cpm=deal_params.target_cpm),
            terms=TermsInfo(impressions=deal_params.impressions),
        )
        return OrchestrationResult(
            discovered_sellers=[MagicMock(agent_id="seller-1")],
            quote_results=[],
            ranked_quotes=[],
            selection=DealSelection(
                booked_deals=[deal],
                failed_bookings=[],
                total_spend=budget,
                remaining_budget=0.0,
            ),
        )

    orch = AsyncMock(spec=MultiSellerOrchestrator)
    orch.orchestrate.side_effect = _fake_orchestrate
    return orch


def _flow_with_approved(recs, budget=100000):
    flow = DealBookingFlow(client=MagicMock(), orchestrator=_make_orchestrator())
    flow.state.campaign_brief = {
        "name": "Test Campaign",
        "objectives": ["reach"],
        "budget": budget,
        "start_date": "2026-04-01",
        "end_date": "2026-04-30",
        "target_audience": {"geo_locations": ["US"]},
    }
    for rec in recs:
        rec.status = "approved"
    flow.state.pending_approvals = recs
    flow.state.execution_status = ExecutionStatus.EXECUTING_BOOKINGS
    return flow


# ===========================================================================
# research_social flow step
# ===========================================================================


class TestResearchSocial:
    def test_research_social_step_exists(self):
        flow = DealBookingFlow(client=MagicMock())
        assert hasattr(flow, "research_social")

    def test_no_social_budget_returns_no_budget(self):
        flow = DealBookingFlow(client=MagicMock())
        flow.state.budget_allocations = {}
        result = flow.research_social({"status": "success"})
        assert result == {"channel": "social", "status": "no_budget"}

    def test_failed_allocation_skips(self):
        flow = DealBookingFlow(client=MagicMock())
        result = flow.research_social({"status": "failed"})
        assert result["channel"] == "social"
        assert result["status"] == "skipped"

    def test_social_research_populates_recommendations(self):
        from ad_buyer.models.flow_state import ChannelAllocation

        flow = DealBookingFlow(client=MagicMock())
        flow.state.campaign_brief = {
            "objectives": ["reach"],
            "budget": 10000,
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
            "target_audience": {},
        }
        flow.state.budget_allocations = {
            "social": ChannelAllocation(
                channel="social", budget=5000, percentage=50, rationale="test"
            )
        }
        crew = MagicMock()
        crew.kickoff.return_value = (
            '```json\n[{"product_id": "meta:feed", "product_name": "Facebook Feed",'
            ' "publisher": "Meta", "impressions": 100000, "cpm": 8.0, "cost": 800}]\n```'
        )
        with patch(
            "ad_buyer.flows.deal_booking_flow.create_social_crew", return_value=crew
        ) as factory:
            result = flow.research_social({"status": "success"})

        factory.assert_called_once()
        assert result["status"] == "success"
        assert result["channel"] == "social"
        recs = flow.state.channel_recommendations["social"]
        assert len(recs) == 1
        assert recs[0].product_id == "meta:feed"

    def test_consolidate_listens_to_research_social(self):
        """consolidate_recommendations must fire only after research_social too."""
        triggers = DealBookingFlow._listeners.get("consolidate_recommendations")
        assert triggers is not None
        assert triggers["type"] == "AND"
        assert "research_social" in triggers["conditions"]


# ===========================================================================
# _execute_bookings routing
# ===========================================================================


class TestMetaBookingRouting:
    def test_social_rec_books_via_meta_not_orchestrator(self):
        rec = _make_recommendation("meta:feed", "social")
        flow = _flow_with_approved([rec])

        with patch.object(
            flow, "_book_via_meta_api", return_value=("camp_123", "adset_456", "meta")
        ) as meta_mock:
            result = flow._execute_bookings()

        meta_mock.assert_called_once_with(rec)
        flow._orchestrator.orchestrate.assert_not_called()
        assert result["status"] == "success"
        assert result["booked"] == 1

        booked = flow.state.booked_lines[0]
        assert booked.deal_id == "camp_123"  # Meta-issued campaign id
        assert booked.order_id == "camp_123"
        assert booked.line_id == "adset_456"
        assert booked.seller_id == "meta"
        assert booked.channel == "social"
        assert booked.booking_status == "paused"

    def test_mixed_channels_route_independently(self):
        social = _make_recommendation("meta:feed", "social")
        branding = _make_recommendation("prod_b", "branding")
        flow = _flow_with_approved([social, branding])

        with patch.object(
            flow, "_book_via_meta_api", return_value=("camp_1", "adset_1", "meta")
        ) as meta_mock:
            result = flow._execute_bookings()

        meta_mock.assert_called_once_with(social)
        flow._orchestrator.orchestrate.assert_called_once()
        assert result["booked"] == 2
        statuses = {b.product_id: b.booking_status for b in flow.state.booked_lines}
        assert statuses["meta:feed"] == "paused"
        assert statuses["prod_b"] == "active"

    def test_meta_failure_is_isolated(self):
        """A Meta booking failure must not sink the seller-side bookings."""
        social = _make_recommendation("meta:feed", "social")
        branding = _make_recommendation("prod_b", "branding")
        flow = _flow_with_approved([social, branding])

        with patch.object(flow, "_book_via_meta_api", side_effect=RuntimeError("Meta down")):
            result = flow._execute_bookings()

        assert result["status"] == "success"  # branding still booked
        assert result["booked"] == 1
        assert any("meta:feed" in f.get("product_id", "") for f in result["failed"])
        assert any("Meta down" in e for e in flow.state.errors)

    def test_all_meta_failures_fail_the_run(self):
        social = _make_recommendation("meta:feed", "social")
        flow = _flow_with_approved([social])

        with patch.object(flow, "_book_via_meta_api", side_effect=RuntimeError("Meta down")):
            result = flow._execute_bookings()

        assert result["status"] == "failed"
        assert result["booked"] == 0
        assert flow.state.execution_status == ExecutionStatus.FAILED

    def test_duplicate_product_ids_deduplicated(self):
        """Same product from two crews: highest impressions wins, booked once."""
        low = _make_recommendation("prod_dup", "branding", impressions=50000)
        high = _make_recommendation("prod_dup", "performance", impressions=200000)
        flow = _flow_with_approved([low, high])

        result = flow._execute_bookings()

        assert result["booked"] == 1
        flow._orchestrator.orchestrate.assert_called_once()
        assert flow.state.booked_lines[0].impressions == 200000

    def test_spend_ceiling_guards_meta_path_too(self):
        rec = _make_recommendation("meta:feed", "social", impressions=1000000, cpm=20.0)
        flow = _flow_with_approved([rec], budget=100)  # cost 20000 >> 100

        with patch.object(flow, "_book_via_meta_api") as meta_mock:
            result = flow._execute_bookings()

        assert result["status"] == "rejected"
        meta_mock.assert_not_called()


# ===========================================================================
# _book_via_meta_api configuration guard
# ===========================================================================


class TestBookViaMetaApi:
    def test_unconfigured_meta_raises(self):
        rec = _make_recommendation("meta:feed", "social")
        flow = _flow_with_approved([rec])

        with patch("ad_buyer.config.settings.settings") as mock_settings:
            mock_settings.meta_access_token = ""
            mock_settings.meta_ad_account_id = ""
            with pytest.raises(ValueError, match="Meta not configured"):
                flow._book_via_meta_api(rec)

    def test_booking_creates_paused_campaign_and_adset(self):
        rec = _make_recommendation("meta:feed", "social")
        flow = _flow_with_approved([rec])

        client = MagicMock()
        client.create_campaign.return_value = {"id": "camp_9"}
        client.create_adset.return_value = {"id": "adset_9"}

        with (
            patch("ad_buyer.config.settings.settings") as mock_settings,
            patch(
                "ad_buyer.clients.meta_ads_client.MetaAdsClient", return_value=client
            ) as client_cls,
        ):
            mock_settings.meta_access_token = "tok"
            mock_settings.meta_ad_account_id = "act_1"
            mock_settings.meta_page_id = "page_1"
            mock_settings.meta_api_version = "v21.0"

            campaign_id, ad_set_id, kind = flow._book_via_meta_api(rec)

        assert (campaign_id, ad_set_id, kind) == ("camp_9", "adset_9", "meta")
        client_cls.assert_called_once()
        client.create_campaign.assert_called_once()
        client.create_adset.assert_called_once()
        # Budget must be forwarded in integer cents
        _, kwargs = client.create_campaign.call_args
        assert isinstance(kwargs["daily_budget_cents"], int)
        assert kwargs["daily_budget_cents"] >= 100
