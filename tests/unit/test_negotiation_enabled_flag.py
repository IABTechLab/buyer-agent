# Tests for seller's negotiation_enabled flag being respected by buyer
# bead: ar-9xi

"""Test that buyer respects the seller's negotiation_enabled flag on packages.

The buyer must check BOTH:
1. BuyerContext.can_negotiate() -- buyer tier allows negotiation
2. PackageDetail.negotiation_enabled -- seller allows negotiation on this package

When negotiation_enabled=False, the buyer should:
- Skip negotiation in request_deal and book at listed price
- Hide negotiation info in get_pricing output
- Reject negotiation attempts in NegotiationClient
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ad_buyer.models.buyer_identity import (
    BuyerContext,
    BuyerIdentity,
)
from ad_buyer.negotiation.client import NegotiationClient
from ad_buyer.negotiation.strategies.simple_threshold import SimpleThresholdStrategy
from ad_buyer.tools.dsp import GetPricingTool, RequestDealTool

# -- Fixtures ----------------------------------------------------------------


@pytest.fixture
def mock_client():
    """Create a mock UnifiedClient."""
    client = MagicMock()
    client.get_product = AsyncMock()
    return client


@pytest.fixture
def agency_context():
    """Agency-tier buyer that CAN negotiate (by tier)."""
    identity = BuyerIdentity(
        seat_id="ttd-seat-123",
        agency_id="omnicom-456",
        agency_name="OMD",
    )
    return BuyerContext(identity=identity, is_authenticated=True)


@pytest.fixture
def negotiable_product():
    """Product where the seller has enabled negotiation."""
    return {
        "id": "prod_negotiable",
        "name": "Premium CTV Package",
        "basePrice": 25.00,
        "publisherId": "pub_1",
        "rateType": "CPM",
        "negotiation_enabled": True,
    }


@pytest.fixture
def non_negotiable_product():
    """Product where the seller has DISABLED negotiation."""
    return {
        "id": "prod_nonneg",
        "name": "Standard Display Package",
        "basePrice": 25.00,
        "publisherId": "pub_1",
        "rateType": "CPM",
        "negotiation_enabled": False,
    }


# -- RequestDealTool tests ---------------------------------------------------


class TestRequestDealRespectsNegotiationEnabled:
    """RequestDealTool must check negotiation_enabled before negotiating."""

    @pytest.mark.asyncio
    async def test_non_negotiable_package_ignores_target_cpm(
        self, mock_client, agency_context, non_negotiable_product
    ):
        """When negotiation_enabled=False, target_cpm should be ignored
        and the deal should be booked at the tier-discounted listed price."""
        mock_client.get_product.return_value = MagicMock(
            success=True,
            data=non_negotiable_product,
        )

        tool = RequestDealTool(
            client=mock_client,
            buyer_context=agency_context,
        )

        result = await tool._arun(
            product_id="prod_nonneg",
            deal_type="PD",
            target_cpm=15.00,  # Agency tries to negotiate down
        )

        # Deal should be created (no error)
        assert "DEAL-" in result
        # Agency tier gets 10% discount: $25 * 0.90 = $22.50
        # NOT the target_cpm of $15.00
        assert "$22.50" in result

    @pytest.mark.asyncio
    async def test_negotiable_package_allows_target_cpm(
        self, mock_client, agency_context, negotiable_product
    ):
        """When negotiation_enabled=True, target_cpm should still work."""
        mock_client.get_product.return_value = MagicMock(
            success=True,
            data=negotiable_product,
        )

        tool = RequestDealTool(
            client=mock_client,
            buyer_context=agency_context,
        )

        result = await tool._arun(
            product_id="prod_negotiable",
            deal_type="PD",
            target_cpm=21.00,  # Within 10% of floor
        )

        # Deal should be created with negotiated price
        assert "DEAL-" in result
        assert "$21.00" in result

    @pytest.mark.asyncio
    async def test_non_negotiable_product_without_target_cpm_works(
        self, mock_client, agency_context, non_negotiable_product
    ):
        """Non-negotiable package should still create deals (just no negotiation)."""
        mock_client.get_product.return_value = MagicMock(
            success=True,
            data=non_negotiable_product,
        )

        tool = RequestDealTool(
            client=mock_client,
            buyer_context=agency_context,
        )

        result = await tool._arun(
            product_id="prod_nonneg",
            deal_type="PD",
        )

        # Should succeed with tier-discounted price
        assert "DEAL-" in result
        assert "$22.50" in result


# -- GetPricingTool tests ----------------------------------------------------


class TestGetPricingRespectsNegotiationEnabled:
    """GetPricingTool should only show negotiation info when package allows it."""

    @pytest.mark.asyncio
    async def test_non_negotiable_hides_negotiation_section(
        self, mock_client, agency_context, non_negotiable_product
    ):
        """When negotiation_enabled=False, don't show negotiation availability."""
        mock_client.get_product.return_value = MagicMock(
            success=True,
            data=non_negotiable_product,
        )

        tool = GetPricingTool(
            client=mock_client,
            buyer_context=agency_context,
        )

        result = await tool._arun(product_id="prod_nonneg")

        # Should NOT show "Price negotiation is available"
        assert "negotiation is available" not in result.lower()

    @pytest.mark.asyncio
    async def test_negotiable_shows_negotiation_section(
        self, mock_client, agency_context, negotiable_product
    ):
        """When negotiation_enabled=True AND buyer can negotiate, show it."""
        mock_client.get_product.return_value = MagicMock(
            success=True,
            data=negotiable_product,
        )

        tool = GetPricingTool(
            client=mock_client,
            buyer_context=agency_context,
        )

        result = await tool._arun(product_id="prod_negotiable")

        # Should show negotiation section
        assert "negotiation" in result.lower()
        assert "available" in result.lower()


# -- NegotiationClient tests -------------------------------------------------


class TestNegotiationClientRespectsNegotiationEnabled:
    """NegotiationClient should reject negotiation on non-negotiable packages."""

    @pytest.mark.asyncio
    async def test_auto_negotiate_rejects_non_negotiable(self):
        """auto_negotiate should reject when negotiation_enabled=False."""
        client = NegotiationClient()
        strategy = SimpleThresholdStrategy(
            target_cpm=20.0,
            max_cpm=30.0,
            concession_step=2.0,
            max_rounds=5,
        )

        result = await client.auto_negotiate(
            seller_url="http://localhost:8000",
            proposal_id="prop-001",
            strategy=strategy,
            negotiation_enabled=False,
        )

        # Should return a result indicating negotiation was rejected
        from ad_buyer.negotiation.models import NegotiationOutcome

        assert result.outcome == NegotiationOutcome.DECLINED
        assert result.rounds_count == 0

    @pytest.mark.asyncio
    async def test_start_negotiation_rejects_non_negotiable(self):
        """start_negotiation should raise when negotiation_enabled=False."""
        client = NegotiationClient()
        strategy = SimpleThresholdStrategy(
            target_cpm=20.0,
            max_cpm=30.0,
            concession_step=2.0,
            max_rounds=5,
        )

        with pytest.raises(ValueError, match="[Nn]egotiation.*not.*enabled|not.*negotiable"):
            await client.start_negotiation(
                seller_url="http://localhost:8000",
                proposal_id="prop-001",
                initial_price=20.0,
                strategy=strategy,
                negotiation_enabled=False,
            )

    @pytest.mark.asyncio
    async def test_auto_negotiate_works_when_enabled(self):
        """auto_negotiate should work normally when negotiation_enabled=True."""
        strategy = SimpleThresholdStrategy(
            target_cpm=20.0,
            max_cpm=30.0,
            concession_step=2.0,
            max_rounds=5,
        )

        start_response = MagicMock()
        start_response.status_code = 200
        start_response.json.return_value = {
            "negotiation_id": "neg-abc123",
            "proposal_id": "prop-001",
            "status": "active",
            "current_price": 28.0,
            "round_number": 1,
            "action": "counter",
            "seller_price": 28.0,
            "buyer_price": 20.0,
        }
        start_response.raise_for_status = MagicMock()

        accept_response = MagicMock()
        accept_response.status_code = 200
        accept_response.json.return_value = {
            "status": "accepted",
            "deal_price": 28.0,
            "proposal_id": "prop-001",
        }
        accept_response.raise_for_status = MagicMock()

        client = NegotiationClient()

        with patch("httpx.AsyncClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.post.side_effect = [start_response, accept_response]
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client_instance

            from ad_buyer.negotiation.models import NegotiationOutcome

            result = await client.auto_negotiate(
                seller_url="http://localhost:8000",
                proposal_id="prop-001",
                strategy=strategy,
                negotiation_enabled=True,
            )

            assert result.outcome == NegotiationOutcome.ACCEPTED
