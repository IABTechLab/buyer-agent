"""MCP Streamable HTTP Smoke Tests — /mcp endpoint.

Tests the buyer agent's primary MCP transport (Streamable HTTP at /mcp).
Separate from test_mcp_e2e.py which covers the legacy SSE transport.

Usage:
    # Start the buyer server first:
    #   uvicorn ad_buyer.interfaces.api.main:app --port 8000
    #
    # Then run:
    #   pytest tests/smoke/test_mcp_streamable.py -v

Requires a running buyer server on port 8000 (or set BUYER_MCP_HTTP_URL).

Note: no @pytest.mark.asyncio decorators needed — pyproject.toml sets
asyncio_mode = "auto" which handles all async test functions automatically.
Adding the decorator alongside AUTO mode causes double collection.
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager

import pytest

# ---------------------------------------------------------------------------
# Optional MCP SDK imports
# ---------------------------------------------------------------------------
try:
    from mcp.client.streamable_http import streamable_http_client
    from mcp import ClientSession
    MCP_HTTP_AVAILABLE = True
except ImportError:
    try:
        # Older SDK versions use the camelCase name
        from mcp.client.streamable_http import streamablehttp_client as streamable_http_client  # type: ignore[no-redef]
        from mcp import ClientSession
        MCP_HTTP_AVAILABLE = True
    except ImportError:
        MCP_HTTP_AVAILABLE = False

MCP_HTTP_URL = os.environ.get("BUYER_MCP_HTTP_URL", "http://127.0.0.1:8000/mcp")
TOOL_TIMEOUT = float(os.environ.get("MCP_TOOL_TIMEOUT", "15"))

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(not MCP_HTTP_AVAILABLE, reason="mcp streamable_http client not available"),
]


# ---------------------------------------------------------------------------
# Session helper — context manager, not a fixture, avoids AUTO-mode doubling
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _mcp_session():
    """Open a fresh Streamable HTTP MCP session for one test."""
    try:
        async with streamable_http_client(MCP_HTTP_URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    except Exception as exc:
        pytest.skip(f"Buyer /mcp not reachable at {MCP_HTTP_URL}: {exc}")


async def _call(session: "ClientSession", name: str, args: dict | None = None):
    """Call an MCP tool and return (is_error, data)."""
    try:
        result = await asyncio.wait_for(
            session.call_tool(name, arguments=args or {}),
            timeout=TOOL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        pytest.fail(f"Tool '{name}' timed out after {TOOL_TIMEOUT}s on /mcp")

    content = result.content
    if not content or not hasattr(content[0], "text"):
        return False, {}
    text = content[0].text
    if text.startswith("Error executing tool"):
        return True, {"raw_error": text}
    try:
        return False, json.loads(text)
    except json.JSONDecodeError:
        return False, {"raw_text": text}


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

async def test_streamable_http_connection():
    """/mcp must accept a session and initialize successfully."""
    async with _mcp_session() as session:
        assert session is not None


async def test_streamable_http_tool_list():
    """/mcp must advertise all foundation tools."""
    async with _mcp_session() as session:
        result = await asyncio.wait_for(session.list_tools(), timeout=TOOL_TIMEOUT)
        tool_names = {t.name for t in result.tools}
        for required in ("health_check", "get_setup_status", "get_config"):
            assert required in tool_names, (
                f"Required tool '{required}' missing — got: {sorted(tool_names)}"
            )


# ---------------------------------------------------------------------------
# Foundation tools
# ---------------------------------------------------------------------------

async def test_health_check():
    async with _mcp_session() as session:
        err, data = await _call(session, "health_check")
    assert not err, f"health_check error: {data}"
    assert data.get("status") == "healthy"
    assert "services" in data


async def test_get_setup_status():
    async with _mcp_session() as session:
        err, data = await _call(session, "get_setup_status")
    assert not err, f"get_setup_status error: {data}"
    assert "setup_complete" in data
    assert data["checks"]["database_accessible"] is True


async def test_get_config():
    async with _mcp_session() as session:
        err, data = await _call(session, "get_config")
    assert not err, f"get_config error: {data}"
    assert "environment" in data
    assert "database_url" in data
    assert "anthropic_api_key" not in str(data), "API key must not be exposed"


# ---------------------------------------------------------------------------
# Deal library
# ---------------------------------------------------------------------------

async def test_list_deals():
    async with _mcp_session() as session:
        err, data = await _call(session, "list_deals")
    assert not err, f"list_deals error: {data}"
    assert "deals" in data
    assert isinstance(data["deals"], list)


async def test_create_and_inspect_deal():
    """Create a deal via /mcp, then inspect it — verifies round-trip."""
    async with _mcp_session() as session:
        err, data = await _call(session, "create_deal_manual", {
            "display_name": "Streamable HTTP Test Deal",
            "seller_url": "http://mcp-http-test.example.com",
            "deal_type": "PD",
            "price": 18.0,
            "currency": "USD",
        })
        assert not err and data.get("success"), f"create_deal_manual failed: {data}"
        deal_id = data["deal_id"]

        err, inspect = await _call(session, "inspect_deal", {"deal_id": deal_id})
    assert not err, f"inspect_deal error: {inspect}"
    assert inspect.get("deal_id") == deal_id
    assert inspect.get("display_name") == "Streamable HTTP Test Deal"


async def test_get_portfolio_summary():
    async with _mcp_session() as session:
        err, data = await _call(session, "get_portfolio_summary")
    assert not err, f"get_portfolio_summary error: {data}"
    assert "total_deals" in data


# ---------------------------------------------------------------------------
# Seller discovery
# ---------------------------------------------------------------------------

async def test_discover_sellers():
    async with _mcp_session() as session:
        err, data = await _call(session, "discover_sellers")
    assert not err, f"discover_sellers error: {data}"
    assert "sellers" in data or "error" in data


async def test_get_seller_media_kit_unreachable():
    """Unreachable seller must return structured error, not crash."""
    async with _mcp_session() as session:
        err, data = await _call(session, "get_seller_media_kit",
                                {"seller_url": "http://127.0.0.1:19999"})
    assert not err, f"get_seller_media_kit raised: {data}"
    assert "error" in data


# ---------------------------------------------------------------------------
# Campaigns & Orders
# ---------------------------------------------------------------------------

async def test_list_campaigns():
    async with _mcp_session() as session:
        err, data = await _call(session, "list_campaigns")
    assert not err, f"list_campaigns error: {data}"
    assert "campaigns" in data


async def test_list_orders():
    async with _mcp_session() as session:
        err, data = await _call(session, "list_orders")
    assert not err, f"list_orders error: {data}"
    assert "orders" in data


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------

async def test_api_key_lifecycle():
    """Full create → list → revoke lifecycle over /mcp."""
    seller = "http://mcp-http-key-test.example.com"
    raw_key = "mcp-http-test-key-xyz999"

    async with _mcp_session() as session:
        err, created = await _call(session, "create_api_key",
                                   {"seller_url": seller, "api_key": raw_key})
        assert not err and created.get("created"), f"create_api_key failed: {created}"
        assert raw_key not in created["masked_key"], "Raw key must be masked"

        err, listed = await _call(session, "list_api_keys")
        assert not err
        assert any(k["seller_url"] == seller for k in listed.get("keys", []))

        err, revoked = await _call(session, "revoke_api_key", {"seller_url": seller})
        assert not err and revoked.get("revoked"), f"revoke_api_key failed: {revoked}"
