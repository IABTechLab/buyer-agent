from unittest.mock import AsyncMock, MagicMock

import pytest

from ad_buyer.clients.unified_client import Protocol, UnifiedClient, UnifiedResult
from ad_buyer.models.buyer_identity import BuyerContext, BuyerIdentity
from ad_buyer.tools.buyer_deals import DiscoverInventoryTool


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filters", [{"min_impressions": 1}, {"targeting": ["geo"]}, {"publisher": "x"}]
)
async def test_client_reports_products_that_could_not_be_checked(filters):
    client = UnifiedClient(base_url="http://localhost:8000")
    client.call_tool = AsyncMock(
        return_value=UnifiedResult(data=[{"product_id": "p", "name": "CTV"}])
    )
    result = await client.discover_inventory(**filters)
    assert result.data == []
    assert result.filter_summary.applied
    assert result.filter_summary.excluded_count == 1
    assert result.filter_summary.unchecked_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", list(Protocol))
@pytest.mark.parametrize(
    "data", ["Ask the seller about inventory", {"results": [{"id": "p"}]}, ["text"]]
)
async def test_client_preserves_unsupported_answers_and_marks_filters_unapplied(protocol, data):
    client = UnifiedClient(base_url="http://localhost:8000")
    raw = object()
    client.call_tool = AsyncMock(return_value=UnifiedResult(data=data, protocol=protocol, raw=raw))
    result = await client.discover_inventory(max_cpm=1)
    assert result.data is data
    assert result.raw is raw
    assert result.protocol is protocol
    assert not result.filter_summary.applied
    assert "could not be applied" in result.filter_summary.note
    assert result.filter_summary.excluded_count == 0


@pytest.mark.asyncio
async def test_mobile_alias_and_price_types_have_distinct_diagnostics():
    client = UnifiedClient(base_url="http://localhost:8000")
    valid = {
        "product_id": "mobile",
        "inventory_type": "mobile_app",
        "basePrice": None,
        "base_cpm": 5,
    }
    client.call_tool = AsyncMock(
        return_value=UnifiedResult(
            data=[
                valid,
                {"id": "zero", "inventory_type": "mobile_app", "base_cpm": 0},
                {"id": "bool", "inventory_type": "mobile_app", "base_cpm": True},
                {"id": "costly", "inventory_type": "mobile_app", "base_cpm": 50},
            ]
        )
    )
    result = await client.discover_inventory(channel="mobile", max_cpm=10)
    assert result.data == [valid]
    assert result.filter_summary.excluded_count == 3
    assert result.filter_summary.unchecked_count == 2


@pytest.mark.asyncio
async def test_tool_reports_unchecked_products():
    client = MagicMock()
    client.list_products = AsyncMock(return_value=UnifiedResult(data=[{"id": "p", "name": "CTV"}]))
    tool = DiscoverInventoryTool(
        client=client, buyer_context=BuyerContext(identity=BuyerIdentity())
    )
    result = await tool._arun(min_impressions=1)
    assert "excluded 1 product(s)" in result
    assert "1 could not be checked" in result


@pytest.mark.asyncio
async def test_tool_does_not_present_text_as_an_empty_catalog():
    client = MagicMock()
    client.list_products = AsyncMock(
        return_value=UnifiedResult(data="Ask the seller about inventory")
    )
    tool = DiscoverInventoryTool(
        client=client, buyer_context=BuyerContext(identity=BuyerIdentity())
    )
    result = await tool._arun(max_cpm=1)
    assert "could not be applied" in result
    assert "No inventory found" not in result
