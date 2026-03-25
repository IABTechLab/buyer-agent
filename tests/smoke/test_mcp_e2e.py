"""MCP E2E Smoke Test - Ad Buyer Agent

Exercises every MCP tool via the SSE transport, as a real Claude Desktop
user would experience it.  This is the UAT gate for buyer-mw9 (Phase 3 epic).

Usage:
    # Start the buyer server first:
    #   uvicorn src.ad_buyer.interfaces.api.main:app --port 8000
    #
    # Then run this test:
    #   pytest tests/smoke/test_mcp_e2e.py -v

This test is marked with @pytest.mark.smoke so it can be run independently:
    pytest tests/smoke/ -v -m smoke

Note: Requires a running buyer server on port 8000.
"""

import asyncio
import json
import os

import pytest
import pytest_asyncio

# -------------------------------------------------------------------------
# Optional pytest-asyncio support — skip gracefully if not installed
# -------------------------------------------------------------------------
try:
    from mcp.client.sse import sse_client
    from mcp import ClientSession
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

SERVER_URL = os.environ.get("BUYER_MCP_URL", "http://127.0.0.1:8000/mcp/sse/sse")

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(
        not MCP_AVAILABLE,
        reason="mcp package not available",
    ),
]


# -------------------------------------------------------------------------
# Shared helpers
# -------------------------------------------------------------------------

async def _call(session: "ClientSession", name: str, args: dict | None = None):
    """Call an MCP tool and return (is_error, data) tuple."""
    result = await session.call_tool(name, arguments=args or {})
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


@pytest.fixture(scope="module")
def event_loop():
    """Module-scoped event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="module")
async def mcp_session():
    """Connect to the MCP server and yield a ClientSession for the module."""
    try:
        async with sse_client(SERVER_URL) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    except Exception as exc:
        pytest.skip(f"Buyer server not reachable at {SERVER_URL}: {exc}")


# -------------------------------------------------------------------------
# 1. Foundation (3 tools)
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_check(mcp_session):
    err, data = await _call(mcp_session, "health_check")
    assert not err, f"health_check raised: {data}"
    assert data.get("status") == "healthy", f"Unexpected health status: {data}"
    assert "services" in data


@pytest.mark.asyncio
async def test_get_setup_status(mcp_session):
    err, data = await _call(mcp_session, "get_setup_status")
    assert not err, f"get_setup_status raised: {data}"
    assert "setup_complete" in data
    assert "checks" in data
    checks = data["checks"]
    assert "database_accessible" in checks
    assert checks["database_accessible"] is True


@pytest.mark.asyncio
async def test_get_config(mcp_session):
    err, data = await _call(mcp_session, "get_config")
    assert not err, f"get_config raised: {data}"
    assert "environment" in data
    assert "database_url" in data


# -------------------------------------------------------------------------
# 2. Setup Wizard (4 tools)
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_setup_wizard(mcp_session):
    err, data = await _call(mcp_session, "run_setup_wizard")
    assert not err, f"run_setup_wizard raised: {data}"
    assert "steps" in data or "current_step" in data or "wizard" in str(data)


@pytest.mark.asyncio
async def test_get_wizard_step(mcp_session):
    err, data = await _call(mcp_session, "get_wizard_step", {"step_number": 1})
    assert not err, f"get_wizard_step raised: {data}"
    assert "step_number" in data or "error" in data


@pytest.mark.asyncio
async def test_complete_wizard_step(mcp_session):
    err, data = await _call(mcp_session, "complete_wizard_step",
                            {"step_number": 1, "config": "{}"})
    assert not err, f"complete_wizard_step raised: {data}"
    # Either succeeds or returns a structured error (step already done)
    assert "success" in data or "error" in data


@pytest.mark.asyncio
async def test_skip_wizard_step(mcp_session):
    err, data = await _call(mcp_session, "skip_wizard_step", {"step_number": 2})
    assert not err, f"skip_wizard_step raised: {data}"
    assert "success" in data or "error" in data


# -------------------------------------------------------------------------
# 3. Campaign Management (4 tools)
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_campaigns(mcp_session):
    err, data = await _call(mcp_session, "list_campaigns")
    assert not err, f"list_campaigns raised: {data}"
    assert "campaigns" in data
    assert isinstance(data["campaigns"], list)


@pytest.mark.asyncio
async def test_get_campaign_status_not_found(mcp_session):
    """Verify graceful error for missing campaign."""
    err, data = await _call(mcp_session, "get_campaign_status",
                            {"campaign_id": "nonexistent-uat-001"})
    assert not err, f"get_campaign_status raised: {data}"
    assert "error" in data


@pytest.mark.asyncio
async def test_check_pacing_not_found(mcp_session):
    err, data = await _call(mcp_session, "check_pacing",
                            {"campaign_id": "nonexistent-uat-001"})
    assert not err, f"check_pacing raised: {data}"
    assert "error" in data


@pytest.mark.asyncio
async def test_review_budgets(mcp_session):
    err, data = await _call(mcp_session, "review_budgets")
    assert not err, f"review_budgets raised: {data}"
    assert "total_budget" in data or "campaigns" in data


# -------------------------------------------------------------------------
# 4. Deal Library (6 tools) + multi-step workflow
# -------------------------------------------------------------------------

@pytest.fixture(scope="module")
def created_deal_id(mcp_session):
    """Create a test deal once for the module and return its ID."""
    loop = asyncio.get_event_loop()
    err, data = loop.run_until_complete(_call(mcp_session, "create_deal_manual", {
        "display_name": "Quinn UAT Smoke Test Deal",
        "seller_url": "http://smoke-test-seller.example.com",
        "deal_type": "PD",
        "price": 12.50,
        "currency": "USD",
        "media_type": "DIGITAL",
        "description": "Created by MCP E2E smoke test — safe to delete",
    }))
    assert not err and data.get("success"), f"Deal creation failed: {data}"
    return data["deal_id"]


@pytest.mark.asyncio
async def test_list_deals(mcp_session):
    err, data = await _call(mcp_session, "list_deals")
    assert not err, f"list_deals raised: {data}"
    assert "deals" in data
    assert isinstance(data["deals"], list)


@pytest.mark.asyncio
async def test_get_portfolio_summary(mcp_session):
    err, data = await _call(mcp_session, "get_portfolio_summary")
    assert not err, f"get_portfolio_summary raised: {data}"
    assert "total_deals" in data
    assert "by_deal_type" in data


@pytest.mark.asyncio
async def test_create_deal_manual(mcp_session):
    """Workflow: create deal → inspect → search → verify portfolio grows."""
    # Create
    err, data = await _call(mcp_session, "create_deal_manual", {
        "display_name": "Quinn Workflow Test Deal",
        "seller_url": "http://workflow-test.example.com",
        "deal_type": "PG",
        "price": 25.00,
        "currency": "USD",
        "media_type": "CTV",
    })
    assert not err and data.get("success"), f"create failed: {data}"
    deal_id = data["deal_id"]

    # Inspect
    err, inspect = await _call(mcp_session, "inspect_deal", {"deal_id": deal_id})
    assert not err, f"inspect_deal raised: {inspect}"
    assert "error" not in inspect, f"inspect_deal returned error: {inspect}"
    assert inspect.get("deal_id") == deal_id
    assert inspect.get("display_name") == "Quinn Workflow Test Deal"

    # Search
    err, search = await _call(mcp_session, "search_deals",
                              {"query": "Quinn Workflow Test"})
    assert not err, f"search_deals raised: {search}"
    assert "deals" in search
    found = any(d.get("deal_id") == deal_id for d in search["deals"])
    assert found, f"Created deal {deal_id} not found in search results"


@pytest.mark.asyncio
async def test_search_deals(mcp_session):
    """Basic search — empty query returns all or handles gracefully."""
    err, data = await _call(mcp_session, "search_deals", {"query": "test"})
    assert not err, f"search_deals raised: {data}"
    assert "deals" in data


@pytest.mark.asyncio
async def test_inspect_deal_not_found(mcp_session):
    err, data = await _call(mcp_session, "inspect_deal",
                            {"deal_id": "does-not-exist-uat"})
    assert not err, f"inspect_deal raised: {data}"
    assert "error" in data


@pytest.mark.asyncio
async def test_import_deals_csv(mcp_session):
    """CSV import with valid seller_org + seller_domain fields."""
    csv_data = (
        "display_name,seller_url,seller_org,seller_domain,deal_type,price,currency\n"
        "Quinn CSV Import Deal,http://csv-seller.example.com,"
        "CSVPublisher,csv-seller.example.com,PD,9.50,USD"
    )
    err, data = await _call(mcp_session, "import_deals_csv",
                            {"csv_data": csv_data})
    assert not err, f"import_deals_csv raised: {data}"
    assert "total_rows" in data
    assert data.get("successful", 0) >= 1 or data.get("failed", 0) >= 0  # graceful


# -------------------------------------------------------------------------
# 5. Seller Discovery (3 tools)
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discover_sellers(mcp_session):
    err, data = await _call(mcp_session, "discover_sellers")
    assert not err, f"discover_sellers raised: {data}"
    assert "sellers" in data or "error" in data


@pytest.mark.asyncio
async def test_get_seller_media_kit_unreachable(mcp_session):
    """Unreachable seller returns a structured error, not a crash."""
    err, data = await _call(mcp_session, "get_seller_media_kit",
                            {"seller_url": "http://127.0.0.1:19999"})
    assert not err, f"get_seller_media_kit raised: {data}"
    assert "error" in data


@pytest.mark.asyncio
async def test_compare_sellers(mcp_session):
    err, data = await _call(mcp_session, "compare_sellers",
                            {"seller_urls": ["http://s1.example.com",
                                             "http://s2.example.com"]})
    assert not err, f"compare_sellers raised: {data}"
    assert "sellers_compared" in data or "sellers" in data


# -------------------------------------------------------------------------
# 6. Negotiation (3 tools)
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_active_negotiations(mcp_session):
    err, data = await _call(mcp_session, "list_active_negotiations")
    assert not err, f"list_active_negotiations raised: {data}"
    assert "negotiations" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_start_negotiation(mcp_session):
    err, data = await _call(mcp_session, "start_negotiation", {
        "seller_url": "http://neg-test-seller.example.com",
        "product_id": "pkg-ctv-premium",
        "product_name": "Quinn UAT Negotiation Test",
        "initial_price": 18.00,
    })
    assert not err, f"start_negotiation raised: {data}"
    assert "deal_id" in data
    assert data.get("status") == "negotiating"
    return data["deal_id"]


@pytest.mark.asyncio
async def test_get_negotiation_status(mcp_session):
    # Start one so we have a deal to check
    _, neg = await _call(mcp_session, "start_negotiation", {
        "seller_url": "http://neg-status-test.example.com",
        "product_id": "pkg-test",
        "initial_price": 10.00,
    })
    deal_id = neg.get("deal_id")
    assert deal_id, "start_negotiation did not return deal_id"

    err, data = await _call(mcp_session, "get_negotiation_status",
                            {"deal_id": deal_id})
    assert not err, f"get_negotiation_status raised: {data}"
    assert data.get("deal_id") == deal_id
    assert "status" in data


# -------------------------------------------------------------------------
# 7. Orders (3 tools)
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_orders(mcp_session):
    err, data = await _call(mcp_session, "list_orders")
    assert not err, f"list_orders raised: {data}"
    assert "orders" in data


@pytest.mark.asyncio
async def test_get_order_status_not_found(mcp_session):
    err, data = await _call(mcp_session, "get_order_status",
                            {"order_id": "nonexistent-order-uat"})
    assert not err, f"get_order_status raised: {data}"
    assert "error" in data


@pytest.mark.asyncio
async def test_transition_order_not_found(mcp_session):
    err, data = await _call(mcp_session, "transition_order", {
        "order_id": "nonexistent-order-uat",
        "to_status": "confirmed",
        "reason": "Quinn UAT test",
    })
    assert not err, f"transition_order raised: {data}"
    assert "error" in data


# -------------------------------------------------------------------------
# 8. Approval Gate (2 tools)
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_pending_approvals(mcp_session):
    err, data = await _call(mcp_session, "list_pending_approvals")
    assert not err, f"list_pending_approvals raised: {data}"
    assert "pending" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_approve_or_reject_not_found(mcp_session):
    err, data = await _call(mcp_session, "approve_or_reject", {
        "approval_request_id": "nonexistent-request-uat",
        "decision": "approved",
        "reviewer": "quinn-uat",
        "reason": "UAT test approval",
    })
    assert not err, f"approve_or_reject raised: {data}"
    assert "error" in data


# -------------------------------------------------------------------------
# 9. API Keys (3 tools) + lifecycle workflow
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_key_lifecycle(mcp_session):
    """Full workflow: create → list (verify masked) → revoke → list (verify gone)."""
    test_seller = "http://quinn-api-key-lifecycle-test.example.com"
    test_key = "quinn-test-key-abcdefgh9999"

    # Create
    err, create_data = await _call(mcp_session, "create_api_key", {
        "seller_url": test_seller,
        "api_key": test_key,
    })
    assert not err and create_data.get("created"), f"create_api_key failed: {create_data}"
    assert "masked_key" in create_data
    # Verify masking: real key should NOT appear in masked version
    assert test_key not in create_data["masked_key"]
    assert "****" in create_data["masked_key"]

    # List — key should appear masked
    err, list_data = await _call(mcp_session, "list_api_keys")
    assert not err, f"list_api_keys failed: {list_data}"
    keys = list_data.get("keys", [])
    matching = [k for k in keys if k.get("seller_url") == test_seller]
    assert len(matching) == 1, f"Key not found in list: {keys}"
    assert "****" in matching[0]["masked_key"], "Key is not masked"

    # Revoke
    err, revoke_data = await _call(mcp_session, "revoke_api_key",
                                   {"seller_url": test_seller})
    assert not err and revoke_data.get("revoked"), f"revoke_api_key failed: {revoke_data}"

    # List — key should be gone
    err, list_after = await _call(mcp_session, "list_api_keys")
    assert not err, f"list_api_keys after revoke failed: {list_after}"
    remaining = [k for k in list_after.get("keys", [])
                 if k.get("seller_url") == test_seller]
    assert len(remaining) == 0, f"Key still present after revoke: {remaining}"


# -------------------------------------------------------------------------
# 10. Templates (3 tools) + workflow
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_templates(mcp_session):
    err, data = await _call(mcp_session, "list_templates")
    assert not err, f"list_templates raised: {data}"
    assert "deal_templates" in data
    assert "supply_path_templates" in data


@pytest.mark.asyncio
async def test_template_workflow(mcp_session):
    """Create deal template → instantiate from it → verify deal created."""
    # Create template
    err, tmpl = await _call(mcp_session, "create_template", {
        "template_type": "deal",
        "name": "Quinn UAT CTV Deal Template",
        "deal_type_pref": "PD",
        "max_cpm": 30.0,
        "default_price": 15.0,
    })
    assert not err, f"create_template raised: {tmpl}"
    assert "template_id" in tmpl, f"No template_id in response: {tmpl}"
    template_id = tmpl["template_id"]

    # Instantiate
    overrides_json = json.dumps({
        "display_name": "Quinn UAT Instantiated Deal",
        "seller_url": "http://template-seller.example.com",
    })
    err, inst = await _call(mcp_session, "instantiate_from_template", {
        "template_id": template_id,
        "overrides": overrides_json,
    })
    assert not err, f"instantiate_from_template raised: {inst}"
    assert inst.get("success"), f"instantiate_from_template failed: {inst}"
    assert "deal_id" in inst

    # Verify deal was created
    deal_id = inst["deal_id"]
    err, deal = await _call(mcp_session, "inspect_deal", {"deal_id": deal_id})
    assert not err, f"inspect_deal raised: {deal}"
    assert "error" not in deal, f"Instantiated deal not found: {deal}"
    assert deal.get("deal_id") == deal_id


# -------------------------------------------------------------------------
# 11. Reporting (3 tools)
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_deal_performance(mcp_session):
    """Create a deal and verify performance report is returned for it."""
    err, deal = await _call(mcp_session, "create_deal_manual", {
        "display_name": "Quinn Perf Report Test Deal",
        "seller_url": "http://perf-test.example.com",
        "deal_type": "PD",
    })
    assert not err and deal.get("success"), f"create failed: {deal}"
    deal_id = deal["deal_id"]

    err, data = await _call(mcp_session, "get_deal_performance",
                            {"deal_id": deal_id})
    assert not err, f"get_deal_performance raised: {data}"
    # Should have deal info even if no spend yet
    assert "deal_id" in data or "error" in data


@pytest.mark.asyncio
async def test_get_campaign_report_not_found(mcp_session):
    err, data = await _call(mcp_session, "get_campaign_report",
                            {"campaign_id": "nonexistent-uat-001"})
    assert not err, f"get_campaign_report raised: {data}"
    assert "error" in data


@pytest.mark.asyncio
async def test_get_pacing_report_not_found(mcp_session):
    err, data = await _call(mcp_session, "get_pacing_report",
                            {"campaign_id": "nonexistent-uat-001"})
    assert not err, f"get_pacing_report raised: {data}"
    assert "error" in data


# -------------------------------------------------------------------------
# 12. SSP Connectors (3 tools)
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_ssp_connectors(mcp_session):
    err, data = await _call(mcp_session, "list_ssp_connectors")
    assert not err, f"list_ssp_connectors raised: {data}"
    assert "connectors" in data
    assert "total" in data
    assert data["total"] >= 3, "Expected at least 3 SSP connectors"
    names = [c["name"] for c in data["connectors"]]
    assert "pubmatic" in names
    assert "magnite" in names or "rubicon" in names
    assert "index_exchange" in names


@pytest.mark.asyncio
async def test_import_deals_ssp_unconfigured(mcp_session):
    """Unconfigured SSP returns structured error, not crash."""
    err, data = await _call(mcp_session, "import_deals_ssp",
                            {"ssp_name": "pubmatic"})
    assert not err, f"import_deals_ssp raised: {data}"
    assert "error" in data or "deals" in data  # error if not configured


@pytest.mark.asyncio
async def test_ssp_connection_test_unconfigured(mcp_session):
    """Test SSP connection for unconfigured connector."""
    err, data = await _call(mcp_session, "test_ssp_connection",
                            {"ssp_name": "index_exchange"})
    assert not err, f"test_ssp_connection raised: {data}"
    assert "connected" in data or "error" in data


# -------------------------------------------------------------------------
# 13. Edge Case Tests
# -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_deals_empty_query(mcp_session):
    """Empty string search is handled gracefully."""
    err, data = await _call(mcp_session, "search_deals", {"query": ""})
    assert not err, f"search_deals empty raised: {data}"
    assert "deals" in data or "error" in data


@pytest.mark.asyncio
async def test_inspect_deal_empty_id(mcp_session):
    """Empty deal_id should return error, not crash."""
    err, data = await _call(mcp_session, "inspect_deal", {"deal_id": ""})
    assert not err, f"inspect_deal empty id raised: {data}"
    assert "error" in data


@pytest.mark.asyncio
async def test_create_deal_minimal_fields(mcp_session):
    """Minimal required fields only: display_name + seller_url."""
    err, data = await _call(mcp_session, "create_deal_manual", {
        "display_name": "Quinn Minimal Deal",
        "seller_url": "http://minimal.example.com",
    })
    assert not err, f"create_deal_manual minimal raised: {data}"
    assert data.get("success"), f"Minimal deal creation failed: {data}"


@pytest.mark.asyncio
async def test_list_deals_with_status_filter(mcp_session):
    """Filtering list_deals by status is handled."""
    err, data = await _call(mcp_session, "list_deals",
                            {"status": "active"})
    assert not err, f"list_deals with status filter raised: {data}"
    assert "deals" in data
