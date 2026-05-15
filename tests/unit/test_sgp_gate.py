# Author: SafeGuard Privacy
# Donated to IAB Tech Lab

"""Tests for the SafeGuard Privacy deal-request gate in RequestDealTool."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ad_buyer.clients.sgp_client import SGPClientError
from ad_buyer.models.buyer_identity import BuyerContext, BuyerIdentity
from ad_buyer.models.sgp import ApprovalRecord
from ad_buyer.tools.buyer_deals import RequestDealTool


@pytest.fixture
def agency_context() -> BuyerContext:
    identity = BuyerIdentity(
        seat_id="ttd-seat-123",
        agency_id="omnicom-456",
        agency_name="OMD",
    )
    return BuyerContext(identity=identity, is_authenticated=True)


@pytest.fixture
def mock_client() -> MagicMock:
    """UnifiedClient mock that returns a product with a seller_url."""
    client = MagicMock()
    client.get_product = AsyncMock(
        return_value=MagicMock(
            success=True,
            data={
                "id": "prod_1",
                "name": "Premium CTV",
                "basePrice": 20.00,
                "seller_url": "http://seller.example.com:8001",
            },
        )
    )
    return client


def _approved(domain: str) -> ApprovalRecord:
    return ApprovalRecord.model_validate(
        {
            "vendorId": 1,
            "vendorCompanyId": 10,
            "companyName": "Example Seller",
            "domain": domain,
            "iabBuyerAgentApproval": True,
            "iabBuyerAgentApprovedAt": "2026-03-01T00:00:00Z",
        }
    )


def _denied(domain: str) -> ApprovalRecord:
    return ApprovalRecord.model_validate(
        {
            "vendorId": 2,
            "vendorCompanyId": 20,
            "companyName": "Shady Seller",
            "domain": domain,
            "iabBuyerAgentApproval": False,
            "iabBuyerAgentApprovedAt": None,
        }
    )


# ---------------------------------------------------------------------------
# Gate off
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_sgp_client_bypasses_gate(mock_client, agency_context):
    """When no SGP client is wired in, the tool operates as before."""
    tool = RequestDealTool(
        client=mock_client,
        buyer_context=agency_context,
        sgp_client=None,
    )
    result = await tool._arun(product_id="prod_1", impressions=100)
    assert "DEAL CREATED SUCCESSFULLY" in result


@pytest.mark.asyncio
async def test_enforce_false_bypasses_gate(mock_client, agency_context):
    """When enforcement is off, the gate does not block."""
    sgp = MagicMock()
    sgp.normalize_domain = MagicMock(return_value="seller.example.com")
    sgp.check_approvals = AsyncMock(
        return_value={"seller.example.com": _denied("seller.example.com")}
    )
    tool = RequestDealTool(
        client=mock_client,
        buyer_context=agency_context,
        sgp_client=sgp,
        sgp_enforce=False,
    )
    result = await tool._arun(product_id="prod_1", impressions=100)
    assert "DEAL CREATED SUCCESSFULLY" in result
    sgp.check_approvals.assert_not_called()


# ---------------------------------------------------------------------------
# Approved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approved_vendor_allows_deal(mock_client, agency_context):
    sgp = MagicMock()
    sgp.normalize_domain = MagicMock(return_value="seller.example.com")
    sgp.check_approvals = AsyncMock(
        return_value={"seller.example.com": _approved("seller.example.com")}
    )
    tool = RequestDealTool(
        client=mock_client,
        buyer_context=agency_context,
        sgp_client=sgp,
        sgp_enforce=True,
    )
    result = await tool._arun(product_id="prod_1", impressions=100)
    assert "DEAL CREATED SUCCESSFULLY" in result
    assert "SGP: ✓" in result
    assert "approved" in result.lower()


# ---------------------------------------------------------------------------
# Denied
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_denied_vendor_blocks_deal(mock_client, agency_context):
    sgp = MagicMock()
    sgp.normalize_domain = MagicMock(return_value="seller.example.com")
    sgp.check_approvals = AsyncMock(
        return_value={"seller.example.com": _denied("seller.example.com")}
    )
    tool = RequestDealTool(
        client=mock_client,
        buyer_context=agency_context,
        sgp_client=sgp,
        sgp_enforce=True,
    )
    result = await tool._arun(product_id="prod_1", impressions=100)
    assert "Deal blocked" in result
    assert "IAB buyer-agent approval" in result
    assert "DEAL CREATED SUCCESSFULLY" not in result


# ---------------------------------------------------------------------------
# Unknown vendor policies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_vendor_blocks_by_default(mock_client, agency_context):
    sgp = MagicMock()
    sgp.normalize_domain = MagicMock(return_value="seller.example.com")
    sgp.check_approvals = AsyncMock(return_value={"seller.example.com": None})
    tool = RequestDealTool(
        client=mock_client,
        buyer_context=agency_context,
        sgp_client=sgp,
        sgp_enforce=True,
        sgp_unknown_policy="block",
    )
    result = await tool._arun(product_id="prod_1", impressions=100)
    assert "Deal blocked" in result
    assert "not in your SafeGuard Privacy" in result
    assert "DEAL CREATED SUCCESSFULLY" not in result


@pytest.mark.asyncio
async def test_unknown_vendor_warn_allows_with_banner(mock_client, agency_context):
    sgp = MagicMock()
    sgp.normalize_domain = MagicMock(return_value="seller.example.com")
    sgp.check_approvals = AsyncMock(return_value={"seller.example.com": None})
    tool = RequestDealTool(
        client=mock_client,
        buyer_context=agency_context,
        sgp_client=sgp,
        sgp_enforce=True,
        sgp_unknown_policy="warn",
    )
    result = await tool._arun(product_id="prod_1", impressions=100)
    assert "SGP WARNING" in result
    assert "DEAL CREATED SUCCESSFULLY" in result


@pytest.mark.asyncio
async def test_unknown_vendor_allow_proceeds_silently(mock_client, agency_context):
    sgp = MagicMock()
    sgp.normalize_domain = MagicMock(return_value="seller.example.com")
    sgp.check_approvals = AsyncMock(return_value={"seller.example.com": None})
    tool = RequestDealTool(
        client=mock_client,
        buyer_context=agency_context,
        sgp_client=sgp,
        sgp_enforce=True,
        sgp_unknown_policy="allow",
    )
    result = await tool._arun(product_id="prod_1", impressions=100)
    assert "DEAL CREATED SUCCESSFULLY" in result


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transport_error_fails_closed_when_enforcing(mock_client, agency_context):
    """When SGP is unreachable and enforcement is on, deal must not be issued."""
    sgp = MagicMock()
    sgp.normalize_domain = MagicMock(return_value="seller.example.com")
    sgp.check_approvals = AsyncMock(side_effect=SGPClientError("upstream 503"))
    tool = RequestDealTool(
        client=mock_client,
        buyer_context=agency_context,
        sgp_client=sgp,
        sgp_enforce=True,
    )
    result = await tool._arun(product_id="prod_1", impressions=100)
    assert "Deal blocked" in result
    assert "SafeGuard Privacy lookup failed" in result
    assert "DEAL CREATED SUCCESSFULLY" not in result


@pytest.mark.asyncio
async def test_product_without_domain_blocks_when_enforcing(agency_context):
    """A product missing any seller domain field cannot be evaluated, so block."""
    mock_client = MagicMock()
    mock_client.get_product = AsyncMock(
        return_value=MagicMock(
            success=True,
            data={"id": "prod_1", "name": "Test", "basePrice": 20.00},
        )
    )
    sgp = MagicMock()
    sgp.normalize_domain = MagicMock(return_value="")
    sgp.check_approvals = AsyncMock()
    tool = RequestDealTool(
        client=mock_client,
        buyer_context=agency_context,
        sgp_client=sgp,
        sgp_enforce=True,
    )
    result = await tool._arun(product_id="prod_1", impressions=100)
    assert "Deal blocked" in result
    assert "seller domain" in result
    sgp.check_approvals.assert_not_called()


def test_invalid_unknown_policy_rejected(mock_client, agency_context):
    with pytest.raises(ValueError, match="sgp_unknown_policy"):
        RequestDealTool(
            client=mock_client,
            buyer_context=agency_context,
            sgp_unknown_policy="maybe",
        )


# ---------------------------------------------------------------------------
# Flow-level wiring of SGPVendorApprovalTool
# ---------------------------------------------------------------------------


def test_flow_wires_vendor_approval_tool_when_sgp_configured(agency_context):
    """BuyerDealFlow exposes the vendor approval tool to the deal agent."""
    from ad_buyer.clients.sgp_client import SGPClient
    from ad_buyer.flows.buyer_deal_flow import BuyerDealFlow
    from ad_buyer.tools.research import SGPVendorApprovalTool

    sgp = SGPClient(api_key="k", base_url="https://sgp.test")
    flow = BuyerDealFlow(
        client=MagicMock(),
        buyer_context=agency_context,
        sgp_client=sgp,
    )
    assert isinstance(flow._vendor_approval_tool, SGPVendorApprovalTool)


def test_flow_omits_vendor_approval_tool_without_sgp(agency_context, monkeypatch):
    """Without an SGP client (and no SGP_API_KEY env), the tool is not built."""
    from ad_buyer.config.settings import settings
    from ad_buyer.flows.buyer_deal_flow import BuyerDealFlow

    monkeypatch.setattr(settings, "sgp_api_key", "")
    flow = BuyerDealFlow(
        client=MagicMock(),
        buyer_context=agency_context,
        sgp_client=None,
    )
    assert flow._vendor_approval_tool is None


# ---------------------------------------------------------------------------
# DiscoverInventoryTool enforcement (filters before the agent sees products)
# ---------------------------------------------------------------------------


def _product(product_id: str, domain: str, price: float = 20.0) -> dict:
    return {
        "id": product_id,
        "name": f"Product {product_id}",
        "publisherId": "pub-1",
        "channel": "ctv",
        "basePrice": price,
        "availableImpressions": 1_000_000,
        "seller_url": f"http://{domain}",
    }


@pytest.fixture
def discovery_client() -> MagicMock:
    """UnifiedClient mock returning a mixed list of seller domains."""
    client = MagicMock()
    products = [
        _product("p1", "approved.example.com"),
        _product("p2", "denied.example.com"),
        _product("p3", "unknown.example.com"),
    ]
    client.search_products = AsyncMock(
        return_value=MagicMock(success=True, data=products)
    )
    client.list_products = AsyncMock(
        return_value=MagicMock(success=True, data=products)
    )
    return client


def _strip_scheme(d: str) -> str:
    """Tiny stand-in for SGPClient.normalize_domain for test mocks."""
    return d.replace("http://", "").replace("https://", "").split(":")[0]


def _discovery_sgp_mock() -> MagicMock:
    """SGP mock with approved/denied/unknown for the three discovery_client domains."""
    sgp = MagicMock()
    sgp.normalize_domain = MagicMock(side_effect=_strip_scheme)
    sgp.check_approvals = AsyncMock(
        return_value={
            "approved.example.com": _approved("approved.example.com"),
            "denied.example.com": _denied("denied.example.com"),
            "unknown.example.com": None,
        }
    )
    return sgp


@pytest.mark.asyncio
async def test_discovery_enforce_filters_not_approved(
    discovery_client, agency_context
):
    """When enforcing, NOT APPROVED rows are dropped before formatting."""
    from ad_buyer.tools.buyer_deals import DiscoverInventoryTool

    sgp = _discovery_sgp_mock()
    tool = DiscoverInventoryTool(
        client=discovery_client,
        buyer_context=agency_context,
        sgp_client=sgp,
        sgp_enforce=True,
        sgp_unknown_policy="block",
    )
    result = await tool._arun(query="ctv inventory")
    assert "approved.example.com" in result
    assert "denied.example.com" not in result
    assert "unknown.example.com" not in result
    assert "filtered" in result.lower()


@pytest.mark.asyncio
async def test_discovery_enforce_warn_keeps_unknowns(
    discovery_client, agency_context
):
    """warn policy keeps unknowns in the result and emits a warning line."""
    from ad_buyer.tools.buyer_deals import DiscoverInventoryTool

    sgp = _discovery_sgp_mock()
    tool = DiscoverInventoryTool(
        client=discovery_client,
        buyer_context=agency_context,
        sgp_client=sgp,
        sgp_enforce=True,
        sgp_unknown_policy="warn",
    )
    result = await tool._arun(query="ctv inventory")
    assert "approved.example.com" in result
    assert "denied.example.com" not in result
    assert "unknown.example.com" in result
    assert "SGP WARNING" in result


@pytest.mark.asyncio
async def test_discovery_enforce_allow_keeps_unknowns_silently(
    discovery_client, agency_context
):
    """allow policy keeps unknowns and suppresses the per-row annotation."""
    from ad_buyer.tools.buyer_deals import DiscoverInventoryTool

    sgp = _discovery_sgp_mock()
    tool = DiscoverInventoryTool(
        client=discovery_client,
        buyer_context=agency_context,
        sgp_client=sgp,
        sgp_enforce=True,
        sgp_unknown_policy="allow",
    )
    result = await tool._arun(query="ctv inventory")
    # p3 is the unknown vendor — kept silently under "allow" policy
    assert "Product p3" in result
    assert "SGP WARNING" not in result
    # NOT APPROVED (p2) is still filtered regardless of unknown policy
    assert "Product p2" not in result


@pytest.mark.asyncio
async def test_discovery_no_enforce_annotates_only(discovery_client, agency_context):
    """Without enforcement, all products pass through with annotations."""
    from ad_buyer.tools.buyer_deals import DiscoverInventoryTool

    sgp = _discovery_sgp_mock()
    tool = DiscoverInventoryTool(
        client=discovery_client,
        buyer_context=agency_context,
        sgp_client=sgp,
        sgp_enforce=False,
    )
    result = await tool._arun(query="ctv inventory")
    assert "approved.example.com" in result
    assert "denied.example.com" in result  # not filtered
    assert "unknown.example.com" in result
    assert "NOT APPROVED" in result
    assert "filtered" not in result.lower()


@pytest.mark.asyncio
async def test_discovery_fails_closed_when_sgp_unreachable_and_enforcing(
    discovery_client, agency_context
):
    """SGP transport error propagates so the flow can mark FAILED."""
    from ad_buyer.tools.buyer_deals import DiscoverInventoryTool

    sgp = MagicMock()
    sgp.normalize_domain = MagicMock(side_effect=lambda d: d)
    sgp.check_approvals = AsyncMock(side_effect=SGPClientError("upstream 503"))
    tool = DiscoverInventoryTool(
        client=discovery_client,
        buyer_context=agency_context,
        sgp_client=sgp,
        sgp_enforce=True,
    )
    with pytest.raises(SGPClientError, match="Inventory discovery halted"):
        await tool._arun(query="ctv inventory")


@pytest.mark.asyncio
async def test_discovery_no_enforce_swallows_sgp_error(
    discovery_client, agency_context, caplog
):
    """Without enforcement, transport error returns unannotated results."""
    from ad_buyer.tools.buyer_deals import DiscoverInventoryTool

    sgp = MagicMock()
    sgp.normalize_domain = MagicMock(side_effect=lambda d: d)
    sgp.check_approvals = AsyncMock(side_effect=SGPClientError("upstream 503"))
    tool = DiscoverInventoryTool(
        client=discovery_client,
        buyer_context=agency_context,
        sgp_client=sgp,
        sgp_enforce=False,
    )
    result = await tool._arun(query="ctv inventory")
    assert "Product p1" in result
    assert "Product p2" in result  # not filtered (annotate-only mode)
    assert "Product p3" in result
    assert "SGP Approval" not in result  # no annotations
    assert "Total products found: 3" in result


@pytest.mark.asyncio
async def test_discovery_no_sgp_client_pass_through(
    discovery_client, agency_context
):
    """Without an SGP client, discovery behaves as before — no annotations, no filter."""
    from ad_buyer.tools.buyer_deals import DiscoverInventoryTool

    tool = DiscoverInventoryTool(
        client=discovery_client,
        buyer_context=agency_context,
        sgp_client=None,
        sgp_enforce=True,  # no-op without a client
    )
    result = await tool._arun(query="ctv inventory")
    assert "Product p1" in result
    assert "Product p2" in result
    assert "Product p3" in result
    assert "SGP Approval" not in result
    assert "Total products found: 3" in result
