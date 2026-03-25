# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for Approval & API Key MCP tools.

Tests five MCP tools: list_pending_approvals, approve_or_reject,
list_api_keys, create_api_key, revoke_api_key.

bead: buyer-j7f
"""

import json
import os
import tempfile

import pytest

from ad_buyer.interfaces.mcp_server import mcp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_text(call_result) -> str:
    """Extract text content from a FastMCP call_tool result."""
    content_list = call_result[0]
    return content_list[0].text


def _make_campaign_store():
    """Create and connect a file-backed CampaignStore for testing."""
    from ad_buyer.storage.campaign_store import CampaignStore

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = CampaignStore(f"sqlite:///{path}")
    store.connect()
    return store


def _make_api_key_store():
    """Create an ApiKeyStore backed by a temp file."""
    from pathlib import Path

    from ad_buyer.auth.key_store import ApiKeyStore

    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    # Remove the file so ApiKeyStore starts empty
    os.unlink(path)
    return ApiKeyStore(store_path=Path(path))


def _reconnecting(store):
    """Return a lambda that reconnects the store before returning it."""
    def _get():
        if hasattr(store, "_conn") and store._conn is None:
            store.connect()
        return store
    return _get


def _ensure_campaign(store, campaign_id: str) -> None:
    """Create a minimal campaign record if it doesn't already exist.

    Required because approval_requests has a FK to campaigns(campaign_id).
    """
    existing = store.get_campaign(campaign_id)
    if existing is not None:
        return
    store.save_campaign(
        campaign_id=campaign_id,
        advertiser_id="adv-test",
        campaign_name=f"Test Campaign {campaign_id}",
        total_budget=10000.0,
        flight_start="2026-03-01",
        flight_end="2026-03-31",
    )


def _seed_approval_request(
    store,
    approval_request_id: str = "req-001",
    campaign_id: str = "camp-001",
    stage: str = "PLAN_REVIEW",
    status: str = "pending",
    requested_at: str = "2026-03-25T12:00:00+00:00",
    context: str = "{}",
) -> str:
    """Insert an approval request directly into the campaign store."""
    store.create_approval_requests_table()
    _ensure_campaign(store, campaign_id)
    store.save_approval_request(
        approval_request_id=approval_request_id,
        campaign_id=campaign_id,
        stage=stage,
        status=status,
        requested_at=requested_at,
        context=context,
    )
    return approval_request_id


# ---------------------------------------------------------------------------
# Test tool registration
# ---------------------------------------------------------------------------


class TestApprovalApiKeyToolRegistration:
    """Verify all 5 approval/API key MCP tools are registered."""

    @pytest.mark.asyncio
    async def test_list_pending_approvals_registered(self):
        """list_pending_approvals should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "list_pending_approvals" in names

    @pytest.mark.asyncio
    async def test_approve_or_reject_registered(self):
        """approve_or_reject should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "approve_or_reject" in names

    @pytest.mark.asyncio
    async def test_list_api_keys_registered(self):
        """list_api_keys should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "list_api_keys" in names

    @pytest.mark.asyncio
    async def test_create_api_key_registered(self):
        """create_api_key should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "create_api_key" in names

    @pytest.mark.asyncio
    async def test_revoke_api_key_registered(self):
        """revoke_api_key should be registered as an MCP tool."""
        tools = await mcp.list_tools()
        names = [t.name for t in tools]
        assert "revoke_api_key" in names


# ---------------------------------------------------------------------------
# Test list_pending_approvals
# ---------------------------------------------------------------------------


class TestListPendingApprovals:
    """Tests for the list_pending_approvals MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_none(self, monkeypatch):
        """list_pending_approvals should return empty list when no pending items."""
        store = _make_campaign_store()
        store.create_approval_requests_table()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            _reconnecting(store),
        )

        result = await mcp.call_tool("list_pending_approvals", {})
        data = json.loads(_extract_text(result))
        assert data["pending"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_returns_pending_approvals(self, monkeypatch):
        """list_pending_approvals should return items in pending status."""
        store = _make_campaign_store()
        _seed_approval_request(store, "req-001", "camp-001", "PLAN_REVIEW", "pending")
        _seed_approval_request(store, "req-002", "camp-002", "BOOKING", "pending")
        _seed_approval_request(store, "req-003", "camp-003", "PLAN_REVIEW", "approved")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            _reconnecting(store),
        )

        result = await mcp.call_tool("list_pending_approvals", {})
        data = json.loads(_extract_text(result))
        assert data["total"] == 2
        ids = {item["approval_request_id"] for item in data["pending"]}
        assert ids == {"req-001", "req-002"}

    @pytest.mark.asyncio
    async def test_filter_by_campaign_id(self, monkeypatch):
        """list_pending_approvals should filter by campaign_id when provided."""
        store = _make_campaign_store()
        _seed_approval_request(store, "req-001", "camp-001", "PLAN_REVIEW", "pending")
        _seed_approval_request(store, "req-002", "camp-002", "BOOKING", "pending")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            _reconnecting(store),
        )

        result = await mcp.call_tool(
            "list_pending_approvals", {"campaign_id": "camp-001"}
        )
        data = json.loads(_extract_text(result))
        assert data["total"] == 1
        assert data["pending"][0]["campaign_id"] == "camp-001"

    @pytest.mark.asyncio
    async def test_includes_timestamp(self, monkeypatch):
        """list_pending_approvals should include a timestamp."""
        store = _make_campaign_store()
        store.create_approval_requests_table()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            _reconnecting(store),
        )

        result = await mcp.call_tool("list_pending_approvals", {})
        data = json.loads(_extract_text(result))
        assert "timestamp" in data


# ---------------------------------------------------------------------------
# Test approve_or_reject
# ---------------------------------------------------------------------------


class TestApproveOrReject:
    """Tests for the approve_or_reject MCP tool."""

    @pytest.mark.asyncio
    async def test_approve_pending_request(self, monkeypatch):
        """approve_or_reject should approve a pending request."""
        store = _make_campaign_store()
        _seed_approval_request(store, "req-001", "camp-001", "PLAN_REVIEW", "pending")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            _reconnecting(store),
        )

        result = await mcp.call_tool("approve_or_reject", {
            "approval_request_id": "req-001",
            "decision": "approved",
            "reviewer": "test-user",
        })
        data = json.loads(_extract_text(result))
        assert data["approval_request_id"] == "req-001"
        assert data["new_status"] == "approved"
        assert data["reviewer"] == "test-user"

    @pytest.mark.asyncio
    async def test_reject_pending_request(self, monkeypatch):
        """approve_or_reject should reject a pending request."""
        store = _make_campaign_store()
        _seed_approval_request(store, "req-001", "camp-001", "PLAN_REVIEW", "pending")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            _reconnecting(store),
        )

        result = await mcp.call_tool("approve_or_reject", {
            "approval_request_id": "req-001",
            "decision": "rejected",
            "reviewer": "test-user",
            "reason": "Budget too high",
        })
        data = json.loads(_extract_text(result))
        assert data["new_status"] == "rejected"
        assert data["reason"] == "Budget too high"

    @pytest.mark.asyncio
    async def test_request_not_found(self, monkeypatch):
        """approve_or_reject should return error for unknown request."""
        store = _make_campaign_store()
        store.create_approval_requests_table()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            _reconnecting(store),
        )

        result = await mcp.call_tool("approve_or_reject", {
            "approval_request_id": "nonexistent",
            "decision": "approved",
            "reviewer": "test-user",
        })
        data = json.loads(_extract_text(result))
        assert "error" in data

    @pytest.mark.asyncio
    async def test_already_decided_returns_error(self, monkeypatch):
        """approve_or_reject should return error for already-decided request."""
        store = _make_campaign_store()
        _seed_approval_request(store, "req-001", "camp-001", "PLAN_REVIEW", "approved")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            _reconnecting(store),
        )

        result = await mcp.call_tool("approve_or_reject", {
            "approval_request_id": "req-001",
            "decision": "rejected",
            "reviewer": "test-user",
        })
        data = json.loads(_extract_text(result))
        assert "error" in data

    @pytest.mark.asyncio
    async def test_includes_timestamp(self, monkeypatch):
        """approve_or_reject should include a timestamp."""
        store = _make_campaign_store()
        _seed_approval_request(store, "req-001", "camp-001", "PLAN_REVIEW", "pending")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_campaign_store",
            _reconnecting(store),
        )

        result = await mcp.call_tool("approve_or_reject", {
            "approval_request_id": "req-001",
            "decision": "approved",
            "reviewer": "test-user",
        })
        data = json.loads(_extract_text(result))
        assert "timestamp" in data


# ---------------------------------------------------------------------------
# Test list_api_keys
# ---------------------------------------------------------------------------


class TestListApiKeys:
    """Tests for the list_api_keys MCP tool."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_none(self, monkeypatch):
        """list_api_keys should return empty list when no keys configured."""
        key_store = _make_api_key_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_api_key_store",
            lambda: key_store,
        )

        result = await mcp.call_tool("list_api_keys", {})
        data = json.loads(_extract_text(result))
        assert data["keys"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_returns_configured_keys(self, monkeypatch):
        """list_api_keys should return seller URLs with masked keys."""
        key_store = _make_api_key_store()
        key_store.add_key("http://seller-a.com", "secret-key-aaa-bbb")
        key_store.add_key("http://seller-b.com", "secret-key-ccc-ddd")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_api_key_store",
            lambda: key_store,
        )

        result = await mcp.call_tool("list_api_keys", {})
        data = json.loads(_extract_text(result))
        assert data["total"] == 2
        urls = {k["seller_url"] for k in data["keys"]}
        assert urls == {"http://seller-a.com", "http://seller-b.com"}

    @pytest.mark.asyncio
    async def test_keys_are_masked(self, monkeypatch):
        """list_api_keys should never expose the full API key value."""
        key_store = _make_api_key_store()
        key_store.add_key("http://seller-a.com", "secret-key-aaa-bbb")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_api_key_store",
            lambda: key_store,
        )

        result = await mcp.call_tool("list_api_keys", {})
        text = _extract_text(result)
        # The full key should NOT appear in the output
        assert "secret-key-aaa-bbb" not in text

    @pytest.mark.asyncio
    async def test_includes_timestamp(self, monkeypatch):
        """list_api_keys should include a timestamp."""
        key_store = _make_api_key_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_api_key_store",
            lambda: key_store,
        )

        result = await mcp.call_tool("list_api_keys", {})
        data = json.loads(_extract_text(result))
        assert "timestamp" in data


# ---------------------------------------------------------------------------
# Test create_api_key
# ---------------------------------------------------------------------------


class TestCreateApiKey:
    """Tests for the create_api_key MCP tool."""

    @pytest.mark.asyncio
    async def test_creates_key_for_seller(self, monkeypatch):
        """create_api_key should store a key for the given seller URL."""
        key_store = _make_api_key_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_api_key_store",
            lambda: key_store,
        )

        result = await mcp.call_tool("create_api_key", {
            "seller_url": "http://seller-a.com",
            "api_key": "new-secret-key-123",
        })
        data = json.loads(_extract_text(result))
        assert data["seller_url"] == "http://seller-a.com"
        assert data["created"] is True

        # Verify key was actually stored
        assert key_store.get_key("http://seller-a.com") == "new-secret-key-123"

    @pytest.mark.asyncio
    async def test_replaces_existing_key(self, monkeypatch):
        """create_api_key should replace an existing key for the same seller."""
        key_store = _make_api_key_store()
        key_store.add_key("http://seller-a.com", "old-key")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_api_key_store",
            lambda: key_store,
        )

        result = await mcp.call_tool("create_api_key", {
            "seller_url": "http://seller-a.com",
            "api_key": "new-key",
        })
        data = json.loads(_extract_text(result))
        assert data["created"] is True
        assert key_store.get_key("http://seller-a.com") == "new-key"

    @pytest.mark.asyncio
    async def test_does_not_expose_full_key(self, monkeypatch):
        """create_api_key response should not contain the full key value."""
        key_store = _make_api_key_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_api_key_store",
            lambda: key_store,
        )

        result = await mcp.call_tool("create_api_key", {
            "seller_url": "http://seller-a.com",
            "api_key": "supersecretvalue12345",
        })
        text = _extract_text(result)
        assert "supersecretvalue12345" not in text

    @pytest.mark.asyncio
    async def test_includes_timestamp(self, monkeypatch):
        """create_api_key should include a timestamp."""
        key_store = _make_api_key_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_api_key_store",
            lambda: key_store,
        )

        result = await mcp.call_tool("create_api_key", {
            "seller_url": "http://seller-a.com",
            "api_key": "test-key",
        })
        data = json.loads(_extract_text(result))
        assert "timestamp" in data


# ---------------------------------------------------------------------------
# Test revoke_api_key
# ---------------------------------------------------------------------------


class TestRevokeApiKey:
    """Tests for the revoke_api_key MCP tool."""

    @pytest.mark.asyncio
    async def test_revokes_existing_key(self, monkeypatch):
        """revoke_api_key should remove an existing key."""
        key_store = _make_api_key_store()
        key_store.add_key("http://seller-a.com", "the-key")
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_api_key_store",
            lambda: key_store,
        )

        result = await mcp.call_tool("revoke_api_key", {
            "seller_url": "http://seller-a.com",
        })
        data = json.loads(_extract_text(result))
        assert data["revoked"] is True
        assert data["seller_url"] == "http://seller-a.com"

        # Verify key was removed
        assert key_store.get_key("http://seller-a.com") is None

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_returns_false(self, monkeypatch):
        """revoke_api_key should return revoked=false for unknown seller."""
        key_store = _make_api_key_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_api_key_store",
            lambda: key_store,
        )

        result = await mcp.call_tool("revoke_api_key", {
            "seller_url": "http://nonexistent.com",
        })
        data = json.loads(_extract_text(result))
        assert data["revoked"] is False

    @pytest.mark.asyncio
    async def test_includes_timestamp(self, monkeypatch):
        """revoke_api_key should include a timestamp."""
        key_store = _make_api_key_store()
        monkeypatch.setattr(
            "ad_buyer.interfaces.mcp_server._get_api_key_store",
            lambda: key_store,
        )

        result = await mcp.call_tool("revoke_api_key", {
            "seller_url": "http://seller-a.com",
        })
        data = json.loads(_extract_text(result))
        assert "timestamp" in data
