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
            "internalId": "",
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
            "internalId": "",
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
