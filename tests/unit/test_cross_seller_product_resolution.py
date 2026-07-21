# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Cross-seller product identity in the quote path.

Wave-A proof run 2026-07-21 (S2): buyer research read the single
OPENDIRECT_BASE_URL catalog (display-seller in the rig) and recommended
prod-c5255ec5; the MultiSellerOrchestrator then quoted ctv-seller with that
ID -> Seller API 404 product_not_found -> "no viable quotes" -> walk before
negotiation Stage 3.5 could fire.

Key invariant under test: a product ID must only ever be sent to the seller
it came from. Before quoting a seller, the orchestrator resolves an
equivalent product on THAT seller's own catalog:

  1. exact product-ID match (the seller the recommendation came from),
  2. equivalent product by name (case-insensitive exact),
  3. equivalent product by channel/ad-format match (deterministic pick:
     cheapest declared-price candidate, tie-broken by product id),
  4. no match -> skip that seller with a clear ``product_not_resolvable``
     error + ``product.resolution`` event (NOT a confusing global walk).

Resolution is active only when a ``catalog_client_factory`` is injected
(mirroring the optional ``capability_client`` idiom); without one the
legacy passthrough behavior is preserved.
"""

from unittest.mock import AsyncMock

import pytest

from ad_buyer.booking.quote_normalizer import QuoteNormalizer
from ad_buyer.events.models import EventType
from ad_buyer.models.deals import (
    AvailabilityInfo,
    PricingInfo,
    ProductInfo,
    QuoteResponse,
    TermsInfo,
)
from ad_buyer.models.opendirect import Product, RateType
from ad_buyer.orchestration.multi_seller import (
    DealParams,
    MultiSellerOrchestrator,
    NegotiationConfig,
)
from ad_buyer.registry.models import AgentCapability, AgentCard, TrustLevel

SELLER_A_URL = "http://display-seller.example.com"
SELLER_B_URL = "http://ctv-seller.example.com"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seller_card(agent_id: str, url: str, capability: str = "display") -> AgentCard:
    return AgentCard(
        agent_id=agent_id,
        name=agent_id,
        url=url,
        protocols=["a2a", "deals-api-v1"],
        capabilities=[AgentCapability(name=capability, description=f"{capability} inventory")],
        trust_level=TrustLevel.VERIFIED,
    )


def _catalog_product(
    *,
    product_id: str,
    name: str,
    base_price: float = 10.0,
    ad_formats: list[str] | None = None,
) -> Product:
    """Build a seller-catalog product as the OpenDirect client returns it.

    ``ad_formats`` rides in ``ext`` exactly as ``from_wire_product`` maps it.
    """
    ext = {"ad_formats": ad_formats} if ad_formats is not None else None
    return Product(
        id=product_id,
        publisherid="pub-1",
        name=name,
        baseprice=base_price,
        ratetype=RateType.CPM,
        ext=ext,
    )


def _quote(
    *,
    quote_id: str,
    seller_id: str,
    product_id: str,
    product_name: str = "Premium Display",
    final_cpm: float = 8.0,
) -> QuoteResponse:
    return QuoteResponse(
        quote_id=quote_id,
        status="available",
        product=ProductInfo(product_id=product_id, name=product_name),
        pricing=PricingInfo(base_cpm=final_cpm, final_cpm=final_cpm),
        terms=TermsInfo(
            impressions=500_000,
            flight_start="2026-08-01",
            flight_end="2026-08-31",
            guaranteed=False,
        ),
        availability=AvailabilityInfo(inventory_available=True, estimated_fill_rate=0.85),
        seller_id=seller_id,
        buyer_tier="agency",
    )


def _deal_params(
    *,
    product_id: str = "prod-a",
    product_name: str | None = "Premium Display",
    channel: str | None = "display",
) -> DealParams:
    return DealParams(
        product_id=product_id,
        deal_type="PD",
        impressions=500_000,
        flight_start="2026-08-01",
        flight_end="2026-08-31",
        target_cpm=10.0,
        media_type="digital",
        product_name=product_name,
        channel=channel,
    )


@pytest.fixture
def mock_deals_client_factory():
    """Factory producing per-URL AsyncMock DealsClients."""
    clients: dict[str, AsyncMock] = {}

    def factory(seller_url: str, **kwargs) -> AsyncMock:
        if seller_url not in clients:
            mock = AsyncMock()
            mock.seller_url = seller_url
            mock.request_quote = AsyncMock(return_value=None)
            mock.book_deal = AsyncMock(return_value=None)
            mock.close = AsyncMock()
            clients[seller_url] = mock
        return clients[seller_url]

    factory._clients = clients
    return factory


@pytest.fixture
def mock_event_bus():
    bus = AsyncMock()
    bus.publish = AsyncMock()
    return bus


def _catalog_factory(catalogs: dict[str, list[Product]]):
    """Catalog client factory backed by an in-memory per-URL catalog."""
    clients: dict[str, AsyncMock] = {}

    def factory(seller_url: str, **kwargs) -> AsyncMock:
        if seller_url not in clients:
            mock = AsyncMock()
            mock.list_products = AsyncMock(return_value=list(catalogs.get(seller_url, [])))
            clients[seller_url] = mock
        return clients[seller_url]

    factory._clients = clients
    return factory


def _make_orchestrator(
    deals_factory,
    event_bus,
    catalog_factory=None,
) -> MultiSellerOrchestrator:
    return MultiSellerOrchestrator(
        registry_client=AsyncMock(),
        deals_client_factory=deals_factory,
        event_bus=event_bus,
        quote_normalizer=QuoteNormalizer(),
        quote_timeout=5.0,
        catalog_client_factory=catalog_factory,
    )


def _resolution_events(event_bus) -> list[dict]:
    payloads = []
    for call in event_bus.publish.call_args_list:
        event = call.args[0]
        if event.event_type == EventType.PRODUCT_RESOLUTION:
            payloads.append(event.payload)
    return payloads


# ---------------------------------------------------------------------------
# DealParams identity fields
# ---------------------------------------------------------------------------


class TestDealParamsIdentityFields:
    def test_product_name_and_channel_default_none(self):
        params = DealParams(
            product_id="p1",
            deal_type="PD",
            impressions=1,
            flight_start="2026-08-01",
            flight_end="2026-08-31",
        )
        assert params.product_name is None
        assert params.channel is None

    def test_product_name_and_channel_accepted(self):
        params = _deal_params(product_name="Premium Display", channel="display")
        assert params.product_name == "Premium Display"
        assert params.channel == "display"


# ---------------------------------------------------------------------------
# The cross-seller invariant (the S2 404 bug)
# ---------------------------------------------------------------------------


class TestCrossSellerInvariant:
    """A product ID is only ever sent to the seller whose catalog it is in."""

    async def test_never_sends_foreign_product_id(self, mock_deals_client_factory, mock_event_bus):
        """Seller B is quoted with ITS equivalent product, not seller A's ID."""
        catalogs = {
            SELLER_A_URL: [
                _catalog_product(
                    product_id="prod-a", name="Premium Display", ad_formats=["display"]
                )
            ],
            SELLER_B_URL: [
                _catalog_product(
                    product_id="prod-b", name="Premium Display", ad_formats=["display"]
                )
            ],
        }
        orchestrator = _make_orchestrator(
            mock_deals_client_factory, mock_event_bus, _catalog_factory(catalogs)
        )
        client_a = mock_deals_client_factory(SELLER_A_URL)
        client_a.request_quote.return_value = _quote(
            quote_id="q-a", seller_id="seller-a", product_id="prod-a"
        )
        client_b = mock_deals_client_factory(SELLER_B_URL)
        client_b.request_quote.return_value = _quote(
            quote_id="q-b", seller_id="seller-b", product_id="prod-b"
        )

        sellers = [
            _seller_card("seller-a", SELLER_A_URL),
            _seller_card("seller-b", SELLER_B_URL),
        ]
        results = await orchestrator.request_quotes_parallel(sellers, _deal_params())

        assert all(r.error is None for r in results)
        sent_to_a = client_a.request_quote.call_args.args[0].product_id
        sent_to_b = client_b.request_quote.call_args.args[0].product_id
        assert sent_to_a == "prod-a"
        assert sent_to_b == "prod-b"  # NOT prod-a: the S2 404 bug

    async def test_exact_id_match_short_circuits(self, mock_deals_client_factory, mock_event_bus):
        """The seller that owns the recommended ID is quoted with that ID."""
        catalogs = {
            SELLER_A_URL: [
                _catalog_product(product_id="prod-a", name="Premium Display"),
                _catalog_product(product_id="prod-other", name="Other Package"),
            ]
        }
        orchestrator = _make_orchestrator(
            mock_deals_client_factory, mock_event_bus, _catalog_factory(catalogs)
        )
        client_a = mock_deals_client_factory(SELLER_A_URL)
        client_a.request_quote.return_value = _quote(
            quote_id="q-a", seller_id="seller-a", product_id="prod-a"
        )

        results = await orchestrator.request_quotes_parallel(
            [_seller_card("seller-a", SELLER_A_URL)], _deal_params()
        )

        assert results[0].error is None
        assert client_a.request_quote.call_args.args[0].product_id == "prod-a"
        events = _resolution_events(mock_event_bus)
        assert len(events) == 1
        assert events[0]["outcome"] == "exact_id"

    async def test_name_match_resolves_equivalent_product(
        self, mock_deals_client_factory, mock_event_bus
    ):
        """Same-name product on the target seller wins over channel fallback."""
        catalogs = {
            SELLER_B_URL: [
                _catalog_product(
                    product_id="prod-cheap",
                    name="Bargain Display",
                    base_price=2.0,
                    ad_formats=["display"],
                ),
                _catalog_product(
                    product_id="prod-b",
                    name="premium display",  # case-insensitive match
                    base_price=9.0,
                    ad_formats=["display"],
                ),
            ]
        }
        orchestrator = _make_orchestrator(
            mock_deals_client_factory, mock_event_bus, _catalog_factory(catalogs)
        )
        client_b = mock_deals_client_factory(SELLER_B_URL)
        client_b.request_quote.return_value = _quote(
            quote_id="q-b", seller_id="seller-b", product_id="prod-b"
        )

        results = await orchestrator.request_quotes_parallel(
            [_seller_card("seller-b", SELLER_B_URL)], _deal_params()
        )

        assert results[0].error is None
        assert client_b.request_quote.call_args.args[0].product_id == "prod-b"
        events = _resolution_events(mock_event_bus)
        assert events[0]["outcome"] == "name_match"
        assert events[0]["resolved_product_id"] == "prod-b"

    async def test_channel_fallback_picks_cheapest_deterministically(
        self, mock_deals_client_factory, mock_event_bus
    ):
        """No ID/name match: cheapest channel-matching product is chosen."""
        catalogs = {
            SELLER_B_URL: [
                _catalog_product(
                    product_id="prod-exp",
                    name="Expensive Display",
                    base_price=20.0,
                    ad_formats=["display"],
                ),
                _catalog_product(
                    product_id="prod-cheap",
                    name="Bargain Display",
                    base_price=5.0,
                    ad_formats=["banner"],  # placement vocab normalizes to display
                ),
                _catalog_product(
                    product_id="prod-video",
                    name="Video Package",
                    base_price=1.0,
                    ad_formats=["video"],  # wrong channel: excluded
                ),
            ]
        }
        orchestrator = _make_orchestrator(
            mock_deals_client_factory, mock_event_bus, _catalog_factory(catalogs)
        )
        client_b = mock_deals_client_factory(SELLER_B_URL)
        client_b.request_quote.return_value = _quote(
            quote_id="q-b", seller_id="seller-b", product_id="prod-cheap"
        )

        results = await orchestrator.request_quotes_parallel(
            [_seller_card("seller-b", SELLER_B_URL)],
            _deal_params(product_name="No Such Name", channel="display"),
        )

        assert results[0].error is None
        assert client_b.request_quote.call_args.args[0].product_id == "prod-cheap"
        events = _resolution_events(mock_event_bus)
        assert events[0]["outcome"] == "channel_match"

    async def test_undeclared_formats_survive_channel_fallback(
        self, mock_deals_client_factory, mock_event_bus
    ):
        """Products with no declared ad_formats stay eligible."""
        catalogs = {
            SELLER_B_URL: [
                _catalog_product(
                    product_id="prod-undeclared",
                    name="Undeclared Package",
                    base_price=7.0,
                    ad_formats=None,
                ),
            ]
        }
        orchestrator = _make_orchestrator(
            mock_deals_client_factory, mock_event_bus, _catalog_factory(catalogs)
        )
        client_b = mock_deals_client_factory(SELLER_B_URL)
        client_b.request_quote.return_value = _quote(
            quote_id="q-b", seller_id="seller-b", product_id="prod-undeclared"
        )

        results = await orchestrator.request_quotes_parallel(
            [_seller_card("seller-b", SELLER_B_URL)],
            _deal_params(product_name=None, channel="display"),
        )

        assert results[0].error is None
        assert client_b.request_quote.call_args.args[0].product_id == "prod-undeclared"


# ---------------------------------------------------------------------------
# Graceful fallback: unresolvable / catalog errors skip the seller cleanly
# ---------------------------------------------------------------------------


class TestGracefulFallback:
    async def test_no_match_skips_seller_with_clear_error(
        self, mock_deals_client_factory, mock_event_bus
    ):
        """Unresolvable product -> per-seller skip, never a quote request."""
        catalogs = {
            SELLER_B_URL: [
                _catalog_product(
                    product_id="prod-video",
                    name="Video Package",
                    ad_formats=["video"],
                ),
            ]
        }
        orchestrator = _make_orchestrator(
            mock_deals_client_factory, mock_event_bus, _catalog_factory(catalogs)
        )
        client_b = mock_deals_client_factory(SELLER_B_URL)

        results = await orchestrator.request_quotes_parallel(
            [_seller_card("seller-b", SELLER_B_URL)],
            _deal_params(product_name="No Such Name", channel="display"),
        )

        assert results[0].quote is None
        assert results[0].error is not None
        assert "product_not_resolvable" in results[0].error
        client_b.request_quote.assert_not_awaited()
        events = _resolution_events(mock_event_bus)
        assert events[0]["outcome"] == "unresolved"
        assert events[0]["seller_id"] == "seller-b"
        assert events[0]["requested_product_id"] == "prod-a"

    async def test_catalog_fetch_failure_skips_seller(
        self, mock_deals_client_factory, mock_event_bus
    ):
        """A catalog error skips that seller; it never gets a foreign ID."""

        def factory(seller_url: str, **kwargs) -> AsyncMock:
            mock = AsyncMock()
            mock.list_products = AsyncMock(side_effect=RuntimeError("boom"))
            return mock

        orchestrator = _make_orchestrator(mock_deals_client_factory, mock_event_bus, factory)
        client_b = mock_deals_client_factory(SELLER_B_URL)

        results = await orchestrator.request_quotes_parallel(
            [_seller_card("seller-b", SELLER_B_URL)], _deal_params()
        )

        assert results[0].quote is None
        assert results[0].error is not None
        assert "product_not_resolvable" in results[0].error
        client_b.request_quote.assert_not_awaited()
        events = _resolution_events(mock_event_bus)
        assert events[0]["outcome"] == "catalog_error"

    async def test_one_unresolvable_seller_does_not_block_others(
        self, mock_deals_client_factory, mock_event_bus
    ):
        """Per-seller isolation: seller A still quotes when B is skipped."""
        catalogs = {
            SELLER_A_URL: [_catalog_product(product_id="prod-a", name="Premium Display")],
            SELLER_B_URL: [],  # empty catalog: unresolvable
        }
        orchestrator = _make_orchestrator(
            mock_deals_client_factory, mock_event_bus, _catalog_factory(catalogs)
        )
        client_a = mock_deals_client_factory(SELLER_A_URL)
        client_a.request_quote.return_value = _quote(
            quote_id="q-a", seller_id="seller-a", product_id="prod-a"
        )

        results = await orchestrator.request_quotes_parallel(
            [
                _seller_card("seller-a", SELLER_A_URL),
                _seller_card("seller-b", SELLER_B_URL),
            ],
            _deal_params(),
        )

        by_seller = {r.seller_id: r for r in results}
        assert by_seller["seller-a"].quote is not None
        assert by_seller["seller-a"].error is None
        assert by_seller["seller-b"].quote is None
        assert "product_not_resolvable" in by_seller["seller-b"].error


# ---------------------------------------------------------------------------
# Legacy passthrough (no catalog client factory injected)
# ---------------------------------------------------------------------------


class TestLegacyPassthrough:
    async def test_no_factory_preserves_existing_behavior(
        self, mock_deals_client_factory, mock_event_bus
    ):
        orchestrator = _make_orchestrator(mock_deals_client_factory, mock_event_bus, None)
        client_b = mock_deals_client_factory(SELLER_B_URL)
        client_b.request_quote.return_value = _quote(
            quote_id="q-b", seller_id="seller-b", product_id="prod-a"
        )

        results = await orchestrator.request_quotes_parallel(
            [_seller_card("seller-b", SELLER_B_URL)], _deal_params()
        )

        assert results[0].error is None
        assert client_b.request_quote.call_args.args[0].product_id == "prod-a"
        assert _resolution_events(mock_event_bus) == []


# ---------------------------------------------------------------------------
# Negotiation path uses the seller-local product id
# ---------------------------------------------------------------------------


class TestNegotiationUsesSellerLocalProductId:
    async def test_negotiation_and_requote_use_quoted_product_id(
        self, mock_deals_client_factory, mock_event_bus
    ):
        """Stage 3.5 must negotiate/re-quote the QUOTED product, never the
        original recommendation's foreign ID."""
        negotiation_client = AsyncMock()
        # Seller accepts the buyer's opening offer outright.
        negotiation_client.submit_proposal = AsyncMock(
            return_value={
                "proposal_id": "prop-1",
                "recommendation": "accept",
                "status": "accepted",
                "counter_terms": None,
            }
        )

        orchestrator = MultiSellerOrchestrator(
            registry_client=AsyncMock(),
            deals_client_factory=mock_deals_client_factory,
            event_bus=mock_event_bus,
            quote_normalizer=QuoteNormalizer(),
            quote_timeout=5.0,
            negotiation_client=negotiation_client,
            negotiation_config=NegotiationConfig(enabled=True, band=1.5, max_rounds=3),
        )

        client_b = mock_deals_client_factory(SELLER_B_URL)
        # Above-ceiling quote for seller B's OWN product id.
        high_quote = _quote(
            quote_id="q-high",
            seller_id="seller-b",
            product_id="prod-b",
            final_cpm=14.0,
        )
        requote = _quote(
            quote_id="q-requote",
            seller_id="seller-b",
            product_id="prod-b",
            final_cpm=10.0,
        )
        client_b.request_quote.side_effect = [high_quote, requote]

        results = await orchestrator.request_quotes_parallel(
            [_seller_card("seller-b", SELLER_B_URL)],
            _deal_params(product_id="prod-a"),
        )
        new_results, records = await orchestrator.negotiate_above_ceiling(
            results,
            _deal_params(product_id="prod-a"),
            max_cpm=12.0,
        )

        assert records[0]["outcome"] == "accepted"
        # The proposal opened on the seller-local product id.
        assert negotiation_client.submit_proposal.call_args.kwargs["product_id"] == "prod-b"
        # The post-agreement re-quote also targeted the seller-local id.
        requote_request = client_b.request_quote.call_args.args[0]
        assert requote_request.product_id == "prod-b"
        assert records[0]["product_id"] == "prod-b"


# ---------------------------------------------------------------------------
# Production wiring
# ---------------------------------------------------------------------------


class TestProductionWiring:
    def test_settings_flag_defaults_on(self):
        from ad_buyer.config.settings import Settings

        assert Settings().product_resolution_enabled is True

    def test_build_default_orchestrator_wires_catalog_factory(self):
        from ad_buyer.flows.deal_booking_flow import build_default_orchestrator

        orchestrator = build_default_orchestrator()
        assert orchestrator._catalog_client_factory is not None

    def test_build_default_orchestrator_respects_disabled_flag(self, monkeypatch):
        import importlib

        from ad_buyer.flows.deal_booking_flow import build_default_orchestrator

        # `ad_buyer.config.settings` the attribute is shadowed by the lazy
        # `settings` proxy on the package -- import the module explicitly.
        settings_module = importlib.import_module("ad_buyer.config.settings")

        monkeypatch.setattr(
            settings_module,
            "get_settings",
            lambda: settings_module.Settings(product_resolution_enabled=False),
        )
        orchestrator = build_default_orchestrator()
        assert orchestrator._catalog_client_factory is None

    def test_book_approved_threads_product_identity(self):
        """_book_approved forwards product_name + channel for resolution."""
        import asyncio
        from unittest.mock import MagicMock

        from ad_buyer.flows.deal_booking_flow import DealBookingFlow
        from ad_buyer.models.flow_state import ProductRecommendation

        orchestrator = AsyncMock(spec=MultiSellerOrchestrator)
        orchestrator.orchestrate.side_effect = RuntimeError("stop here")
        flow = DealBookingFlow(client=MagicMock(), orchestrator=orchestrator)
        flow.state.campaign_brief = {
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
        }
        rec = ProductRecommendation(
            product_id="prod-a",
            product_name="Premium Display",
            publisher="pub-1",
            channel="branding",
            impressions=500_000,
            cpm=10.0,
            cost=5_000.0,
        )

        results = asyncio.run(flow._book_approved([rec]))

        assert results[0][2] == "stop here"  # orchestrate was reached
        deal_params = orchestrator.orchestrate.call_args.kwargs["deal_params"]
        assert deal_params.product_name == "Premium Display"
        assert deal_params.channel == "display"  # branding -> display (discovery vocab)
