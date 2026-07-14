# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for fail-closed delivery of audit-class events.

Audit-class events (AUDIT_EVENT_TYPES) must never be silently dropped:
- bus failure -> event lands in the durable fallback JSONL, caller proceeds
- bus failure + fallback failure -> exception propagates (fail-closed)
- non-audit events keep the existing fail-open behavior
- happy path is unchanged
"""

import json
from unittest.mock import patch

import pytest

import ad_buyer.events.bus as bus_mod
from ad_buyer.events.bus import InMemoryEventBus
from ad_buyer.events.helpers import emit_event, emit_event_sync
from ad_buyer.events.models import AUDIT_EVENT_TYPES, Event, EventType


class FailingBus(InMemoryEventBus):
    """Event bus whose publish always raises."""

    async def publish(self, event: Event) -> None:
        raise RuntimeError("bus down")


@pytest.fixture(autouse=True)
def reset_bus_singleton():
    """Isolate the global event bus singleton per test."""
    bus_mod._event_bus_instance = None
    yield
    bus_mod._event_bus_instance = None


@pytest.fixture
def fallback_path(tmp_path, monkeypatch):
    """Point the audit fallback JSONL at a temp file via settings."""
    from ad_buyer.config.settings import get_settings

    path = tmp_path / "audit_fallback.jsonl"
    monkeypatch.setenv("AUDIT_FALLBACK_PATH", str(path))
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


# ---------------------------------------------------------------------------
# AUDIT_EVENT_TYPES selection
# ---------------------------------------------------------------------------


class TestAuditEventTypes:
    def test_money_decision_types_are_audit_class(self):
        expected = {
            EventType.DEAL_BOOKED,
            EventType.DEAL_CANCELLED,
            EventType.BOOKING_SUBMITTED,
            EventType.BUDGET_ALLOCATED,
            EventType.CAMPAIGN_BOOKING_STARTED,
            EventType.CAMPAIGN_BOOKING_COMPLETED,
            EventType.NEGOTIATION_STARTED,
            EventType.NEGOTIATION_ROUND,
            EventType.NEGOTIATION_CONCLUDED,
            EventType.APPROVAL_REQUESTED,
            EventType.APPROVAL_GRANTED,
            EventType.APPROVAL_REJECTED,
            EventType.PACING_REALLOCATION_APPLIED,
        }
        assert expected <= AUDIT_EVENT_TYPES

    def test_observability_types_are_not_audit_class(self):
        for et in (
            EventType.QUOTE_REQUESTED,
            EventType.INVENTORY_DISCOVERED,
            EventType.SESSION_CREATED,
            EventType.PACING_SNAPSHOT_TAKEN,
        ):
            assert et not in AUDIT_EVENT_TYPES


# ---------------------------------------------------------------------------
# (a) audit event + failing bus -> fallback JSONL, transaction proceeds
# ---------------------------------------------------------------------------


class TestAuditFallbackWrite:
    async def test_publish_failure_writes_fallback(self, fallback_path):
        bus_mod._event_bus_instance = FailingBus()

        result = await emit_event(
            event_type=EventType.DEAL_BOOKED,
            flow_id="f1",
            deal_id="d1",
            payload={"price": 15.0},
        )

        # Transaction proceeds (no raise); emit reports failure via None
        assert result is None

        records = _read_jsonl(fallback_path)
        assert len(records) == 1
        assert records[0]["event_type"] == "deal.booked"
        assert records[0]["deal_id"] == "d1"
        assert records[0]["payload"] == {"price": 15.0}
        assert "bus down" in records[0]["emit_error"]
        assert records[0]["event_id"]  # full event was captured

    async def test_bus_factory_failure_writes_fallback(self, fallback_path):
        """Even if the bus factory fails before Event construction, the
        record is reconstructed from the emit arguments."""
        with patch(
            "ad_buyer.events.bus.get_event_bus",
            side_effect=RuntimeError("no bus"),
        ):
            result = await emit_event(
                event_type=EventType.APPROVAL_GRANTED,
                flow_id="f2",
                payload={"approved_by": "ops"},
            )

        assert result is None
        records = _read_jsonl(fallback_path)
        assert len(records) == 1
        assert records[0]["event_type"] == "approval.granted"
        assert records[0]["flow_id"] == "f2"
        assert records[0]["payload"] == {"approved_by": "ops"}

    def test_sync_publish_failure_writes_fallback(self, fallback_path):
        bus_mod._event_bus_instance = FailingBus()

        result = emit_event_sync(
            event_type=EventType.BOOKING_SUBMITTED,
            flow_id="f3",
            deal_id="d3",
        )

        assert result is None
        records = _read_jsonl(fallback_path)
        assert len(records) == 1
        assert records[0]["event_type"] == "booking.submitted"
        assert records[0]["deal_id"] == "d3"

    async def test_fallback_appends_multiple_records(self, fallback_path):
        bus_mod._event_bus_instance = FailingBus()

        await emit_event(event_type=EventType.DEAL_BOOKED, deal_id="d1")
        await emit_event(event_type=EventType.DEAL_CANCELLED, deal_id="d2")

        records = _read_jsonl(fallback_path)
        assert [r["event_type"] for r in records] == ["deal.booked", "deal.cancelled"]


# ---------------------------------------------------------------------------
# (b) audit event + failing bus + failing fallback -> raises (fail-closed)
# ---------------------------------------------------------------------------


class TestAuditFailClosed:
    async def test_fallback_failure_raises(self, fallback_path):
        bus_mod._event_bus_instance = FailingBus()

        with patch(
            "ad_buyer.events.helpers.write_audit_fallback",
            side_effect=OSError("disk full"),
        ):
            with pytest.raises(OSError, match="disk full"):
                await emit_event(event_type=EventType.DEAL_BOOKED, deal_id="d1")

    def test_sync_fallback_failure_raises(self, fallback_path):
        bus_mod._event_bus_instance = FailingBus()

        with patch(
            "ad_buyer.events.helpers.write_audit_fallback",
            side_effect=OSError("disk full"),
        ):
            with pytest.raises(OSError, match="disk full"):
                emit_event_sync(event_type=EventType.DEAL_CANCELLED, deal_id="d1")


# ---------------------------------------------------------------------------
# (c) non-audit event + failing bus -> swallowed (unchanged fail-open)
# ---------------------------------------------------------------------------


class TestNonAuditUnchanged:
    async def test_non_audit_failure_swallowed(self, fallback_path):
        bus_mod._event_bus_instance = FailingBus()

        result = await emit_event(event_type=EventType.QUOTE_REQUESTED, flow_id="f1")

        assert result is None
        assert not fallback_path.exists()  # no fallback write for non-audit

    def test_sync_non_audit_failure_swallowed(self, fallback_path):
        bus_mod._event_bus_instance = FailingBus()

        result = emit_event_sync(event_type=EventType.INVENTORY_DISCOVERED)

        assert result is None
        assert not fallback_path.exists()

    async def test_non_audit_failure_swallowed_even_if_fallback_broken(self, fallback_path):
        """Non-audit events never touch the fallback writer at all."""
        bus_mod._event_bus_instance = FailingBus()

        with patch(
            "ad_buyer.events.helpers.write_audit_fallback",
            side_effect=OSError("disk full"),
        ):
            result = await emit_event(event_type=EventType.SESSION_CREATED)

        assert result is None


# ---------------------------------------------------------------------------
# (d) happy path unchanged
# ---------------------------------------------------------------------------


class TestHappyPathUnchanged:
    async def test_audit_event_happy_path(self, fallback_path):
        event = await emit_event(
            event_type=EventType.DEAL_BOOKED,
            flow_id="f1",
            deal_id="d1",
            payload={"price": 15.0},
        )

        assert event is not None
        assert event.event_type == EventType.DEAL_BOOKED
        assert not fallback_path.exists()  # no fallback on success

        bus = bus_mod._event_bus_instance
        assert bus is not None
        stored = await bus.get_event(event.event_id)
        assert stored is not None

    async def test_non_audit_event_happy_path(self, fallback_path):
        event = await emit_event(event_type=EventType.QUOTE_REQUESTED, flow_id="f1")
        assert event is not None
        assert not fallback_path.exists()

    def test_sync_happy_path(self, fallback_path):
        event = emit_event_sync(event_type=EventType.DEAL_BOOKED, deal_id="d1")
        assert event is not None
        assert not fallback_path.exists()

    async def test_subscriber_error_still_isolated_for_audit_events(self, fallback_path):
        """Subscriber failures are not emission failures: the event is already
        stored on the bus, so no fallback write and no raise."""
        bus = InMemoryEventBus()
        bus_mod._event_bus_instance = bus

        def bad_subscriber(e):
            raise RuntimeError("subscriber boom")

        await bus.subscribe("deal.booked", bad_subscriber)

        event = await emit_event(event_type=EventType.DEAL_BOOKED, deal_id="d1")
        assert event is not None
        assert not fallback_path.exists()


# ---------------------------------------------------------------------------
# Fallback writer details
# ---------------------------------------------------------------------------


class TestFallbackWriter:
    def test_default_path_from_settings(self, fallback_path):
        from ad_buyer.events.audit_fallback import get_audit_fallback_path

        assert get_audit_fallback_path() == fallback_path

    def test_creates_parent_directories(self, tmp_path, monkeypatch):
        from ad_buyer.config.settings import get_settings
        from ad_buyer.events.audit_fallback import write_audit_fallback

        nested = tmp_path / "deep" / "nested" / "audit.jsonl"
        monkeypatch.setenv("AUDIT_FALLBACK_PATH", str(nested))
        get_settings.cache_clear()
        try:
            write_audit_fallback({"event_type": "deal.booked"})
        finally:
            get_settings.cache_clear()

        assert nested.exists()
        assert json.loads(nested.read_text())["event_type"] == "deal.booked"
