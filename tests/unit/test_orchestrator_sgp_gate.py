# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SGP vendor-approval gate in the CANONICAL booking path.

PR #82 added the IAB Diligence Platform (SGP) client and gated the
example-only ``RequestDealTool`` / ``DiscoverInventoryTool``, but nothing
constructed those tools in the real runtime: ``SGP_ENFORCE`` was unwired
and the DealBookingFlow -> MultiSellerOrchestrator pipeline booked deals
with zero vendor-approval checks.

Contract under test:

1. ``MultiSellerOrchestrator`` accepts ``sgp_client`` / ``sgp_enforce`` /
   ``sgp_unknown_policy`` and applies the vendor-approval gate at the
   discovery stage (Stage 1), so unapproved sellers never reach quoting
   or booking.
2. Default OFF: with ``sgp_enforce=False`` (the default) behavior is
   byte-identical -- no SGP calls, no new events, same sellers returned.
3. Enforcing: NOT-APPROVED sellers are excluded; unknown sellers follow
   ``sgp_unknown_policy`` ("block" | "warn" | "allow"); every decision
   emits an ``sgp.vendor_gate`` event with a truthful outcome + reason.
4. FAIL-CLOSED: when enforcing and the SGP check cannot complete
   (transport error, or no client configured at all), NO seller passes
   the gate and the emitted reason is causeful -- never empty/blank.
5. Production wiring: ``build_default_orchestrator`` and the chat
   interface thread the SGP settings knobs (``SGP_ENFORCE``,
   ``SGP_API_KEY``, ``SGP_BASE_URL``, ``SGP_UNKNOWN_VENDOR_POLICY``,
   ``SGP_CACHE_TTL_SECONDS``) into the orchestrator.
"""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from ad_buyer.clients.sgp_client import SGPClient, SGPClientError
from ad_buyer.events.models import EventType
from ad_buyer.models.deals import (
    PricingInfo,
    ProductInfo,
    QuoteResponse,
    TermsInfo,
)
from ad_buyer.models.sgp import ApprovalRecord
from ad_buyer.orchestration.multi_seller import (
    DealParams,
    InventoryRequirements,
    MultiSellerOrchestrator,
)
from ad_buyer.registry.models import AgentCapability, AgentCard, TrustLevel

settings_module = importlib.import_module("ad_buyer.config.settings")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seller(agent_id: str, url: str) -> AgentCard:
    return AgentCard(
        agent_id=agent_id,
        name=agent_id,
        url=url,
        protocols=["deals-api-v1"],
        capabilities=[AgentCapability(name="ctv", description="ctv inventory")],
        trust_level=TrustLevel.VERIFIED,
    )


def _approval(domain: str, approved: bool) -> ApprovalRecord:
    return ApprovalRecord.model_validate(
        {
            "vendorId": 1,
            "vendorCompanyId": 10,
            "companyName": f"{domain} Inc",
            "domain": domain,
            "iabBuyerAgentApproval": approved,
        }
    )


def _requirements() -> InventoryRequirements:
    return InventoryRequirements(media_type="ctv", deal_types=["PD"])


def _make_orchestrator(
    sellers: list[AgentCard],
    *,
    deals_client_factory=None,
    **sgp_kwargs,
) -> tuple[MultiSellerOrchestrator, MagicMock]:
    """Orchestrator with a stub registry and a capture event bus."""
    registry = MagicMock()
    registry.discover_sellers = AsyncMock(return_value=sellers)
    bus = MagicMock()
    bus.publish = AsyncMock()
    orch = MultiSellerOrchestrator(
        registry_client=registry,
        deals_client_factory=deals_client_factory or (lambda url, **kw: MagicMock()),
        event_bus=bus,
        **sgp_kwargs,
    )
    return orch, bus


def _gate_events(bus: MagicMock) -> list:
    return [
        call.args[0]
        for call in bus.publish.call_args_list
        if call.args[0].event_type == EventType.SGP_VENDOR_GATE
    ]


def _sgp_client_mock(approvals_by_domain: dict) -> MagicMock:
    client = MagicMock()
    client.check_approvals = AsyncMock(return_value=approvals_by_domain)
    return client


# ---------------------------------------------------------------------------
# 1. Default OFF: byte-identical behavior, zero SGP calls, zero new events
# ---------------------------------------------------------------------------


class TestGateDisabledDefault:
    @pytest.mark.asyncio
    async def test_default_construction_has_gate_off(self):
        sellers = [_seller("seller-a", "http://seller-a.example.com")]
        orch, bus = _make_orchestrator(sellers)
        result = await orch.discover_sellers(_requirements())
        assert result == sellers
        assert _gate_events(bus) == []

    @pytest.mark.asyncio
    async def test_enforce_false_makes_zero_sgp_calls_even_with_client(self):
        """SGP_ENFORCE off must mean ZERO new network calls, client or not."""
        sellers = [
            _seller("seller-a", "http://seller-a.example.com"),
            _seller("seller-b", "http://seller-b.example.com"),
        ]
        sgp = _sgp_client_mock({})
        orch, bus = _make_orchestrator(sellers, sgp_client=sgp, sgp_enforce=False)
        result = await orch.discover_sellers(_requirements())
        assert result == sellers
        sgp.check_approvals.assert_not_awaited()
        assert _gate_events(bus) == []


# ---------------------------------------------------------------------------
# 2. Enforcing: approval decisions filter discovery, with a truthful trail
# ---------------------------------------------------------------------------


class TestGateEnforcing:
    @pytest.mark.asyncio
    async def test_denied_vendor_excluded_approved_kept(self):
        sellers = [
            _seller("seller-good", "http://seller-good.example.com"),
            _seller("seller-bad", "http://seller-bad.example.com"),
        ]
        sgp = _sgp_client_mock(
            {
                "seller-good.example.com": _approval("seller-good.example.com", True),
                "seller-bad.example.com": _approval("seller-bad.example.com", False),
            }
        )
        orch, bus = _make_orchestrator(sellers, sgp_client=sgp, sgp_enforce=True)
        result = await orch.discover_sellers(_requirements())
        assert [s.agent_id for s in result] == ["seller-good"]

        events = _gate_events(bus)
        by_seller = {e.payload["seller_id"]: e.payload for e in events}
        assert by_seller["seller-good"]["outcome"] == "approved"
        assert by_seller["seller-bad"]["outcome"] == "denied"
        # The trail must be truthful and causeful.
        assert by_seller["seller-bad"]["reason"]
        assert "seller-bad.example.com" in by_seller["seller-bad"]["reason"]

    @pytest.mark.asyncio
    async def test_single_batched_lookup_for_all_sellers(self):
        """One check_approvals call covers all sellers (client chunks by 10)."""
        sellers = [
            _seller("seller-a", "http://seller-a.example.com"),
            _seller("seller-b", "https://www.seller-b.example.com:8001/path"),
        ]
        sgp = _sgp_client_mock(
            {
                "seller-a.example.com": _approval("seller-a.example.com", True),
                "seller-b.example.com": _approval("seller-b.example.com", True),
            }
        )
        orch, _ = _make_orchestrator(sellers, sgp_client=sgp, sgp_enforce=True)
        result = await orch.discover_sellers(_requirements())
        assert len(result) == 2
        sgp.check_approvals.assert_awaited_once()
        domains = sgp.check_approvals.await_args.args[0]
        assert sorted(domains) == ["seller-a.example.com", "seller-b.example.com"]

    @pytest.mark.asyncio
    async def test_unknown_vendor_blocked_by_default_policy(self):
        sellers = [_seller("seller-x", "http://seller-x.example.com")]
        sgp = _sgp_client_mock({"seller-x.example.com": None})
        orch, bus = _make_orchestrator(sellers, sgp_client=sgp, sgp_enforce=True)
        result = await orch.discover_sellers(_requirements())
        assert result == []
        (event,) = _gate_events(bus)
        assert event.payload["outcome"] == "unknown_blocked"
        assert event.payload["reason"]

    @pytest.mark.asyncio
    async def test_unknown_vendor_warn_policy_keeps_seller(self):
        sellers = [_seller("seller-x", "http://seller-x.example.com")]
        sgp = _sgp_client_mock({"seller-x.example.com": None})
        orch, bus = _make_orchestrator(
            sellers, sgp_client=sgp, sgp_enforce=True, sgp_unknown_policy="warn"
        )
        result = await orch.discover_sellers(_requirements())
        assert [s.agent_id for s in result] == ["seller-x"]
        (event,) = _gate_events(bus)
        assert event.payload["outcome"] == "unknown_warned"
        assert event.payload["reason"]

    @pytest.mark.asyncio
    async def test_unknown_vendor_allow_policy_keeps_seller(self):
        sellers = [_seller("seller-x", "http://seller-x.example.com")]
        sgp = _sgp_client_mock({"seller-x.example.com": None})
        orch, bus = _make_orchestrator(
            sellers, sgp_client=sgp, sgp_enforce=True, sgp_unknown_policy="allow"
        )
        result = await orch.discover_sellers(_requirements())
        assert [s.agent_id for s in result] == ["seller-x"]
        (event,) = _gate_events(bus)
        assert event.payload["outcome"] == "unknown_allowed"

    @pytest.mark.asyncio
    async def test_seller_without_derivable_domain_is_excluded(self):
        """No domain -> unverifiable -> fail closed on THAT seller."""
        sellers = [_seller("seller-nodomain", "")]
        sgp = _sgp_client_mock({})
        orch, bus = _make_orchestrator(sellers, sgp_client=sgp, sgp_enforce=True)
        result = await orch.discover_sellers(_requirements())
        assert result == []
        (event,) = _gate_events(bus)
        assert event.payload["outcome"] == "no_domain"
        assert event.payload["reason"]

    def test_invalid_unknown_policy_rejected_at_construction(self):
        with pytest.raises(ValueError, match="sgp_unknown_policy"):
            MultiSellerOrchestrator(
                registry_client=MagicMock(),
                deals_client_factory=lambda url, **kw: MagicMock(),
                sgp_client=MagicMock(),
                sgp_enforce=True,
                sgp_unknown_policy="bogus",
            )


# ---------------------------------------------------------------------------
# 3. FAIL-CLOSED: unverifiable vendors never book, with a causeful reason
# ---------------------------------------------------------------------------


class TestGateFailClosed:
    @pytest.mark.asyncio
    async def test_sgp_transport_error_excludes_all_sellers(self):
        sellers = [
            _seller("seller-a", "http://seller-a.example.com"),
            _seller("seller-b", "http://seller-b.example.com"),
        ]
        sgp = MagicMock()
        sgp.check_approvals = AsyncMock(
            side_effect=SGPClientError(
                "IAB Diligence Platform request failed: ConnectError: refused"
            )
        )
        orch, bus = _make_orchestrator(sellers, sgp_client=sgp, sgp_enforce=True)
        result = await orch.discover_sellers(_requirements())
        assert result == []

        events = _gate_events(bus)
        assert len(events) == 2
        for event in events:
            assert event.payload["outcome"] == "check_failed"
            reason = event.payload["reason"]
            # Causeful, never blank: exception class + detail.
            assert reason
            assert "SGPClientError" in reason
            assert "ConnectError" in reason

    @pytest.mark.asyncio
    async def test_enforce_without_client_fails_closed(self):
        """SGP_ENFORCE=true with no client (no API key) must not book blind."""
        sellers = [_seller("seller-a", "http://seller-a.example.com")]
        orch, bus = _make_orchestrator(sellers, sgp_client=None, sgp_enforce=True)
        result = await orch.discover_sellers(_requirements())
        assert result == []
        (event,) = _gate_events(bus)
        assert event.payload["outcome"] == "unconfigured"
        assert event.payload["reason"]
        assert "SGP_API_KEY" in event.payload["reason"]

    @pytest.mark.asyncio
    async def test_orchestrate_books_nothing_when_sgp_unreachable(self):
        """End-to-end: SGP outage while enforcing -> zero quotes, zero deals."""
        sellers = [_seller("seller-a", "http://seller-a.example.com")]
        sgp = MagicMock()
        sgp.check_approvals = AsyncMock(side_effect=SGPClientError("boom"))
        factory_calls: list[str] = []

        def _factory(url, **kw):
            factory_calls.append(url)
            return MagicMock()

        orch, _ = _make_orchestrator(
            sellers, deals_client_factory=_factory, sgp_client=sgp, sgp_enforce=True
        )
        result = await orch.orchestrate(
            inventory_requirements=_requirements(),
            deal_params=DealParams(
                product_id="prod-1",
                deal_type="PD",
                impressions=100_000,
                flight_start="2026-08-01",
                flight_end="2026-08-31",
            ),
            budget=10_000.0,
        )
        assert result.selection.booked_deals == []
        assert result.quote_results == []
        assert factory_calls == []

    @pytest.mark.asyncio
    async def test_denied_seller_never_quoted_in_orchestrate(self):
        """The excluded seller must never receive a quote request."""
        sellers = [
            _seller("seller-good", "http://seller-good.example.com"),
            _seller("seller-bad", "http://seller-bad.example.com"),
        ]
        sgp = _sgp_client_mock(
            {
                "seller-good.example.com": _approval("seller-good.example.com", True),
                "seller-bad.example.com": _approval("seller-bad.example.com", False),
            }
        )

        quote = QuoteResponse(
            quote_id="q-1",
            status="available",
            product=ProductInfo(product_id="prod-1", name="CTV"),
            pricing=PricingInfo(base_cpm=10.0, final_cpm=9.0),
            terms=TermsInfo(
                impressions=100_000,
                flight_start="2026-08-01",
                flight_end="2026-08-31",
                guaranteed=False,
            ),
            seller_id="seller-good",
        )
        quoted_urls: list[str] = []

        def _factory(url, **kw):
            quoted_urls.append(url)
            client = MagicMock()
            client.request_quote = AsyncMock(return_value=quote)
            client.book_deal = AsyncMock(side_effect=Exception("stop before booking"))
            return client

        orch, _ = _make_orchestrator(
            sellers, deals_client_factory=_factory, sgp_client=sgp, sgp_enforce=True
        )
        await orch.orchestrate(
            inventory_requirements=_requirements(),
            deal_params=DealParams(
                product_id="prod-1",
                deal_type="PD",
                impressions=100_000,
                flight_start="2026-08-01",
                flight_end="2026-08-31",
            ),
            budget=10_000.0,
        )
        assert "http://seller-bad.example.com" not in quoted_urls
        assert "http://seller-good.example.com" in quoted_urls


# ---------------------------------------------------------------------------
# 4. Event type exists on the shared vocabulary
# ---------------------------------------------------------------------------


class TestEventType:
    def test_sgp_vendor_gate_event_type(self):
        assert EventType.SGP_VENDOR_GATE.value == "sgp.vendor_gate"


# ---------------------------------------------------------------------------
# 5. Settings knobs: default OFF, env-overridable
# ---------------------------------------------------------------------------


class TestSettingsKnobs:
    def test_defaults_are_off_and_block(self, monkeypatch):
        for var in ("SGP_ENFORCE", "SGP_API_KEY", "SGP_UNKNOWN_VENDOR_POLICY"):
            monkeypatch.delenv(var, raising=False)
        s = settings_module.Settings()
        assert s.sgp_enforce is False
        assert s.sgp_api_key == ""
        assert s.sgp_unknown_vendor_policy == "block"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("SGP_ENFORCE", "true")
        monkeypatch.setenv("SGP_UNKNOWN_VENDOR_POLICY", "warn")
        s = settings_module.Settings()
        assert s.sgp_enforce is True
        assert s.sgp_unknown_vendor_policy == "warn"


# ---------------------------------------------------------------------------
# 6. Production wiring: settings knobs reach the canonical orchestrator
# ---------------------------------------------------------------------------


def _patch_settings(monkeypatch, **overrides):
    for var in ("SGP_ENFORCE", "SGP_API_KEY", "SGP_UNKNOWN_VENDOR_POLICY"):
        monkeypatch.delenv(var, raising=False)
    stub = settings_module.Settings(**overrides)
    monkeypatch.setattr(settings_module, "get_settings", lambda: stub)
    return stub


class TestProductionWiring:
    def test_default_settings_leave_gate_off(self, monkeypatch):
        from ad_buyer.flows.deal_booking_flow import build_default_orchestrator

        _patch_settings(monkeypatch)
        orch = build_default_orchestrator()
        assert orch._sgp_enforce is False
        assert orch._sgp_client is None

    def test_enforce_with_key_builds_real_client(self, monkeypatch):
        from ad_buyer.flows.deal_booking_flow import build_default_orchestrator

        _patch_settings(
            monkeypatch,
            sgp_enforce=True,
            sgp_api_key="test-key",
            sgp_base_url="https://api.safeguardprivacy-demo.com",
            sgp_unknown_vendor_policy="warn",
            sgp_cache_ttl_seconds=42,
        )
        orch = build_default_orchestrator()
        assert orch._sgp_enforce is True
        assert isinstance(orch._sgp_client, SGPClient)
        assert orch._sgp_client._base_url == "https://api.safeguardprivacy-demo.com"
        assert orch._sgp_client._cache_ttl == 42
        assert orch._sgp_unknown_policy == "warn"

    def test_enforce_without_key_stays_enforcing_with_no_client(self, monkeypatch):
        """Misconfiguration must fail closed at runtime, not silently open."""
        from ad_buyer.flows.deal_booking_flow import build_default_orchestrator

        _patch_settings(monkeypatch, sgp_enforce=True, sgp_api_key="")
        orch = build_default_orchestrator()
        assert orch._sgp_enforce is True
        assert orch._sgp_client is None

    def test_chat_interface_configured_sellers_wiring(self, monkeypatch):
        """The chat interface's direct construction gets the same gate."""
        from ad_buyer.interfaces.chat import main as chat_main

        _patch_settings(monkeypatch, sgp_enforce=True, sgp_api_key="test-key")

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

        assert iface._orchestrator._sgp_enforce is True
        assert isinstance(iface._orchestrator._sgp_client, SGPClient)
