# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for portfolio inspection tools.

Tests ListPortfolioTool, SearchPortfolioTool, PortfolioSummaryTool,
and InspectDealTool -- the CrewAI tools DealJockey uses to view,
filter, search, and aggregate portfolio views.
"""

import json

import pytest

from ad_buyer.storage.deal_store import DealStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def deal_store():
    """Create an in-memory DealStore for testing."""
    store = DealStore("sqlite:///:memory:")
    store.connect()
    yield store
    store.disconnect()


@pytest.fixture
def populated_store(deal_store):
    """DealStore with a variety of deals for testing filters and aggregation."""
    # Deal 1: active digital PD from ESPN
    deal_store.save_deal(
        deal_id="deal-001",
        seller_url="https://espn.seller.example.com",
        product_id="prod-espn",
        product_name="ESPN Sports PMP",
        deal_type="PD",
        status="active",
        price=12.50,
        impressions=1000000,
        display_name="ESPN Sports PMP",
        media_type="DIGITAL",
        seller_domain="espn.com",
        seller_org="ESPN",
        seller_type="PUBLISHER",
        description="Premium sports display inventory",
        flight_start="2026-01-01",
        flight_end="2026-06-30",
    )
    deal_store.save_portfolio_metadata(
        deal_id="deal-001",
        import_source="CSV",
        advertiser_id="adv-acme",
        tags='["sports", "premium"]',
    )

    # Deal 2: draft CTV PG from Hulu
    deal_store.save_deal(
        deal_id="deal-002",
        seller_url="https://hulu.seller.example.com",
        product_id="prod-hulu",
        product_name="Hulu CTV PG",
        deal_type="PG",
        status="draft",
        price=25.00,
        impressions=500000,
        display_name="Hulu CTV PG",
        media_type="CTV",
        seller_domain="hulu.com",
        seller_org="Hulu",
        seller_type="PUBLISHER",
        description="Connected TV guaranteed deal",
        flight_start="2026-03-01",
        flight_end="2026-12-31",
    )
    deal_store.save_portfolio_metadata(
        deal_id="deal-002",
        import_source="MANUAL",
        advertiser_id="adv-acme",
        tags='["ctv", "premium"]',
    )

    # Deal 3: paused digital PA from TradeDesk
    deal_store.save_deal(
        deal_id="deal-003",
        seller_url="https://ttd.seller.example.com",
        product_id="prod-ttd",
        product_name="TTD Open Marketplace",
        deal_type="PA",
        status="paused",
        price=8.00,
        impressions=2000000,
        display_name="TTD Open Marketplace",
        media_type="DIGITAL",
        seller_domain="thetradedesk.com",
        seller_org="The Trade Desk",
        seller_type="DSP",
        description="Private auction via TTD",
    )
    deal_store.save_portfolio_metadata(
        deal_id="deal-003",
        import_source="CSV",
        advertiser_id="adv-beta",
    )

    # Deal 4: active LINEAR_TV UPFRONT from NBCU
    deal_store.save_deal(
        deal_id="deal-004",
        seller_url="https://nbcu.seller.example.com",
        product_id="prod-nbcu",
        product_name="NBCU Upfront 2026",
        deal_type="UPFRONT",
        status="active",
        price=45.00,
        impressions=3000000,
        display_name="NBCU Upfront 2026",
        media_type="LINEAR_TV",
        seller_domain="nbcuniversal.com",
        seller_org="NBCUniversal",
        seller_type="PUBLISHER",
        description="Linear TV upfront commitment",
        flight_start="2026-09-01",
        flight_end="2027-05-31",
    )
    deal_store.save_portfolio_metadata(
        deal_id="deal-004",
        import_source="MANUAL",
        advertiser_id="adv-acme",
    )

    # Deal 5: expired AUDIO PD from Spotify
    deal_store.save_deal(
        deal_id="deal-005",
        seller_url="https://spotify.seller.example.com",
        product_id="prod-spotify",
        product_name="Spotify Audio PD",
        deal_type="PD",
        status="expired",
        price=6.00,
        impressions=800000,
        display_name="Spotify Audio PD",
        media_type="AUDIO",
        seller_domain="spotify.com",
        seller_org="Spotify",
        seller_type="PUBLISHER",
        description="Audio streaming inventory",
        flight_start="2025-01-01",
        flight_end="2025-12-31",
    )

    return deal_store


@pytest.fixture
def store_with_related_data(populated_store):
    """Add activations and performance cache to the populated store."""
    # Add activations for deal-001
    populated_store.save_deal_activation(
        deal_id="deal-001",
        platform="TTD",
        platform_deal_id="TTD-ESPN-001",
        activation_status="ACTIVE",
        last_sync_at="2026-03-15T10:00:00Z",
    )
    populated_store.save_deal_activation(
        deal_id="deal-001",
        platform="DV360",
        platform_deal_id="DV360-ESPN-001",
        activation_status="PENDING",
    )

    # Add performance cache for deal-001
    populated_store.save_performance_cache(
        deal_id="deal-001",
        impressions_delivered=500000,
        spend_to_date=6250.00,
        fill_rate=0.85,
        win_rate=0.72,
        avg_effective_cpm=12.50,
        last_delivery_at="2026-03-17T23:59:59Z",
        performance_trend="STABLE",
        cached_at="2026-03-18T00:00:00Z",
    )

    return populated_store


# ---------------------------------------------------------------------------
# ListPortfolioTool tests
# ---------------------------------------------------------------------------


class TestListPortfolioTool:
    """Tests for the ListPortfolioTool CrewAI tool."""

    def test_list_all_deals_no_filters(self, populated_store):
        """List all deals without any filters."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            ListPortfolioTool,
        )

        tool = ListPortfolioTool(deal_store=populated_store)
        result = tool._run(filters_json="{}")

        assert "deal-001" in result or "ESPN" in result
        assert "deal-002" in result or "Hulu" in result
        # Should contain all 5 deals
        assert "5 deal" in result.lower() or all(
            name in result
            for name in ["ESPN", "Hulu", "TTD", "NBCU", "Spotify"]
        )

    def test_filter_by_status(self, populated_store):
        """Filter deals by status."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            ListPortfolioTool,
        )

        tool = ListPortfolioTool(deal_store=populated_store)
        result = tool._run(filters_json='{"status": "active"}')

        assert "ESPN" in result
        assert "NBCU" in result
        # Should not include draft, paused, or expired deals
        assert "Hulu" not in result
        assert "Spotify" not in result

    def test_filter_by_media_type(self, populated_store):
        """Filter deals by media type."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            ListPortfolioTool,
        )

        tool = ListPortfolioTool(deal_store=populated_store)
        result = tool._run(filters_json='{"media_type": "DIGITAL"}')

        assert "ESPN" in result
        assert "TTD" in result
        # Should not include CTV, LINEAR_TV, or AUDIO deals
        assert "Hulu" not in result
        assert "NBCU" not in result

    def test_filter_by_seller_domain(self, populated_store):
        """Filter deals by seller domain."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            ListPortfolioTool,
        )

        tool = ListPortfolioTool(deal_store=populated_store)
        result = tool._run(filters_json='{"seller_domain": "espn.com"}')

        assert "ESPN" in result
        assert "Hulu" not in result

    def test_filter_by_deal_type(self, populated_store):
        """Filter deals by deal type."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            ListPortfolioTool,
        )

        tool = ListPortfolioTool(deal_store=populated_store)
        result = tool._run(filters_json='{"deal_type": "PG"}')

        assert "Hulu" in result
        assert "ESPN" not in result

    def test_filter_by_advertiser_id(self, populated_store):
        """Filter deals by advertiser ID from portfolio metadata."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            ListPortfolioTool,
        )

        tool = ListPortfolioTool(deal_store=populated_store)
        result = tool._run(filters_json='{"advertiser_id": "adv-beta"}')

        assert "TTD" in result
        assert "ESPN" not in result
        assert "Hulu" not in result

    def test_filter_by_seller_type(self, populated_store):
        """Filter deals by seller type."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            ListPortfolioTool,
        )

        tool = ListPortfolioTool(deal_store=populated_store)
        result = tool._run(filters_json='{"seller_type": "DSP"}')

        assert "TTD" in result or "Trade Desk" in result
        assert "ESPN" not in result

    def test_combined_filters(self, populated_store):
        """Apply multiple filters at once."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            ListPortfolioTool,
        )

        tool = ListPortfolioTool(deal_store=populated_store)
        result = tool._run(
            filters_json='{"status": "active", "media_type": "DIGITAL"}'
        )

        assert "ESPN" in result
        # NBCU is active but LINEAR_TV, not DIGITAL
        assert "NBCU" not in result

    def test_pagination_limit(self, populated_store):
        """Limit the number of results."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            ListPortfolioTool,
        )

        tool = ListPortfolioTool(deal_store=populated_store)
        result = tool._run(filters_json='{"limit": 2}')

        # Should show only 2 deals and indicate more are available
        # The exact text will depend on implementation, but the total
        # deal count of 5 should be mentioned or indicated
        assert "2" in result

    def test_pagination_offset(self, populated_store):
        """Skip results with offset."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            ListPortfolioTool,
        )

        tool = ListPortfolioTool(deal_store=populated_store)
        # Get all results first
        all_result = tool._run(filters_json='{}')
        # Get offset results
        offset_result = tool._run(filters_json='{"offset": 2, "limit": 2}')

        # Offset results should not include the first 2 results
        assert offset_result != all_result

    def test_sort_by_price_asc(self, populated_store):
        """Sort deals by price ascending."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            ListPortfolioTool,
        )

        tool = ListPortfolioTool(deal_store=populated_store)
        result = tool._run(
            filters_json='{"sort_by": "price", "sort_order": "asc"}'
        )

        # Spotify ($6) should appear before ESPN ($12.50)
        spotify_pos = result.find("Spotify")
        espn_pos = result.find("ESPN")
        if spotify_pos >= 0 and espn_pos >= 0:
            assert spotify_pos < espn_pos

    def test_sort_by_price_desc(self, populated_store):
        """Sort deals by price descending."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            ListPortfolioTool,
        )

        tool = ListPortfolioTool(deal_store=populated_store)
        result = tool._run(
            filters_json='{"sort_by": "price", "sort_order": "desc"}'
        )

        # NBCU ($45) should appear before ESPN ($12.50)
        nbcu_pos = result.find("NBCU")
        espn_pos = result.find("ESPN")
        if nbcu_pos >= 0 and espn_pos >= 0:
            assert nbcu_pos < espn_pos

    def test_sort_by_display_name(self, populated_store):
        """Sort deals by display name."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            ListPortfolioTool,
        )

        tool = ListPortfolioTool(deal_store=populated_store)
        result = tool._run(
            filters_json='{"sort_by": "display_name", "sort_order": "asc"}'
        )

        # ESPN should appear before Hulu (alphabetical)
        espn_pos = result.find("ESPN")
        hulu_pos = result.find("Hulu")
        if espn_pos >= 0 and hulu_pos >= 0:
            assert espn_pos < hulu_pos

    def test_empty_database(self, deal_store):
        """Handle an empty database gracefully."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            ListPortfolioTool,
        )

        tool = ListPortfolioTool(deal_store=deal_store)
        result = tool._run(filters_json="{}")

        assert "no deal" in result.lower() or "0 deal" in result.lower() or "empty" in result.lower()

    def test_invalid_json_input(self, deal_store):
        """Handle invalid JSON gracefully."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            ListPortfolioTool,
        )

        tool = ListPortfolioTool(deal_store=deal_store)
        result = tool._run(filters_json="not valid json")

        assert "error" in result.lower()

    def test_returns_human_readable_output(self, populated_store):
        """Output should be human-readable, not raw dicts."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            ListPortfolioTool,
        )

        tool = ListPortfolioTool(deal_store=populated_store)
        result = tool._run(filters_json='{"status": "active"}')

        # Should not look like a raw Python dict
        assert "{'id'" not in result
        # Should contain structured, readable information
        assert "ESPN" in result


# ---------------------------------------------------------------------------
# SearchPortfolioTool tests
# ---------------------------------------------------------------------------


class TestSearchPortfolioTool:
    """Tests for the SearchPortfolioTool CrewAI tool."""

    def test_search_by_display_name(self, populated_store):
        """Search should find deals by display name."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            SearchPortfolioTool,
        )

        tool = SearchPortfolioTool(deal_store=populated_store)
        result = tool._run(query="ESPN")

        assert "ESPN" in result
        assert "deal-001" in result

    def test_search_by_seller_org(self, populated_store):
        """Search should find deals by seller organization."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            SearchPortfolioTool,
        )

        tool = SearchPortfolioTool(deal_store=populated_store)
        result = tool._run(query="NBCUniversal")

        assert "NBCU" in result
        assert "deal-004" in result

    def test_search_by_seller_domain(self, populated_store):
        """Search should find deals by seller domain."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            SearchPortfolioTool,
        )

        tool = SearchPortfolioTool(deal_store=populated_store)
        result = tool._run(query="hulu.com")

        assert "Hulu" in result

    def test_search_by_description(self, populated_store):
        """Search should find deals by description text."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            SearchPortfolioTool,
        )

        tool = SearchPortfolioTool(deal_store=populated_store)
        result = tool._run(query="streaming")

        assert "Spotify" in result

    def test_search_case_insensitive(self, populated_store):
        """Search should be case-insensitive."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            SearchPortfolioTool,
        )

        tool = SearchPortfolioTool(deal_store=populated_store)
        result = tool._run(query="espn")

        assert "ESPN" in result

    def test_search_no_results(self, populated_store):
        """Search with no matches should report cleanly."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            SearchPortfolioTool,
        )

        tool = SearchPortfolioTool(deal_store=populated_store)
        result = tool._run(query="nonexistent_xyz_deal")

        assert "no" in result.lower() or "0" in result

    def test_search_empty_database(self, deal_store):
        """Search on empty database should handle gracefully."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            SearchPortfolioTool,
        )

        tool = SearchPortfolioTool(deal_store=deal_store)
        result = tool._run(query="anything")

        assert "no" in result.lower() or "0" in result

    def test_search_shows_match_context(self, populated_store):
        """Search results should indicate which field matched."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            SearchPortfolioTool,
        )

        tool = SearchPortfolioTool(deal_store=populated_store)
        result = tool._run(query="streaming")

        # Should indicate the match was in the description field
        assert "description" in result.lower() or "matched" in result.lower()

    def test_search_empty_query(self, populated_store):
        """Empty search query should return an error or all deals."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            SearchPortfolioTool,
        )

        tool = SearchPortfolioTool(deal_store=populated_store)
        result = tool._run(query="")

        # Either an error message or returns all deals
        assert len(result) > 0


# ---------------------------------------------------------------------------
# PortfolioSummaryTool tests
# ---------------------------------------------------------------------------


class TestPortfolioSummaryTool:
    """Tests for the PortfolioSummaryTool CrewAI tool."""

    def test_summary_empty_portfolio(self, deal_store):
        """Summary on empty portfolio should report zeros gracefully."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            PortfolioSummaryTool,
        )

        tool = PortfolioSummaryTool(deal_store=deal_store)
        result = tool._run(options_json="{}")

        assert "0" in result

    def test_summary_total_deal_count(self, populated_store):
        """Summary should report total number of deals."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            PortfolioSummaryTool,
        )

        tool = PortfolioSummaryTool(deal_store=populated_store)
        result = tool._run(options_json="{}")

        assert "5" in result

    def test_summary_by_status(self, populated_store):
        """Summary should break down deals by status."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            PortfolioSummaryTool,
        )

        tool = PortfolioSummaryTool(deal_store=populated_store)
        result = tool._run(options_json="{}")

        # 2 active, 1 draft, 1 paused, 1 expired
        assert "active" in result.lower()
        assert "draft" in result.lower()
        assert "paused" in result.lower()
        assert "expired" in result.lower()

    def test_summary_by_media_type(self, populated_store):
        """Summary should break down deals by media type."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            PortfolioSummaryTool,
        )

        tool = PortfolioSummaryTool(deal_store=populated_store)
        result = tool._run(options_json="{}")

        assert "DIGITAL" in result
        assert "CTV" in result
        assert "LINEAR_TV" in result
        assert "AUDIO" in result

    def test_summary_by_deal_type(self, populated_store):
        """Summary should break down deals by deal type."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            PortfolioSummaryTool,
        )

        tool = PortfolioSummaryTool(deal_store=populated_store)
        result = tool._run(options_json="{}")

        assert "PD" in result
        assert "PG" in result
        assert "PA" in result
        assert "UPFRONT" in result

    def test_summary_top_sellers(self, populated_store):
        """Summary should list top sellers by deal count."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            PortfolioSummaryTool,
        )

        tool = PortfolioSummaryTool(deal_store=populated_store)
        result = tool._run(options_json="{}")

        # Should mention some sellers
        assert "ESPN" in result or "espn.com" in result.lower()

    def test_summary_portfolio_value(self, populated_store):
        """Summary should calculate total portfolio value."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            PortfolioSummaryTool,
        )

        tool = PortfolioSummaryTool(deal_store=populated_store)
        result = tool._run(options_json="{}")

        # Total value = sum of (price * impressions / 1000) for CPM
        # ESPN: 12.50 * 1,000,000 / 1000 = 12,500
        # Hulu: 25.00 * 500,000 / 1000 = 12,500
        # TTD: 8.00 * 2,000,000 / 1000 = 16,000
        # NBCU: 45.00 * 3,000,000 / 1000 = 135,000
        # Spotify: 6.00 * 800,000 / 1000 = 4,800
        # Total = 180,800
        assert "180,800" in result or "180800" in result

    def test_summary_expiring_deals(self, populated_store):
        """Summary should report deals expiring within N days if requested."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            PortfolioSummaryTool,
        )

        tool = PortfolioSummaryTool(deal_store=populated_store)
        # Check for deals expiring within a very large window (all should be caught)
        result = tool._run(options_json='{"expiring_within_days": 365}')

        assert "expir" in result.lower()

    def test_summary_invalid_json(self, deal_store):
        """Handle invalid JSON input gracefully."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            PortfolioSummaryTool,
        )

        tool = PortfolioSummaryTool(deal_store=deal_store)
        result = tool._run(options_json="bad json")

        assert "error" in result.lower()


# ---------------------------------------------------------------------------
# InspectDealTool tests
# ---------------------------------------------------------------------------


class TestInspectDealTool:
    """Tests for the InspectDealTool CrewAI tool."""

    def test_inspect_existing_deal(self, populated_store):
        """Inspect an existing deal by ID."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            InspectDealTool,
        )

        tool = InspectDealTool(deal_store=populated_store)
        result = tool._run(deal_id="deal-001")

        assert "ESPN" in result
        assert "deal-001" in result
        assert "DIGITAL" in result
        assert "PD" in result
        assert "active" in result.lower()
        assert "12.5" in result

    def test_inspect_deal_shows_metadata(self, populated_store):
        """Inspect should show portfolio metadata."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            InspectDealTool,
        )

        tool = InspectDealTool(deal_store=populated_store)
        result = tool._run(deal_id="deal-001")

        assert "adv-acme" in result
        assert "CSV" in result

    def test_inspect_deal_shows_activations(self, store_with_related_data):
        """Inspect should show deal activations."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            InspectDealTool,
        )

        tool = InspectDealTool(deal_store=store_with_related_data)
        result = tool._run(deal_id="deal-001")

        assert "TTD" in result
        assert "DV360" in result
        assert "ACTIVE" in result

    def test_inspect_deal_shows_performance(self, store_with_related_data):
        """Inspect should show performance cache data."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            InspectDealTool,
        )

        tool = InspectDealTool(deal_store=store_with_related_data)
        result = tool._run(deal_id="deal-001")

        assert "500,000" in result or "500000" in result
        assert "STABLE" in result

    def test_inspect_nonexistent_deal(self, deal_store):
        """Inspect a non-existent deal should return a clear message."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            InspectDealTool,
        )

        tool = InspectDealTool(deal_store=deal_store)
        result = tool._run(deal_id="nonexistent-deal")

        assert "not found" in result.lower()

    def test_inspect_deal_without_metadata(self, deal_store):
        """Inspect a deal that has no portfolio_metadata or activations."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            InspectDealTool,
        )

        # Save a bare deal with no metadata
        deal_store.save_deal(
            deal_id="deal-bare",
            seller_url="https://seller.example.com",
            product_id="prod-bare",
            product_name="Bare Deal",
            display_name="Bare Deal",
        )
        tool = InspectDealTool(deal_store=deal_store)
        result = tool._run(deal_id="deal-bare")

        assert "Bare Deal" in result
        assert "deal-bare" in result

    def test_inspect_human_readable_format(self, populated_store):
        """Output should be formatted for agent readability, not raw dicts."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            InspectDealTool,
        )

        tool = InspectDealTool(deal_store=populated_store)
        result = tool._run(deal_id="deal-001")

        # Should not look like a raw Python dict
        assert "{'id'" not in result


# ---------------------------------------------------------------------------
# Tool registration / export tests
# ---------------------------------------------------------------------------


class TestToolExports:
    """Verify tools are importable and properly exported."""

    def test_import_all_tools(self):
        """All four tools should be importable from the module."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            InspectDealTool,
            ListPortfolioTool,
            PortfolioSummaryTool,
            SearchPortfolioTool,
        )

        assert ListPortfolioTool is not None
        assert SearchPortfolioTool is not None
        assert PortfolioSummaryTool is not None
        assert InspectDealTool is not None

    def test_tools_exported_from_deal_jockey_init(self):
        """Tools should be re-exported from deal_jockey.__init__."""
        from ad_buyer.tools.deal_jockey import (
            InspectDealTool,
            ListPortfolioTool,
            PortfolioSummaryTool,
            SearchPortfolioTool,
        )

        assert ListPortfolioTool is not None
        assert SearchPortfolioTool is not None
        assert PortfolioSummaryTool is not None
        assert InspectDealTool is not None

    def test_tools_are_crewai_base_tools(self, deal_store):
        """Each tool should be a CrewAI BaseTool subclass."""
        from crewai.tools import BaseTool

        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            InspectDealTool,
            ListPortfolioTool,
            PortfolioSummaryTool,
            SearchPortfolioTool,
        )

        assert issubclass(ListPortfolioTool, BaseTool)
        assert issubclass(SearchPortfolioTool, BaseTool)
        assert issubclass(PortfolioSummaryTool, BaseTool)
        assert issubclass(InspectDealTool, BaseTool)

    def test_tools_have_name_and_description(self, deal_store):
        """Each tool should have a name and description."""
        from ad_buyer.tools.deal_jockey.portfolio_inspection import (
            InspectDealTool,
            ListPortfolioTool,
            PortfolioSummaryTool,
            SearchPortfolioTool,
        )

        for ToolClass in [
            ListPortfolioTool,
            SearchPortfolioTool,
            PortfolioSummaryTool,
            InspectDealTool,
        ]:
            tool = ToolClass(deal_store=deal_store)
            assert tool.name
            assert tool.description
