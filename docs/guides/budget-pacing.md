# Budget Pacing & Reallocation

The budget pacing engine monitors campaign spend against plan in real time, detects over-delivery and under-delivery, and proposes cross-channel budget reallocations mid-flight. After a campaign's deals are booked via the [campaign pipeline](campaign-pipeline.md), pacing answers three questions continuously: **Are we on pace? What is off? What should we do about it?**

The pacing engine is implemented by `BudgetPacingEngine` in `ad_buyer.pacing.engine`. Snapshots are persisted by `PacingStore` in `ad_buyer.storage.pacing_store`, and pacing events flow through the [event bus](../event-bus/overview.md).

---

## How It Works

The engine uses a **linear pacing model**: expected spend at any point is proportional to the fraction of the flight window that has elapsed. It calculates pacing at three levels --- campaign, channel, and deal --- and generates reallocation recommendations when channels deviate from plan.

```mermaid
flowchart LR
    Spend["Collect\nSpend Data"]
    Calculate["Calculate\nExpected vs Actual"]
    Detect["Detect\nDeviations"]
    Recommend["Propose\nReallocations"]
    Snapshot["Save\nSnapshot"]

    Spend --> Calculate --> Detect --> Recommend --> Snapshot
```

---

## Quick Example

```python
from datetime import datetime, timezone
from ad_buyer.pacing.engine import BudgetPacingEngine, PacingConfig
from ad_buyer.storage.pacing_store import PacingStore
from ad_buyer.events.bus import InMemoryEventBus

# Setup
engine = BudgetPacingEngine(
    config=PacingConfig(),
    event_bus=InMemoryEventBus(),
)

store = PacingStore("sqlite:///./ad_buyer.db")
store.connect()

# Generate a pacing snapshot
snapshot = engine.generate_snapshot(
    campaign_id="campaign-abc",
    total_budget=150_000.0,
    flight_start=datetime(2026, 7, 1, tzinfo=timezone.utc),
    flight_end=datetime(2026, 9, 30, tzinfo=timezone.utc),
    current_time=datetime(2026, 8, 15, tzinfo=timezone.utc),
    channel_data={
        "CTV": {"allocated_budget": 75_000, "spend": 30_000, "impressions": 1_200_000},
        "DISPLAY": {"allocated_budget": 45_000, "spend": 28_000, "impressions": 2_800_000},
        "AUDIO": {"allocated_budget": 30_000, "spend": 10_000, "impressions": 600_000},
    },
    deal_data=[
        {"deal_id": "deal-001", "allocated_budget": 40_000, "spend": 16_000},
        {"deal_id": "deal-002", "allocated_budget": 35_000, "spend": 14_000},
    ],
)

# Inspect results
print(f"Campaign pacing: {snapshot.pacing_pct:.1f}%")
print(f"Deviation: {snapshot.deviation_pct:+.1f}%")

for ch in snapshot.channel_snapshots:
    print(f"  {ch.channel}: {ch.pacing_pct:.1f}% paced, ${ch.spend:,.0f} spent")

for rec in snapshot.recommendations:
    print(f"  Recommend: move ${rec.amount:,.0f} from {rec.source_channel} to {rec.target_channel}")

# Persist the snapshot
store.save_pacing_snapshot(snapshot)
```

---

## Pacing Calculations

### Expected Spend (Linear Model)

Expected spend at any point in the campaign is:

```
expected_spend = total_budget * (elapsed_time / total_flight_duration)
```

The engine clamps the calculation to the flight window --- before the start date, expected spend is zero; after the end date, expected spend equals the total budget.

```python
expected = engine.calculate_expected_spend(
    total_budget=150_000.0,
    flight_start=datetime(2026, 7, 1, tzinfo=timezone.utc),
    flight_end=datetime(2026, 9, 30, tzinfo=timezone.utc),
    current_time=datetime(2026, 8, 15, tzinfo=timezone.utc),
)
# expected ≈ 73,770 (about 49.2% of flight elapsed)
```

### Pacing Percentage

Pacing percentage is `(actual_spend / expected_spend) * 100`:

| Value | Meaning |
|-------|---------|
| 100% | Exactly on pace |
| < 100% | Underpacing (spending slower than planned) |
| > 100% | Overpacing (spending faster than planned) |

```python
pacing = engine.calculate_pacing_pct(actual_spend=68_000, expected_spend=73_770)
# pacing ≈ 92.2% (slightly underpacing)
```

### Deviation Percentage

Deviation is `pacing_pct - 100`. Negative means underpacing, positive means overpacing:

```python
deviation = engine.calculate_deviation_pct(actual_spend=68_000, expected_spend=73_770)
# deviation ≈ -7.8%
```

---

## Deviation Detection

The engine detects pacing deviations at two severity levels, for both underpacing and overpacing:

| Direction | Warning Threshold | Critical Threshold |
|-----------|------------------:|-----------------:|
| Underpacing | > 10% below expected | > 25% below expected |
| Overpacing | > 10% above expected | > 25% above expected |

!!! tip "Configuring thresholds"
    All thresholds are configurable via `PacingConfig`:

    ```python
    config = PacingConfig(
        underpacing_warning_pct=10.0,
        underpacing_critical_pct=25.0,
        overpacing_warning_pct=10.0,
        overpacing_critical_pct=25.0,
    )
    ```

When a deviation exceeds a threshold, `detect_deviation()` returns a `PacingAlert`:

```python
alert = engine.detect_deviation(actual_spend=50_000, expected_spend=73_770)
# alert.level = "warning"
# alert.direction = "underpacing"
# alert.deviation_pct ≈ -32.2
# alert.message = "Critical underpacing: -32.2% deviation (threshold: -25.0%)"
```

Alerts at the **critical** level indicate the campaign is severely off-pace and likely needs intervention. Warning-level alerts are informational --- the campaign is drifting but may self-correct.

---

## Cross-Channel Budget Reallocation

When the pacing engine detects that some channels are underpacing while others are overpacing, it proposes **budget reallocations** --- shifting money from underperforming channels to channels that are spending efficiently.

### How Proposals Are Generated

1. For each channel, the engine calculates expected spend proportionally: `channel_expected = campaign_expected * (channel_budget / total_budget)`
2. Channels deviating below the underpacing warning threshold are classified as **sources** (potential budget donors)
3. Channels deviating above the overpacing warning threshold are classified as **targets** (budget recipients)
4. For each source-target pair, the reallocation amount is the minimum of the source's underspend, the target's overspend, and the max reallocation cap

### Reallocation Constraints

| Constraint | Default | Description |
|-----------|---------|-------------|
| `min_reallocation_amount` | $100 | Amounts below this are not worth the operational cost |
| `max_reallocation_pct` | 30% | Maximum percentage of total budget that can be reallocated in a single proposal |

```python
config = PacingConfig(
    min_reallocation_amount=500.0,   # Only propose if $500+
    max_reallocation_pct=20.0,       # Cap at 20% of total budget
)
```

### Proposal Data Model

Each `ReallocationProposal` contains:

| Field | Type | Description |
|-------|------|-------------|
| `source_channel` | `str` | Channel to take budget from (underpacing) |
| `target_channel` | `str` | Channel to give budget to (overpacing) |
| `amount` | `float` | Dollar amount to reallocate |
| `reason` | `str` | Human-readable justification |

```python
proposals = engine.propose_reallocations(
    channel_snapshots=snapshot.channel_snapshots,
    total_budget=150_000.0,
    expected_spend=73_770.0,
)

for p in proposals:
    print(f"Move ${p.amount:,.0f} from {p.source_channel} to {p.target_channel}")
    print(f"  Reason: {p.reason}")
```

!!! warning "Proposals require approval"
    Reallocation proposals are **recommendations**, not automatic actions. By default, the `PACING_ADJUSTMENT` approval stage is disabled, meaning proposals can be applied automatically. Enable it in the campaign brief's `approval_config` to require human sign-off before budget is moved.

---

## Deal-Level Pacing

In addition to campaign and channel-level pacing, the engine tracks pacing for individual deals:

```python
from ad_buyer.models.campaign import DealSnapshot

deal_pacing = engine.calculate_deal_pacing(
    deal_snapshot=DealSnapshot(
        deal_id="deal-001",
        allocated_budget=40_000,
        spend=16_000,
        impressions=640_000,
        effective_cpm=25.0,
    ),
    flight_start=datetime(2026, 7, 1, tzinfo=timezone.utc),
    flight_end=datetime(2026, 9, 30, tzinfo=timezone.utc),
    current_time=datetime(2026, 8, 15, tzinfo=timezone.utc),
)

print(f"Deal {deal_pacing['deal_id']}: {deal_pacing['pacing_pct']:.1f}% paced")
print(f"  Expected: ${deal_pacing['expected_spend']:,.0f}")
print(f"  Actual: ${deal_pacing['actual_spend']:,.0f}")
if deal_pacing["alert"]:
    print(f"  Alert: {deal_pacing['alert'].message}")
```

---

## Pacing Snapshots

A `PacingSnapshot` is a point-in-time capture of the entire campaign's pacing state. The `generate_snapshot()` method produces one by computing pacing at all three levels and generating any applicable recommendations.

### Snapshot Data Model

```python
class PacingSnapshot(BaseModel):
    snapshot_id: str            # UUID
    campaign_id: str
    timestamp: datetime
    total_budget: float
    total_spend: float
    pacing_pct: float           # Campaign-level pacing %
    expected_spend: float
    deviation_pct: float        # Positive = overpacing, negative = underpacing
    channel_snapshots: list[ChannelSnapshot]
    deal_snapshots: list[DealSnapshot]
    recommendations: list[PacingRecommendation]
```

Each `ChannelSnapshot` captures:

| Field | Type | Description |
|-------|------|-------------|
| `channel` | `str` | Channel name (CTV, DISPLAY, etc.) |
| `allocated_budget` | `float` | Budget allocated to this channel |
| `spend` | `float` | Actual spend to date |
| `pacing_pct` | `float` | Channel pacing percentage |
| `impressions` | `int` | Impressions delivered |
| `effective_cpm` | `float` | Blended CPM |
| `fill_rate` | `float` | Fill rate (0.0 to 1.0) |

Each `DealSnapshot` adds `deal_id`, `win_rate`, and the same spend/impression metrics.

### Persistence

Snapshots are persisted via `PacingStore`, a SQLite-backed store with the same thread-safety pattern as `DealStore`:

```python
from ad_buyer.storage.pacing_store import PacingStore

store = PacingStore("sqlite:///./ad_buyer.db")
store.connect()

# Save
store.save_pacing_snapshot(snapshot)

# Retrieve latest
latest = store.get_latest_pacing_snapshot("campaign-abc")

# List with time filter
from datetime import datetime, timezone
snapshots = store.list_pacing_snapshots(
    campaign_id="campaign-abc",
    start_time=datetime(2026, 8, 1, tzinfo=timezone.utc),
    end_time=datetime(2026, 8, 15, tzinfo=timezone.utc),
)
```

The `pacing_snapshots` table is indexed on `campaign_id` and `timestamp` for efficient time-series queries.

---

## Events Emitted

The pacing engine emits three event types:

| Event | When | Payload |
|-------|------|---------|
| `pacing.snapshot_taken` | After every snapshot generation | `snapshot_id`, budget/spend/pacing metrics, channel and deal counts |
| `pacing.deviation_detected` | When campaign-level deviation exceeds a threshold | `alert_level`, `direction`, `deviation_pct`, `message` |
| `pacing.reallocation_recommended` | For each reallocation proposal | `source_channel`, `target_channel`, `amount`, `reason` |

Subscribe to these events to build dashboards, trigger alerts, or automate reallocation approval workflows.

---

## Integration with the Campaign Lifecycle

Budget pacing operates on campaigns that have reached **ACTIVE** status. The typical flow:

1. Campaign pipeline books deals and moves campaign to READY
2. Campaign is activated (manually or on flight start date) --- status becomes ACTIVE
3. Pacing engine begins generating snapshots at regular intervals
4. If deviation exceeds the critical threshold, the state machine can transition the campaign to **PACING_HOLD** --- an automated hold distinct from manual PAUSED
5. When deviation resolves, PACING_HOLD auto-transitions back to ACTIVE
6. If it does not resolve, it can be escalated to PAUSED for manual intervention

---

## Configuration Reference

`PacingConfig` controls all engine behavior:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `underpacing_warning_pct` | 10.0 | Warning threshold for underpacing |
| `underpacing_critical_pct` | 25.0 | Critical threshold for underpacing |
| `overpacing_warning_pct` | 10.0 | Warning threshold for overpacing |
| `overpacing_critical_pct` | 25.0 | Critical threshold for overpacing |
| `min_reallocation_amount` | 100.0 | Minimum amount for a reallocation proposal |
| `max_reallocation_pct` | 30.0 | Max percentage of total budget per proposal |

---

## Related

- [Campaign Brief to Deal Pipeline](campaign-pipeline.md) --- Initial campaign setup (pacing monitors what the pipeline books)
- [Deals API](../api/deals.md) --- Deal status and modification endpoints
- [Multi-Seller Orchestration](multi-seller-orchestration.md) --- Cross-seller portfolio management
- [Architecture Overview](../architecture/overview.md) --- Agent hierarchy and system design
