# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Dedicated negotiation timeout + causeful conclusions.

S2 live proof 2026-07-21 (Bugs I + K): the NegotiationClient used for
Stage 3.5 proposal/negotiation calls was constructed with
``timeout=self._quote_timeout`` (30 s default, no settings/env knob),
while the live seller's synchronous LLM ``ProposalHandlingFlow`` takes
~10m46s per proposal -- so every live negotiation died at exactly 30.0 s
with ``outcome="unavailable", rounds=0``. Worse, ``str(httpx.ReadTimeout)``
is empty, so the conclusion carried no cause at all.

Contract under test:

1. ``Settings.negotiation_timeout_seconds`` exists (env
   ``NEGOTIATION_TIMEOUT_SECONDS``), default 720 s -- the measured
   ~646 s seller proposal flow plus headroom, finite. It is threaded via
   ``NegotiationConfig`` through BOTH production wirings
   (``build_default_orchestrator`` and the chat interface's direct
   construction) into the lazily-built ``NegotiationClient``. The quote
   timeout is untouched.
2. When a negotiation attempt dies on a timeout or transport error, the
   record's ``error`` and the ``negotiation.concluded`` payload's
   ``reason`` carry the exception class plus useful detail (e.g.
   ``"ReadTimeout after 720s (POST .../proposals)"``) -- never an empty
   string. This applies to the walk/unavailable path generally, not just
   ``ReadTimeout``.
"""

import importlib
from unittest.mock import AsyncMock

import httpx
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
from ad_buyer.negotiation.models import NegotiationRound
from ad_buyer.orchestration.multi_seller import (
    DealParams,
    MultiSellerOrchestrator,
    NegotiationConfig,
    SellerQuoteResult,
)

settings_module = importlib.import_module("ad_buyer.config.settings")

SELLER_URL = "http://seller-a.example.com"
CEILING = 12.0
TARGET = 10.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _quote(*, quote_id: str = "q-high", final_cpm: float = 15.0) -> QuoteResponse:
    return QuoteResponse(
        quote_id=quote_id,
        status="available",
        product=ProductInfo(product_id="prod-display-001", name="Premium Display"),
        pricing=PricingInfo(base_cpm=15.0, final_cpm=final_cpm),
        terms=TermsInfo(
            impressions=500_000,
            flight_start="2026-08-01",
            flight_end="2026-08-31",
            guaranteed=False,
        ),
        availability=AvailabilityInfo(inventory_available=True, estimated_fill_rate=0.85),
        seller_id="seller-a",
        buyer_tier="agency",
    )


def _deal_params() -> DealParams:
    return DealParams(
        product_id="prod-display-001",
        deal_type="PD",
        impressions=500_000,
        flight_start="2026-08-01",
        flight_end="2026-08-31",
        target_cpm=TARGET,
        media_type="display",
    )


def _quote_result() -> SellerQuoteResult:
    return SellerQuoteResult(
        seller_id="seller-a",
        seller_url=SELLER_URL,
        quote=_quote(),
        deal_type="PD",
        error=None,
    )


def _orchestrator(
    negotiation_client: AsyncMock,
    bus: AsyncMock,
    config: NegotiationConfig | None = None,
) -> MultiSellerOrchestrator:
    return MultiSellerOrchestrator(
        registry_client=AsyncMock(),
        deals_client_factory=lambda seller_url, **kwargs: AsyncMock(),
        event_bus=bus,
        quote_normalizer=QuoteNormalizer(),
        quote_timeout=5.0,
        negotiation_client=negotiation_client,
        negotiation_config=config or NegotiationConfig(timeout_seconds=600.0),
    )


def _concluded_payloads(bus: AsyncMock) -> list[dict]:
    return [
        call.args[0].payload
        for call in bus.publish.call_args_list
        if call.args[0].event_type == EventType.NEGOTIATION_CONCLUDED
    ]


# ---------------------------------------------------------------------------
# 1. Settings knob: default + env override
# ---------------------------------------------------------------------------


class TestSettingsKnob:
    def test_default_is_720_seconds(self, monkeypatch):
        """Default accommodates the measured ~646 s seller LLM proposal flow."""
        monkeypatch.delenv("NEGOTIATION_TIMEOUT_SECONDS", raising=False)
        assert settings_module.Settings().negotiation_timeout_seconds == 720.0

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("NEGOTIATION_TIMEOUT_SECONDS", "45")
        assert settings_module.Settings().negotiation_timeout_seconds == 45.0


# ---------------------------------------------------------------------------
# 2. NegotiationConfig threading
# ---------------------------------------------------------------------------


class TestNegotiationConfigTimeout:
    def test_config_default_matches_settings_default(self):
        assert NegotiationConfig().timeout_seconds == 720.0

    def test_from_settings_threads_timeout(self):
        settings = settings_module.Settings(negotiation_timeout_seconds=123.0)
        assert NegotiationConfig.from_settings(settings).timeout_seconds == 123.0


# ---------------------------------------------------------------------------
# 3. Wiring: default client gets the negotiation timeout, NOT the quote timeout
# ---------------------------------------------------------------------------


class TestClientWiring:
    def test_default_client_uses_negotiation_timeout_not_quote_timeout(self):
        orch = MultiSellerOrchestrator(
            registry_client=AsyncMock(),
            deals_client_factory=lambda seller_url, **kwargs: AsyncMock(),
            quote_timeout=5.0,
            negotiation_config=NegotiationConfig(timeout_seconds=600.0),
        )
        client = orch._get_negotiation_client()
        assert client._timeout == 600.0
        # Quote timeout is a separate, unchanged knob.
        assert orch._quote_timeout == 5.0

    def test_build_default_orchestrator_wires_negotiation_timeout(self, monkeypatch):
        from ad_buyer.flows.deal_booking_flow import build_default_orchestrator

        monkeypatch.setattr(
            settings_module,
            "get_settings",
            lambda: settings_module.Settings(negotiation_timeout_seconds=222.0),
        )
        orch = build_default_orchestrator()
        assert orch._negotiation_config.timeout_seconds == 222.0
        assert orch._get_negotiation_client()._timeout == 222.0

    def test_chat_direct_construction_wires_negotiation_timeout(self, monkeypatch):
        """The chat interface's configured-sellers orchestrator has the knob too."""
        import ad_buyer.events.bus as bus_mod
        from ad_buyer.interfaces.chat import main as chat_main

        class _StubSettings:
            opendirect_base_url = ""

            @staticmethod
            def get_seller_endpoints():
                return ["http://127.0.0.1:1"]

        monkeypatch.setattr(chat_main, "settings", _StubSettings())
        monkeypatch.setattr(chat_main.SellerConnection, "check_health", lambda self: True)
        monkeypatch.setattr(
            settings_module,
            "get_settings",
            lambda: settings_module.Settings(negotiation_timeout_seconds=333.0),
        )
        monkeypatch.setattr(bus_mod, "_event_bus_instance", None)

        iface = object.__new__(chat_main.ChatInterface)
        iface.conversation_history = []
        iface.context = {}
        iface._sellers = []
        iface._tools = []
        iface._initialize_sellers()

        assert iface._orchestrator._negotiation_config.timeout_seconds == 333.0
        assert iface._orchestrator._get_negotiation_client()._timeout == 333.0


# ---------------------------------------------------------------------------
# 4. Causeful conclusions: timeout/transport failures carry class + detail
# ---------------------------------------------------------------------------


class TestCausefulConclusions:
    @pytest.mark.asyncio
    async def test_read_timeout_reason_is_never_empty(self):
        """str(ReadTimeout) is "" (Bug K): the reason must still carry cause."""
        bus = AsyncMock()
        neg_client = AsyncMock()
        neg_client.submit_proposal.side_effect = httpx.ReadTimeout("")

        orch = _orchestrator(neg_client, bus)
        _, records = await orch.negotiate_above_ceiling(
            [_quote_result()], _deal_params(), max_cpm=CEILING
        )

        record = records[0]
        assert record["outcome"] == "unavailable"
        assert "ReadTimeout" in record["error"]
        assert "600" in record["error"]  # the configured bound, for diagnosis
        assert "/proposals" in record["error"]  # which call died

        payloads = _concluded_payloads(bus)
        assert len(payloads) == 1
        reason = payloads[0]["reason"]
        assert reason == record["error"]
        assert reason  # never empty

    @pytest.mark.asyncio
    async def test_transport_error_reason_carries_class_and_detail(self):
        """Not just ReadTimeout: any transport failure names its cause."""
        bus = AsyncMock()
        neg_client = AsyncMock()
        neg_client.submit_proposal.side_effect = httpx.ConnectError("connection refused")

        orch = _orchestrator(neg_client, bus)
        _, records = await orch.negotiate_above_ceiling(
            [_quote_result()], _deal_params(), max_cpm=CEILING
        )

        record = records[0]
        assert record["outcome"] == "unavailable"
        assert "ConnectError" in record["error"]
        assert "connection refused" in record["error"]
        assert _concluded_payloads(bus)[0]["reason"] == record["error"]

    @pytest.mark.asyncio
    async def test_counter_round_timeout_reason_is_never_empty(self):
        """Rounds 2..max failures are causeful too, not just the proposal open."""
        bus = AsyncMock()
        neg_client = AsyncMock()
        neg_client.submit_proposal.return_value = {
            "proposal_id": "prop-1",
            "recommendation": "counter",
            "status": "counter_pending",
            "counter_terms": {
                "proposed_price": 13.0,
                "negotiation_id": "neg-1",
                "round_number": 1,
                "action": "counter",
            },
        }
        neg_client.counter_offer.side_effect = httpx.ReadTimeout("")

        orch = _orchestrator(neg_client, bus)
        _, records = await orch.negotiate_above_ceiling(
            [_quote_result()], _deal_params(), max_cpm=CEILING
        )

        record = records[0]
        assert record["outcome"] == "unavailable"
        assert "ReadTimeout" in record["error"]
        assert "600" in record["error"]
        reason = _concluded_payloads(bus)[0]["reason"]
        assert reason == record["error"]
        assert reason

    @pytest.mark.asyncio
    async def test_no_counter_unavailable_carries_reason(self):
        """The non-exception unavailable path (no counter to evaluate) too."""
        bus = AsyncMock()
        neg_client = AsyncMock()
        neg_client.submit_proposal.return_value = {
            "proposal_id": "prop-1",
            "recommendation": "pending",
            "status": "pending_review",
            "counter_terms": None,
        }

        orch = _orchestrator(neg_client, bus)
        _, records = await orch.negotiate_above_ceiling(
            [_quote_result()], _deal_params(), max_cpm=CEILING
        )

        record = records[0]
        assert record["outcome"] == "unavailable"
        assert record.get("error")  # non-empty cause
        assert _concluded_payloads(bus)[0]["reason"] == record["error"]

    @pytest.mark.asyncio
    async def test_successful_negotiation_reason_is_none(self):
        """No failure -> no reason; concluded payload stays honest."""
        bus = AsyncMock()
        neg_client = AsyncMock()
        neg_client.submit_proposal.return_value = {
            "proposal_id": "prop-1",
            "recommendation": "counter",
            "status": "counter_pending",
            "counter_terms": {
                "proposed_price": 13.0,
                "negotiation_id": "neg-1",
                "round_number": 1,
                "action": "counter",
            },
        }
        neg_client.counter_offer.return_value = NegotiationRound(
            round_number=2,
            buyer_price=TARGET,
            seller_price=11.5,
            action="counter",
            rationale="",
        )

        deals_client = AsyncMock()
        deals_client.request_quote.return_value = _quote(quote_id="q-neg", final_cpm=11.5)

        orch = MultiSellerOrchestrator(
            registry_client=AsyncMock(),
            deals_client_factory=lambda seller_url, **kwargs: deals_client,
            event_bus=bus,
            quote_normalizer=QuoteNormalizer(),
            quote_timeout=5.0,
            negotiation_client=neg_client,
            negotiation_config=NegotiationConfig(timeout_seconds=600.0),
        )
        new_results, records = await orch.negotiate_above_ceiling(
            [_quote_result()], _deal_params(), max_cpm=CEILING
        )

        assert records[0]["outcome"] == "accepted"
        assert _concluded_payloads(bus)[0]["reason"] is None
        assert len(new_results) == 1
