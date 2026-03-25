# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for Template & Reporting MCP tools.

Tests six MCP tools: list_templates, create_template,
instantiate_from_template, get_deal_performance, get_campaign_report,
get_pacing_report.

bead: buyer-5x7
"""

import json
from datetime import UTC, datetime

import pytest

from ad_buyer.interfaces.mcp_server import mcp, _set_deal_store
from ad_buyer.storage.campaign_store import CampaignStore
from ad_buyer.storage.deal_store import DealStore
from ad_buyer.storage.pacing_store import PacingStore
from ad_buyer.models.campaign import (
    ChannelSnapshot,
    DealSnapshot,
    PacingSnapshot,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DB_URL = "sqlite:///:memory:"


def _extract_text(call_result) -> str:
    """Extract text content from a FastMCP call_tool result."""
    content_list = call_result[0]
    return content_list[0].text


def _make_deal_store() -> DealStore:
    """Create and connect an in-memory DealStore."""
    store = DealStore(DB_URL)
    store.connect()
    return store


def _make_campaign_store() -> CampaignStore:
    """Create and connect an in-memory CampaignStore."""
    store = CampaignStore(DB_URL)
    store.connect()
    return store


def _make_pacing_store() -> PacingStore:
    """Create and connect an in-memory PacingStore."""
    store = PacingStore(DB_URL)
    store.connect()
    return store


def _seed_campaign(store: CampaignStore, **overrides) -> str:
    """Create a campaign with sensible defaults. Returns campaign_id."""
    defaults = {
        "advertiser_id": "adv-001",
        "campaign_name": "Test Campaign",
        "status": "ACTIVE",
        "total_budget": 100000.0,
        "currency": "USD",
        "flight_start": "2026-03-01",
        "flight_end": "2026-03-31",
        "channels": json.dumps([
            {"channel": "CTV", "budget_pct": 0.6},
            {"channel": "DISPLAY", "budget_pct": 0.4},
        ]),
    }
    defaults.update(overrides)
    return store.save_campaign(**defaults)


def _seed_pacing_snapshot(
    pacing_store: PacingStore,
    campaign_id: str,
    total_budget: float = 100000.0,
    total_spend: float = 50000.0,
    expected_spend: float = 50000.0,
    pacing_pct: float = 100.0,
    deviation_pct: float = 0.0,
    channel_snapshots: list | None = None,
    deal_snapshots: list | None = None,
) -> str:
    """Create a pacing snapshot. Returns snapshot_id."""
    snapshot = PacingSnapshot(
        campaign_id=campaign_id,
        timestamp=datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC),
        total_budget=total_budget,
        total_spend=total_spend,
        pacing_pct=pacing_pct,
        expected_spend=expected_spend,
        deviation_pct=deviation_pct,
        channel_snapshots=channel_snapshots or [],
        deal_snapshots=deal_snapshots or [],
    )
    return pacing_store.save_pacing_snapshot(snapshot)


# ---------------------------------------------------------------------------
# Test tool registration
# ---------------------------------------------------------------------------


class TestTemplateReportingToolRegistration:
    """Verify all 6 template/reporting MCP tools are registered."""

    @pytest.mark.asyncio
    async def test_list_templates_registered(self):
        """list_templates should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "list_templates" in names

    @pytest.mark.asyncio
    async def test_create_template_registered(self):
        """create_template should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "create_template" in names

    @pytest.mark.asyncio
    async def test_instantiate_from_template_registered(self):
        """instantiate_from_template should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "instantiate_from_template" in names

    @pytest.mark.asyncio
    async def test_get_deal_performance_registered(self):
        """get_deal_performance should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "get_deal_performance" in names

    @pytest.mark.asyncio
    async def test_get_campaign_report_registered(self):
        """get_campaign_report should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "get_campaign_report" in names

    @pytest.mark.asyncio
    async def test_get_pacing_report_registered(self):
        """get_pacing_report should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "get_pacing_report" in names

    @pytest.mark.asyncio
    async def test_total_tool_count(self):
        """Should have 12 existing + 6 new = 18 tools."""
        tools = await mcp.list_tools()
        names = sorted(t.name for t in tools)
        assert len(names) == 18, f"Expected 18 tools, got {len(names)}: {names}"


# ---------------------------------------------------------------------------
# Test list_templates
# ---------------------------------------------------------------------------


class TestListTemplates:
    """Tests for the list_templates MCP tool."""

    @pytest.mark.asyncio
    async def test_no_templates_returns_empty(self):
        """list_templates should return empty list when no templates exist."""
        store = _make_deal_store()
        _set_deal_store(store)
        try:
            result = await mcp.call_tool("list_templates", {})
            data = json.loads(_extract_text(result))
            assert data["deal_templates"] == []
            assert data["supply_path_templates"] == []
            assert data["total_deal_templates"] == 0
            assert data["total_supply_path_templates"] == 0
        finally:
            _set_deal_store(None)

    @pytest.mark.asyncio
    async def test_lists_deal_templates(self):
        """list_templates should return deal templates."""
        store = _make_deal_store()
        store.save_deal_template(name="Sports PG", deal_type_pref="PG")
        store.save_deal_template(name="News PMP", deal_type_pref="PMP")
        _set_deal_store(store)
        try:
            result = await mcp.call_tool("list_templates", {})
            data = json.loads(_extract_text(result))
            assert data["total_deal_templates"] == 2
            names = {t["name"] for t in data["deal_templates"]}
            assert names == {"Sports PG", "News PMP"}
        finally:
            _set_deal_store(None)

    @pytest.mark.asyncio
    async def test_lists_supply_path_templates(self):
        """list_templates should return supply path templates."""
        store = _make_deal_store()
        store.save_supply_path_template(name="Direct Paths")
        _set_deal_store(store)
        try:
            result = await mcp.call_tool("list_templates", {})
            data = json.loads(_extract_text(result))
            assert data["total_supply_path_templates"] == 1
            assert data["supply_path_templates"][0]["name"] == "Direct Paths"
        finally:
            _set_deal_store(None)

    @pytest.mark.asyncio
    async def test_filter_by_template_type(self):
        """list_templates with template_type='deal' should only return deal templates."""
        store = _make_deal_store()
        store.save_deal_template(name="Sports PG")
        store.save_supply_path_template(name="Direct Paths")
        _set_deal_store(store)
        try:
            result = await mcp.call_tool("list_templates", {"template_type": "deal"})
            data = json.loads(_extract_text(result))
            assert data["total_deal_templates"] == 1
            assert data["total_supply_path_templates"] == 0
        finally:
            _set_deal_store(None)

    @pytest.mark.asyncio
    async def test_filter_by_supply_path_type(self):
        """list_templates with template_type='supply_path' should only return supply path templates."""
        store = _make_deal_store()
        store.save_deal_template(name="Sports PG")
        store.save_supply_path_template(name="Direct Paths")
        _set_deal_store(store)
        try:
            result = await mcp.call_tool("list_templates", {"template_type": "supply_path"})
            data = json.loads(_extract_text(result))
            assert data["total_deal_templates"] == 0
            assert data["total_supply_path_templates"] == 1
        finally:
            _set_deal_store(None)

    @pytest.mark.asyncio
    async def test_returns_valid_json(self):
        """list_templates should return valid JSON."""
        store = _make_deal_store()
        _set_deal_store(store)
        try:
            result = await mcp.call_tool("list_templates", {})
            data = json.loads(_extract_text(result))
            assert "timestamp" in data
        finally:
            _set_deal_store(None)


# ---------------------------------------------------------------------------
# Test create_template
# ---------------------------------------------------------------------------


class TestCreateTemplate:
    """Tests for the create_template MCP tool."""

    @pytest.mark.asyncio
    async def test_create_deal_template(self):
        """create_template should create a deal template and return its ID."""
        store = _make_deal_store()
        _set_deal_store(store)
        try:
            result = await mcp.call_tool("create_template", {
                "template_type": "deal",
                "name": "Sports PG",
                "deal_type_pref": "PG",
                "max_cpm": 25.0,
            })
            data = json.loads(_extract_text(result))
            assert "template_id" in data
            assert data["name"] == "Sports PG"
            assert data["template_type"] == "deal"

            # Verify it was persisted
            tmpl = store.get_deal_template(data["template_id"])
            assert tmpl is not None
            assert tmpl["name"] == "Sports PG"
            assert tmpl["max_cpm"] == 25.0
        finally:
            _set_deal_store(None)

    @pytest.mark.asyncio
    async def test_create_supply_path_template(self):
        """create_template should create a supply path template."""
        store = _make_deal_store()
        _set_deal_store(store)
        try:
            result = await mcp.call_tool("create_template", {
                "template_type": "supply_path",
                "name": "Direct Paths",
                "max_reseller_hops": 2,
            })
            data = json.loads(_extract_text(result))
            assert "template_id" in data
            assert data["template_type"] == "supply_path"
            assert data["name"] == "Direct Paths"

            # Verify persistence
            tmpl = store.get_supply_path_template(data["template_id"])
            assert tmpl is not None
            assert tmpl["name"] == "Direct Paths"
        finally:
            _set_deal_store(None)

    @pytest.mark.asyncio
    async def test_create_requires_name(self):
        """create_template should error if no name provided."""
        store = _make_deal_store()
        _set_deal_store(store)
        try:
            result = await mcp.call_tool("create_template", {
                "template_type": "deal",
            })
            data = json.loads(_extract_text(result))
            assert "error" in data
        finally:
            _set_deal_store(None)

    @pytest.mark.asyncio
    async def test_create_requires_template_type(self):
        """create_template should error if no template_type provided."""
        store = _make_deal_store()
        _set_deal_store(store)
        try:
            result = await mcp.call_tool("create_template", {
                "name": "Test",
            })
            data = json.loads(_extract_text(result))
            assert "error" in data
        finally:
            _set_deal_store(None)

    @pytest.mark.asyncio
    async def test_create_invalid_template_type(self):
        """create_template should error if invalid template_type."""
        store = _make_deal_store()
        _set_deal_store(store)
        try:
            result = await mcp.call_tool("create_template", {
                "template_type": "unknown",
                "name": "Test",
            })
            data = json.loads(_extract_text(result))
            assert "error" in data
        finally:
            _set_deal_store(None)


# ---------------------------------------------------------------------------
# Test instantiate_from_template
# ---------------------------------------------------------------------------


class TestInstantiateFromTemplate:
    """Tests for the instantiate_from_template MCP tool."""

    @pytest.mark.asyncio
    async def test_instantiate_creates_deal_from_template(self):
        """instantiate_from_template should create a deal from a template."""
        store = _make_deal_store()
        tmpl_id = store.save_deal_template(
            name="Sports PG",
            deal_type_pref="PG",
            default_price=20.0,
            min_impressions=500000,
            default_flight_days=30,
        )
        _set_deal_store(store)
        try:
            result = await mcp.call_tool("instantiate_from_template", {
                "template_id": tmpl_id,
            })
            data = json.loads(_extract_text(result))
            assert "deal_id" in data
            assert data["template_id"] == tmpl_id
            assert data["template_name"] == "Sports PG"

            # Verify a deal was saved
            deal = store.get_deal(data["deal_id"])
            assert deal is not None
        finally:
            _set_deal_store(None)

    @pytest.mark.asyncio
    async def test_instantiate_with_overrides(self):
        """instantiate_from_template should apply overrides.

        Note: overrides is typed as str, but MCP pre-parses JSON string
        values to their native types. We pass a non-parseable string
        (not valid JSON object) so MCP passes it through as a string,
        OR we rely on the function's isinstance(overrides, dict) fallback.
        Using a simple string that is NOT a JSON object ensures MCP
        does not pre-parse it.
        """
        store = _make_deal_store()
        tmpl_id = store.save_deal_template(
            name="Sports PG",
            deal_type_pref="PG",
            default_price=20.0,
        )
        _set_deal_store(store)
        try:
            # Call the function directly to bypass MCP's pre-parse
            from ad_buyer.interfaces.mcp_server import instantiate_from_template
            result_str = instantiate_from_template(
                template_id=tmpl_id,
                overrides='{"price": 25.0}',
            )
            data = json.loads(result_str)
            assert "deal_id" in data
            assert data["price"] == 25.0
            # The deal price should reflect the override
            deal = store.get_deal(data["deal_id"])
            assert deal is not None
            assert deal["price"] == 25.0
        finally:
            _set_deal_store(None)

    @pytest.mark.asyncio
    async def test_instantiate_template_not_found(self):
        """instantiate_from_template should error for nonexistent template."""
        store = _make_deal_store()
        _set_deal_store(store)
        try:
            result = await mcp.call_tool("instantiate_from_template", {
                "template_id": "nonexistent-id",
            })
            data = json.loads(_extract_text(result))
            assert "error" in data
        finally:
            _set_deal_store(None)

    @pytest.mark.asyncio
    async def test_instantiate_requires_template_id(self):
        """instantiate_from_template should error when template_id is missing."""
        store = _make_deal_store()
        _set_deal_store(store)
        try:
            result = await mcp.call_tool("instantiate_from_template", {})
            data = json.loads(_extract_text(result))
            assert "error" in data
        finally:
            _set_deal_store(None)


# ---------------------------------------------------------------------------
# Test get_deal_performance
# ---------------------------------------------------------------------------


class TestGetDealPerformance:
    """Tests for the get_deal_performance MCP tool."""

    @pytest.mark.asyncio
    async def test_deal_not_found(self):
        """get_deal_performance should error when deal not found."""
        store = _make_deal_store()
        _set_deal_store(store)
        try:
            result = await mcp.call_tool("get_deal_performance", {
                "deal_id": "nonexistent",
            })
            data = json.loads(_extract_text(result))
            assert "error" in data
        finally:
            _set_deal_store(None)

    @pytest.mark.asyncio
    async def test_deal_performance_basic(self):
        """get_deal_performance should return deal info and performance."""
        store = _make_deal_store()
        deal_id = store.save_deal(
            seller_url="http://seller:5000",
            product_id="prod-001",
            product_name="Premium CTV",
            status="booked",
            price=15.0,
        )
        _set_deal_store(store)
        try:
            result = await mcp.call_tool("get_deal_performance", {
                "deal_id": deal_id,
            })
            data = json.loads(_extract_text(result))
            assert data["deal_id"] == deal_id
            assert data["product_name"] == "Premium CTV"
            assert data["price"] == 15.0
            assert "timestamp" in data
        finally:
            _set_deal_store(None)

    @pytest.mark.asyncio
    async def test_returns_valid_json(self):
        """get_deal_performance should return valid JSON."""
        store = _make_deal_store()
        deal_id = store.save_deal(
            seller_url="http://seller:5000",
            product_id="prod-001",
            product_name="Test",
            status="booked",
            price=10.0,
        )
        _set_deal_store(store)
        try:
            result = await mcp.call_tool("get_deal_performance", {
                "deal_id": deal_id,
            })
            data = json.loads(_extract_text(result))
            assert isinstance(data, dict)
        finally:
            _set_deal_store(None)


# ---------------------------------------------------------------------------
# Test get_campaign_report
# ---------------------------------------------------------------------------


class TestGetCampaignReport:
    """Tests for the get_campaign_report MCP tool."""

    @pytest.mark.asyncio
    async def test_campaign_not_found(self, monkeypatch):
        """get_campaign_report should error when campaign not found."""
        store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store", lambda: store
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store", lambda: pacing_store
        )
        result = await mcp.call_tool("get_campaign_report", {
            "campaign_id": "nonexistent",
        })
        data = json.loads(_extract_text(result))
        assert "error" in data

    @pytest.mark.asyncio
    async def test_campaign_report_basic(self, monkeypatch):
        """get_campaign_report should return a full report with all sections."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        cid = _seed_campaign(campaign_store)
        _seed_pacing_snapshot(
            pacing_store, cid,
            channel_snapshots=[
                ChannelSnapshot(
                    channel="CTV", allocated_budget=60000.0,
                    spend=30000.0, pacing_pct=100.0,
                    impressions=2000000, effective_cpm=15.0, fill_rate=0.85,
                ),
            ],
            deal_snapshots=[
                DealSnapshot(
                    deal_id="deal-001", allocated_budget=40000.0,
                    spend=20000.0, impressions=1000000,
                    effective_cpm=20.0, fill_rate=0.9, win_rate=0.3,
                ),
            ],
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store", lambda: campaign_store
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store", lambda: pacing_store
        )
        result = await mcp.call_tool("get_campaign_report", {
            "campaign_id": cid,
        })
        data = json.loads(_extract_text(result))
        assert data["campaign_id"] == cid
        assert "status_summary" in data
        assert "pacing" in data
        assert "creative_summary" in data
        assert "deal_summary" in data
        assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_campaign_report_no_pacing_data(self, monkeypatch):
        """get_campaign_report should work even with no pacing data."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        cid = _seed_campaign(campaign_store)
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store", lambda: campaign_store
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store", lambda: pacing_store
        )
        result = await mcp.call_tool("get_campaign_report", {
            "campaign_id": cid,
        })
        data = json.loads(_extract_text(result))
        assert data["campaign_id"] == cid
        assert "status_summary" in data


# ---------------------------------------------------------------------------
# Test get_pacing_report
# ---------------------------------------------------------------------------


class TestGetPacingReport:
    """Tests for the get_pacing_report MCP tool."""

    @pytest.mark.asyncio
    async def test_campaign_not_found(self, monkeypatch):
        """get_pacing_report should error when campaign not found."""
        store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store", lambda: store
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store", lambda: pacing_store
        )
        result = await mcp.call_tool("get_pacing_report", {
            "campaign_id": "nonexistent",
        })
        data = json.loads(_extract_text(result))
        assert "error" in data

    @pytest.mark.asyncio
    async def test_pacing_report_with_data(self, monkeypatch):
        """get_pacing_report should return pacing details with alerts."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        cid = _seed_campaign(campaign_store)
        _seed_pacing_snapshot(
            pacing_store, cid,
            total_spend=35000.0,
            expected_spend=50000.0,
            pacing_pct=70.0,
            deviation_pct=-30.0,
            channel_snapshots=[
                ChannelSnapshot(
                    channel="CTV", allocated_budget=60000.0,
                    spend=18000.0, pacing_pct=60.0,
                    impressions=1200000, effective_cpm=15.0, fill_rate=0.85,
                ),
                ChannelSnapshot(
                    channel="DISPLAY", allocated_budget=40000.0,
                    spend=17000.0, pacing_pct=85.0,
                    impressions=850000, effective_cpm=20.0, fill_rate=0.7,
                ),
            ],
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store", lambda: campaign_store
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store", lambda: pacing_store
        )
        result = await mcp.call_tool("get_pacing_report", {
            "campaign_id": cid,
        })
        data = json.loads(_extract_text(result))
        assert data["campaign_id"] == cid
        assert data["pacing_status"] in ("behind", "on_track", "ahead", "no_data")
        assert "total_budget" in data
        assert "total_spend" in data
        assert "expected_spend" in data
        assert "pacing_pct" in data
        assert "deviation_pct" in data
        assert "channel_pacing" in data
        assert "alerts" in data
        assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_pacing_report_no_data(self, monkeypatch):
        """get_pacing_report should return no_data when no snapshots exist."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        cid = _seed_campaign(campaign_store)
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store", lambda: campaign_store
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store", lambda: pacing_store
        )
        result = await mcp.call_tool("get_pacing_report", {
            "campaign_id": cid,
        })
        data = json.loads(_extract_text(result))
        assert data["pacing_status"] == "no_data"

    @pytest.mark.asyncio
    async def test_pacing_report_behind(self, monkeypatch):
        """get_pacing_report should report 'behind' when heavily underpacing."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        cid = _seed_campaign(campaign_store)
        _seed_pacing_snapshot(
            pacing_store, cid,
            total_spend=30000.0,
            expected_spend=50000.0,
            pacing_pct=60.0,
            deviation_pct=-40.0,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store", lambda: campaign_store
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store", lambda: pacing_store
        )
        result = await mcp.call_tool("get_pacing_report", {
            "campaign_id": cid,
        })
        data = json.loads(_extract_text(result))
        assert data["pacing_status"] == "behind"

    @pytest.mark.asyncio
    async def test_pacing_report_ahead(self, monkeypatch):
        """get_pacing_report should report 'ahead' when overpacing."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        cid = _seed_campaign(campaign_store)
        _seed_pacing_snapshot(
            pacing_store, cid,
            total_spend=65000.0,
            expected_spend=50000.0,
            pacing_pct=130.0,
            deviation_pct=30.0,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store", lambda: campaign_store
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store", lambda: pacing_store
        )
        result = await mcp.call_tool("get_pacing_report", {
            "campaign_id": cid,
        })
        data = json.loads(_extract_text(result))
        assert data["pacing_status"] == "ahead"
