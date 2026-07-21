# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for the aggregate stores extracted from DealStore.

Covers the EP-2.4 god-class split: each focused store gets
a happy-path test plus one edge case, driven directly against the store
class (constructed over a shared in-memory SQLite connection).  A
structural test asserts DealStore is now a thin facade that composes the
extracted stores rather than owning all the SQL itself.

All tests use in-memory SQLite (`:memory:`) for speed and isolation.
"""

import sqlite3
import threading

import pytest

from ad_buyer.storage import (
    BookingRecordStore,
    CreativeAssetStore,
    DealActivationStore,
    DealEventStore,
    DealStore,
    DealTemplateStore,
    JobStore,
    NegotiationStore,
    PerformanceCacheStore,
    PortfolioMetadataStore,
    StatusTransitionStore,
    SupplyPathTemplateStore,
)
from ad_buyer.storage.schema import initialize_schema

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn_lock():
    """A shared in-memory SQLite connection + lock with schema initialized.

    Mirrors how DealStore.connect() wires the extracted stores: one
    connection, one lock, schema created via initialize_schema.
    """
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    initialize_schema(conn)
    lock = threading.Lock()
    yield conn, lock
    conn.close()


@pytest.fixture
def deal_id(conn_lock):
    """Insert a parent deal (FK target for several sub-stores)."""
    conn, _ = conn_lock
    conn.execute(
        "INSERT INTO deals (id, seller_url, product_id, status) VALUES (?, ?, ?, ?)",
        ("deal-1", "http://seller.example", "prod-1", "draft"),
    )
    conn.commit()
    return "deal-1"


# ---------------------------------------------------------------------------
# NegotiationStore
# ---------------------------------------------------------------------------


def test_negotiation_store_roundtrip(conn_lock, deal_id):
    conn, lock = conn_lock
    store = NegotiationStore(conn, lock)
    store.save_negotiation_round(
        deal_id=deal_id,
        proposal_id="prop-1",
        round_number=2,
        buyer_price=5.0,
        seller_price=6.0,
        action="counter",
    )
    store.save_negotiation_round(
        deal_id=deal_id,
        proposal_id="prop-1",
        round_number=1,
        buyer_price=4.0,
        seller_price=6.0,
        action="counter",
    )
    history = store.get_negotiation_history(deal_id)
    assert [r["round_number"] for r in history] == [1, 2]  # ordered ascending


def test_negotiation_store_empty_history_edge(conn_lock):
    conn, lock = conn_lock
    store = NegotiationStore(conn, lock)
    assert store.get_negotiation_history("nonexistent") == []


# ---------------------------------------------------------------------------
# BookingRecordStore
# ---------------------------------------------------------------------------


def test_booking_record_store_roundtrip(conn_lock, deal_id):
    conn, lock = conn_lock
    store = BookingRecordStore(conn, lock)
    row_id = store.save_booking_record(
        deal_id=deal_id, order_id="ord-1", impressions=1000, cost=12.5
    )
    assert isinstance(row_id, int)
    records = store.get_booking_records(deal_id)
    assert len(records) == 1
    assert records[0]["order_id"] == "ord-1"


def test_booking_record_store_default_metadata_edge(conn_lock, deal_id):
    conn, lock = conn_lock
    store = BookingRecordStore(conn, lock)
    store.save_booking_record(deal_id=deal_id)  # all defaults, metadata None
    records = store.get_booking_records(deal_id)
    assert records[0]["metadata"] == "{}"


# ---------------------------------------------------------------------------
# JobStore
# ---------------------------------------------------------------------------


def test_job_store_upsert_and_json_decode(conn_lock):
    conn, lock = conn_lock
    store = JobStore(conn, lock)
    store.save_job(job_id="job-1", status="pending", recommendations='[{"a": 1}]')
    assert store.get_job("job-1")["recommendations"] == [{"a": 1}]  # JSON decoded
    # Upsert on the same id updates status (and resets unspecified fields).
    store.save_job(job_id="job-1", status="complete")
    job = store.get_job("job-1")
    assert job["status"] == "complete"
    assert job["auto_approve"] is False  # int -> bool coercion


def test_job_store_missing_returns_none_edge(conn_lock):
    conn, lock = conn_lock
    store = JobStore(conn, lock)
    assert store.get_job("nope") is None
    assert store.list_jobs(status="pending") == []


# ---------------------------------------------------------------------------
# DealEventStore
# ---------------------------------------------------------------------------


def test_event_store_roundtrip_and_filter(conn_lock):
    conn, lock = conn_lock
    store = DealEventStore(conn, lock)
    eid = store.save_event(event_type="deal.booked", flow_id="flow-1")
    store.save_event(event_type="deal.created", flow_id="flow-2")
    assert store.get_event(eid)["event_type"] == "deal.booked"
    filtered = store.list_events(event_type="deal.booked")
    assert len(filtered) == 1 and filtered[0]["id"] == eid


def test_event_store_generated_id_edge(conn_lock):
    conn, lock = conn_lock
    store = DealEventStore(conn, lock)
    eid = store.save_event(event_type="x")  # no event_id -> generated UUID
    assert isinstance(eid, str) and len(eid) == 36


# ---------------------------------------------------------------------------
# StatusTransitionStore
# ---------------------------------------------------------------------------


def test_status_transition_store_roundtrip(conn_lock):
    conn, lock = conn_lock
    store = StatusTransitionStore(conn, lock)
    store.record_status_transition(
        entity_type="deal", entity_id="d1", from_status=None, to_status="draft"
    )
    store.record_status_transition(
        entity_type="deal", entity_id="d1", from_status="draft", to_status="active"
    )
    hist = store.get_status_history("deal", "d1")
    assert [h["to_status"] for h in hist] == ["draft", "active"]


def test_status_transition_store_isolated_by_entity_edge(conn_lock):
    conn, lock = conn_lock
    store = StatusTransitionStore(conn, lock)
    store.record_status_transition(
        entity_type="deal", entity_id="d1", from_status=None, to_status="draft"
    )
    assert store.get_status_history("deal", "other") == []


# ---------------------------------------------------------------------------
# PortfolioMetadataStore
# ---------------------------------------------------------------------------


def test_portfolio_metadata_store_crud(conn_lock, deal_id):
    conn, lock = conn_lock
    store = PortfolioMetadataStore(conn, lock)
    store.save_portfolio_metadata(deal_id=deal_id, advertiser_id="adv-1", tags='["a"]')
    assert store.get_portfolio_metadata(deal_id)["advertiser_id"] == "adv-1"
    assert store.update_portfolio_metadata(deal_id, advertiser_id="adv-2") is True
    assert store.get_portfolio_metadata(deal_id)["advertiser_id"] == "adv-2"
    assert store.delete_portfolio_metadata(deal_id) is True


def test_portfolio_metadata_store_update_rejects_unknown_edge(conn_lock, deal_id):
    conn, lock = conn_lock
    store = PortfolioMetadataStore(conn, lock)
    store.save_portfolio_metadata(deal_id=deal_id)
    # Only unknown columns -> no update performed
    assert store.update_portfolio_metadata(deal_id, bogus_col="x") is False


# ---------------------------------------------------------------------------
# DealActivationStore
# ---------------------------------------------------------------------------


def test_deal_activation_store_crud(conn_lock, deal_id):
    conn, lock = conn_lock
    store = DealActivationStore(conn, lock)
    aid = store.save_deal_activation(deal_id=deal_id, platform="TTD", activation_status="PENDING")
    assert store.get_deal_activations(deal_id)[0]["platform"] == "TTD"
    assert store.update_deal_activation(aid, activation_status="ACTIVE") is True
    assert store.get_deal_activations(deal_id)[0]["activation_status"] == "ACTIVE"
    assert store.delete_deal_activation(aid) is True


def test_deal_activation_store_delete_missing_edge(conn_lock):
    conn, lock = conn_lock
    store = DealActivationStore(conn, lock)
    assert store.delete_deal_activation(9999) is False


# ---------------------------------------------------------------------------
# PerformanceCacheStore
# ---------------------------------------------------------------------------


def test_performance_cache_store_latest_and_update(conn_lock, deal_id):
    conn, lock = conn_lock
    store = PerformanceCacheStore(conn, lock)
    store.save_performance_cache(deal_id=deal_id, spend_to_date=100.0)
    store.save_performance_cache(deal_id=deal_id, spend_to_date=250.0)
    # get returns the most recent row
    assert store.get_performance_cache(deal_id)["spend_to_date"] == 250.0
    assert store.update_performance_cache(deal_id, fill_rate=0.9) is True
    assert store.get_performance_cache(deal_id)["fill_rate"] == 0.9


def test_performance_cache_store_missing_edge(conn_lock):
    conn, lock = conn_lock
    store = PerformanceCacheStore(conn, lock)
    assert store.get_performance_cache("nope") is None
    assert store.update_performance_cache("nope", fill_rate=0.5) is False


# ---------------------------------------------------------------------------
# CreativeAssetStore
# ---------------------------------------------------------------------------


def test_creative_asset_store_crud_and_json(conn_lock):
    conn, lock = conn_lock
    store = CreativeAssetStore(conn, lock)
    aid = store.save_creative_asset(
        campaign_id="camp-1",
        asset_name="banner",
        asset_type="display",
        format_spec={"w": 300, "h": 250},
    )
    got = store.get_creative_asset(aid)
    assert got["format_spec"] == {"w": 300, "h": 250}  # JSON decoded
    assert len(store.list_creative_assets(campaign_id="camp-1")) == 1
    assert store.update_creative_asset(aid, validation_status="valid") is True
    assert store.get_creative_asset(aid)["validation_status"] == "valid"
    assert store.delete_creative_asset(aid) is True


def test_creative_asset_store_missing_edge(conn_lock):
    conn, lock = conn_lock
    store = CreativeAssetStore(conn, lock)
    assert store.get_creative_asset("nope") is None
    assert store.update_creative_asset("nope", asset_name="x") is False


# ---------------------------------------------------------------------------
# DealTemplateStore
# ---------------------------------------------------------------------------


def test_deal_template_store_crud(conn_lock):
    conn, lock = conn_lock
    store = DealTemplateStore(conn, lock)
    tid = store.save_deal_template(name="Q4 CTV", advertiser_id="adv-1", default_price=10.0)
    assert store.get_deal_template(tid)["name"] == "Q4 CTV"
    assert len(store.list_deal_templates(advertiser_id="adv-1")) == 1
    assert store.update_deal_template(tid, name="Q4 CTV v2") is True
    assert store.get_deal_template(tid)["name"] == "Q4 CTV v2"
    assert store.delete_deal_template(tid) is True


def test_deal_template_store_update_empty_edge(conn_lock):
    conn, lock = conn_lock
    store = DealTemplateStore(conn, lock)
    tid = store.save_deal_template(name="T")
    assert store.update_deal_template(tid) is False  # no kwargs -> False


# ---------------------------------------------------------------------------
# SupplyPathTemplateStore
# ---------------------------------------------------------------------------


def test_supply_path_template_store_crud(conn_lock):
    conn, lock = conn_lock
    store = SupplyPathTemplateStore(conn, lock)
    tid = store.save_supply_path_template(name="Direct-first", max_reseller_hops=1)
    assert store.get_supply_path_template(tid)["name"] == "Direct-first"
    assert len(store.list_supply_path_templates()) == 1
    assert store.update_supply_path_template(tid, max_reseller_hops=2) is True
    assert store.get_supply_path_template(tid)["max_reseller_hops"] == 2
    assert store.delete_supply_path_template(tid) is True


def test_supply_path_template_store_missing_edge(conn_lock):
    conn, lock = conn_lock
    store = SupplyPathTemplateStore(conn, lock)
    assert store.get_supply_path_template("nope") is None


# ---------------------------------------------------------------------------
# Structural: DealStore is a thin facade composing the extracted stores
# ---------------------------------------------------------------------------


def test_dealstore_composes_extracted_stores():
    """After the EP-2.4 split, DealStore must compose the aggregate stores.

    Asserts the facade wires each focused store over its shared connection
    after connect(), rather than owning all aggregate SQL itself.
    """
    store = DealStore("sqlite:///:memory:")
    store.connect()
    try:
        assert isinstance(store._negotiation_store, NegotiationStore)
        assert isinstance(store._booking_record_store, BookingRecordStore)
        assert isinstance(store._job_store, JobStore)
        assert isinstance(store._event_store, DealEventStore)
        assert isinstance(store._status_store, StatusTransitionStore)
        assert isinstance(store._portfolio_store, PortfolioMetadataStore)
        assert isinstance(store._activation_store, DealActivationStore)
        assert isinstance(store._performance_store, PerformanceCacheStore)
        assert isinstance(store._creative_asset_store, CreativeAssetStore)
        assert isinstance(store._deal_template_store, DealTemplateStore)
        assert isinstance(store._supply_path_template_store, SupplyPathTemplateStore)
        # Sub-stores share the facade's single connection and lock so
        # thread-safety and table visibility are unchanged.
        assert store._negotiation_store._conn is store._conn
        assert store._job_store._lock is store._lock
    finally:
        store.disconnect()


def _is_delegating_shim(func) -> bool:
    """True if a method's body is a single ``return self._<store>....`` call."""
    import inspect
    import textwrap

    src = textwrap.dedent(inspect.getsource(func))
    body = [
        ln.strip()
        for ln in src.splitlines()
        if ln.strip()
        and not ln.strip().startswith(("def ", "@", '"""', "#"))
        and not ln.strip().endswith(('"""',))
    ]
    # Drop a one-line docstring if present as the sole non-return line.
    returns = [ln for ln in body if ln.startswith("return self._")]
    non_returns = [ln for ln in body if not ln.startswith("return self._")]
    # A shim: exactly one delegating return, everything else is docstring text.
    return len(returns) == 1 and all(not ln.startswith("return") for ln in non_returns)


def test_dealstore_body_owns_only_core_deal_logic():
    """DealStore's own class body should be a thin facade (<15 real methods).

    Every non-shim method is counted: lifecycle (connect/disconnect/
    _wire_stores), core deal CRUD (save_deal/get_deal/list_deals/
    update_deal_status), and _parse_url.  The dozens of extracted-aggregate
    methods are now one-line delegating shims and don't count against the
    facade's own logic.
    """
    own_methods = {
        name: (obj.__func__ if isinstance(obj, staticmethod) else obj)
        for name, obj in vars(DealStore).items()
        if not name.startswith("__") and callable(getattr(DealStore, name, None))
    }
    substantive = [name for name, fn in own_methods.items() if not _is_delegating_shim(fn)]
    assert len(substantive) < 15, sorted(substantive)
    # Core deal logic stayed; aggregate methods became shims.
    assert "save_deal" in substantive
    assert "save_negotiation_round" not in substantive
