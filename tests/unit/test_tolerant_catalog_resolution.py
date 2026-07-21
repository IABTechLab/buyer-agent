# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tolerant per-product catalog parsing in the resolution path.

Wave-B rig proof 2026-07-21 (docs/reports/WAVE_B_RIG_PROOF_2026-07-21.md):
every rig seller's 13-product catalog contains two names longer than the
buyer OpenDirect model's 38-char ``name`` cap. Cross-seller resolution's Stage 1.5
resolution fetched the FULL catalog through the strict model, so ONE
invalid product failed the entire fetch -> ``catalog_error`` -> seller
skipped -> with PRODUCT_RESOLUTION_ENABLED default-on the fleet booked
NOTHING (S1 regression, S2 negotiation blocked pre-quote).

Contract under test:

- ``OpenDirectClient.list_products_tolerant`` parses the catalog
  PER-PRODUCT: invalid items are skipped and collected as reject records
  (product_id / raw name / validation reason), never failing the whole
  catalog. The strict ``list_products`` is unchanged.
- ``MultiSellerOrchestrator`` resolution operates on the valid subset and
  SURFACES the rejects: warning log + ``invalid_products`` count and
  details on the ``product.resolution`` event payload.
- A seller whose ENTIRE catalog is invalid still produces a clear
  ``catalog_error`` outcome carrying the reasons (not a silent skip).
- The cross-seller invariant (never quote a foreign product ID) is
  untouched -- see test_cross_seller_product_resolution.py.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from ad_buyer.booking.quote_normalizer import QuoteNormalizer
from ad_buyer.clients.opendirect_client import OpenDirectClient
from ad_buyer.events.models import EventType
from ad_buyer.models.deals import (
    AvailabilityInfo,
    PricingInfo,
    ProductInfo,
    QuoteResponse,
    TermsInfo,
)
from ad_buyer.models.opendirect import Product, RateType
from ad_buyer.orchestration.multi_seller import DealParams, MultiSellerOrchestrator
from ad_buyer.registry.models import AgentCapability, AgentCard, TrustLevel

SELLER_URL = "http://ctv-seller.example.com"

# The two live rig catalog names that exceed the OpenDirect 38-char cap
# (seller catalog_service.py:181; 44 and 46 chars respectively).
OVERSIZED_NAME_1 = "Programmatic Linear Reach — A25-54 Primetime"
OVERSIZED_NAME_2 = "Digital Out-of-Home — Times Square Spectacular"


def _wire_product(product_id: str, name: str, **overrides) -> dict:
    """Minimal shared-contract Product record as served on the wire."""
    record = {
        "product_id": product_id,
        "seller_organization_id": "org-seller",
        "name": name,
        "pricing_type": "fixed",
        "pricing_model": "cpm",
        "delivery_type": "Guaranteed",
        "ad_formats": ["display"],
    }
    record.update(overrides)
    return record


def _envelope(products: list[dict]) -> dict:
    """Shared ProductListResponse envelope."""
    return {
        "products": products,
        "total_count": len(products),
        "limit": 500,
        "offset": 0,
    }


def _client_serving(payload: dict) -> OpenDirectClient:
    """OpenDirectClient whose GET /products returns ``payload``."""
    client = OpenDirectClient(base_url="http://seller.example.com")
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    client._request = AsyncMock(return_value=response)  # type: ignore[method-assign]
    return client


# ---------------------------------------------------------------------------
# Client layer: OpenDirectClient.list_products_tolerant
# ---------------------------------------------------------------------------


class TestListProductsTolerant:
    async def test_one_invalid_of_n_yields_valid_subset_plus_reject(self):
        """One oversized name must not poison the other products."""
        client = _client_serving(
            _envelope(
                [
                    _wire_product("prod-1", "Premium Display"),
                    _wire_product("prod-2", OVERSIZED_NAME_1),
                    _wire_product("prod-3", "CTV Prime"),
                ]
            )
        )
        products, rejects = await client.list_products_tolerant(top=500)

        assert [p.id for p in products] == ["prod-1", "prod-3"]
        assert len(rejects) == 1
        reject = rejects[0]
        assert reject["product_id"] == "prod-2"
        assert reject["name"] == OVERSIZED_NAME_1
        assert "38" in reject["reason"]
        assert "name" in reject["reason"]

    async def test_wire_invalid_item_rejected_others_survive(self):
        """An item that breaks even the shared wire model is skipped too."""
        bad = _wire_product("prod-bad", "x")
        del bad["name"]  # required on the wire model
        client = _client_serving(_envelope([_wire_product("prod-1", "Premium Display"), bad]))
        products, rejects = await client.list_products_tolerant(top=500)

        assert [p.id for p in products] == ["prod-1"]
        assert len(rejects) == 1
        assert rejects[0]["product_id"] == "prod-bad"
        assert "name" in rejects[0]["reason"]

    async def test_all_valid_yields_no_rejects(self):
        client = _client_serving(
            _envelope(
                [
                    _wire_product("prod-1", "Premium Display"),
                    _wire_product("prod-2", "CTV Prime"),
                ]
            )
        )
        products, rejects = await client.list_products_tolerant(top=500)
        assert [p.id for p in products] == ["prod-1", "prod-2"]
        assert rejects == []

    async def test_all_invalid_yields_empty_valid_list_with_reasons(self):
        client = _client_serving(
            _envelope(
                [
                    _wire_product("prod-1", OVERSIZED_NAME_1),
                    _wire_product("prod-2", OVERSIZED_NAME_2),
                ]
            )
        )
        products, rejects = await client.list_products_tolerant(top=500)
        assert products == []
        assert {r["product_id"] for r in rejects} == {"prod-1", "prod-2"}
        assert all("38" in r["reason"] for r in rejects)

    async def test_filters_apply_to_valid_subset(self):
        client = _client_serving(
            _envelope(
                [
                    _wire_product("prod-1", "Premium Display", ad_formats=["display"]),
                    _wire_product("prod-2", "Video Pack", ad_formats=["video"]),
                    _wire_product("prod-3", OVERSIZED_NAME_1),
                ]
            )
        )
        products, rejects = await client.list_products_tolerant(top=500, adFormat="display")
        assert [p.id for p in products] == ["prod-1"]
        assert len(rejects) == 1

    async def test_strict_list_products_behavior_unchanged(self):
        """The strict path must STILL fail whole-catalog on an invalid item.

        Tolerance is scoped to the resolution path; the 38-char model
        constraint itself is deliberately NOT loosened here.
        """
        client = _client_serving(
            _envelope(
                [
                    _wire_product("prod-1", "Premium Display"),
                    _wire_product("prod-2", OVERSIZED_NAME_1),
                ]
            )
        )
        with pytest.raises(Exception, match="38"):
            await client.list_products(top=500)


# ---------------------------------------------------------------------------
# Orchestrator layer: resolution on the valid subset + surfaced rejects
# ---------------------------------------------------------------------------


def _seller_card(agent_id: str = "seller-b", url: str = SELLER_URL) -> AgentCard:
    return AgentCard(
        agent_id=agent_id,
        name=agent_id,
        url=url,
        protocols=["a2a", "deals-api-v1"],
        capabilities=[AgentCapability(name="display", description="display inventory")],
        trust_level=TrustLevel.VERIFIED,
    )


def _catalog_product(product_id: str, name: str, base_price: float = 10.0) -> Product:
    return Product(
        id=product_id,
        publisherid="pub-1",
        name=name,
        baseprice=base_price,
        ratetype=RateType.CPM,
        ext={"ad_formats": ["display"]},
    )


def _quote(quote_id: str, product_id: str) -> QuoteResponse:
    return QuoteResponse(
        quote_id=quote_id,
        status="available",
        product=ProductInfo(product_id=product_id, name="Premium Display"),
        pricing=PricingInfo(base_cpm=8.0, final_cpm=8.0),
        terms=TermsInfo(
            impressions=500_000,
            flight_start="2026-08-01",
            flight_end="2026-08-31",
            guaranteed=False,
        ),
        availability=AvailabilityInfo(inventory_available=True, estimated_fill_rate=0.85),
        seller_id="seller-b",
        buyer_tier="agency",
    )


def _deal_params(product_id: str = "prod-foreign") -> DealParams:
    return DealParams(
        product_id=product_id,
        deal_type="PD",
        impressions=500_000,
        flight_start="2026-08-01",
        flight_end="2026-08-31",
        target_cpm=10.0,
        media_type="digital",
        product_name="Premium Display",
        channel="display",
    )


def _tolerant_catalog_factory(result: tuple[list[Product], list[dict]]):
    """Catalog client factory whose client supports tolerant listing."""

    def factory(seller_url: str, **kwargs) -> AsyncMock:
        mock = AsyncMock()
        mock.list_products_tolerant = AsyncMock(return_value=result)
        mock.list_products = AsyncMock(return_value=list(result[0]))
        return mock

    return factory


def _make_orchestrator(deals_factory, event_bus, catalog_factory) -> MultiSellerOrchestrator:
    return MultiSellerOrchestrator(
        registry_client=AsyncMock(),
        deals_client_factory=deals_factory,
        event_bus=event_bus,
        quote_normalizer=QuoteNormalizer(),
        quote_timeout=5.0,
        catalog_client_factory=catalog_factory,
    )


def _resolution_events(event_bus) -> list[dict]:
    return [
        call.args[0].payload
        for call in event_bus.publish.call_args_list
        if call.args[0].event_type == EventType.PRODUCT_RESOLUTION
    ]


@pytest.fixture
def mock_event_bus():
    bus = AsyncMock()
    bus.publish = AsyncMock()
    return bus


@pytest.fixture
def mock_deals_client_factory():
    clients: dict[str, AsyncMock] = {}

    def factory(seller_url: str, **kwargs) -> AsyncMock:
        if seller_url not in clients:
            mock = AsyncMock()
            mock.request_quote = AsyncMock(return_value=None)
            clients[seller_url] = mock
        return clients[seller_url]

    factory._clients = clients
    return factory


REJECT_1 = {
    "product_id": "prod-linear",
    "name": OVERSIZED_NAME_1,
    "reason": "name: String should have at most 38 characters",
}
REJECT_2 = {
    "product_id": "prod-dooh",
    "name": OVERSIZED_NAME_2,
    "reason": "name: String should have at most 38 characters",
}


class TestResolutionOnValidSubset:
    async def test_invalid_products_skipped_resolution_succeeds(
        self, mock_deals_client_factory, mock_event_bus, caplog
    ):
        """1 invalid of N: the N-1 valid products still resolve a quote."""
        valid = [_catalog_product("prod-b", "Premium Display")]
        factory = _tolerant_catalog_factory((valid, [REJECT_1]))
        orchestrator = _make_orchestrator(mock_deals_client_factory, mock_event_bus, factory)
        client = mock_deals_client_factory(SELLER_URL)
        client.request_quote.return_value = _quote("q-b", "prod-b")

        with caplog.at_level(logging.WARNING):
            results = await orchestrator.request_quotes_parallel([_seller_card()], _deal_params())

        assert results[0].error is None
        assert results[0].quote is not None
        assert client.request_quote.call_args.args[0].product_id == "prod-b"

        # The reject is surfaced, not silently dropped: warning log ...
        assert any(
            "invalid" in rec.message.lower() and "prod-linear" in rec.message
            for rec in caplog.records
        )
        # ... AND on the product.resolution event payload.
        events = _resolution_events(mock_event_bus)
        assert len(events) == 1
        assert events[0]["outcome"] == "name_match"
        assert events[0]["invalid_products"] == 1
        assert events[0]["invalid_product_details"] == [REJECT_1]

    async def test_all_invalid_catalog_yields_catalog_error_with_reasons(
        self, mock_deals_client_factory, mock_event_bus
    ):
        """Entirely-invalid catalog: clear catalog_error carrying the reasons."""
        factory = _tolerant_catalog_factory(([], [REJECT_1, REJECT_2]))
        orchestrator = _make_orchestrator(mock_deals_client_factory, mock_event_bus, factory)
        client = mock_deals_client_factory(SELLER_URL)

        results = await orchestrator.request_quotes_parallel([_seller_card()], _deal_params())

        assert results[0].quote is None
        assert "product_not_resolvable" in results[0].error
        assert "prod-linear" in results[0].error
        assert "prod-dooh" in results[0].error
        client.request_quote.assert_not_awaited()

        events = _resolution_events(mock_event_bus)
        assert events[0]["outcome"] == "catalog_error"
        assert events[0]["invalid_products"] == 2
        assert events[0]["invalid_product_details"] == [REJECT_1, REJECT_2]

    async def test_valid_empty_catalog_stays_unresolved_not_catalog_error(
        self, mock_deals_client_factory, mock_event_bus
    ):
        """A genuinely empty (valid) catalog keeps the 'unresolved' outcome."""
        factory = _tolerant_catalog_factory(([], []))
        orchestrator = _make_orchestrator(mock_deals_client_factory, mock_event_bus, factory)

        results = await orchestrator.request_quotes_parallel([_seller_card()], _deal_params())

        assert results[0].quote is None
        events = _resolution_events(mock_event_bus)
        assert events[0]["outcome"] == "unresolved"
        assert events[0]["invalid_products"] == 0

    async def test_clean_catalog_reports_zero_invalid_products(
        self, mock_deals_client_factory, mock_event_bus
    ):
        valid = [_catalog_product("prod-b", "Premium Display")]
        factory = _tolerant_catalog_factory((valid, []))
        orchestrator = _make_orchestrator(mock_deals_client_factory, mock_event_bus, factory)
        client = mock_deals_client_factory(SELLER_URL)
        client.request_quote.return_value = _quote("q-b", "prod-b")

        await orchestrator.request_quotes_parallel([_seller_card()], _deal_params())

        events = _resolution_events(mock_event_bus)
        assert events[0]["invalid_products"] == 0
        assert events[0]["invalid_product_details"] == []


class TestRigRegressionEndToEnd:
    async def test_rig_catalog_with_two_oversized_names_still_quotes(
        self, mock_deals_client_factory, mock_event_bus
    ):
        """S1 regression mirror: a real OpenDirectClient catalog client
        serving the rig's oversized names resolves via the valid subset."""
        payload = _envelope(
            [
                _wire_product("prod-linear", OVERSIZED_NAME_1),
                _wire_product("prod-dooh", OVERSIZED_NAME_2),
                _wire_product("prod-b", "Premium Display"),
            ]
        )

        def catalog_factory(seller_url: str, **kwargs) -> OpenDirectClient:
            return _client_serving(payload)

        orchestrator = _make_orchestrator(
            mock_deals_client_factory, mock_event_bus, catalog_factory
        )
        client = mock_deals_client_factory(SELLER_URL)
        client.request_quote.return_value = _quote("q-b", "prod-b")

        results = await orchestrator.request_quotes_parallel([_seller_card()], _deal_params())

        assert results[0].error is None, results[0].error
        assert results[0].quote is not None
        assert client.request_quote.call_args.args[0].product_id == "prod-b"
        events = _resolution_events(mock_event_bus)
        assert events[0]["invalid_products"] == 2
        rejected_ids = {d["product_id"] for d in events[0]["invalid_product_details"]}
        assert rejected_ids == {"prod-linear", "prod-dooh"}
