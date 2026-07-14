# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""End-to-end tests for the ONE canonical buyer booking pipeline (bead ar-j2nw).

The canonical lifecycle is:

    brief -> DealBookingFlow (planning: audience plan -> budget ->
    channel research -> approval gate) -> approved recommendations ->
    DealParams -> MultiSellerOrchestrator (discover -> quote -> rank ->
    select_and_book) -> SELLER-issued deal_id + quote_id + confirmed terms.

These tests drive the REAL MultiSellerOrchestrator against a
mocked-transport seller (fake registry + fake DealsClient returning a
quote and a 201 DealResponse) and assert:

1. The booking record keys on the SELLER-issued deal_id, the quote_id it
   was booked from, and the seller's confirmed terms.
2. No placeholder identifiers ("order_pending") and no locally minted
   DEAL-xxxx ids appear anywhere on the path.
3. The EP-0.1 spend ceiling sits BEFORE the handoff (money cannot be
   committed past the approved budget).
4. Source-tree guard: "order_pending" no longer exists anywhere in src/.
"""

import json
import subprocess
from pathlib import Path

import pytest

from ad_buyer.flows.deal_booking_flow import DealBookingFlow
from ad_buyer.models.deals import (
    DealBookingRequest,
    DealResponse,
    PricingInfo,
    ProductInfo,
    QuoteRequest,
    QuoteResponse,
    TermsInfo,
)
from ad_buyer.models.flow_state import ExecutionStatus, ProductRecommendation
from ad_buyer.orchestration.multi_seller import MultiSellerOrchestrator
from ad_buyer.registry.models import AgentCard, TrustLevel
from ad_buyer.storage import DealStore

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"

SELLER_URL = "http://seller-one.test"
SELLER_ISSUED_DEAL_ID = "SLR-DEAL-8F3A2C91"  # minted by the SELLER, not the buyer
SELLER_ISSUED_QUOTE_ID = "SLR-QUOTE-77B1"


# ---------------------------------------------------------------------------
# Mocked-transport seller: fake registry + fake DealsClient
# ---------------------------------------------------------------------------


class FakeRegistry:
    """Registry stub returning a single verified seller."""

    async def discover_sellers(self, capabilities_filter=None):
        return [
            AgentCard(
                agent_id="seller-one",
                name="Seller One",
                url=SELLER_URL,
                trust_level=TrustLevel.VERIFIED,
            )
        ]


class FakeDealsClient:
    """Mocked-transport seller speaking the real quotes -> deals contract.

    request_quote returns a priced QuoteResponse; book_deal returns the
    201 DealResponse with the SELLER-issued deal_id and confirmed terms.
    """

    def __init__(self, seller_url: str, **kwargs):
        self.seller_url = seller_url
        self.quote_requests: list[QuoteRequest] = []
        self.booking_requests: list[DealBookingRequest] = []

    async def request_quote(self, quote_request: QuoteRequest) -> QuoteResponse:
        self.quote_requests.append(quote_request)
        return QuoteResponse(
            quote_id=SELLER_ISSUED_QUOTE_ID,
            status="available",
            product=ProductInfo(product_id=quote_request.product_id, name="Homepage Takeover"),
            pricing=PricingInfo(base_cpm=14.0, final_cpm=14.0),
            terms=TermsInfo(
                impressions=quote_request.impressions,
                flight_start=quote_request.flight_start,
                flight_end=quote_request.flight_end,
            ),
            seller_id="seller-one",
        )

    async def book_deal(self, booking_request: DealBookingRequest) -> DealResponse:
        self.booking_requests.append(booking_request)
        # The seller's 201 response: the ONLY place a deal id is minted.
        return DealResponse(
            deal_id=SELLER_ISSUED_DEAL_ID,
            deal_type="PD",
            status="active",
            quote_id=booking_request.quote_id,
            product=ProductInfo(product_id="prod-display-001", name="Homepage Takeover"),
            pricing=PricingInfo(base_cpm=14.0, final_cpm=14.0),
            terms=TermsInfo(
                impressions=100_000,
                flight_start="2026-04-01",
                flight_end="2026-04-30",
            ),
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_deals_clients() -> list[FakeDealsClient]:
    return []


@pytest.fixture
def real_orchestrator(fake_deals_clients) -> MultiSellerOrchestrator:
    """A REAL MultiSellerOrchestrator over the mocked-transport seller."""

    def _factory(seller_url: str, **kwargs) -> FakeDealsClient:
        client = FakeDealsClient(seller_url, **kwargs)
        fake_deals_clients.append(client)
        return client

    return MultiSellerOrchestrator(
        registry_client=FakeRegistry(),
        deals_client_factory=_factory,
    )


@pytest.fixture
def deal_store():
    store = DealStore("sqlite:///:memory:")
    store.connect()
    yield store
    store.disconnect()


def _brief() -> dict:
    return {
        "name": "Canonical Path Campaign",
        "objectives": ["brand awareness"],
        "budget": 10_000,
        "start_date": "2026-04-01",
        "end_date": "2026-04-30",
        "target_audience": {"geo": ["US"]},
    }


def _approved_recommendation() -> ProductRecommendation:
    rec = ProductRecommendation(
        product_id="prod-display-001",
        product_name="Homepage Takeover",
        publisher=SELLER_URL,
        channel="branding",
        impressions=100_000,
        cpm=15.0,
        cost=1_500.0,
    )
    rec.status = "pending_approval"
    return rec


def _flow(orchestrator, store=None) -> DealBookingFlow:
    from unittest.mock import MagicMock

    flow = DealBookingFlow(
        client=MagicMock(),
        store=store,
        orchestrator=orchestrator,
        campaign_brief=_brief(),
    )
    return flow


# ---------------------------------------------------------------------------
# 1. End-to-end: brief -> approval -> real orchestrator -> seller deal id
# ---------------------------------------------------------------------------


class TestCanonicalBookingEndToEnd:
    """brief -> approved recommendation -> quote -> 201 deal -> booking record."""

    def test_booking_record_keys_on_seller_issued_identifiers(
        self, real_orchestrator, fake_deals_clients, deal_store
    ):
        flow = _flow(real_orchestrator, store=deal_store)

        # Planning half output, past the approval gate.
        store_deal_id = deal_store.save_deal(
            seller_url=SELLER_URL,
            product_id="prod-display-001",
            product_name="Homepage Takeover",
            deal_type="PD",
            status="awaiting_approval",
        )
        rec = _approved_recommendation()
        rec._store_deal_id = store_deal_id
        flow.state.pending_approvals = [rec]
        flow.state.execution_status = ExecutionStatus.AWAITING_APPROVAL

        result = flow.approve_all()

        # The seller was really driven through quote -> book. (The
        # orchestrator constructs one client for quoting and another for
        # booking, so aggregate across instances.)
        quote_requests = [q for c in fake_deals_clients for q in c.quote_requests]
        booking_requests = [b for c in fake_deals_clients for b in c.booking_requests]
        assert len(quote_requests) == 1
        assert quote_requests[0].product_id == "prod-display-001"
        assert len(booking_requests) == 1
        assert booking_requests[0].quote_id == SELLER_ISSUED_QUOTE_ID

        # Flow state: booked line keyed by the SELLER-issued identifiers
        # and the seller's confirmed terms.
        assert result["status"] == "success"
        assert result["booked"] == 1
        booked = flow.state.booked_lines[0]
        assert booked.deal_id == SELLER_ISSUED_DEAL_ID
        assert booked.quote_id == SELLER_ISSUED_QUOTE_ID
        assert booked.cpm == 14.0  # confirmed CPM, not the researched estimate
        assert booked.impressions == 100_000
        assert booked.cost == 1_400.0  # 100_000 * 14.0 / 1000 (confirmed terms)
        assert booked.order_id is None
        assert booked.line_id is None

        # Persistence: the booking record carries the seller-issued keys.
        records = deal_store.get_booking_records(store_deal_id)
        assert len(records) == 1
        metadata = json.loads(records[0]["metadata"])
        assert metadata["seller_deal_id"] == SELLER_ISSUED_DEAL_ID
        assert metadata["quote_id"] == SELLER_ISSUED_QUOTE_ID
        assert metadata["final_cpm"] == 14.0
        assert deal_store.get_deal(store_deal_id)["status"] == "booked"

        # No placeholder / locally minted identifiers anywhere.
        state_dump = flow.state.model_dump_json()
        assert "order_pending" not in state_dump
        assert not booked.deal_id.startswith("DEAL-")  # buyer's legacy local-mint prefix

    def test_seller_booking_failure_yields_no_booked_line(
        self, fake_deals_clients, deal_store
    ):
        """A seller that never confirms produces NO booking record at all."""

        class RejectingDealsClient(FakeDealsClient):
            async def book_deal(self, booking_request):
                from ad_buyer.clients.deals_client import DealsClientError

                raise DealsClientError(
                    "booking rejected", status_code=409, detail="inventory gone"
                )

        orchestrator = MultiSellerOrchestrator(
            registry_client=FakeRegistry(),
            deals_client_factory=lambda url, **kw: RejectingDealsClient(url, **kw),
        )
        flow = _flow(orchestrator, store=deal_store)
        rec = _approved_recommendation()
        flow.state.pending_approvals = [rec]

        result = flow.approve_all()

        assert result["booked"] == 0
        assert result["status"] == "failed"
        assert flow.state.booked_lines == []
        assert flow.state.execution_status == ExecutionStatus.FAILED
        # No fabricated booking: nothing pretends an order/deal exists.
        assert "order_pending" not in flow.state.model_dump_json()

    def test_spend_ceiling_guards_before_any_seller_contact(
        self, real_orchestrator, fake_deals_clients
    ):
        """EP-0.1: over-budget approvals are rejected BEFORE quotes/bookings."""
        flow = _flow(real_orchestrator)
        rec = _approved_recommendation()
        rec.cost = 999_999.0  # exceeds the 10k budget in the brief
        flow.state.pending_approvals = [rec]

        result = flow.approve_all()

        assert result["status"] == "rejected"
        assert result["booked"] == 0
        assert flow.state.execution_status == ExecutionStatus.FAILED
        # The seller was NEVER contacted: no client was even constructed.
        assert fake_deals_clients == []


# ---------------------------------------------------------------------------
# 2. Source-tree guard: the fake booking path stays dead
# ---------------------------------------------------------------------------


class TestNoFakeBookingIdentifiersInSource:
    """Grep-style guards: rival-path artifacts must not reappear in src/."""

    def test_no_order_pending_anywhere_in_src(self):
        """'order_pending' (the faked booking placeholder) is banned in src/."""
        completed = subprocess.run(
            ["grep", "-r", "-l", "order_pending", str(SRC_ROOT)],
            capture_output=True,
            text=True,
            check=False,
        )
        offenders = [line for line in completed.stdout.splitlines() if line.strip()]
        assert completed.returncode == 1 and not offenders, (
            "'order_pending' must not exist anywhere in src/ — the canonical "
            f"path books real seller deals. Offending files: {offenders}"
        )

    def test_flows_do_not_mint_local_deal_ids(self):
        """No flow may import the local deal-id minter (seller issues ids)."""
        flows_dir = SRC_ROOT / "ad_buyer" / "flows"
        offenders = [
            str(path)
            for path in flows_dir.rglob("*.py")
            if "deal_id import generate_deal_id" in path.read_text()
            or "generate_deal_id(" in path.read_text()
        ]
        assert not offenders, (
            f"Flows must never mint local DEAL-xxxx ids: {offenders}"
        )
