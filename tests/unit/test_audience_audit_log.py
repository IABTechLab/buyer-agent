# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the audience audit-log helper (proposal §5.7 + §6 row 13a).

Covers the deliverables called out in the bead:
  1. `log_event` writes a row; `get_events(plan_id)` returns it.
  2. Multiple events for one plan_id are returned in order.
  3. Events for different plan_ids do not bleed.
  4. `payload_json` is JSON-deserialized into the event dict.
  5. Schema migration: existing DBs without the table accept first
     `log_event` without crashing.
  6. Integration: `MultiSellerOrchestrator._book_with_audience_retry`
     emits a `degradation` audit event when degrade-and-retry fires.

Bead: ar-q2uh.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from ad_buyer.clients.deals_client import DealsClientError
from ad_buyer.models.audience_plan import AudiencePlan, AudienceRef
from ad_buyer.models.deals import (
    DealBookingRequest,
    DealResponse,
    PricingInfo,
    ProductInfo,
    TermsInfo,
)
from ad_buyer.orchestration.multi_seller import MultiSellerOrchestrator
from ad_buyer.storage import audience_audit_log
from ad_buyer.storage.audience_audit_log import (
    EVENT_CAPABILITY_REJECTION,
    EVENT_DEGRADATION,
    KNOWN_EVENT_TYPES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_audit_db(tmp_path, monkeypatch):
    """Point the audit-log helper at a fresh per-test SQLite file.

    Uses a file (not :memory:) so we can also exercise the
    "table-was-missing" migration path by re-opening the same path with a
    new connection, which is impossible with :memory:.
    """

    db_path = tmp_path / "audit.db"
    audience_audit_log.configure(f"sqlite:///{db_path}")
    yield db_path
    # Reset module-global connection so the next test gets a clean handle.
    audience_audit_log.configure("sqlite:///:memory:")


# ---------------------------------------------------------------------------
# 1: basic round-trip
# ---------------------------------------------------------------------------


class TestLogAndRead:
    def test_log_event_writes_row_and_get_events_returns_it(self, temp_audit_db):
        audience_audit_log.log_event(
            plan_id="plan-aaa",
            event_type=EVENT_DEGRADATION,
            payload={"seller_id": "seller-1", "log": []},
        )

        events = audience_audit_log.get_events("plan-aaa")

        assert len(events) == 1
        evt = events[0]
        assert evt["plan_id"] == "plan-aaa"
        assert evt["event_type"] == EVENT_DEGRADATION
        assert evt["payload"] == {"seller_id": "seller-1", "log": []}
        assert evt["created_at"]  # non-empty ISO string

    def test_payload_is_json_deserialized(self, temp_audit_db):
        # The wire format on disk is `payload_json` (string) but the helper
        # returns a dict. Assert the round-trip is real.
        payload = {
            "nested": {"a": 1, "b": [1, 2, 3]},
            "string": "hello",
            "bool": True,
        }
        audience_audit_log.log_event(
            plan_id="plan-payload",
            event_type=EVENT_DEGRADATION,
            payload=payload,
        )
        events = audience_audit_log.get_events("plan-payload")
        assert events[0]["payload"] == payload

    def test_payload_serializes_pydantic_models(self, temp_audit_db):
        # Real callers pass `DegradationLogEntry` instances (Pydantic). The
        # helper should serialize them via model_dump(mode="json").
        from ad_buyer.orchestration.audience_degradation import DegradationLogEntry

        entry = DegradationLogEntry(
            path="extensions[0]",
            reason="agentic refs not supported",
            original_ref={"type": "agentic", "identifier": "x"},
            action="dropped",
        )
        audience_audit_log.log_event(
            plan_id="plan-pyd",
            event_type=EVENT_DEGRADATION,
            payload={"log": [entry]},
        )
        events = audience_audit_log.get_events("plan-pyd")
        assert events[0]["payload"]["log"][0]["path"] == "extensions[0]"
        assert events[0]["payload"]["log"][0]["action"] == "dropped"

    def test_known_event_types_includes_documented_types(self):
        # Light guard: the constants we exposed match the proposal §5.7 list.
        assert "degradation" in KNOWN_EVENT_TYPES
        assert "capability_rejection" in KNOWN_EVENT_TYPES
        assert "snapshot_honor" in KNOWN_EVENT_TYPES
        assert "preflight_cache" in KNOWN_EVENT_TYPES


# ---------------------------------------------------------------------------
# 2: multiple events for one plan_id, ordered by created_at
# ---------------------------------------------------------------------------


class TestOrdering:
    def test_multiple_events_returned_in_insertion_order(self, temp_audit_db):
        audience_audit_log.log_event(
            "plan-multi", EVENT_CAPABILITY_REJECTION, {"step": 1}
        )
        audience_audit_log.log_event(
            "plan-multi", EVENT_DEGRADATION, {"step": 2}
        )
        audience_audit_log.log_event(
            "plan-multi", EVENT_DEGRADATION, {"step": 3}
        )

        events = audience_audit_log.get_events("plan-multi")
        assert len(events) == 3
        assert [e["payload"]["step"] for e in events] == [1, 2, 3]
        # First event should be the capability_rejection per insertion order.
        assert events[0]["event_type"] == EVENT_CAPABILITY_REJECTION
        assert events[1]["event_type"] == EVENT_DEGRADATION
        assert events[2]["event_type"] == EVENT_DEGRADATION


# ---------------------------------------------------------------------------
# 3: events for different plan_ids do not bleed
# ---------------------------------------------------------------------------


class TestIsolation:
    def test_events_isolated_by_plan_id(self, temp_audit_db):
        audience_audit_log.log_event("plan-A", EVENT_DEGRADATION, {"who": "A"})
        audience_audit_log.log_event("plan-B", EVENT_DEGRADATION, {"who": "B"})
        audience_audit_log.log_event("plan-A", EVENT_DEGRADATION, {"who": "A2"})

        events_a = audience_audit_log.get_events("plan-A")
        events_b = audience_audit_log.get_events("plan-B")

        assert [e["payload"]["who"] for e in events_a] == ["A", "A2"]
        assert [e["payload"]["who"] for e in events_b] == ["B"]

    def test_get_events_unknown_plan_returns_empty(self, temp_audit_db):
        audience_audit_log.log_event("plan-X", EVENT_DEGRADATION, {})
        assert audience_audit_log.get_events("plan-does-not-exist") == []


# ---------------------------------------------------------------------------
# 4: edge cases on log_event
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_plan_id_is_ignored(self, temp_audit_db):
        audience_audit_log.log_event("", EVENT_DEGRADATION, {"x": 1})
        # Nothing should have been written.
        all_events = audience_audit_log._all_events()
        assert all_events == []

    def test_unknown_event_type_still_writes(self, temp_audit_db):
        # Forward-compat: callers can experiment with new types ahead of
        # constants landing here. The helper logs a WARN but does NOT drop.
        audience_audit_log.log_event(
            "plan-fc", "future_event_type", {"x": 1}
        )
        events = audience_audit_log.get_events("plan-fc")
        assert len(events) == 1
        assert events[0]["event_type"] == "future_event_type"

    def test_none_payload_stored_as_empty_dict(self, temp_audit_db):
        audience_audit_log.log_event("plan-none", EVENT_DEGRADATION, None)
        events = audience_audit_log.get_events("plan-none")
        assert events[0]["payload"] == {}


# ---------------------------------------------------------------------------
# 5: schema migration -- pre-existing DB without the table
# ---------------------------------------------------------------------------


class TestSchemaMigration:
    def test_log_event_creates_table_on_existing_db_without_it(self, tmp_path):
        """A DB created before this bead lands has no audience_audit_log table.

        First call to `log_event` must not crash -- the helper's
        `_ensure_table` runs CREATE IF NOT EXISTS at connection time.
        """

        db_path = tmp_path / "legacy.db"

        # Simulate a legacy DB by creating a file with some other table but
        # NOT `audience_audit_log`. We use `deals` from the schema module so
        # the legacy DB is realistic.
        legacy = sqlite3.connect(str(db_path))
        legacy.execute(
            "CREATE TABLE pretend_other_table (id INTEGER PRIMARY KEY)"
        )
        legacy.commit()
        legacy.close()

        # Confirm the table is genuinely missing before the helper touches it.
        check = sqlite3.connect(str(db_path))
        cursor = check.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND "
            "name='audience_audit_log'"
        )
        assert cursor.fetchone() is None
        check.close()

        # Point the helper at this legacy file and write an event.
        audience_audit_log.configure(f"sqlite:///{db_path}")
        try:
            audience_audit_log.log_event(
                "plan-legacy", EVENT_DEGRADATION, {"first": True}
            )

            # The table now exists.
            check2 = sqlite3.connect(str(db_path))
            cursor = check2.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND "
                "name='audience_audit_log'"
            )
            assert cursor.fetchone() is not None
            check2.close()

            # And the event we wrote is readable.
            events = audience_audit_log.get_events("plan-legacy")
            assert events[0]["payload"] == {"first": True}
        finally:
            # Reset module-global connection so other tests get a clean handle.
            audience_audit_log.configure("sqlite:///:memory:")


# ---------------------------------------------------------------------------
# 6: integration -- orchestrator emits a degradation event
# ---------------------------------------------------------------------------


def _make_audience_plan() -> AudiencePlan:
    """A minimal plan with a primary (so degradation cannot strip it) plus an
    extension that the seller will reject."""

    return AudiencePlan(
        primary=AudienceRef(
            type="standard",
            identifier="3-7",
            taxonomy="iab-audience",
            version="1.1",
            source="explicit",
        ),
        extensions=[
            AudienceRef(
                type="standard",
                identifier="3-99",
                taxonomy="iab-audience",
                version="1.1",
                source="explicit",
            )
        ],
    )


def _make_deal_response(deal_id: str = "deal-001") -> DealResponse:
    """Minimal `DealResponse` covering the fields the orchestrator reads."""

    return DealResponse(
        deal_id=deal_id,
        deal_type="PD",
        status="active",
        product=ProductInfo(product_id="prod-1", name="Test Product"),
        pricing=PricingInfo(
            base_cpm=10.0,
            final_cpm=10.0,
            currency="USD",
        ),
        terms=TermsInfo(
            impressions=100_000,
            flight_start="2026-04-01",
            flight_end="2026-04-30",
        ),
    )


class _FakeDealsClient:
    """Test double: rejects the first booking with audience_plan_unsupported,
    accepts the second."""

    def __init__(self):
        self.calls = 0

    async def book_deal(self, request: DealBookingRequest) -> DealResponse:
        self.calls += 1
        if self.calls == 1:
            raise DealsClientError(
                "Audience plan rejected",
                status_code=400,
                error_code="audience_plan_unsupported",
                unsupported=[
                    {
                        "path": "extensions[0]",
                        "reason": "extensions not supported by this seller",
                    }
                ],
            )
        return _make_deal_response()


class TestOrchestratorEmitsAuditEvents:
    @pytest.mark.asyncio
    async def test_degrade_and_retry_emits_degradation_and_rejection(
        self, temp_audit_db
    ):
        plan = _make_audience_plan()
        plan_id = plan.audience_plan_id
        assert plan_id  # sanity: auto-computed

        # Pre-condition: no events for this plan yet.
        assert audience_audit_log.get_events(plan_id) == []

        client = _FakeDealsClient()

        orchestrator = MultiSellerOrchestrator(
            registry_client=object(),
            deals_client_factory=lambda url, **kw: client,
        )

        deal, deg_log = await orchestrator._book_with_audience_retry(
            client=client,
            quote_id="quote-1",
            seller_id="seller-1",
            audience_plan=plan,
        )

        # The retry succeeded.
        assert client.calls == 2
        assert deal.deal_id == "deal-001"
        assert len(deg_log) >= 1  # extension was dropped

        # Two audit events should have landed: a capability_rejection
        # (when the seller said no) and a degradation (when the retry
        # succeeded with the degraded plan).
        events = audience_audit_log.get_events(plan_id)
        types = [e["event_type"] for e in events]
        assert EVENT_CAPABILITY_REJECTION in types
        assert EVENT_DEGRADATION in types

        # Capability-rejection event preserves the seller's structured list.
        rejection = next(
            e for e in events if e["event_type"] == EVENT_CAPABILITY_REJECTION
        )
        assert rejection["payload"]["seller_id"] == "seller-1"
        assert rejection["payload"]["unsupported"] == [
            {
                "path": "extensions[0]",
                "reason": "extensions not supported by this seller",
            }
        ]

        # Degradation event captures what was stripped.
        degradation = next(
            e for e in events if e["event_type"] == EVENT_DEGRADATION
        )
        assert degradation["payload"]["seller_id"] == "seller-1"
        assert degradation["payload"]["deal_id"] == "deal-001"
        assert isinstance(degradation["payload"]["log"], list)
        assert degradation["payload"]["log"]  # at least one entry
        # Original-plan id is the audit key; degraded id is in the payload.
        assert degradation["plan_id"] == plan_id
        assert "degraded_plan_id" in degradation["payload"]
