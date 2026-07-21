# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Event-bus wiring for the production orchestrator (bead ar-nly5).

Wave-B rig proof 2026-07-21: ``build_default_orchestrator`` (and the chat
interface's direct construction) built ``MultiSellerOrchestrator`` WITHOUT
an ``event_bus``, so ``_emit`` no-oped and ``product.resolution`` plus all
``negotiation.*`` events (audit-class) never reached the live ``/events``
surface -- the S1/S2 drivers saw only ``campaign.created`` and
``budget.allocated``.

Contract under test: production wiring passes the SAME global event bus
singleton that the API's ``/events`` endpoint reads
(``ad_buyer.events.bus.get_event_bus``), so orchestrator-emitted events
are observable there.
"""

import asyncio

import pytest

import ad_buyer.events.bus as bus_mod
from ad_buyer.events.models import EventType
from ad_buyer.flows.deal_booking_flow import build_default_orchestrator
from ad_buyer.registry.models import AgentCapability, AgentCard, TrustLevel


@pytest.fixture(autouse=True)
def _reset_bus_singleton():
    """Isolate the global event bus singleton per test."""
    bus_mod._event_bus_instance = None
    yield
    bus_mod._event_bus_instance = None


class TestBuildDefaultOrchestratorEventBus:
    def test_orchestrator_gets_the_global_event_bus_singleton(self):
        orchestrator = build_default_orchestrator()
        bus = asyncio.run(bus_mod.get_event_bus())
        assert orchestrator._event_bus is bus, (
            "build_default_orchestrator must wire the /events singleton bus; "
            "a None bus makes _emit a no-op and drops product.resolution and "
            "negotiation.* (audit-class) events"
        )

    def test_emitted_events_reach_the_events_surface(self):
        """Behavioral check: a resolution attempt lands on the shared bus.

        The seller URL points at an unreachable port, so the catalog fetch
        fails and Stage 1.5 emits ``product.resolution`` with
        ``catalog_error`` -- exactly the observability the rig proof found
        missing. The event must be listed by the same bus the API's
        ``/events`` endpoint queries.
        """
        from ad_buyer.orchestration.multi_seller import DealParams

        orchestrator = build_default_orchestrator()
        # Keep the failing catalog fetch fast.
        orchestrator._quote_timeout = 2.0
        seller = AgentCard(
            agent_id="seller-unreachable",
            name="seller-unreachable",
            url="http://127.0.0.1:1",
            protocols=["a2a", "deals-api-v1"],
            capabilities=[AgentCapability(name="display", description="display")],
            trust_level=TrustLevel.VERIFIED,
        )
        params = DealParams(
            product_id="prod-x",
            deal_type="PD",
            impressions=1000,
            flight_start="2026-08-01",
            flight_end="2026-08-31",
        )

        async def _drive():
            await orchestrator.request_quotes_parallel([seller], params)
            bus = await bus_mod.get_event_bus()
            return await bus.list_events()

        events = asyncio.run(_drive())
        types = [e.event_type for e in events]
        assert EventType.PRODUCT_RESOLUTION in types
        assert EventType.QUOTE_RECEIVED in types


class TestChatInterfaceEventBus:
    def test_configured_sellers_orchestrator_gets_the_singleton_bus(self, monkeypatch):
        """The chat interface's direct construction had the same gap."""
        from ad_buyer.interfaces.chat import main as chat_main

        class _StubSettings:
            opendirect_base_url = ""

            @staticmethod
            def get_seller_endpoints():
                return ["http://127.0.0.1:1"]

        monkeypatch.setattr(chat_main, "settings", _StubSettings())
        monkeypatch.setattr(chat_main.SellerConnection, "check_health", lambda self: True)

        iface = object.__new__(chat_main.ChatInterface)
        iface.conversation_history = []
        iface.context = {}
        iface._sellers = []
        iface._tools = []
        iface._initialize_sellers()

        bus = asyncio.run(bus_mod.get_event_bus())
        assert iface._orchestrator._event_bus is bus

    def test_fallback_default_orchestrator_gets_the_singleton_bus(self, monkeypatch):
        """No configured sellers -> build_default_orchestrator path is wired."""
        from ad_buyer.interfaces.chat import main as chat_main

        class _StubSettings:
            opendirect_base_url = ""

            @staticmethod
            def get_seller_endpoints():
                return []

        monkeypatch.setattr(chat_main, "settings", _StubSettings())

        iface = object.__new__(chat_main.ChatInterface)
        iface.conversation_history = []
        iface.context = {}
        iface._sellers = []
        iface._tools = []
        iface._initialize_sellers()

        bus = asyncio.run(bus_mod.get_event_bus())
        assert iface._orchestrator._event_bus is bus


class TestSyncAccessor:
    def test_get_event_bus_sync_returns_the_same_singleton(self):
        sync_bus = bus_mod.get_event_bus_sync()
        async_bus = asyncio.run(bus_mod.get_event_bus())
        assert sync_bus is async_bus

    def test_emit_event_sync_uses_the_same_singleton(self):
        """emit_event_sync (flow events) and the orchestrator share one bus."""
        from ad_buyer.events.helpers import emit_event_sync

        emit_event_sync(EventType.CAMPAIGN_CREATED, payload={"name": "t"})
        bus = bus_mod.get_event_bus_sync()
        events = asyncio.run(bus.list_events())
        assert [e.event_type for e in events] == [EventType.CAMPAIGN_CREATED]
