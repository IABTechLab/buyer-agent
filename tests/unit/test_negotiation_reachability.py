# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Stage 3.5 reachability from the booking flow.

Bug G: ``DealBookingFlow._book_approved`` used to set BOTH the
negotiation ceiling (``InventoryRequirements.max_cpm``) AND the RFQ target
(``DealParams.target_cpm``) to the same ``rec.cpm``, discarding the brief's
target/ceiling split (``kpis.target_cpm_usd`` / ``kpis.max_cpm_usd``).
Because the seller grants any target >= floor inside the quote, Stage 3.5's
entry band ``(max_cpm, max_cpm * band]`` could never contain a quote, making
negotiation structurally unreachable from the API booking flow (proven in
docs/reports/S2_NEGOTIATION_FORCING_PROOF_2026-07-21.md).

These tests pin the fix:

1. A brief with a distinct target/ceiling reaches the orchestrator with
   BOTH distinct values (ceiling -> max_cpm, target -> target_cpm).
2. The forcing-proof attempt-7 scenario shape (target $25 / ceiling $32 vs
   a $35-list, $28-floor seller) actually ENTERS Stage 3.5 (negotiation
   events emitted) and books at <= the ceiling.
3. A brief WITHOUT an explicit split preserves the previous behavior:
   both values fall back to ``rec.cpm``.

Bug H: the audit rationale on a booked line must state the TRUE
final price (the seller's confirmed ``final_cpm``), never the base/list
price. The buyer assembles it from the confirmed deal pricing so the text
can never contradict ``final_cpm``.
"""

from unittest.mock import MagicMock

import pytest

from ad_buyer.events.models import EventType
from ad_buyer.models.deals import (
    DealResponse,
    PricingInfo,
    ProductInfo,
    QuoteRequest,
    QuoteResponse,
    TermsInfo,
)
from ad_buyer.models.flow_state import ExecutionStatus, ProductRecommendation
from ad_buyer.orchestration.multi_seller import (
    DealSelection,
    MultiSellerOrchestrator,
    OrchestrationResult,
)
from ad_buyer.registry.models import AgentCard, TrustLevel

SELLER_URL = "http://ctv-seller.test"

LIST_PRICE = 35.0  # seller's list/base CPM (CTV Premium Streaming shape)
FLOOR = 28.0  # seller's negotiation floor
CEILING = 32.0  # brief kpis.max_cpm_usd
TARGET = 25.0  # brief kpis.target_cpm_usd (below the floor -> refused)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRegistry:
    """Registry stub returning a single verified seller."""

    async def discover_sellers(self, capabilities_filter=None):
        return [
            AgentCard(
                agent_id="ctv-seller",
                name="CTV Seller",
                url=SELLER_URL,
                trust_level=TrustLevel.VERIFIED,
            )
        ]


class GrantOrListDealsClient:
    """Mocked-transport seller mirroring the live quote engine's pricing.

    A target at/above the floor is granted as the quote's ``final_cpm``;
    a target below the floor (or no target) quotes the LIST price. This is
    exactly the grant-or-list behavior that made Stage 3.5 unreachable
    when the RFQ target equaled the ceiling.
    """

    def __init__(self, seller_url: str, quote_cpms: dict | None = None, **kwargs):
        self.seller_url = seller_url
        self.quote_requests: list[QuoteRequest] = []
        self.booking_requests = []
        # Shared across instances: the orchestrator constructs one client
        # for quoting and another for booking.
        self._quote_cpms: dict[str, float] = quote_cpms if quote_cpms is not None else {}

    def _price_for_target(self, target_cpm):
        if target_cpm is not None and target_cpm >= FLOOR:
            return float(target_cpm)
        return LIST_PRICE

    async def request_quote(self, quote_request: QuoteRequest) -> QuoteResponse:
        self.quote_requests.append(quote_request)
        final_cpm = self._price_for_target(quote_request.target_cpm)
        quote_id = f"q-{len(self._quote_cpms) + 1}"
        self._quote_cpms[quote_id] = final_cpm
        return QuoteResponse(
            quote_id=quote_id,
            status="available",
            product=ProductInfo(
                product_id=quote_request.product_id, name="CTV Premium Streaming"
            ),
            pricing=PricingInfo(base_cpm=LIST_PRICE, final_cpm=final_cpm),
            terms=TermsInfo(
                impressions=quote_request.impressions,
                flight_start=quote_request.flight_start,
                flight_end=quote_request.flight_end,
            ),
            seller_id="ctv-seller",
        )

    async def book_deal(self, booking_request) -> DealResponse:
        self.booking_requests.append(booking_request)
        final_cpm = self._quote_cpms.get(booking_request.quote_id, LIST_PRICE)
        return DealResponse(
            deal_id="SLR-DEAL-NEGOTIATED",
            deal_type="PD",
            status="active",
            quote_id=booking_request.quote_id,
            product=ProductInfo(product_id="prod-ctv-001", name="CTV Premium Streaming"),
            pricing=PricingInfo(base_cpm=LIST_PRICE, final_cpm=final_cpm),
            terms=TermsInfo(
                impressions=1_000_000,
                flight_start="2026-09-01",
                flight_end="2026-09-30",
            ),
        )


class FloorCounterNegotiationClient:
    """Negotiation surface that counters every proposal at the seller floor."""

    def __init__(self):
        self.proposals = []
        self.accepted = []
        self.declined = []

    async def submit_proposal(self, **kwargs):
        self.proposals.append(kwargs)
        return {
            "proposal_id": "prop-1",
            "recommendation": "counter",
            "status": "counter_pending",
            "counter_terms": {
                "proposed_price": FLOOR,
                "floor_price": FLOOR,
                "negotiation_id": "neg-1",
                "round_number": 1,
                "action": "counter",
            },
        }

    async def counter_offer(self, session, price):  # pragma: no cover - not reached
        raise AssertionError("floor counter is <= ceiling; no round 2 expected")

    async def accept(self, session):
        self.accepted.append(session)
        return {}

    async def decline(self, session):
        self.declined.append(session)
        return None


class RecordingBus:
    """Event bus recorder for orchestrator-emitted events."""

    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


class CapturingOrchestrator:
    """Stub orchestrator capturing the flow's handoff arguments."""

    def __init__(self):
        self.calls = []

    async def orchestrate(self, inventory_requirements, deal_params, budget, max_deals=3):
        self.calls.append(
            {
                "inventory_requirements": inventory_requirements,
                "deal_params": deal_params,
                "budget": budget,
            }
        )
        return OrchestrationResult(
            discovered_sellers=[],
            quote_results=[],
            ranked_quotes=[],
            selection=DealSelection(
                booked_deals=[],
                failed_bookings=[],
                total_spend=0.0,
                remaining_budget=budget,
            ),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _brief(kpis: dict | None = None) -> dict:
    brief = {
        "name": "S2F Negotiation Campaign",
        "objectives": ["awareness"],
        "budget": 40_000,
        "start_date": "2026-09-01",
        "end_date": "2026-09-30",
        "target_audience": {"geo": ["US"]},
    }
    if kpis is not None:
        brief["kpis"] = kpis
    return brief


def _split_kpis() -> dict:
    return {"target_cpm_usd": TARGET, "max_cpm_usd": CEILING}


def _rec(cpm: float = CEILING, impressions: int = 1_000_000) -> ProductRecommendation:
    rec = ProductRecommendation(
        product_id="prod-ctv-001",
        product_name="CTV Premium Streaming",
        publisher=SELLER_URL,
        channel="ctv",
        impressions=impressions,
        cpm=cpm,
        cost=round(impressions * cpm / 1000.0, 2),
    )
    rec.status = "pending_approval"
    return rec


def _flow(orchestrator, brief: dict, rec: ProductRecommendation):
    from ad_buyer.flows.deal_booking_flow import DealBookingFlow

    flow = DealBookingFlow(
        client=MagicMock(),
        orchestrator=orchestrator,
        campaign_brief=brief,
    )
    flow.state.pending_approvals = [rec]
    flow.state.execution_status = ExecutionStatus.AWAITING_APPROVAL
    return flow


# ---------------------------------------------------------------------------
# 1. The brief's target/ceiling split reaches the orchestrator distinct
# ---------------------------------------------------------------------------


class TestBriefPriceSplitReachesOrchestrator:
    def test_distinct_target_and_ceiling_are_threaded(self):
        """kpis target $25 / ceiling $32 arrive as target_cpm=25, max_cpm=32."""
        orch = CapturingOrchestrator()
        flow = _flow(orch, _brief(kpis=_split_kpis()), _rec(cpm=CEILING))

        flow.approve_all()

        assert len(orch.calls) == 1
        call = orch.calls[0]
        assert call["inventory_requirements"].max_cpm == CEILING
        assert call["deal_params"].target_cpm == TARGET
        # The split must actually be a split.
        assert call["deal_params"].target_cpm != call["inventory_requirements"].max_cpm

    def test_legacy_top_level_keys_are_honored(self):
        """Legacy top-level max_cpm/target_cpm work like the kpis keys."""
        orch = CapturingOrchestrator()
        brief = _brief()
        brief["max_cpm"] = CEILING
        brief["target_cpm"] = TARGET
        flow = _flow(orch, brief, _rec(cpm=CEILING))

        flow.approve_all()

        call = orch.calls[0]
        assert call["inventory_requirements"].max_cpm == CEILING
        assert call["deal_params"].target_cpm == TARGET

    def test_no_split_falls_back_to_rec_cpm(self):
        """No explicit split: both values fall back to rec.cpm (old behavior)."""
        orch = CapturingOrchestrator()
        flow = _flow(orch, _brief(), _rec(cpm=15.0, impressions=100_000))

        flow.approve_all()

        call = orch.calls[0]
        assert call["inventory_requirements"].max_cpm == 15.0
        assert call["deal_params"].target_cpm == 15.0

    def test_partial_split_only_ceiling(self):
        """Only a ceiling in kpis: target still falls back to rec.cpm."""
        orch = CapturingOrchestrator()
        flow = _flow(
            orch, _brief(kpis={"max_cpm_usd": CEILING}), _rec(cpm=30.0)
        )

        flow.approve_all()

        call = orch.calls[0]
        assert call["inventory_requirements"].max_cpm == CEILING
        assert call["deal_params"].target_cpm == 30.0

    def test_non_positive_split_values_are_ignored(self):
        """Zero/negative/non-numeric kpi values behave as absent."""
        orch = CapturingOrchestrator()
        flow = _flow(
            orch,
            _brief(kpis={"target_cpm_usd": 0, "max_cpm_usd": "high"}),
            _rec(cpm=15.0, impressions=100_000),
        )

        flow.approve_all()

        call = orch.calls[0]
        assert call["inventory_requirements"].max_cpm == 15.0
        assert call["deal_params"].target_cpm == 15.0


# ---------------------------------------------------------------------------
# 2. Attempt-7 scenario: Stage 3.5 fires end-to-end and books <= ceiling
# ---------------------------------------------------------------------------


class TestStage35ReachableEndToEnd:
    def _run_flow(self):
        """brief target 25 / ceiling 32 vs $35-list, $28-floor seller."""
        clients: list[GrantOrListDealsClient] = []
        quote_cpms: dict[str, float] = {}

        def factory(seller_url: str, **kwargs) -> GrantOrListDealsClient:
            client = GrantOrListDealsClient(seller_url, quote_cpms=quote_cpms, **kwargs)
            clients.append(client)
            return client

        bus = RecordingBus()
        negotiation_client = FloorCounterNegotiationClient()
        orchestrator = MultiSellerOrchestrator(
            registry_client=FakeRegistry(),
            deals_client_factory=factory,
            event_bus=bus,
            negotiation_client=negotiation_client,
        )
        flow = _flow(orchestrator, _brief(kpis=_split_kpis()), _rec(cpm=CEILING))
        result = flow.approve_all()
        return flow, result, clients, bus, negotiation_client

    def test_negotiation_fires_and_books_at_or_below_ceiling(self):
        flow, result, clients, bus, negotiation_client = self._run_flow()

        # The RFQ carried the brief TARGET (below floor) -> quote at list.
        first_quote = [q for c in clients for q in c.quote_requests][0]
        assert first_quote.target_cpm == TARGET

        # Stage 3.5 actually fired: proposal opened at the target.
        assert len(negotiation_client.proposals) == 1
        assert negotiation_client.proposals[0]["price"] == TARGET

        # Negotiation events were emitted on the bus.
        types = [e.event_type for e in bus.events]
        assert EventType.NEGOTIATION_STARTED in types
        assert EventType.NEGOTIATION_CONCLUDED in types

        # Booked via the negotiated re-quote at the floor, under the ceiling.
        assert result["status"] == "success"
        assert result["booked"] == 1
        booked = flow.state.booked_lines[0]
        assert booked.cpm == FLOOR
        assert booked.cpm <= CEILING

    def test_negotiated_booking_never_at_list_price(self):
        flow, result, clients, bus, negotiation_client = self._run_flow()
        booked = flow.state.booked_lines[0]
        assert booked.cpm != LIST_PRICE
        assert booked.cost == pytest.approx(1_000_000 * FLOOR / 1000.0)


# ---------------------------------------------------------------------------
# 3. Bug H: the booked-line rationale states the TRUE final price
# ---------------------------------------------------------------------------


class TestBookedRationaleStatesTrueFinalPrice:
    def _booked_line_and_event(self, monkeypatch):
        """Book through a target-grant seller: base $35 -> final $32."""
        clients: list[GrantOrListDealsClient] = []
        quote_cpms: dict[str, float] = {}

        def factory(seller_url: str, **kwargs) -> GrantOrListDealsClient:
            client = GrantOrListDealsClient(seller_url, quote_cpms=quote_cpms, **kwargs)
            clients.append(client)
            return client

        orchestrator = MultiSellerOrchestrator(
            registry_client=FakeRegistry(),
            deals_client_factory=factory,
        )
        # Brief target at the ceiling (the pre-fix live shape): the seller
        # grants $32 inside the quote while listing at $35.
        flow = _flow(
            orchestrator,
            _brief(kpis={"target_cpm_usd": CEILING, "max_cpm_usd": CEILING}),
            _rec(cpm=CEILING),
        )

        events = []

        def record_event(event_type, **kwargs):
            events.append((event_type, kwargs))

        monkeypatch.setattr(
            "ad_buyer.flows.deal_booking_flow.emit_event_sync", record_event
        )

        result = flow.approve_all()
        assert result["booked"] == 1
        booked = flow.state.booked_lines[0]
        deal_events = [
            kwargs for etype, kwargs in events if etype == EventType.DEAL_BOOKED
        ]
        return booked, deal_events[0]["payload"]

    def test_rationale_reports_final_cpm_not_base(self, monkeypatch):
        booked, payload = self._booked_line_and_event(monkeypatch)

        assert booked.cpm == CEILING  # the granted concession price
        assert booked.rationale is not None
        # The TRUE final price is stated...
        assert f"Final price: ${CEILING:.2f} CPM" in booked.rationale
        # ...and the base/list price is never misreported as final.
        assert f"Final price: ${LIST_PRICE:.2f}" not in booked.rationale
        # Base price is reported separately for the audit trail.
        assert f"Base price: ${LIST_PRICE:.2f} CPM" in booked.rationale

    def test_deal_booked_event_carries_the_same_rationale(self, monkeypatch):
        booked, payload = self._booked_line_and_event(monkeypatch)

        assert payload["final_cpm"] == CEILING
        assert payload["rationale"] == booked.rationale
