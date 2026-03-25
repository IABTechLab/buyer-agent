# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for Campaign Management MCP tools.

Tests four campaign management MCP tools: list_campaigns,
get_campaign_status, check_pacing, review_budgets.

bead: buyer-3w3
"""

import json
from datetime import UTC, datetime

import pytest

from ad_buyer.interfaces.mcp_server import mcp
from ad_buyer.storage.campaign_store import CampaignStore
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


class TestCampaignToolRegistration:
    """Verify all 4 campaign MCP tools are registered."""

    @pytest.mark.asyncio
    async def test_list_campaigns_registered(self):
        """list_campaigns should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "list_campaigns" in names

    @pytest.mark.asyncio
    async def test_get_campaign_status_registered(self):
        """get_campaign_status should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "get_campaign_status" in names

    @pytest.mark.asyncio
    async def test_check_pacing_registered(self):
        """check_pacing should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "check_pacing" in names

    @pytest.mark.asyncio
    async def test_review_budgets_registered(self):
        """review_budgets should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "review_budgets" in names

    @pytest.mark.asyncio
    async def test_total_tool_count(self):
        """Should have at least the 4 campaign management tools registered."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        expected = ["list_campaigns", "get_campaign_status", "check_pacing", "review_budgets"]
        for tool_name in expected:
            assert tool_name in names, f"Expected tool {tool_name!r} to be registered"


# ---------------------------------------------------------------------------
# Test list_campaigns
# ---------------------------------------------------------------------------


class TestListCampaigns:
    """Tests for the list_campaigns MCP tool."""

    @pytest.mark.asyncio
    async def test_no_campaigns_returns_empty(self, monkeypatch):
        """list_campaigns should return empty list when no campaigns exist."""
        store = _make_campaign_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store", lambda: store
        )

        result = await mcp.call_tool("list_campaigns", {})
        data = json.loads(_extract_text(result))
        assert data["campaigns"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_lists_all_campaigns(self, monkeypatch):
        """list_campaigns should return all campaigns when no filter."""
        store = _make_campaign_store()
        _seed_campaign(store, campaign_name="Campaign A", status="ACTIVE")
        _seed_campaign(store, campaign_name="Campaign B", status="DRAFT")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store", lambda: store
        )

        result = await mcp.call_tool("list_campaigns", {})
        data = json.loads(_extract_text(result))
        assert data["total"] == 2
        names = {c["campaign_name"] for c in data["campaigns"]}
        assert names == {"Campaign A", "Campaign B"}

    @pytest.mark.asyncio
    async def test_filter_by_status(self, monkeypatch):
        """list_campaigns should filter by status when provided."""
        store = _make_campaign_store()
        _seed_campaign(store, campaign_name="Active One", status="ACTIVE")
        _seed_campaign(store, campaign_name="Draft One", status="DRAFT")
        _seed_campaign(store, campaign_name="Active Two", status="ACTIVE")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store", lambda: store
        )

        result = await mcp.call_tool("list_campaigns", {"status": "ACTIVE"})
        data = json.loads(_extract_text(result))
        assert data["total"] == 2
        for c in data["campaigns"]:
            assert c["status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_filter_returns_empty_for_unmatched(self, monkeypatch):
        """Filtering by a status with no matches should return empty."""
        store = _make_campaign_store()
        _seed_campaign(store, status="ACTIVE")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store", lambda: store
        )

        result = await mcp.call_tool("list_campaigns", {"status": "COMPLETED"})
        data = json.loads(_extract_text(result))
        assert data["total"] == 0
        assert data["campaigns"] == []

    @pytest.mark.asyncio
    async def test_campaign_fields_included(self, monkeypatch):
        """Each campaign in the list should include key fields."""
        store = _make_campaign_store()
        _seed_campaign(store)
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store", lambda: store
        )

        result = await mcp.call_tool("list_campaigns", {})
        data = json.loads(_extract_text(result))
        campaign = data["campaigns"][0]

        required_fields = [
            "campaign_id", "campaign_name", "status",
            "total_budget", "flight_start", "flight_end",
        ]
        for field in required_fields:
            assert field in campaign, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_returns_valid_json(self, monkeypatch):
        """list_campaigns should return valid JSON."""
        store = _make_campaign_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store", lambda: store
        )

        result = await mcp.call_tool("list_campaigns", {})
        data = json.loads(_extract_text(result))
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# Test get_campaign_status
# ---------------------------------------------------------------------------


class TestGetCampaignStatus:
    """Tests for the get_campaign_status MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_campaign_details(self, monkeypatch):
        """get_campaign_status should return detailed campaign info."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        cid = _seed_campaign(campaign_store, campaign_name="My Campaign")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            lambda: campaign_store,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store",
            lambda: pacing_store,
        )

        result = await mcp.call_tool("get_campaign_status", {"campaign_id": cid})
        data = json.loads(_extract_text(result))

        assert data["campaign_id"] == cid
        assert data["campaign_name"] == "My Campaign"
        assert data["status"] == "ACTIVE"
        assert "total_budget" in data
        assert "flight_start" in data
        assert "flight_end" in data

    @pytest.mark.asyncio
    async def test_includes_pacing_data(self, monkeypatch):
        """get_campaign_status should include pacing metrics when available."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        cid = _seed_campaign(campaign_store)
        _seed_pacing_snapshot(
            pacing_store, cid,
            total_spend=30000.0, expected_spend=50000.0,
            pacing_pct=60.0, deviation_pct=-40.0,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            lambda: campaign_store,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store",
            lambda: pacing_store,
        )

        result = await mcp.call_tool("get_campaign_status", {"campaign_id": cid})
        data = json.loads(_extract_text(result))

        assert "pacing" in data
        assert data["pacing"]["total_spend"] == 30000.0
        assert data["pacing"]["pacing_pct"] == 60.0

    @pytest.mark.asyncio
    async def test_no_pacing_data(self, monkeypatch):
        """get_campaign_status should handle campaigns with no pacing data."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        cid = _seed_campaign(campaign_store)
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            lambda: campaign_store,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store",
            lambda: pacing_store,
        )

        result = await mcp.call_tool("get_campaign_status", {"campaign_id": cid})
        data = json.loads(_extract_text(result))

        assert data["pacing"] is None or data["pacing"]["total_spend"] == 0.0

    @pytest.mark.asyncio
    async def test_campaign_not_found(self, monkeypatch):
        """get_campaign_status should return error for unknown campaign."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            lambda: campaign_store,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store",
            lambda: pacing_store,
        )

        result = await mcp.call_tool(
            "get_campaign_status", {"campaign_id": "nonexistent-id"}
        )
        data = json.loads(_extract_text(result))
        assert "error" in data

    @pytest.mark.asyncio
    async def test_returns_valid_json(self, monkeypatch):
        """get_campaign_status should return valid JSON."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        cid = _seed_campaign(campaign_store)
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            lambda: campaign_store,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store",
            lambda: pacing_store,
        )

        result = await mcp.call_tool("get_campaign_status", {"campaign_id": cid})
        data = json.loads(_extract_text(result))
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# Test check_pacing
# ---------------------------------------------------------------------------


class TestCheckPacing:
    """Tests for the check_pacing MCP tool."""

    @pytest.mark.asyncio
    async def test_on_track_pacing(self, monkeypatch):
        """check_pacing should report 'on_track' when pacing is near 100%."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        cid = _seed_campaign(campaign_store)
        _seed_pacing_snapshot(
            pacing_store, cid,
            total_spend=50000.0, expected_spend=50000.0,
            pacing_pct=100.0, deviation_pct=0.0,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            lambda: campaign_store,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store",
            lambda: pacing_store,
        )

        result = await mcp.call_tool("check_pacing", {"campaign_id": cid})
        data = json.loads(_extract_text(result))

        assert data["pacing_status"] == "on_track"
        assert data["pacing_pct"] == 100.0

    @pytest.mark.asyncio
    async def test_behind_pacing(self, monkeypatch):
        """check_pacing should report 'behind' when underpacing."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        cid = _seed_campaign(campaign_store)
        _seed_pacing_snapshot(
            pacing_store, cid,
            total_spend=30000.0, expected_spend=50000.0,
            pacing_pct=60.0, deviation_pct=-40.0,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            lambda: campaign_store,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store",
            lambda: pacing_store,
        )

        result = await mcp.call_tool("check_pacing", {"campaign_id": cid})
        data = json.loads(_extract_text(result))

        assert data["pacing_status"] == "behind"
        assert data["deviation_pct"] == -40.0

    @pytest.mark.asyncio
    async def test_ahead_pacing(self, monkeypatch):
        """check_pacing should report 'ahead' when overpacing."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        cid = _seed_campaign(campaign_store)
        _seed_pacing_snapshot(
            pacing_store, cid,
            total_spend=70000.0, expected_spend=50000.0,
            pacing_pct=140.0, deviation_pct=40.0,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            lambda: campaign_store,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store",
            lambda: pacing_store,
        )

        result = await mcp.call_tool("check_pacing", {"campaign_id": cid})
        data = json.loads(_extract_text(result))

        assert data["pacing_status"] == "ahead"
        assert data["deviation_pct"] == 40.0

    @pytest.mark.asyncio
    async def test_no_pacing_data(self, monkeypatch):
        """check_pacing should handle no pacing data gracefully."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        cid = _seed_campaign(campaign_store)
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            lambda: campaign_store,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store",
            lambda: pacing_store,
        )

        result = await mcp.call_tool("check_pacing", {"campaign_id": cid})
        data = json.loads(_extract_text(result))

        assert data["pacing_status"] == "no_data"

    @pytest.mark.asyncio
    async def test_campaign_not_found(self, monkeypatch):
        """check_pacing should return error for unknown campaign."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            lambda: campaign_store,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store",
            lambda: pacing_store,
        )

        result = await mcp.call_tool(
            "check_pacing", {"campaign_id": "nonexistent-id"}
        )
        data = json.loads(_extract_text(result))
        assert "error" in data

    @pytest.mark.asyncio
    async def test_includes_budget_info(self, monkeypatch):
        """check_pacing should include budget and spend information."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        cid = _seed_campaign(campaign_store, total_budget=200000.0)
        _seed_pacing_snapshot(
            pacing_store, cid,
            total_budget=200000.0, total_spend=90000.0,
            expected_spend=100000.0, pacing_pct=90.0, deviation_pct=-10.0,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            lambda: campaign_store,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store",
            lambda: pacing_store,
        )

        result = await mcp.call_tool("check_pacing", {"campaign_id": cid})
        data = json.loads(_extract_text(result))

        assert data["total_budget"] == 200000.0
        assert data["total_spend"] == 90000.0
        assert data["expected_spend"] == 100000.0

    @pytest.mark.asyncio
    async def test_includes_channel_pacing(self, monkeypatch):
        """check_pacing should include per-channel pacing when available."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        cid = _seed_campaign(campaign_store)
        _seed_pacing_snapshot(
            pacing_store, cid,
            channel_snapshots=[
                ChannelSnapshot(
                    channel="CTV", allocated_budget=60000.0,
                    spend=30000.0, pacing_pct=100.0,
                ),
                ChannelSnapshot(
                    channel="DISPLAY", allocated_budget=40000.0,
                    spend=15000.0, pacing_pct=75.0,
                ),
            ],
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            lambda: campaign_store,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store",
            lambda: pacing_store,
        )

        result = await mcp.call_tool("check_pacing", {"campaign_id": cid})
        data = json.loads(_extract_text(result))

        assert "channel_pacing" in data
        assert len(data["channel_pacing"]) == 2


# ---------------------------------------------------------------------------
# Test review_budgets
# ---------------------------------------------------------------------------


class TestReviewBudgets:
    """Tests for the review_budgets MCP tool."""

    @pytest.mark.asyncio
    async def test_no_campaigns(self, monkeypatch):
        """review_budgets should handle no campaigns gracefully."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            lambda: campaign_store,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store",
            lambda: pacing_store,
        )

        result = await mcp.call_tool("review_budgets", {})
        data = json.loads(_extract_text(result))

        assert data["total_budget"] == 0.0
        assert data["total_spend"] == 0.0
        assert data["campaigns"] == []

    @pytest.mark.asyncio
    async def test_aggregates_budgets(self, monkeypatch):
        """review_budgets should aggregate budget across campaigns."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        cid1 = _seed_campaign(
            campaign_store, campaign_name="Campaign A",
            total_budget=100000.0, status="ACTIVE",
        )
        cid2 = _seed_campaign(
            campaign_store, campaign_name="Campaign B",
            total_budget=50000.0, status="ACTIVE",
        )
        _seed_pacing_snapshot(
            pacing_store, cid1,
            total_budget=100000.0, total_spend=40000.0,
            expected_spend=50000.0, pacing_pct=80.0, deviation_pct=-20.0,
        )
        _seed_pacing_snapshot(
            pacing_store, cid2,
            total_budget=50000.0, total_spend=30000.0,
            expected_spend=25000.0, pacing_pct=120.0, deviation_pct=20.0,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            lambda: campaign_store,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store",
            lambda: pacing_store,
        )

        result = await mcp.call_tool("review_budgets", {})
        data = json.loads(_extract_text(result))

        assert data["total_budget"] == 150000.0
        assert data["total_spend"] == 70000.0
        assert len(data["campaigns"]) == 2

    @pytest.mark.asyncio
    async def test_per_campaign_budget_info(self, monkeypatch):
        """review_budgets should include per-campaign budget details."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        cid = _seed_campaign(
            campaign_store, campaign_name="My Campaign",
            total_budget=100000.0,
        )
        _seed_pacing_snapshot(
            pacing_store, cid,
            total_budget=100000.0, total_spend=45000.0,
            expected_spend=50000.0, pacing_pct=90.0, deviation_pct=-10.0,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            lambda: campaign_store,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store",
            lambda: pacing_store,
        )

        result = await mcp.call_tool("review_budgets", {})
        data = json.loads(_extract_text(result))

        campaign = data["campaigns"][0]
        assert campaign["campaign_name"] == "My Campaign"
        assert campaign["total_budget"] == 100000.0
        assert campaign["total_spend"] == 45000.0
        assert "delivery_pct" in campaign

    @pytest.mark.asyncio
    async def test_campaign_without_pacing_shows_zero_spend(self, monkeypatch):
        """review_budgets should show zero spend for campaigns without pacing."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        _seed_campaign(
            campaign_store, campaign_name="No Pacing",
            total_budget=50000.0,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            lambda: campaign_store,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store",
            lambda: pacing_store,
        )

        result = await mcp.call_tool("review_budgets", {})
        data = json.loads(_extract_text(result))

        campaign = data["campaigns"][0]
        assert campaign["total_spend"] == 0.0
        assert campaign["delivery_pct"] == 0.0

    @pytest.mark.asyncio
    async def test_returns_valid_json(self, monkeypatch):
        """review_budgets should return valid JSON."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            lambda: campaign_store,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store",
            lambda: pacing_store,
        )

        result = await mcp.call_tool("review_budgets", {})
        data = json.loads(_extract_text(result))
        assert isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_includes_timestamp(self, monkeypatch):
        """review_budgets should include a timestamp."""
        campaign_store = _make_campaign_store()
        pacing_store = _make_pacing_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            lambda: campaign_store,
        )
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_pacing_store",
            lambda: pacing_store,
        )

        result = await mcp.call_tool("review_budgets", {})
        data = json.loads(_extract_text(result))
        assert "timestamp" in data
